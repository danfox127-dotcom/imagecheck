import json
import os
import threading

import pytest

from imagecheck import index as index_mod
from imagecheck.fetch import Fetched
from imagecheck.index import Index
from imagecheck.pages import PageStore
from imagecheck.scan import (ScanJob, ScanLock, ScanNotAllowed, ScanSettings, ScanStatus, run_import,
                             run_reindex, run_scan, validate_start_url)
from tests.conftest import FakeEmbedder, synthetic_photo, to_jpeg
from tests.fake_site import BASE, FakeSite, crawler_factory, html, sitemap

ORIG = f"{BASE}/sites/default/files/media/images/2026-01"


def _site():
    return FakeSite({
        f"{BASE}": html(links=[f"{BASE}/a", f"{BASE}/b"], images=["hero.jpg"], menu_images=["menu.jpg"]),
        f"{BASE}/a": html(images=["a1.jpg", "hero.jpg"], menu_images=["menu.jpg"]),
        f"{BASE}/b": html(images=["b1.jpg"], menu_images=["menu.jpg"]),
    })


@pytest.fixture
def env(tmp_path, monkeypatch):
    import imagecheck.scan as scan_mod
    monkeypatch.setattr(scan_mod, "STATUS_EVERY_S", 0.0)  # report every page, so cancel tests are exact
    calls = []
    photos = {}

    def fake_fetch(url, variants=(), session=None):
        calls.append(url)
        name = url.rsplit("/", 1)[-1]
        photos.setdefault(name, synthetic_photo(len(photos) + 500))
        return Fetched(to_jpeg(photos[name]), url)

    monkeypatch.setattr(index_mod, "fetch_image", fake_fetch)
    dirs = {"scan_dir": tmp_path / "scans", "index_dir": tmp_path / "index", "cache_dir": tmp_path / "cache"}
    emb = FakeEmbedder()
    return {"dirs": dirs, "calls": calls, "embedder_factory": lambda: emb}


def _scan(env, site, **settings):
    s = ScanSettings(start_url=f"{BASE}/", respect_robots=False, delay=0, workers=2, **settings)
    return run_scan(s, embedder_factory=env["embedder_factory"], crawler_factory=crawler_factory(site), **env["dirs"])


def _index_urls(env):
    return {r["url"].rsplit("/", 1)[-1] for r in Index.load(env["dirs"]["index_dir"]).images}


def test_full_scan_finds_lazy_images_and_builds_index(env):
    st = _scan(env, _site())
    assert st.state == "done", st.error
    assert st.pages_done == 3 and st.summary["complete"]
    assert _index_urls(env) == {"hero.jpg", "a1.jpg", "b1.jpg", "menu.jpg"}
    idx = Index.load(env["dirs"]["index_dir"])
    hero = next(r for r in idx.images if r["url"].endswith("hero.jpg"))
    assert hero["url"] == f"{ORIG}/hero.jpg"  # canonical original, not a style variant
    assert set(hero["pages"]) == {f"{BASE}", f"{BASE}/a"}
    assert idx.meta["source"]["last_full_scan"]["kind"] == "scan"
    assert ScanStatus.load(env["dirs"]["scan_dir"]).state == "done"
    assert not (env["dirs"]["scan_dir"] / "scan.lock").exists()


def test_rescan_revalidates_pages_and_downloads_only_new_images(env):
    site = _site()
    _scan(env, site)
    env["calls"].clear()
    site.pages[f"{BASE}/b"] = html(images=["b1.jpg", "b2-new.jpg"])
    st = _scan(env, site)
    assert st.state == "done"
    assert st.pages_unchanged == 2  # 304s for the home page and /a
    assert env["calls"] == [f"{ORIG}/b2-new.jpg"]
    assert st.summary["added"] == 1 and "b2-new.jpg" in _index_urls(env)


def test_full_scan_drops_images_no_longer_on_the_site(env):
    site = _site()
    _scan(env, site)
    site.pages[f"{BASE}/b"] = html(images=[])
    st = _scan(env, site)
    assert st.summary["removed"] == 1 and "b1.jpg" not in _index_urls(env)


def test_section_scan_keeps_other_pages(env):
    site = _site()
    _scan(env, site)
    site.pages[f"{BASE}/a"] = html(images=["a2.jpg"])
    site.pages[f"{BASE}/b"] = html(images=[])  # changed, but outside the scanned section
    st = _scan(env, site, include_paths=["a"])
    assert st.state == "done" and not st.summary["complete"]
    urls = _index_urls(env)
    assert "a2.jpg" in urls and "b1.jpg" in urls
    store = PageStore.load(env["dirs"]["scan_dir"])
    assert store.meta["last_full_scan"]["include_paths"] == []
    assert store.meta["last_scan"]["include_paths"] == ["a"]


def test_cancel_during_crawl_saves_pages_but_leaves_index(env):
    site = _site()
    _scan(env, site)
    before = Index.load(env["dirs"]["index_dir"]).meta["build_id"]
    site.pages[f"{BASE}/a"] = html(images=["a9.jpg"])
    stop = threading.Event()
    s = ScanSettings(start_url=f"{BASE}/", respect_robots=False, delay=0, workers=1)
    st = run_scan(s, embedder_factory=env["embedder_factory"], crawler_factory=crawler_factory(site),
                  on_status=lambda status: stop.set() if status.phase == "crawling" and status.pages_done >= 1 else None,
                  stop_event=stop, **env["dirs"])
    assert st.state == "cancelled"
    assert Index.load(env["dirs"]["index_dir"]).meta["build_id"] == before
    assert len(PageStore.load(env["dirs"]["scan_dir"]).pages) == 3  # nothing removed

    st = run_reindex(embedder_factory=env["embedder_factory"], **env["dirs"])
    assert st.state == "done"


def test_second_scan_is_refused_while_one_holds_the_lock(env):
    with ScanLock(env["dirs"]["scan_dir"]):
        st = _scan(env, _site())
    assert st.state == "failed" and "already running" in st.error


def test_stale_lock_and_dead_running_status_are_detected(env):
    scan_dir = env["dirs"]["scan_dir"]
    scan_dir.mkdir(parents=True)
    (scan_dir / "scan.lock").write_text(json.dumps({"pid": 999_999_999}))
    ScanStatus(state="running").save(scan_dir)
    assert ScanStatus.load(scan_dir).state == "interrupted"
    assert _scan(env, _site()).state == "done"  # stale lock taken over


def test_cancel_file_stops_a_scan_from_another_process(env):
    scan_dir = env["dirs"]["scan_dir"]
    site = _site()

    def on_status(status):
        if status.state == "running" and status.pages_done >= 1:
            (scan_dir / "cancel").write_text("x")

    s = ScanSettings(start_url=f"{BASE}/", respect_robots=False, delay=0, workers=1)
    st = run_scan(s, embedder_factory=env["embedder_factory"], crawler_factory=crawler_factory(site),
                  on_status=on_status, **env["dirs"])
    assert st.state == "cancelled" and not (scan_dir / "cancel").exists()


def test_scan_job_runs_in_background(env, monkeypatch):
    import imagecheck.scan as scan_mod
    site = _site()
    real = scan_mod.run_scan
    monkeypatch.setattr(scan_mod, "run_scan",
                        lambda **kw: real(crawler_factory=crawler_factory(site), **kw))
    job = ScanJob(env["embedder_factory"], **env["dirs"])
    assert job.start_scan(ScanSettings(start_url=f"{BASE}/", respect_robots=False, delay=0))
    job.join(60)
    assert not job.running
    assert job.last_result.state == "done" and job.status().state == "done"


def test_import_then_scan(env, tmp_path):
    csv = tmp_path / "inlinks.csv"
    csv.write_text('﻿"Type","Source","Destination","Alt Text","Link Position"\n'
                   f'"Image","{BASE}/old-page","{ORIG}/old.jpg","Old","Content"\n'
                   f'"Image","{BASE}/a","{ORIG}/a1.jpg","A1","Content"\n')
    st = run_import([csv], embedder_factory=env["embedder_factory"], **env["dirs"])
    assert st.state == "done" and _index_urls(env) == {"old.jpg", "a1.jpg"}
    st = _scan(env, _site())
    assert st.state == "done"
    assert "old.jpg" not in _index_urls(env)  # its page wasn't found by a complete scan


@pytest.mark.parametrize("url", [
    "http://www.columbiadoctors.org/",          # not https
    "https://169.254.169.254/latest/meta-data",  # cloud metadata
    "https://localhost:8501/",
    "https://www.columbiadoctors.org.evil.example/",
    "https://user:pw@www.columbiadoctors.org/",
    "https://www.columbiadoctors.org:8443/",
    "file:///etc/passwd",
    "",
])
def test_start_url_must_be_the_site(url):
    with pytest.raises(ScanNotAllowed):
        validate_start_url(url)


def test_allowed_start_urls():
    assert validate_start_url(" https://www.columbiadoctors.org/news ") == "https://www.columbiadoctors.org/news"
    assert validate_start_url("https://columbiadoctors.org/")
    assert validate_start_url("https://WWW.COLUMBIADOCTORS.ORG./")


def test_run_scan_refuses_off_site_start_without_fetching(env):
    site = _site()
    s = ScanSettings(start_url="https://169.254.169.254/", respect_robots=False, delay=0)
    st = run_scan(s, embedder_factory=env["embedder_factory"], crawler_factory=crawler_factory(site), **env["dirs"])
    assert st.state == "failed" and "ScanNotAllowed" in st.error
    assert site.requests == []


def test_run_scan_stops_if_crawl_is_redirected_off_site(env):
    site = _site()

    def factory(*a, **kw):
        c = crawler_factory(site)(*a, **kw)
        c.base_netloc = "internal.example"  # as if the start URL redirected there
        return c

    s = ScanSettings(start_url=f"{BASE}/", respect_robots=False, delay=0, workers=1)
    st = run_scan(s, embedder_factory=env["embedder_factory"], crawler_factory=factory, **env["dirs"])
    assert st.state == "failed" and "internal.example" in st.error
    assert PageStore.load(env["dirs"]["scan_dir"]).is_empty


def test_default_scan_skips_health_library_articles_but_keeps_landing_page(env):
    hl = f"{BASE}/health-library"
    site = FakeSite({
        f"{BASE}": html(links=[f"{BASE}/a", hl], images=["hero.jpg"]),
        f"{BASE}/a": html(images=["a1.jpg"]),
        hl: html(links=[f"{hl}/article/x", f"{hl}/condition/y"], images=["hl-landing.jpg"]),
        f"{hl}/article/x": html(images=["hw-art.jpg"]),
        f"{hl}/condition/y": html(images=["hw-cond.jpg"]),
    })
    st = _scan(env, site, exclude_paths=[])  # everything, once
    assert {"hw-art.jpg", "hl-landing.jpg"} <= _index_urls(env)

    site.requests.clear()
    st = _scan(env, site)  # defaults: health-library/* skipped
    assert st.state == "done" and st.summary["complete"]
    fetched = {u for u, _ in site.requests}
    assert hl in fetched and f"{hl}/article/x" not in fetched and f"{hl}/condition/y" not in fetched
    assert {"hero.jpg", "a1.jpg", "hl-landing.jpg", "hw-art.jpg", "hw-cond.jpg"} <= _index_urls(env)
    store = PageStore.load(env["dirs"]["scan_dir"])
    assert f"{hl}/article/x" in store.pages  # skipped, so kept as it was
    assert store.meta["last_full_scan"]["exclude_paths"] == ["health-library/*"]


def test_settings_reachable():
    s = ScanSettings()
    assert s.reachable(f"{BASE}/health-library") and s.reachable(f"{BASE}/health-library/")
    assert not s.reachable(f"{BASE}/health-library/article/abc")
    assert ScanSettings(include_paths=["news"]).reachable(f"{BASE}/news/x")
    assert not ScanSettings(include_paths=["news"]).reachable(f"{BASE}/specialties")
    assert ScanSettings(include_paths=["news/*"]).full_site is False


def test_orphan_pages_are_reached_via_sitemap_and_known_pages(env):
    site = _site()
    site.pages[f"{BASE}/report-2021"] = html(images=["report.jpg"])  # nothing links here
    site.pages[f"{BASE}/sitemap.xml"] = sitemap(f"{BASE}/report-2021", f"{BASE}/health-library/article/z",
                                                "https://evil.example/x")
    st = _scan(env, site)
    assert st.state == "done", st.error
    assert "report.jpg" in _index_urls(env)
    assert st.summary["seeds"]["sitemap_pages"] == 2  # health-library/* excluded; evil.example dropped later
    assert not any("evil.example" in u for u, _ in site.requests)

    # Next scan: the sitemap no longer lists it, but it's a known page, so it's rechecked.
    site.pages[f"{BASE}/sitemap.xml"] = sitemap()
    site.requests.clear()
    st = _scan(env, site)
    assert f"{BASE}/report-2021" in {u for u, _ in site.requests}
    assert "report.jpg" in _index_urls(env)
    # ...and once it's gone (404), its images drop out.
    del site.pages[f"{BASE}/report-2021"]
    st = _scan(env, site)
    assert "report.jpg" not in _index_urls(env)


def test_off_site_sitemaps_are_never_fetched(env):
    from imagecheck.scan import _sitemap_seeds
    site = _site()
    from tests.fake_site import crawler_factory as cf
    c = cf(site)(f"{BASE}/", respect_robots=False)
    c.sitemap_urls = lambda: ["https://169.254.169.254/sitemap.xml", f"{BASE}/sitemap.xml"]
    site.pages[f"{BASE}/sitemap.xml"] = (
        '<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<sitemap><loc>http://10.0.0.1/inner.xml</loc></sitemap></sitemapindex>')
    urls, warnings = _sitemap_seeds(c, ScanSettings(start_url=f"{BASE}/"), __import__("imagecheck.config").config.Scope())
    requested = [u for u, _ in site.requests]
    assert requested == [f"{BASE}/sitemap.xml"]
    assert urls == [] and any("10.0.0.1" in w for w in warnings)
