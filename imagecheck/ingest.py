"""Turn Screaming Frog crawl exports into an inventory of canonical site images.

A single editorial photo is served under many URLs: Drupal image styles
(`/styles/cola_media_320_16_9/public/...`), cache-busting `?itok=` queries, and
Next.js export variants (`-opt-640.WEBP`). We collapse all of them to one
canonical "original" URL and merge every page that uses any variant.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

import pandas as pd

from .config import Scope

_STYLE_RE = re.compile(r"/styles/[^/]+/public/")
_NEXT_OPT_RE = re.compile(r"-opt-\d+(?=\.[A-Za-z]+$)")
_GETTY_RE = re.compile(r"getty[\s_-]*images?[\s_-]*(\d{6,})", re.IGNORECASE)


def canonical_url(url: str) -> str:
    """Map any served variant of an image to its canonical original URL.

    >>> canonical_url("https://www.columbiadoctors.org/sites/default/files/styles/cola_media_320_16_9/public/media/images/2024-02/ivf.jpg?itok=x")
    'https://www.columbiadoctors.org/sites/default/files/media/images/2024-02/ivf.jpg'
    """
    url = url.strip()
    parts = urlsplit(url)
    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme
    host = parts.netloc.lower()
    path = _STYLE_RE.sub("/", parts.path)
    path = _NEXT_OPT_RE.sub("", path)
    return urlunsplit((scheme, host, path, "", ""))


def getty_id(name: str | None) -> str | None:
    """Extract a Getty Images asset ID from a filename or URL, if present.

    Handles `gettyimages-2251110353.jpg`, `GettyImages-1655164789_0.jpg`,
    `getty_images_123456789.jpeg`.
    """
    if not name:
        return None
    m = _GETTY_RE.search(name)
    return m.group(1) if m else None


def in_scope(url: str, scope: Scope) -> bool:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return False
    parts = urlsplit(url)
    if parts.netloc.lower() not in scope.hosts:
        return False
    path = parts.path.lower()
    if not path.endswith(scope.extensions):
        return False
    return not any(frag in path for frag in scope.excluded_path_fragments)


def read_crawl(path: str | Path) -> pd.DataFrame:
    """Read a Screaming Frog CSV export, optionally inside a .zip (Mac-safe)."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            names = [
                n for n in z.namelist()
                if n.lower().endswith(".csv") and not n.startswith("__MACOSX") and "/._" not in n
                and not Path(n).name.startswith("._")
            ]
            if not names:
                raise ValueError(f"No CSV found inside {path}")
            with z.open(names[0]) as f:
                df = pd.read_csv(io.BytesIO(f.read()), encoding="utf-8-sig", dtype=str, keep_default_na=False)
    else:
        df = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    return df


@dataclass
class SiteImage:
    url: str  # canonical original
    variants: set[str] = field(default_factory=set)  # served URLs seen in crawls
    pages: set[str] = field(default_factory=set)
    alt_texts: set[str] = field(default_factory=set)
    positions: set[str] = field(default_factory=set)

    @property
    def getty_id(self) -> str | None:
        return getty_id(self.url.rsplit("/", 1)[-1])


def _add(inv: dict[str, SiteImage], img_url: str, page: str = "", alt: str = "", position: str = "") -> None:
    key = canonical_url(img_url)
    item = inv.get(key)
    if item is None:
        item = inv[key] = SiteImage(url=key)
    item.variants.add(img_url.strip())
    if page:
        item.pages.add(page.strip())
    if alt:
        item.alt_texts.add(alt.strip())
    item.positions.add(position.strip())


def build_inventory(crawl_files: Iterable[str | Path], scope: Scope | None = None) -> dict[str, SiteImage]:
    """Merge one or more crawl exports into {canonical_url: SiteImage}.

    Supports two export shapes:
      * "All Image Inlinks" (Source/Destination columns) - gives page usage.
      * "Images" / "Internal: All" (Address column) - image list without pages.
    """
    scope = scope or Scope()
    inv: dict[str, SiteImage] = {}
    for path in crawl_files:
        df = read_crawl(path)
        cols = set(df.columns)
        blank = pd.Series([""] * len(df), index=df.index)
        if {"Source", "Destination"} <= cols:
            alt = df["Alt Text"] if "Alt Text" in cols else blank
            pos = df["Link Position"] if "Link Position" in cols else blank
            for dest, src, a, p in zip(df["Destination"], df["Source"], alt, pos):
                if in_scope(dest, scope):
                    _add(inv, dest, src, a, p)
        elif "Address" in cols:
            ctype = df["Content Type"] if "Content Type" in cols else blank
            for addr, ct in zip(df["Address"], ctype):
                if ct and not ct.startswith("image/"):
                    continue
                if in_scope(addr, scope):
                    _add(inv, addr)
        else:
            raise ValueError(f"{path}: unrecognised crawl export (columns: {sorted(cols)[:8]}...)")

    chrome = set(scope.chrome_positions)
    return {
        k: v for k, v in inv.items()
        # Drop images that only ever appear in site chrome (logos etc.). Images
        # with no position info (Address-only exports) are kept.
        if not (v.positions - {""}) or not (v.positions - {""}) <= chrome
    }


def clean_pages(pages: Iterable[str]) -> list[str]:
    """Pages for display: drop pagination/listing URLs, shortest (most canonical) first."""
    valid = {p for p in pages if p and p.lower() != "nan" and "page=" not in p.lower()}
    return sorted(valid, key=lambda p: (len(p), p))
