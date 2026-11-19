"""Publish the current index so teammates' installs pick it up (maintainers; needs the gh CLI).

    python scripts/publish_data.py              # after a full scan
    python scripts/publish_data.py --dry-run    # just build dist/imagecheck-data.zip

Creates a GitHub release named after the scan date (data-YYYY-MM-DD) with the bundle attached and
marks it latest; installs download it from there. Publishing the same scan again replaces the file.
The bundle holds the index and page list only, no site images.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from imagecheck import bundle  # noqa: E402


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args, "--repo", bundle.REPO], cwd=REPO_ROOT, check=check,
                          capture_output=True, text=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publish the current index as a GitHub release.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    out = REPO_ROOT / "dist" / bundle.ASSET
    try:
        info = bundle.make(out)
    except bundle.BundleError as e:
        print(e)
        return 1
    day = info["scanned"][:10] or "undated"
    print(f"Built {out} ({info['bytes'] / 1e6:.0f} MB): {info['images']:,} images, scan of {day}")
    if args.dry_run:
        return 0

    tag = f"data-{day}"
    notes = (f"Site data from the full scan of {day}: {info['images']:,} images indexed.\n\n"
             "Installs download this automatically. It contains the index and page list only, "
             "no site images.")
    if gh("release", "view", tag, check=False).returncode == 0:
        gh("release", "upload", tag, str(out), "--clobber")
        gh("release", "edit", tag, "--notes", notes, "--latest")
    else:
        gh("release", "create", tag, str(out), "--title", f"Site data {day}", "--notes", notes, "--latest")
    print(f"Published {tag}: https://github.com/{bundle.REPO}/releases/tag/{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
