"""Build or refresh the site image index from Screaming Frog crawl exports.

    python scripts/build_index.py                       # all files in data/crawls/
    python scripts/build_index.py data/crawls/all_images.csv.zip
    python scripts/build_index.py --refresh             # ignore previous index

Re-run after every new crawl; already-indexed images are reused.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imagecheck.config import DEFAULT_CACHE_DIR, DEFAULT_CRAWL_DIR, DEFAULT_INDEX_DIR, EmbeddingConfig, Scope  # noqa: E402
from imagecheck.features import Embedder  # noqa: E402
from imagecheck.index import build_index  # noqa: E402
from imagecheck.ingest import build_inventory  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("crawls", nargs="*", help="Screaming Frog CSV/ZIP exports (default: everything in data/crawls/)")
    ap.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    ap.add_argument("--refresh", action="store_true", help="re-download and re-embed everything")
    ap.add_argument("--workers", type=int, default=8, help="parallel downloads")
    ap.add_argument("--limit", type=int, help="only index the first N images (for testing)")
    ap.add_argument("--host", action="append", help="host to include (repeatable); default: the main site")
    args = ap.parse_args(argv)

    crawls = [Path(p) for p in args.crawls] or sorted(
        p for p in DEFAULT_CRAWL_DIR.iterdir() if p.suffix.lower() in (".csv", ".zip")
    )
    if not crawls:
        ap.error(f"no crawl files given and none found in {DEFAULT_CRAWL_DIR}")
    scope = Scope(hosts=tuple(args.host)) if args.host else Scope()

    print(f"Reading {len(crawls)} crawl file(s)...")
    inventory = build_inventory(crawls, scope)
    if args.limit:
        inventory = dict(sorted(inventory.items())[: args.limit])
    print(f"Loading embedding model {EmbeddingConfig().model_name}...")
    embedder = Embedder()
    idx = build_index(
        inventory, args.index_dir, args.cache_dir, embedder,
        refresh=args.refresh, workers=args.workers, crawl_files=[p.name for p in crawls],
    )
    failed = idx.failed_images
    if failed:
        print(f"\n{len(failed)} image(s) could not be indexed (searches will report them as unchecked):")
        for r in failed[:20]:
            print(f"  {r['url']}\n    -> {r.get('error', '')}")
        if len(failed) > 20:
            print(f"  ... and {len(failed) - 20} more (see {args.index_dir / 'images.json'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
