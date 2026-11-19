"""Scan the live site and bring the image index up to date.

    crawl (Columbia-Crawler) -> merge into the page store -> inventory -> build_index

Used by the Streamlit "Site scan" page (on a background thread, via ScanJob)
and by scripts/scan_site.py. Progress is written to `<scan dir>/status.json`,
so the UI can follow a scan started from either place. A lock file stops two
scans running at once, and a `cancel` file asks a running scan to stop.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .config import DEFAULT_CACHE_DIR, DEFAULT_INDEX_DIR, DEFAULT_SCAN_DIR, Scope
from .index import BuildCancelled, Index, build_index
from .ingest import inventory_from_pages
from .pages import PageStore, import_screaming_frog, now_iso

DEFAULT_START_URL = "https://www.columbiadoctors.org/"
# Healthwise's licensed health library: ~6,700 pages (70% of the site) whose
# images are third-party and out of scope. "/*" keeps its landing page, which
# does show site-owned images.
DEFAULT_SKIP_FOLDERS = ("health-library/*",)
STATUS_EVERY_S = 1.0


@dataclass
class ScanSettings:
    start_url: str = DEFAULT_START_URL
    # Folders to scan (e.g. ["news", "childrens-health"]); empty = the whole site.
    include_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=lambda: list(DEFAULT_SKIP_FOLDERS))
    max_pages: int = 15000
    workers: int = 8
    # 4 requests/s: load-tested on columbiadoctors.org (Fastly) with no
    # throttling, blocking or slowdown; the crawler backs off by itself on 429/503.
    delay: float = 0.25
    respect_robots: bool = True
    # columbiadoctors.org's robots.txt asks for 10s between requests; like
    # Screaming Frog we obey its Disallow rules but not the delay by default.
    honor_crawl_delay: bool = False

    @property
    def full_site(self) -> bool:
        return not [p for p in self.include_paths if p.strip().removesuffix("/*").strip("/")]

    def reachable(self, url: str) -> bool:
        """Could this scan visit `url`? (Same folder rules as the crawler.)"""
        from crawler import Crawler

        path = urlsplit(url).path
        if any(Crawler._path_matches(ex, path) for ex in self.exclude_paths if ex.strip()):
            return False
        includes = [p for p in self.include_paths if p.strip().removesuffix("/*").strip("/")]
        return not includes or any(Crawler._path_matches(inc.strip().removesuffix("/*"), path) for inc in includes)


@dataclass
class ScanStatus:
    state: str = "idle"  # idle | running | done | failed | cancelled | interrupted
    phase: str = ""  # crawling | indexing | importing
    kind: str = "scan"  # scan | import | reindex
    scan_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    updated_at: str = ""
    settings: dict = field(default_factory=dict)
    pages_done: int = 0
    pages_queued: int = 0
    pages_unchanged: int = 0  # 304: reused from the previous scan
    pages_failed: int = 0
    images_seen: int = 0  # distinct in-scope originals on pages crawled so far
    index_done: int = 0
    index_total: int = 0
    message: str = ""
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    pid: int = 0

    @property
    def active(self) -> bool:
        return self.state == "running"

    def save(self, scan_dir: Path) -> None:
        scan_dir.mkdir(parents=True, exist_ok=True)
        self.updated_at = now_iso()
        tmp = scan_dir / f"status.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_text(json.dumps(asdict(self), indent=1))
        os.replace(tmp, scan_dir / "status.json")

    @classmethod
    def load(cls, scan_dir: Path) -> "ScanStatus":
        path = Path(scan_dir) / "status.json"
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return cls()
        st = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        if st.state == "running" and not ScanLock.held(Path(scan_dir)):
            st.state = "interrupted"  # the process running it died
        return st


# ---------------------------------------------------------------- locking


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ScanBusy(RuntimeError):
    pass


class ScanNotAllowed(ValueError):
    """The scan would leave the site it's configured for."""


def _host_allowed(host: str, scope: Scope) -> bool:
    return host.rstrip(".").lower() in {h.lower() for h in scope.hosts}


def validate_start_url(url: str, scope: Scope | None = None) -> str:
    """Only crawl the configured site over https.

    The app may be shared with a team, and a free-text start URL would otherwise
    let anyone make the server fetch arbitrary (including internal) addresses.
    """
    scope = scope or Scope()
    url = (url or "").strip()
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or not _host_allowed(host, scope) or parts.username or parts.password \
            or parts.port not in (None, 443):
        raise ScanNotAllowed(f"Scans can only start on https://{scope.hosts[0]}/ (allowed hosts: "
                             f"{', '.join(scope.hosts)}); got {url!r}.")
    return url


class ScanLock:
    """One scan at a time across the app and the CLI (lock file with the owner's pid)."""

    def __init__(self, scan_dir: Path):
        self.path = Path(scan_dir) / "scan.lock"

    @staticmethod
    def held(scan_dir: Path) -> bool:
        path = Path(scan_dir) / "scan.lock"
        try:
            pid = int(json.loads(path.read_text()).get("pid", 0))
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        return _pid_alive(pid)

    def __enter__(self) -> "ScanLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if ScanLock.held(self.path.parent):
                    raise ScanBusy("another scan is already running") from None
                self.path.unlink(missing_ok=True)  # stale: its owner is gone
                continue
            with os.fdopen(fd, "w") as f:
                json.dump({"pid": os.getpid(), "since": now_iso()}, f)
            return self
        raise ScanBusy("could not acquire the scan lock")

    def __exit__(self, *exc) -> None:
        self.path.unlink(missing_ok=True)


def request_cancel(scan_dir: Path = DEFAULT_SCAN_DIR) -> None:
    Path(scan_dir).mkdir(parents=True, exist_ok=True)
    (Path(scan_dir) / "cancel").write_text(now_iso())


# ---------------------------------------------------------------- seeding


def _sitemap_seeds(crawler, settings: "ScanSettings", scope: Scope) -> tuple[list[str], list[str]]:
    """Page URLs from the sitemaps robots.txt lists (or /sitemap.xml), filtered like the crawl.

    Only sitemaps on allowed hosts are fetched, including ones nested in a sitemap index.
    """
    import requests

    from sitemap import fetch_sitemap_urls  # Columbia-Crawler

    parts = urlsplit(settings.start_url)
    sitemaps = list(getattr(crawler, "sitemap_urls", lambda: [])()) or [f"{parts.scheme}://{parts.netloc}/sitemap.xml"]
    session_get = crawler.session.get

    def safe_get(url, **kw):
        if not _host_allowed(urlsplit(url).hostname or "", scope):
            raise requests.RequestException(f"off-site sitemap {url} skipped")
        return session_get(url, **kw)

    urls: list[str] = []
    warnings: list[str] = []
    includes = [p for p in settings.include_paths if p.strip().removesuffix("/*").strip("/")]
    for sm in sitemaps:
        if not _host_allowed(urlsplit(sm).hostname or "", scope):
            continue
        res = fetch_sitemap_urls(sm, settings.max_pages, excluded_paths=settings.exclude_paths,
                                 included_paths=includes, get=safe_get)
        urls += res.urls
        warnings += [w for w in res.warnings if "include path" not in w]
    return urls, warnings


# ---------------------------------------------------------------- the job


class _Reporter:
    """Throttled status writes plus the cancel check."""

    def __init__(self, status: ScanStatus, scan_dir: Path, on_status: Callable[[ScanStatus], None] | None,
                 stop_event: threading.Event | None):
        self.status, self.scan_dir, self.on_status, self.stop_event = status, scan_dir, on_status, stop_event
        self._last = 0.0

    def push(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last >= STATUS_EVERY_S:
            self._last = now
            self.status.save(self.scan_dir)
            if self.on_status:
                self.on_status(self.status)

    def should_stop(self) -> bool:
        return bool(self.stop_event and self.stop_event.is_set()) or (self.scan_dir / "cancel").exists()


def _reindex(store: PageStore, rep: _Reporter, *, embedder_factory, index_dir: Path, cache_dir: Path,
             scope: Scope, log: Callable[[str], None]) -> dict:
    """Rebuild the index from the page store; returns before/after image counts."""
    st = rep.status
    st.phase = "indexing"
    st.message = "Updating the image index"
    rep.push(force=True)
    inventory = inventory_from_pages(store.pages.values(), scope)
    old_urls: set[str] = set()
    if (index_dir / "images.json").exists():
        old_urls = {r["url"] for r in Index.load(index_dir).images}

    def progress(phase: str, done: int, total: int) -> None:
        st.message = "Downloading new images" if phase == "downloading" else "Re-embedding cached images"
        st.index_done, st.index_total = done, total
        rep.push()

    idx = build_index(
        inventory, index_dir, cache_dir, embedder_factory(),
        source={**store.freshness(), "pages": len(store.real_pages())},
        log=log, progress=progress, should_stop=rep.should_stop,
    )
    new_urls = {r["url"] for r in idx.images}
    return {
        "images": len(idx.images),
        "indexed": len(idx.ok_images),
        "added": len(new_urls - old_urls),
        "removed": len(old_urls - new_urls),
        "not_indexed": len(idx.failed_images),
        "index_cancelled": bool(idx.meta.get("cancelled")),
    }


def _run(kind: str, settings: dict, body, *, scan_dir: Path, on_status, stop_event) -> ScanStatus:
    scan_dir = Path(scan_dir)
    status = ScanStatus(state="running", kind=kind, message="Starting", scan_id=time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
                        started_at=now_iso(), settings=settings, pid=os.getpid())
    rep = _Reporter(status, scan_dir, on_status, stop_event)
    try:
        with ScanLock(scan_dir):
            (scan_dir / "cancel").unlink(missing_ok=True)
            rep.push(force=True)
            try:
                body(rep)
                if status.state == "running":
                    status.state = "done"
            except BuildCancelled as e:
                status.state, status.message = "cancelled", str(e)
            except Exception as e:  # surfaced in the UI, never swallowed
                status.state, status.error = "failed", f"{type(e).__name__}: {e}"
            finally:
                status.finished_at = now_iso()
                (scan_dir / "cancel").unlink(missing_ok=True)
                rep.push(force=True)
    except ScanBusy as e:
        status.state, status.error = "failed", str(e)
        if on_status:
            on_status(status)  # don't overwrite the running scan's status file
    return status


def run_scan(
    settings: ScanSettings,
    *,
    embedder_factory: Callable[[], Any],
    scan_dir: Path = DEFAULT_SCAN_DIR,
    index_dir: Path = DEFAULT_INDEX_DIR,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    scope: Scope | None = None,
    on_status: Callable[[ScanStatus], None] | None = None,
    stop_event: threading.Event | None = None,
    crawler_factory: Callable[..., Any] | None = None,
    log: Callable[[str], None] = lambda m: None,
) -> ScanStatus:
    """Crawl the site, update the page store, then update the image index."""
    scan_dir, index_dir, cache_dir = Path(scan_dir), Path(index_dir), Path(cache_dir)
    scope = scope or Scope()
    if crawler_factory is None:
        from crawler import Crawler as crawler_factory  # Columbia-Crawler

    def body(rep: _Reporter) -> None:
        st = rep.status
        validate_start_url(settings.start_url, scope)
        st.phase, st.message = "crawling", "Crawling pages"
        store = PageStore.load(scan_dir)
        crawler = crawler_factory(
            settings.start_url,
            max_pages=settings.max_pages,
            delay=settings.delay,
            respect_robots=settings.respect_robots,
            exclude_paths=settings.exclude_paths,
            include_paths=[p for p in settings.include_paths if p.strip().strip("/")],
            max_workers=settings.workers,
            cached_pages={u: p for u, p in store.pages.items() if not p.get("synthetic")},
            honor_crawl_delay=settings.honor_crawl_delay,
        )
        # Link-following never reaches orphan pages (e.g. old annual reports), so
        # also start from every page we already know about and from the sitemaps.
        if hasattr(crawler, "add_seeds"):
            st.message = "Reading sitemaps"
            rep.push(force=True)
            known = [u for u, p in store.pages.items() if not p.get("synthetic") and settings.reachable(u)]
            sitemap_urls, sitemap_warnings = _sitemap_seeds(crawler, settings, scope)
            st.warnings += sitemap_warnings
            crawler.add_seeds(known + sitemap_urls)
            st.summary["seeds"] = {"known_pages": len(known), "sitemap_pages": len(sitemap_urls)}
        st.message = "Crawling pages"
        run_dir = scan_dir / "runs" / st.scan_id
        run_dir.mkdir(parents=True, exist_ok=True)
        fetched: list[dict] = []
        seen_images: set[str] = set()
        cancelled = False
        with (run_dir / "pages.jsonl").open("w") as out:
            for page, done, remaining in crawler.crawl():
                # The crawler follows a start-URL redirect to a new host; never off the site.
                host = urlsplit(f"//{getattr(crawler, 'base_netloc', '')}").hostname or ""
                if host and not _host_allowed(host, scope):
                    raise ScanNotAllowed(f"The site redirected the crawl to {host}, which isn't an allowed host.")
                rec = page.to_dict()
                out.write(json.dumps(rec, separators=(",", ":")) + "\n")
                fetched.append(rec)
                st.pages_done, st.pages_queued = done, remaining
                st.pages_unchanged += bool(rec.get("from_cache"))
                st.pages_failed += bool(rec.get("error") or int(rec.get("status_code") or 0) >= 400
                                        or not rec.get("status_code"))
                seen_images.update(inventory_from_pages([rec], scope))
                st.images_seen = len(seen_images)
                rep.push()
                if rep.should_stop():
                    cancelled = True
                    break
        st.warnings = list(getattr(crawler, "warnings", []) or [])
        capped = len(getattr(crawler, "queue", ()) or ()) > 0 and st.pages_done >= settings.max_pages
        if capped:
            st.warnings.append(f"Stopped at the {settings.max_pages:,}-page limit; pages beyond it weren't checked, "
                               "so their images were kept from earlier scans.")
        complete = settings.full_site and not cancelled and not capped

        merge = store.merge(fetched, full_and_complete=complete, reachable=settings.reachable)
        if merge.note:
            st.warnings.append(merge.note)
        store.record_scan(kind="scan", finished_at=now_iso(), complete=complete and merge.replaced_store,
                          start_url=settings.start_url, include_paths=settings.include_paths,
                          exclude_paths=settings.exclude_paths, pages=len(fetched))
        store.save(scan_dir)
        st.summary = {**st.summary, "pages": len(fetched), "pages_unchanged": st.pages_unchanged,
                      "pages_failed": st.pages_failed, "pages_removed": merge.removed,
                      "complete": complete and merge.replaced_store}
        if cancelled:
            st.state = "cancelled"
            st.message = (f"Cancelled after {len(fetched):,} pages. Those pages were saved; "
                          "the image index was not updated. Run a scan (or Update index) to finish.")
            return
        st.summary.update(_reindex(store, rep, embedder_factory=embedder_factory, index_dir=index_dir,
                                   cache_dir=cache_dir, scope=scope, log=log))
        if st.summary.get("index_cancelled"):
            st.state, st.message = "cancelled", "Cancelled while downloading new images; the index was saved as far as it got."
        else:
            st.message = "Scan complete"

    return _run("scan", asdict(settings), body, scan_dir=scan_dir, on_status=on_status, stop_event=stop_event)


def run_import(
    crawl_files: list[Path],
    *,
    embedder_factory: Callable[[], Any],
    scan_dir: Path = DEFAULT_SCAN_DIR,
    index_dir: Path = DEFAULT_INDEX_DIR,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    scope: Scope | None = None,
    on_status: Callable[[ScanStatus], None] | None = None,
    stop_event: threading.Event | None = None,
    log: Callable[[str], None] = lambda m: None,
) -> ScanStatus:
    """Load Screaming Frog exports into the page store, then update the index."""
    scan_dir = Path(scan_dir)

    def body(rep: _Reporter) -> None:
        st = rep.status
        st.phase, st.message = "importing", "Reading Screaming Frog exports"
        rep.push(force=True)
        store = PageStore.load(scan_dir)
        merge = import_screaming_frog(store, [Path(p) for p in crawl_files])
        store.save(scan_dir)
        st.pages_done = merge.fetched
        st.summary = {"pages": merge.fetched, "pages_added": merge.added}
        if not store.is_empty and not merge.replaced_store:
            st.warnings.append("Pages already known from scans were kept; the export only added pages they lacked.")
        st.summary.update(_reindex(store, rep, embedder_factory=embedder_factory, index_dir=Path(index_dir),
                                   cache_dir=Path(cache_dir), scope=scope or Scope(), log=log))
        st.message = "Import complete"

    return _run("import", {"files": [Path(p).name for p in crawl_files]}, body,
                scan_dir=scan_dir, on_status=on_status, stop_event=stop_event)


def run_reindex(*, embedder_factory, scan_dir: Path = DEFAULT_SCAN_DIR, index_dir: Path = DEFAULT_INDEX_DIR,
                cache_dir: Path = DEFAULT_CACHE_DIR, scope: Scope | None = None, on_status=None,
                stop_event=None, log=lambda m: None) -> ScanStatus:
    """Bring the index in line with the page store (e.g. after a cancelled scan)."""
    scan_dir = Path(scan_dir)

    def body(rep: _Reporter) -> None:
        store = PageStore.load(scan_dir)
        rep.status.summary = _reindex(store, rep, embedder_factory=embedder_factory, index_dir=Path(index_dir),
                                      cache_dir=Path(cache_dir), scope=scope or Scope(), log=log)
        rep.status.message = "Index updated"

    return _run("reindex", {}, body, scan_dir=scan_dir, on_status=on_status, stop_event=stop_event)


class ScanJob:
    """Runs scans on a background thread (one at a time) for the Streamlit app."""

    def __init__(self, embedder_factory: Callable[[], Any], *, scan_dir: Path = DEFAULT_SCAN_DIR,
                 index_dir: Path = DEFAULT_INDEX_DIR, cache_dir: Path = DEFAULT_CACHE_DIR):
        self.embedder_factory = embedder_factory
        self.dirs = {"scan_dir": Path(scan_dir), "index_dir": Path(index_dir), "cache_dir": Path(cache_dir)}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.last_result: ScanStatus | None = None

    @property
    def running(self) -> bool:
        return (self._thread is not None and self._thread.is_alive()) or ScanLock.held(self.dirs["scan_dir"])

    def status(self) -> ScanStatus:
        return ScanStatus.load(self.dirs["scan_dir"])

    def _start(self, target, **kwargs) -> bool:
        with self._lock:
            if self.running:
                return False
            self._stop = threading.Event()

            def go():
                self.last_result = target(embedder_factory=self.embedder_factory, stop_event=self._stop,
                                          **self.dirs, **kwargs)

            self._thread = threading.Thread(target=go, name="imagecheck-scan", daemon=True)
            self._thread.start()
            return True

    def start_scan(self, settings: ScanSettings) -> bool:
        return self._start(run_scan, settings=settings)

    def start_import(self, crawl_files: list[Path]) -> bool:
        return self._start(run_import, crawl_files=crawl_files)

    def start_reindex(self) -> bool:
        return self._start(run_reindex)

    def cancel(self) -> None:
        self._stop.set()
        request_cancel(self.dirs["scan_dir"])  # also reaches a scan started from the CLI

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)
