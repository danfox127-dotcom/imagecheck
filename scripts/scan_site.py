"""Scan the live site and update the image index (same as the app's "Site scan" page).

    python scripts/scan_site.py                        # whole site (minus health-library/*)
    python scripts/scan_site.py --include news         # just /news/...
    python scripts/scan_site.py --exclude news/archive/* --exclude health-library/*
    python scripts/scan_site.py --no-skip              # really everything (~2.5x longer)
    python scripts/scan_site.py --honor-crawl-delay    # slower, politer
    python scripts/scan_site.py --reindex              # only rebuild the index from saved pages

Ctrl-C stops the scan gracefully; pages fetched so far are kept. Suitable for
cron or a scheduled CI job.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imagecheck.config import DEFAULT_CACHE_DIR, DEFAULT_INDEX_DIR, DEFAULT_SCAN_DIR  # noqa: E402
from imagecheck.scan import (DEFAULT_SKIP_FOLDERS, DEFAULT_START_URL, ScanSettings, ScanStatus,  # noqa: E402
                             run_reindex, run_scan)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start-url", default=DEFAULT_START_URL)
    ap.add_argument("--include", action="append", default=[], metavar="FOLDER",
                    help="only scan this folder (repeatable), e.g. --include news")
    ap.add_argument("--exclude", action="append", metavar="FOLDER",
                    help="skip this folder (repeatable; replaces the default "
                         f"{', '.join(DEFAULT_SKIP_FOLDERS)}). End with /* to keep the folder's own page.")
    ap.add_argument("--no-skip", action="store_true", help="don't skip any folders")
    ap.add_argument("--max-pages", type=int, default=ScanSettings.max_pages)
    ap.add_argument("--workers", type=int, default=ScanSettings.workers)
    ap.add_argument("--delay", type=float, default=ScanSettings.delay, help="seconds between requests")
    ap.add_argument("--ignore-robots", action="store_true", help="don't obey robots.txt Disallow rules")
    ap.add_argument("--honor-crawl-delay", action="store_true", help="obey robots.txt Crawl-delay (capped at 1s)")
    ap.add_argument("--reindex", action="store_true", help="skip crawling; rebuild the index from saved pages")
    ap.add_argument("--scan-dir", type=Path, default=DEFAULT_SCAN_DIR)
    ap.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    args = ap.parse_args(argv)

    stop = threading.Event()

    def on_sigint(signum, frame):
        if stop.is_set():
            raise KeyboardInterrupt
        print("\nStopping after the current pages... (Ctrl-C again to abort)", flush=True)
        stop.set()

    signal.signal(signal.SIGINT, on_sigint)

    def show(st: ScanStatus) -> None:
        if st.phase == "crawling":
            line = (f"crawling: {st.pages_done:,} pages ({st.pages_unchanged:,} unchanged, "
                    f"{st.pages_failed:,} failed), {st.pages_queued:,} queued, {st.images_seen:,} images")
        elif st.phase == "indexing":
            line = f"indexing: {st.message.lower()} {st.index_done:,}/{st.index_total:,}"
        else:
            line = st.message
        print(f"\r{line:<110}", end="", flush=True)

    def embedder_factory():
        from imagecheck.features import Embedder
        if not hasattr(embedder_factory, "e"):
            embedder_factory.e = Embedder()
        return embedder_factory.e

    common = dict(embedder_factory=embedder_factory, scan_dir=args.scan_dir, index_dir=args.index_dir,
                  cache_dir=args.cache_dir, on_status=show, stop_event=stop)
    if args.reindex:
        st = run_reindex(**common)
    else:
        settings = ScanSettings(
            start_url=args.start_url, include_paths=args.include,
            exclude_paths=[] if args.no_skip else (args.exclude or list(DEFAULT_SKIP_FOLDERS)),
            max_pages=args.max_pages, workers=args.workers, delay=args.delay,
            respect_robots=not args.ignore_robots, honor_crawl_delay=args.honor_crawl_delay,
        )
        st = run_scan(settings, **common)
    print()
    print(f"{st.state.upper()}: {st.message or st.error}")
    for w in st.warnings:
        print(f"  warning: {w}")
    for k, v in st.summary.items():
        print(f"  {k}: {v}")
    return 0 if st.state == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
