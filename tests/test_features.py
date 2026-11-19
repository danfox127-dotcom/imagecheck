from PIL import ImageEnhance, ImageOps

from imagecheck import features
from tests.conftest import synthetic_photo, to_jpeg


def test_center_crop_aspect():
    img = synthetic_photo(1, (900, 600))
    assert features.center_crop_aspect(img, 3.0).size == (900, 300)
    assert features.center_crop_aspect(img, 1.0).size == (600, 600)


def test_views_skips_near_identical_crops_and_adds_flips():
    img = synthetic_photo(1, (1600, 900))  # already 16:9
    names = [n for n, _ in features.views(img, (16 / 9, 3.0), offcenter=False)]
    assert names == ["full", "crop3.00@0.5"]
    names = [n for n, _ in features.views(img, (16 / 9, 3.0), flip=True, offcenter=False)]
    assert names == ["full", "crop3.00@0.5", "flip-full", "flip-crop3.00@0.5"]


def test_views_adds_offcenter_versions_of_narrow_crops():
    wide = synthetic_photo(1, (1200, 400))  # 3:1 banner; a square crop keeps 1/3 of it
    named = dict(features.views(wide, (1.0,)))
    assert set(named) == {"full", "crop1.00@0.5", "crop1.00@0", "crop1.00@1"}
    assert named["crop1.00@0"].size == (400, 400)
    assert named["crop1.00@0"].tobytes() == wide.crop((0, 0, 400, 400)).tobytes()
    assert named["crop1.00@1"].tobytes() == wide.crop((800, 0, 1200, 400)).tobytes()
    assert "crop1.00@0" not in dict(features.views(wide, (1.0,), offcenter=False))


def test_load_image_flattens_transparency():
    from PIL import Image
    import io
    im = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    out = features.load_image(buf.getvalue())
    assert out.mode == "RGB" and out.getpixel((0, 0)) == (255, 255, 255)


def test_phash_distance_survives_recompression_and_resize():
    img = synthetic_photo(2)
    a = features.phash(img)
    b = features.phash(features.load_image(to_jpeg(img.resize((300, 200)), quality=50)))
    assert features.phash_distance(a, b) <= 4


def test_verify_confirms_edited_copies():
    img = synthetic_photo(3)
    ref = features.local_features(img)
    edits = {
        "crop_3_1": features.center_crop_aspect(img, 3.0),
        "offcenter_crop": img.crop((50, 120, 650, 520)),
        "thumbnail": img.resize((260, 173)),
        "brighter": ImageEnhance.Brightness(img).enhance(1.2),
        "jpeg_q40": features.load_image(to_jpeg(img, 40)),
    }
    for name, q in edits.items():
        v = features.verify(features.local_features(q), ref)
        assert v.inliers >= 30, (name, v)
        assert v.coverage >= 0.1, (name, v)
        assert v.agreement >= 0.85, (name, v)


def test_verify_rejects_different_photos():
    a = features.local_features(synthetic_photo(4))
    for seed in (5, 6, 7):
        v = features.verify(a, features.local_features(synthetic_photo(seed)))
        assert v.inliers < 15, (seed, v)


def test_mirrored_copy_only_matches_via_flipped_query():
    img = synthetic_photo(8)
    ref = features.local_features(img)
    mirrored = ImageOps.mirror(img)
    assert features.verify(features.local_features(ImageOps.mirror(mirrored)), ref).inliers >= 30


def _scene_with_subject(subject_xy, seed=20):
    """Same static background, a large textured 'subject' placed at different spots."""
    from PIL import ImageDraw
    img = synthetic_photo(seed)
    subject = synthetic_photo(seed + 1, (330, 420))
    out = img.copy()
    out.paste(subject, subject_xy)
    ImageDraw.Draw(out)
    return out


def test_agreement_rejects_other_frame_from_same_shoot():
    frame_a = _scene_with_subject((60, 90))
    frame_b = _scene_with_subject((500, 120))  # subject moved; background identical
    va = features.verify(features.local_features(frame_b), features.local_features(frame_a))
    assert va.inliers >= 30  # the static background aligns...
    assert va.agreement < 0.75, va  # ...but the content doesn't

    # An edited copy of frame_a agrees nearly everywhere.
    crop = features.center_crop_aspect(frame_a, 3.0)
    vc = features.verify(features.local_features(crop), features.local_features(frame_a))
    assert vc.agreement >= 0.9, vc


def test_load_image_rejects_decompression_bombs_before_decoding():
    import struct
    import time
    import zlib

    import pytest

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    # A ~100-byte PNG whose header claims 30000 x 30000 pixels (900 MP).
    ihdr = struct.pack(">IIBBBBB", 30000, 30000, 8, 2, 0, 0, 0)
    bomb = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b"")
    t = time.perf_counter()
    with pytest.raises(ValueError):
        features.load_image(bomb)
    assert time.perf_counter() - t < 1.0


def test_load_image_decodes_large_jpegs_reduced_but_reports_original_size():
    from PIL import Image
    big = Image.new("RGB", (6000, 4000), (120, 30, 200))
    out = features.load_image(to_jpeg(big))
    assert out.info["original_size"] == (6000, 4000)
    assert max(out.size) <= 4096 and max(out.size) >= 1024
