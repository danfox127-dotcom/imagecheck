from imagecheck.config import Scope
from imagecheck.ingest import inventory_from_pages
from imagecheck.pages import PageStore, import_screaming_frog

F = "https://www.columbiadoctors.org/sites/default/files"


def _page(url, *imgs, status=200, **extra):
    return {"kind": "page", "url": url, "status_code": status, "images": list(imgs), **extra}


def test_full_complete_scan_replaces_store():
    s = PageStore()
    s.merge([_page("a"), _page("b"), _page("c")], full_and_complete=True)
    stats = s.merge([_page("a"), _page("b")], full_and_complete=True)
    assert set(s.pages) == {"a", "b"} and stats.removed == 1 and stats.replaced_store


def test_partial_scan_only_updates_fetched_pages():
    s = PageStore()
    s.merge([_page("a", title="old"), _page("b")], full_and_complete=True)
    stats = s.merge([_page("a", title="new")], full_and_complete=False)
    assert set(s.pages) == {"a", "b"} and s.pages["a"]["title"] == "new"
    assert stats.removed == 0 and not stats.replaced_store


def test_suspiciously_small_full_scan_does_not_wipe_store():
    s = PageStore()
    s.merge([_page(str(i)) for i in range(10)], full_and_complete=True)
    stats = s.merge([_page("0"), _page("1")], full_and_complete=True)
    assert len(s.pages) == 10 and not stats.replaced_store and "kept" in stats.note


def test_store_roundtrip(tmp_path):
    s = PageStore()
    s.merge([_page("https://x/a", {"src": "https://x/i.jpg", "alt": None})], full_and_complete=True)
    s.record_scan(kind="scan", finished_at="2026-11-18T10:00:00+00:00", complete=True, pages=1)
    s.save(tmp_path)
    t = PageStore.load(tmp_path)
    assert t.pages == s.pages and t.meta == s.meta


def test_freshness_never_moves_backwards():
    s = PageStore()
    s.record_scan(kind="scan", finished_at="2026-11-18T10:00:00+00:00", complete=True)
    s.record_scan(kind="screaming_frog", finished_at="2026-03-09T12:02:38", complete=True)
    assert s.meta["last_full_scan"]["kind"] == "scan"
    s.record_scan(kind="scan", finished_at="2026-11-19T09:00:00+00:00", complete=False, include_paths=["news"])
    assert s.meta["last_full_scan"]["finished_at"].startswith("2026-11-18")
    assert s.meta["last_scan"]["include_paths"] == ["news"]


def test_inventory_folds_srcset_candidates_and_skips_bad_pages():
    hero = {"src": f"{F}/styles/cola_media_1600_3_1/public/media/images/x.jpg?itok=a", "alt": "Hero",
            "candidates": [f"{F}/styles/cola_media_40_3_1/public/media/images/x.jpg?itok=b",
                           f"{F}/styles/cola_media_1600_3_1/public/media/images/x.jpg?itok=a"],
            "location": "main"}
    logo = {"src": f"{F}/media/images/logo.png", "alt": "Logo", "location": "header"}
    menu = {"src": f"{F}/media/general/menu-cta.jpg", "alt": "CTA", "location": "nav"}
    pages = [
        _page("https://www.columbiadoctors.org/a", hero, logo, menu),
        _page("https://www.columbiadoctors.org/b", hero, logo, menu),
        _page("https://www.columbiadoctors.org/gone", {"src": f"{F}/media/images/404.jpg", "alt": ""}, status=404),
        _page("https://www.columbiadoctors.org/old", {"src": f"{F}/media/images/r.jpg", "alt": ""}, is_redirect=True,
              redirect_chain=["https://www.columbiadoctors.org/old", "https://doctors.columbia.edu/old"]),
    ]
    inv = inventory_from_pages(pages, Scope())
    assert set(inv) == {f"{F}/media/images/x.jpg", f"{F}/media/general/menu-cta.jpg"}  # logo = chrome
    x = inv[f"{F}/media/images/x.jpg"]
    assert x.pages == {"https://www.columbiadoctors.org/a", "https://www.columbiadoctors.org/b"}
    assert len(x.variants) == 2 and x.alt_texts == {"Hero"}


def test_import_screaming_frog_sets_crawl_date_and_never_overwrites_scans(tmp_path):
    csv = tmp_path / "inlinks.csv"
    csv.write_text('﻿"Type","Source","Destination","Alt Text","Link Position"\n'
                   f'"Image","https://www.columbiadoctors.org/a","{F}/media/images/sf.jpg","SF","Content"\n'
                   f'"Image","https://www.columbiadoctors.org/z","{F}/media/images/z.jpg","Z","Content"\n')
    pages_csv = tmp_path / "internal.csv"
    pages_csv.write_text('﻿"Address","Content Type","Crawl Timestamp"\n'
                         '"https://www.columbiadoctors.org/a","text/html","2026-03-09 12:02:38"\n')
    s = PageStore()
    import_screaming_frog(s, [csv, pages_csv])
    assert s.meta["last_full_scan"]["finished_at"] == "2026-03-09T12:02:38"
    assert s.meta["last_full_scan"]["files"] == ["inlinks.csv", "internal.csv"]
    assert s.pages["https://www.columbiadoctors.org/a"]["images"][0]["location"] == "main"

    s2 = PageStore()
    s2.merge([_page("https://www.columbiadoctors.org/a", {"src": f"{F}/media/images/new.jpg", "alt": "N"})],
             full_and_complete=True)
    s2.record_scan(kind="scan", finished_at="2026-11-18T10:00:00+00:00", complete=True)
    import_screaming_frog(s2, [csv, pages_csv])
    assert s2.pages["https://www.columbiadoctors.org/a"]["images"][0]["src"].endswith("new.jpg")  # kept
    assert "https://www.columbiadoctors.org/z" in s2.pages  # gap filled
    assert s2.meta["last_full_scan"]["kind"] == "scan"


def test_complete_scan_only_removes_pages_it_could_reach():
    s = PageStore()
    s.merge([_page("https://x/a"), _page("https://x/lib/1"), _page("https://x/lib/2"), _page("https://x/gone")],
            full_and_complete=True)
    reachable = lambda u: "/lib/" not in u  # noqa: E731  (lib's subpages were skipped)
    stats = s.merge([_page("https://x/a")], full_and_complete=True, reachable=reachable)
    assert set(s.pages) == {"https://x/a", "https://x/lib/1", "https://x/lib/2"}
    assert stats.removed == 1 and stats.replaced_store


def test_coverage_guard_ignores_skipped_folders():
    s = PageStore()
    s.merge([_page(f"https://x/lib/{i}") for i in range(70)] + [_page(f"https://x/p{i}") for i in range(30)],
            full_and_complete=True)
    stats = s.merge([_page(f"https://x/p{i}") for i in range(28)], full_and_complete=True,
                    reachable=lambda u: "/lib/" not in u)
    assert stats.replaced_store and not stats.note  # 28 of 30 reachable pages is fine
    assert stats.removed == 2 and len(s.pages) == 98


def test_redirected_pages_count_under_their_final_url():
    """The crawler fetches /health-library, the site redirects to /health-library/; the
    final URL is never fetched on its own, so its images must come from this record."""
    img = {"src": "https://www.columbiadoctors.org/health-library/images/nextImageExportOptimizer/wellness-opt-3840.WEBP",
           "alt": "W", "location": "main"}
    page = _page("https://www.columbiadoctors.org/health-library", img, is_redirect=True,
                 redirect_chain=["https://www.columbiadoctors.org/health-library",
                                 "https://www.columbiadoctors.org/health-library/"])
    inv = inventory_from_pages([page], Scope())
    [item] = inv.values()
    assert item.url.endswith("/wellness.WEBP")
    assert item.pages == {"https://www.columbiadoctors.org/health-library/"}


def test_imported_pages_must_be_on_the_site():
    pages = [
        _page("javascript:alert(1)", {"src": f"{F}/media/images/a.jpg", "alt": ""}),
        _page("https://evil.example/x", {"src": f"{F}/media/images/b.jpg", "alt": ""}),
        _page("https://www.columbiadoctors.org/ok", {"src": f"{F}/media/images/c.jpg", "alt": ""}),
    ]
    inv = inventory_from_pages(pages, Scope())
    assert set(inv) == {f"{F}/media/images/c.jpg"}


def test_zip_bombs_are_refused(tmp_path, monkeypatch):
    import zipfile

    import pytest

    from imagecheck import ingest
    z = tmp_path / "bomb.zip"
    with zipfile.ZipFile(z, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("x.csv", '"Address"\n' + "a" * 2_000_000)  # compresses ~1000:1
    with pytest.raises(ValueError, match="too large or too compressed"):
        ingest.read_crawl(z)
    monkeypatch.setattr(ingest, "MAX_ZIP_RATIO", 10_000)
    monkeypatch.setattr(ingest, "MAX_CSV_BYTES", 1000)
    with pytest.raises(ValueError, match="too large"):
        ingest.read_crawl(z)
