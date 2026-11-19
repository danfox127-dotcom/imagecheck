"""Share the index as a download, so a new install can check images without scanning first.

A bundle is a zip of the index and the page store. It contains no site images: when the checker
needs one that isn't cached locally, it downloads it from the site (and keeps it).

    python scripts/publish_data.py     # maintainer: zip the current data, attach to a GitHub release
    python scripts/get_data.py         # everyone: download it, if newer than the local data
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from .config import DATA_DIR

REPO = "danfox127-dotcom/imagecheck"
ASSET = "imagecheck-data.zip"
LATEST_URL = f"https://github.com/{REPO}/releases/latest/download/{ASSET}"

# Written in this order: the index loader retries until both index files carry the same build id,
# and images.json going last keeps that window short.
FILES = ("scans/store.json", "scans/pages.jsonl", "index/embeddings.npz", "index/images.json")
MAX_UNPACKED = 1_000_000_000  # refuse anything that would unpack to more than 1 GB


class BundleError(ValueError):
    pass


def scan_date(store_json: bytes | str | None) -> str:
    """ISO timestamp of the last full scan in a store.json ('' if none)."""
    if not store_json:
        return ""
    try:
        meta = json.loads(store_json)
    except ValueError:
        return ""
    return ((meta.get("last_full_scan") or {}).get("finished_at")) or ""


def local_scan_date(data_dir: Path = DATA_DIR) -> str:
    path = data_dir / "scans/store.json"
    has_index = (data_dir / "index/images.json").exists()
    return scan_date(path.read_text()) if path.exists() and has_index else ""


def make(out: Path, data_dir: Path = DATA_DIR) -> dict:
    """Zip the shareable data files; returns a summary for the release notes."""
    missing = [f for f in FILES if not (data_dir / f).exists()]
    if missing:
        raise BundleError(f"Nothing to publish yet; missing {', '.join(missing)}. Scan the site first.")
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in FILES:
            zf.write(data_dir / f, f)
    images = json.loads((data_dir / "index/images.json").read_text())
    return {"scanned": scan_date((data_dir / "scans/store.json").read_text()),
            "images": len(images.get("images", [])), "bytes": out.stat().st_size}


def read(zip_path: Path) -> tuple[zipfile.ZipFile, str]:
    """Open and validate a bundle; returns it with its scan date."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise BundleError(f"Not a valid data bundle: {e}") from None
    names = {i.filename for i in zf.infolist() if not i.is_dir()}
    if names != set(FILES):
        zf.close()
        raise BundleError(f"Unexpected bundle contents: {sorted(names)}")
    if sum(i.file_size for i in zf.infolist()) > MAX_UNPACKED:
        zf.close()
        raise BundleError("Bundle is too large to unpack")
    return zf, scan_date(zf.read("scans/store.json"))


def install(zip_path: Path, data_dir: Path = DATA_DIR) -> None:
    """Replace the local index and page store with the bundle's."""
    zf, _ = read(zip_path)
    with zf:
        for f in FILES:
            dst = data_dir / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=dst.name + ".")
            try:
                with os.fdopen(fd, "wb") as out, zf.open(f) as src:
                    shutil.copyfileobj(src, out, length=1 << 20)
                os.replace(tmp, dst)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
