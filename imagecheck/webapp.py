"""Shared state and helpers for the Streamlit app (app.py + app_pages/)."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from .config import DEFAULT_CACHE_DIR, DEFAULT_INDEX_DIR, DEFAULT_SCAN_DIR, Thresholds
from .features import Embedder
from .fetch import ImageCache
from .index import Index
from .pages import load_meta
from .scan import ScanJob
from .search import Searcher

STALE_AFTER_DAYS = 30


@st.cache_resource(show_spinner="Loading the image model (the first run downloads about 90 MB)...")
def load_embedder() -> Embedder:
    return Embedder()


@st.cache_resource(max_entries=2, show_spinner="Loading the image index...")
def _load_index(version: int) -> Index:
    return Index.load(DEFAULT_INDEX_DIR)


def current_index() -> Index | None:
    """The latest index on disk; a finished scan is picked up on the next rerun."""
    path = DEFAULT_INDEX_DIR / "images.json"
    if not path.exists():
        return None
    return _load_index(path.stat().st_mtime_ns)


def get_searcher() -> Searcher | None:
    index = current_index()
    if index is None:
        return None
    searcher = st.session_state.get("searcher")
    if searcher is None or searcher.index is not index:
        searcher = Searcher(index, load_embedder(), ImageCache(DEFAULT_CACHE_DIR),
                            Thresholds.load(DEFAULT_INDEX_DIR / "thresholds.json"))
        st.session_state.searcher = searcher
    return searcher


@st.cache_data(max_entries=2, show_spinner=False)
def _page_paths(version: int) -> list[str]:
    import json
    from urllib.parse import urlsplit

    paths = []
    with (DEFAULT_SCAN_DIR / "pages.jsonl").open() as f:
        for line in f:
            # Only the URL is needed; avoid parsing whole (large) page records.
            start = line.find('"url":"')
            if start < 0:
                continue
            try:
                url = json.loads(line[start + 6:line.index('"', start + 7) + 1])
            except (ValueError, json.JSONDecodeError):
                continue
            if url.startswith("http"):
                paths.append(urlsplit(url).path or "/")
    return paths


def known_page_paths() -> list[str]:
    """URL paths of every page in the store (for scan-size estimates); cached until the store changes."""
    path = DEFAULT_SCAN_DIR / "pages.jsonl"
    return _page_paths(path.stat().st_mtime_ns) if path.exists() else []


@st.cache_resource
def scan_job() -> ScanJob:
    """One scan runner per server process, shared by every browser session."""
    return ScanJob(embedder_factory=lambda: None, scan_dir=DEFAULT_SCAN_DIR,
                   index_dir=DEFAULT_INDEX_DIR, cache_dir=DEFAULT_CACHE_DIR)


def prepare_job() -> ScanJob:
    """The job, with the model loaded on this script thread (not inside the scan thread)."""
    job = scan_job()
    embedder = load_embedder()
    job.embedder_factory = lambda: embedder
    return job


def _parse_time(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        t = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def describe_age(stamp: str | None) -> tuple[str, int | None]:
    """('Mar 9, 2026', 254) for an ISO timestamp."""
    t = _parse_time(stamp)
    if t is None:
        return "an unknown date", None
    days = (datetime.now(timezone.utc) - t).days
    return f"{t:%b} {t.day}, {t.year}", days


def ago(days: int | None) -> str:
    if days is None:
        return ""
    if days <= 0:
        return "today"
    return "yesterday" if days == 1 else f"{days:,} days ago"


def freshness() -> dict:
    meta = load_meta(DEFAULT_SCAN_DIR)
    full = meta.get("last_full_scan") or {}
    last = meta.get("last_scan") or {}
    date, days = describe_age(full.get("finished_at"))
    if full.get("date_unknown"):
        date, days = "an unknown date", None
    return {"full": full, "last": last, "date": date, "days": days,
            "stale": days is None or days > STALE_AFTER_DAYS}
