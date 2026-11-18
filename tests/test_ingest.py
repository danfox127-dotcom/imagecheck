import zipfile

from imagecheck.config import Scope
from imagecheck.ingest import build_inventory, canonical_url, clean_pages, getty_id, in_scope, read_crawl

BASE = "https://www.columbiadoctors.org/sites/default/files"


def test_canonical_url_strips_drupal_style_and_query():
    assert canonical_url(f"{BASE}/styles/cola_media_320_16_9/public/media/images/2024-02/ivf.jpg?itok=abc") == \
        f"{BASE}/media/images/2024-02/ivf.jpg"
    assert canonical_url(f"{BASE}/styles/cola_media_1600_3_1/public/media/images/2024-02/ivf.jpg?itok=zzz") == \
        f"{BASE}/media/images/2024-02/ivf.jpg"


def test_canonical_url_normalises_scheme_host_and_next_variants():
    assert canonical_url("http://WWW.columbiadoctors.org/a/b.jpg") == "https://www.columbiadoctors.org/a/b.jpg"
    assert canonical_url("https://www.columbiadoctors.org/health-library/images/nextImageExportOptimizer/wellness-opt-3840.WEBP") == \
        "https://www.columbiadoctors.org/health-library/images/nextImageExportOptimizer/wellness.WEBP"


def test_getty_id_variants():
    assert getty_id("gettyimages-2251110353.jpg") == "2251110353"
    assert getty_id("GettyImages-1655164789_0.jpg") == "1655164789"
    assert getty_id(f"{BASE}/media/images/teaser/2026-02/gettyimages-1488226579.jpg") == "1488226579"
    assert getty_id("getty_images_123456789.jpeg") == "123456789"
    assert getty_id("ivf.jpg") is None
    assert getty_id(None) is None


def test_in_scope_filters_hosts_types_and_generated_assets():
    s = Scope()
    assert in_scope(f"{BASE}/media/images/x.jpg", s)
    assert in_scope(f"{BASE}/media/images/x.PNG", s)
    assert not in_scope(f"{BASE}/logo-cd-desktop-white.svg", s)
    assert not in_scope(f"{BASE}/staticmaps/node-1.png", s)
    assert not in_scope("https://www.columbiadoctors.org/profiles/cola/themes/cola/images/map_markers/1.png", s)
    assert not in_scope("https://content.healthwise.net/resources/x.jpg", s)
    assert not in_scope("", s)
    assert not in_scope(float("nan"), s)
    # The old keyword blocklist would have dropped these real photos.
    assert in_scope(f"{BASE}/media/images/2025-10/header_bg_doctor.jpg", s)


INLINKS_HEADER = '"Type","Source","Destination","Alt Text","Link Position"\n'


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text("﻿" + text, encoding="utf-8")
    return p


def test_build_inventory_merges_variants_and_pages(tmp_path):
    rows = [
        ("Image", "https://www.columbiadoctors.org/a", f"{BASE}/styles/cola_media_320_16_9/public/media/images/x.jpg?itok=1", "Doctor", "Content"),
        ("Image", "https://www.columbiadoctors.org/b", f"{BASE}/styles/cola_media_1600_3_1/public/media/images/x.jpg?itok=2", "", "Content"),
        ("Image", "https://www.columbiadoctors.org/c", f"{BASE}/media/images/logo.png", "", "Header"),
        ("Image", "https://www.columbiadoctors.org/d", "https://i.ytimg.com/vi/x/hq.jpg", "", "Content"),
    ]
    text = INLINKS_HEADER + "".join(",".join(f'"{v}"' for v in r) + "\n" for r in rows)
    inv = build_inventory([_write(tmp_path, "inlinks.csv", text)])
    assert list(inv) == [f"{BASE}/media/images/x.jpg"]
    item = inv[f"{BASE}/media/images/x.jpg"]
    assert item.pages == {"https://www.columbiadoctors.org/a", "https://www.columbiadoctors.org/b"}
    assert len(item.variants) == 2
    assert item.alt_texts == {"Doctor"}


def test_build_inventory_accepts_address_exports_and_zip(tmp_path):
    text = '"Address","Content Type"\n' \
           f'"{BASE}/media/images/y.jpg","image/jpeg"\n' \
           '"https://www.columbiadoctors.org/page","text/html"\n'
    csv_path = _write(tmp_path, "images.csv", text)
    zpath = tmp_path / "images.csv.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("__MACOSX/._images.csv", b"\x00junk")
        z.write(csv_path, "images.csv")
    assert len(read_crawl(zpath)) == 2
    inv = build_inventory([zpath])
    assert list(inv) == [f"{BASE}/media/images/y.jpg"]
    assert inv[f"{BASE}/media/images/y.jpg"].pages == set()


def test_clean_pages_drops_pagination_and_sorts_shortest_first():
    pages = ["https://x.org/news?page=2", "https://x.org/a/long/path", "https://x.org/a", "nan", ""]
    assert clean_pages(pages) == ["https://x.org/a", "https://x.org/a/long/path"]
