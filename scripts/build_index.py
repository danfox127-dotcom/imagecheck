"""Import Screaming Frog crawl exports and update the image index.

    python scripts/build_index.py                       # all files in data/crawls/
    python scripts/build_index.py data/crawls/all_images.csv.zip

The exports go into the page store (data/scans/) like a site scan would, and
the index is updated incrementally. Into an empty store they become the whole
picture; otherwise they only add pages the scans haven't seen. To scan the live
site instead, use scripts/scan_site.py or the app's "Site scan" page.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imagecheck.config import DEFAULT_CACHE_DIR, DEFAULT_CRAWL_DIR, DEFAULT_INDEX_DIR, DEFAULT_SCAN_DIR  # noqa: E402
from imagecheck.scan import run_import  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("crawls", nargs="*", help="Screaming Frog CSV/ZIP exports (default: everything in data/crawls/)")
    ap.add_argument("--scan-dir", type=Path, default=DEFAULT_SCAN_DIR)
    ap.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    args = ap.parse_args(argv)

    crawls = [Path(p) for p in args.crawls] or sorted(
        p for p in DEFAULT_CRAWL_DIR.iterdir() if p.suffix.lower() in (".csv", ".zip")
    )
    if not crawls:
        ap.error(f"no crawl files given and none found in {DEFAULT_CRAWL_DIR}")

    def embedder_factory():
        from imagecheck.features import Embedder
        return Embedder()

    st = run_import(crawls, embedder_factory=embedder_factory, scan_dir=args.scan_dir,
                    index_dir=args.index_dir, cache_dir=args.cache_dir, log=print)
    print(f"{st.state.upper()}: {st.message or st.error}")
    for w in st.warnings:
        print(f"  warning: {w}")
    for k, v in st.summary.items():
        print(f"  {k}: {v}")
    return 0 if st.state == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
