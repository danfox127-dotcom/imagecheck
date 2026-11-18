"""Turn crawl results (site scans or Screaming Frog exports) into an inventory of canonical site images.

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


def _crawl_csv_members(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        return [
            n for n in z.namelist()
            if n.lower().endswith(".csv") and not n.startswith("__MACOSX") and "/._" not in n
            and not Path(n).name.startswith("._")
        ]


def read_crawl(path: str | Path) -> pd.DataFrame:
    """Read a Screaming Frog CSV export, optionally inside a .zip (Mac-safe)."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        names = _crawl_csv_members(path)
        if not names:
            raise ValueError(f"No CSV found inside {path}")
        with zipfile.ZipFile(path) as z, z.open(names[0]) as f:
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
    item.positions.add((position or "").strip().lower())


# ---------------------------------------------------------------- page records
#
# Everything is expressed as crawler page records (Columbia-Crawler's
# PageData.to_dict()): {"url", "status_code", "images": [{"src", "alt",
# "candidates"?, "location"?, "kind"?}], ...}. Screaming Frog exports are
# converted into the same shape, so one code path builds the inventory.

# Screaming Frog "Link Position" -> crawler landmark location.
_SF_POSITIONS = {"header": "header", "footer": "footer", "navigation": "nav", "content": "main",
                 "sidebar": "aside", "aside": "aside"}


def page_url_for_inventory(page: dict, scope: Scope) -> str | None:
    """The URL a page's images should be credited to, or None if they don't count.

    A redirected request (the crawler asks for /health-library, the site answers
    with /health-library/) is credited to its final URL. The crawler treats both
    spellings as one page, so the final URL is never fetched separately and
    skipping redirects would lose those images. Redirects that leave the site
    don't count.
    """
    if page.get("synthetic"):
        return ""
    if int(page.get("status_code") or 0) != 200 or page.get("error"):
        return None
    url = page.get("url", "")
    if page.get("is_redirect"):
        url = (page.get("redirect_chain") or [url])[-1]
        if (urlsplit(url).hostname or "").lower() not in scope.hosts:
            return None
    return url


def page_is_usable(page: dict, scope: Scope | None = None) -> bool:
    return page_url_for_inventory(page, scope or Scope()) is not None


def pages_from_screaming_frog(crawl_files: Iterable[str | Path]) -> tuple[list[dict], str | None]:
    """Convert Screaming Frog exports to page records, plus the crawl date if known.

    * "All Image Inlinks" (Source/Destination): one record per source page.
    * "Images" / "Internal: All" (Address): an image list with no page usage,
      kept as one synthetic record per file.
    The crawl date comes from a "Crawl Timestamp" column or, failing that, the
    date stored inside a .zip export.
    """
    pages: dict[str, dict] = {}
    synthetic: list[dict] = []
    stamps: list[str] = []
    for path in crawl_files:
        path = Path(path)
        df = read_crawl(path)
        cols = set(df.columns)
        blank = pd.Series([""] * len(df), index=df.index)
        if "Crawl Timestamp" in cols:
            stamps += [t for t in df["Crawl Timestamp"] if t]
        elif path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as z:
                info = z.getinfo(_crawl_csv_members(path)[0])
                stamps.append("%04d-%02d-%02d %02d:%02d:%02d" % info.date_time)
        if {"Source", "Destination"} <= cols:
            alt = df["Alt Text"] if "Alt Text" in cols else blank
            pos = df["Link Position"] if "Link Position" in cols else blank
            for dest, src, a, p in zip(df["Destination"], df["Source"], alt, pos):
                if not dest or not src:
                    continue
                rec = pages.setdefault(src, {"kind": "page", "url": src, "status_code": 200, "images": [],
                                             "source": "screaming_frog"})
                rec["images"].append({"src": dest, "alt": a or None,
                                      "location": _SF_POSITIONS.get(p.strip().lower(), "")})
        elif "Address" in cols:
            ctype = df["Content Type"] if "Content Type" in cols else blank
            imgs = [{"src": addr, "alt": None} for addr, ct in zip(df["Address"], ctype)
                    if addr and (not ct or ct.startswith("image/"))]
            if imgs:
                synthetic.append({"kind": "image-list", "url": f"screaming-frog:{path.name}", "synthetic": True,
                                  "status_code": 200, "images": imgs, "source": "screaming_frog"})
        else:
            raise ValueError(f"{path}: unrecognised crawl export (columns: {sorted(cols)[:8]}...)")
    return list(pages.values()) + synthetic, (max(stamps) if stamps else None)


def inventory_from_pages(pages: Iterable[dict], scope: Scope | None = None) -> dict[str, SiteImage]:
    """{canonical_url: SiteImage} for every in-scope image on usable pages.

    All URLs an element can load (its srcset `candidates`) fold into their
    canonical originals, so every Drupal crop of a photo maps to one entry.
    """
    scope = scope or Scope()
    inv: dict[str, SiteImage] = {}
    for page in pages:
        page_url = page_url_for_inventory(page, scope)
        if page_url is None:
            continue
        for img in page.get("images") or []:
            urls = dict.fromkeys([img.get("src", "")] + list(img.get("candidates") or []))
            for u in urls:
                if in_scope(u, scope):
                    _add(inv, u, page_url, img.get("alt") or "", img.get("location") or "")

    chrome = set(scope.chrome_positions)
    return {
        k: v for k, v in inv.items()
        # Drop images that only ever appear in site chrome (logos etc.). Images
        # with no position info (Address-only exports) are kept.
        if not (v.positions - {""}) or not (v.positions - {""}) <= chrome
    }


def build_inventory(crawl_files: Iterable[str | Path], scope: Scope | None = None) -> dict[str, SiteImage]:
    """Inventory straight from Screaming Frog exports."""
    pages, _ = pages_from_screaming_frog(crawl_files)
    return inventory_from_pages(pages, scope)


def clean_pages(pages: Iterable[str]) -> list[str]:
    """Pages for display: drop pagination/listing URLs, shortest (most canonical) first."""
    valid = {p for p in pages if p and p.lower() != "nan" and "page=" not in p.lower()}
    return sorted(valid, key=lambda p: (len(p), p))
