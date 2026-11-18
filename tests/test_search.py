import numpy as np
from PIL import ImageEnhance, ImageOps

from imagecheck import features, index as index_mod
from imagecheck.config import Thresholds
from imagecheck.fetch import FetchError, Fetched, ImageCache
from imagecheck.index import Index, build_index
from imagecheck.ingest import SiteImage
from imagecheck.search import NO_MATCH, SAME, Searcher
from tests.conftest import synthetic_photo, to_jpeg

BASE = "https://www.columbiadoctors.org/sites/default/files/media/images"
URLS = [f"{BASE}/2024-01/photo{i}.jpg" for i in range(6)] + [f"{BASE}/2024-02/gettyimages-123456789.jpg"]
BROKEN = f"{BASE}/2024-03/broken.jpg"


def _inventory():
    inv = {u: SiteImage(url=u, variants={u}, pages={f"https://www.columbiadoctors.org/p{i}"})
           for i, u in enumerate(URLS + [BROKEN])}
    return inv


def _site_images():
    return {u: synthetic_photo(100 + i) for i, u in enumerate(URLS)}


def _build(tmp_path, monkeypatch, fake_embedder, calls=None):
    images = _site_images()

    def fake_fetch(url, variants=(), session=None):
        if calls is not None:
            calls.append(url)
        if url == BROKEN:
            raise FetchError("HTTP 404")
        return Fetched(to_jpeg(images[url]), url)

    monkeypatch.setattr(index_mod, "fetch_image", fake_fetch)
    idx = build_index(_inventory(), tmp_path / "index", tmp_path / "cache", fake_embedder, workers=2, log=lambda m: None)
    return idx, images


def test_build_index_records_failures_and_is_incremental(tmp_path, monkeypatch, fake_embedder):
    calls = []
    idx, _ = _build(tmp_path, monkeypatch, fake_embedder, calls)
    cov = idx.coverage()
    assert cov["total"] == 8 and cov["indexed"] == 7
    assert cov["failed"][0]["url"] == BROKEN and "404" in cov["failed"][0]["error"]
    assert set(np.unique(idx.owner)) == {i for i, r in enumerate(idx.images) if r["status"] == "ok"}

    loaded = Index.load(tmp_path / "index")
    assert [r["url"] for r in loaded.images] == [r["url"] for r in idx.images]
    assert np.allclose(loaded.emb, idx.emb)

    # Second build only retries the failure.
    calls.clear()
    idx2 = build_index(_inventory(), tmp_path / "index", tmp_path / "cache", fake_embedder, workers=2, log=lambda m: None)
    assert calls == [BROKEN]
    assert len(idx2.emb) == len(idx.emb)
    for r in idx2.images:
        row = idx2.images.index(r)
        old_row = [x["url"] for x in idx.images].index(r["url"])
        assert np.allclose(idx2.emb[idx2.owner == row], idx.emb[idx.owner == old_row])


def _searcher(tmp_path, monkeypatch, fake_embedder):
    idx, images = _build(tmp_path, monkeypatch, fake_embedder)
    s = Searcher(idx, fake_embedder, ImageCache(tmp_path / "cache"), Thresholds())
    return s, idx, images


def test_search_finds_edited_copies(tmp_path, monkeypatch, fake_embedder):
    s, idx, images = _searcher(tmp_path, monkeypatch, fake_embedder)
    target = URLS[2]
    row = [r["url"] for r in idx.images].index(target)
    img = images[target]
    for name, q in {
        "hero_crop": features.center_crop_aspect(img, 3.0),
        "mirrored": ImageOps.mirror(img),
        "darker": ImageEnhance.Brightness(img).enhance(0.85),
        "thumbnail": img.resize((260, 173)),
    }.items():
        r = s.search(to_jpeg(q), "upload.jpg")
        assert r.verdict == SAME, name
        assert r.same[0].row == row, name
        assert "geometric" in r.same[0].reasons, name


def test_search_exact_file_and_getty_id(tmp_path, monkeypatch, fake_embedder):
    s, idx, images = _searcher(tmp_path, monkeypatch, fake_embedder)
    data = to_jpeg(images[URLS[0]])
    r = s.search(data, "whatever.jpg")
    assert r.verdict == SAME and "exact_file" in r.same[0].reasons

    # Getty ID matches even when the pixels are a different rendition (e.g. a watermarked comp).
    comp = synthetic_photo(999)
    r = s.search(to_jpeg(comp), "GettyImages-123456789.jpg")
    assert r.verdict == SAME
    assert r.same[0].url.endswith("gettyimages-123456789.jpg")
    assert r.same[0].reasons == ["getty_id"]


def test_search_unrelated_image_is_no_match_and_reports_coverage(tmp_path, monkeypatch, fake_embedder):
    s, idx, _ = _searcher(tmp_path, monkeypatch, fake_embedder)
    r = s.search(to_jpeg(synthetic_photo(4242)), "new.jpg")
    assert r.verdict != SAME
    assert all(c.inliers < s.t.min_inliers for c in r.candidates)
    assert r.coverage["indexed"] == 7 and len(r.coverage["failed"]) == 1


def test_search_exclude_supports_leave_one_out(tmp_path, monkeypatch, fake_embedder):
    s, idx, images = _searcher(tmp_path, monkeypatch, fake_embedder)
    row = [r["url"] for r in idx.images].index(URLS[3])
    r = s.search(to_jpeg(images[URLS[3]]), exclude=[row])
    assert row not in {c.row for c in r.candidates}
    assert r.verdict != SAME


def test_thresholds_roundtrip(tmp_path):
    t = Thresholds(min_inliers=33, review_similarity=0.6)
    t.save(tmp_path / "t.json")
    assert Thresholds.load(tmp_path / "t.json") == t
    assert Thresholds.load(tmp_path / "missing.json") == Thresholds()
    assert NO_MATCH != SAME
