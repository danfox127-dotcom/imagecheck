"""The page store: which page shows which image, as of the latest scans.

One record per page URL (Columbia-Crawler's PageData.to_dict() shape), kept in
`data/scans/pages.jsonl`, with metadata in `data/scans/store.json`. The image
index is always rebuilt from this store.

Merge rules keep partial work from destroying good data:
  * a completed full-site scan replaces the store (pages that are gone drop out);
  * a section scan, or a full scan that was capped or cancelled, only replaces
    the pages it actually fetched.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

# A "complete" full scan that saw fewer than this share of the pages we already
# know about is suspicious (blocked by robots.txt, a firewall, an outage...).
# Its pages are merged instead of replacing the store.
MIN_FULL_SCAN_COVERAGE = 0.5


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class MergeStats:
    fetched: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    replaced_store: bool = False
    note: str = ""


@dataclass
class PageStore:
    pages: dict[str, dict] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    # ------------------------------------------------------------ io

    @classmethod
    def load(cls, store_dir: Path) -> "PageStore":
        store_dir = Path(store_dir)
        pages: dict[str, dict] = {}
        path = store_dir / "pages.jsonl"
        if path.exists():
            with path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("url"):
                        pages[rec["url"]] = rec
        meta_path = store_dir / "store.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        return cls(pages, meta)

    def save(self, store_dir: Path) -> None:
        store_dir = Path(store_dir)
        store_dir.mkdir(parents=True, exist_ok=True)
        tmp = store_dir / "pages.jsonl.tmp"
        with tmp.open("w") as f:
            for url in sorted(self.pages):
                f.write(json.dumps(self.pages[url], separators=(",", ":")) + "\n")
        os.replace(tmp, store_dir / "pages.jsonl")
        tmp = store_dir / "store.json.tmp"
        tmp.write_text(json.dumps(self.meta, indent=1))
        os.replace(tmp, store_dir / "store.json")

    @property
    def is_empty(self) -> bool:
        return not self.pages

    def real_pages(self) -> list[dict]:
        return [p for p in self.pages.values() if not p.get("synthetic")]

    # ------------------------------------------------------------ updates

    def merge(self, fetched: Iterable[dict], *, full_and_complete: bool, overwrite: bool = True,
              reachable: Callable[[str], bool] | None = None) -> MergeStats:
        """Fold fetched page records in.

        `overwrite=False` only adds unknown pages (used when importing an export
        that may be older than what we have). `reachable(url)` says whether the
        scan was allowed to visit a page at all: a complete scan only removes
        unvisited pages it could have reached, so skipped folders stay as they were.
        """
        fetched = {p["url"]: p for p in fetched if p.get("url")}
        stats = MergeStats(fetched=len(fetched))
        reachable = reachable or (lambda url: True)
        known = sum(1 for p in self.real_pages() if reachable(p["url"]))
        if full_and_complete and known and len(fetched) < MIN_FULL_SCAN_COVERAGE * known:
            full_and_complete = False
            stats.note = (f"The scan reached only {len(fetched):,} pages, against {known:,} known from earlier "
                          "scans, so pages it didn't reach were kept rather than removed.")
        for url, rec in fetched.items():
            if url in self.pages:
                if not overwrite:
                    continue
                stats.updated += 1
            else:
                stats.added += 1
            self.pages[url] = rec
        if full_and_complete:
            gone = [u for u, p in self.pages.items()
                    if u not in fetched and (p.get("synthetic") or reachable(u))]
            for u in gone:
                del self.pages[u]
            stats.removed = len(gone)
            stats.replaced_store = True
        return stats

    def record_scan(self, *, kind: str, finished_at: str, complete: bool, start_url: str = "",
                    include_paths: list[str] | None = None, exclude_paths: list[str] | None = None,
                    pages: int = 0) -> None:
        """Update freshness metadata after a scan or import."""
        entry = {"kind": kind, "finished_at": finished_at, "complete": complete, "start_url": start_url,
                 "include_paths": include_paths or [], "exclude_paths": exclude_paths or [], "pages": pages}

        def newer(key: str) -> bool:
            prev = self.meta.get(key)
            return prev is None or finished_at >= prev.get("finished_at", "")

        if newer("last_scan"):
            self.meta["last_scan"] = entry
        if complete and not include_paths and newer("last_full_scan"):
            self.meta["last_full_scan"] = entry
        history = self.meta.setdefault("history", [])
        history.append(entry)
        del history[:-20]

    def freshness(self) -> dict:
        """When the store last saw the whole site, and the most recent scan of any kind."""
        return {"last_full_scan": self.meta.get("last_full_scan"), "last_scan": self.meta.get("last_scan")}


def load_meta(store_dir: Path) -> dict:
    """Just the store metadata (cheap: doesn't read the page records)."""
    path = Path(store_dir) / "store.json"
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def import_screaming_frog(store: PageStore, crawl_files: list[Path]) -> MergeStats:
    """Load Screaming Frog exports into the store as if they were one full scan."""
    from .ingest import pages_from_screaming_frog

    pages, crawled_at = pages_from_screaming_frog(crawl_files)
    # Into an empty store the export is the whole picture. Otherwise it only
    # fills gaps: scanned page records are never replaced by an export.
    empty = store.is_empty
    stats = store.merge(pages, full_and_complete=empty, overwrite=empty)
    # A crawl date is required for freshness; with none, use the oldest
    # possible stamp so the export never claims to be newer than a real scan.
    stamp = crawled_at.replace(" ", "T") if crawled_at else "1970-01-01T00:00:00"
    store.record_scan(kind="screaming_frog", finished_at=stamp, complete=True,
                      pages=sum(1 for p in pages if not p.get("synthetic")))
    last = store.meta.get("last_full_scan") or {}
    if last.get("finished_at") == stamp and last.get("kind") == "screaming_frog":
        last["files"] = [Path(p).name for p in crawl_files]
        if not crawled_at:
            last["date_unknown"] = True
    return stats
