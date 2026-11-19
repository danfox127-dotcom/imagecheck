"""A fake site for scan tests: serves HTML through Columbia-Crawler's session.get."""

import threading

from requests.structures import CaseInsensitiveDict

BASE = "https://www.columbiadoctors.org"
FILES = f"{BASE}/sites/default/files"


def lazy_img(name: str, alt: str = "photo") -> str:
    """Drupal-style lazy-loaded image, as on the real site."""
    v = lambda w, ratio: f"{FILES}/styles/cola_media_{w}_{ratio}/public/media/images/2026-01/{name}?itok=x{w}"  # noqa: E731
    srcset = ", ".join(f"{v(w, '16_9')} {w}w" for w in (40, 320, 1040))
    return f'<img class="lazyload" src="{BASE}/assets/images/empty.gif" alt="{alt}" data-srcset="{srcset}">'


def html(links=(), images=(), menu_images=()):
    nav = "".join(lazy_img(n) for n in menu_images)
    body = "".join(f'<a href="{h}">x</a>' for h in links) + "".join(lazy_img(n) for n in images)
    return (f"<html><head><title>t</title></head><body><header><nav>{nav}</nav></header>"
            f"<main>{body}</main></body></html>")


class _Resp:
    def __init__(self, url, status, headers, text, content=None):
        self.url, self.status_code, self.text = url, status, text
        self.content = content if content is not None else text.encode()
        self.headers = CaseInsensitiveDict(headers)
        self.history = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSite:
    """{url: html} pages; each page has an ETag derived from its content (304 when unchanged)."""

    def __init__(self, pages: dict[str, str]):
        self.pages = dict(pages)
        self.lock = threading.Lock()
        self.requests: list[tuple[str, dict]] = []

    def get(self, url, headers=None, **kw):
        with self.lock:
            self.requests.append((url, dict(headers or {})))
        body = self.pages.get(url) or self.pages.get(url.rstrip("/")) or self.pages.get(url + "/")
        if url.endswith(".xml") and body is not None:
            return _Resp(url, 200, {"content-type": "application/xml"}, body, content=body.encode())
        if body is None:
            return _Resp(url, 404, {"content-type": "text/html"}, "<html></html>")
        etag = f'W/"{hash(body) & 0xffffffff:x}"'
        if headers and headers.get("If-None-Match") == etag:
            return _Resp(url, 304, {"etag": etag}, "")
        return _Resp(url, 200, {"content-type": "text/html; charset=utf-8", "etag": etag}, body)

    def count(self, status_filter=None):
        return len(self.requests)


def crawler_factory(site: FakeSite):
    from crawler import Crawler

    def make(*args, **kwargs):
        c = Crawler(*args, **kwargs)
        c.session.get = site.get
        return c
    return make


def sitemap(*urls: str) -> str:
    locs = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'
