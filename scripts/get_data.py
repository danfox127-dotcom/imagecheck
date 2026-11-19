"""Download the latest shared index, unless the data here is already as new.

    python scripts/get_data.py            # update if the published scan is newer
    python scripts/get_data.py --force    # replace the local index regardless
    python scripts/get_data.py --file imagecheck-data.zip   # install a bundle you already have

The bundle replaces the index and page store (data/index, data/scans). Downloaded site images in
data/cache are kept.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imagecheck import bundle  # noqa: E402
from imagecheck.config import DATA_DIR, USER_AGENT  # noqa: E402

MAX_DOWNLOAD = 500_000_000


def download(url: str, dest: Path) -> None:
    import requests

    with requests.get(url, stream=True, timeout=60, headers={"User-Agent": USER_AGENT}) as r:
        if r.status_code == 404:
            raise bundle.BundleError("No shared data has been published yet.")
        r.raise_for_status()
        size = 0
        with dest.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise bundle.BundleError("Download is unexpectedly large; stopped.")
                f.write(chunk)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Download the latest shared imagecheck index.")
    ap.add_argument("--force", action="store_true", help="replace local data even if it's newer")
    ap.add_argument("--file", type=Path, help="install this bundle instead of downloading")
    ap.add_argument("--url", default=bundle.LATEST_URL)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = ap.parse_args(argv)

    local = bundle.local_scan_date(args.data_dir)
    with tempfile.TemporaryDirectory() as tmp:
        path = args.file
        if path is None:
            print("Checking for the latest site data...")
            path = Path(tmp) / bundle.ASSET
            try:
                download(args.url, path)
            except Exception as e:  # noqa: BLE001 - network problems shouldn't break setup
                print(f"Couldn't download the site data ({e}).")
                print("The app still works: scan the site from its Site scan page." if not local else
                      "Keeping the data you already have.")
                return 0
        try:
            zf, remote = bundle.read(path)
            zf.close()
        except bundle.BundleError as e:
            print(f"Couldn't use the site data: {e}")
            return 1
        if local and remote and local >= remote and not args.force:
            print(f"Your site data is up to date (scanned {local[:10]}).")
            return 0
        bundle.install(path, args.data_dir)
        print(f"Installed site data from the scan of {remote[:10] or 'an unknown date'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
