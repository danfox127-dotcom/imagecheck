"""Downloading site images, with retries and a local on-disk cache.

Every failure is reported back to the caller - nothing is silently skipped,
because a skipped image is indistinguishable from "not on the site".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import USER_AGENT

_STYLE_WIDTH_RE = re.compile(r"/styles/cola_media_(\d+)")


class FetchError(Exception):
    pass


def make_session(pool_size: int = 16) -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=4, connect=4, read=4, backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=pool_size, pool_maxsize=pool_size)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers["User-Agent"] = USER_AGENT
    return s


def fetch_bytes(url: str, session: requests.Session | None = None, timeout: float = 30) -> bytes:
    session = session or make_session()
    try:
        resp = session.get(url, timeout=timeout)
    except requests.RequestException as e:
        raise FetchError(f"{type(e).__name__}: {e}") from e
    if resp.status_code != 200:
        raise FetchError(f"HTTP {resp.status_code}")
    ctype = resp.headers.get("Content-Type", "")
    if ctype and not ctype.startswith("image/") and "octet-stream" not in ctype:
        raise FetchError(f"not an image (Content-Type {ctype!r})")
    if not resp.content:
        raise FetchError("empty response")
    return resp.content


def variant_preference(urls: Iterable[str]) -> list[str]:
    """Order served variants largest-first, as fallbacks when the original 404s."""
    def width(u: str) -> int:
        m = _STYLE_WIDTH_RE.search(u)
        return int(m.group(1)) if m else 0
    return sorted(set(urls), key=width, reverse=True)


@dataclass
class Fetched:
    data: bytes
    source_url: str  # the URL actually downloaded (original or a fallback variant)


def fetch_image(canonical: str, variants: Iterable[str] = (), session: requests.Session | None = None) -> Fetched:
    """Fetch the canonical original; fall back to the largest served variant."""
    errors = []
    for url in [canonical] + [v for v in variant_preference(variants) if v != canonical]:
        try:
            return Fetched(fetch_bytes(url, session), url)
        except FetchError as e:
            errors.append(f"{url}: {e}")
    raise FetchError("; ".join(errors[:3]))


class ImageCache:
    """Downscaled JPEG copies of site images, keyed by canonical URL."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def path_for(self, url: str) -> Path:
        h = hashlib.sha1(url.encode()).hexdigest()
        return self.root / h[:2] / f"{h}.jpg"

    def has(self, url: str) -> bool:
        return self.path_for(url).exists()

    def put(self, url: str, img) -> Path:
        p = self.path_for(url)
        p.parent.mkdir(parents=True, exist_ok=True)
        img.save(p, format="JPEG", quality=92)
        return p

    def get(self, url: str):
        from PIL import Image
        p = self.path_for(url)
        if not p.exists():
            return None
        with Image.open(p) as im:
            return im.convert("RGB")
