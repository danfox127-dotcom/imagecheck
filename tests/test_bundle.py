import json
import zipfile

import pytest

from imagecheck import bundle


def _data(root, finished="2026-11-18T22:02:34+00:00"):
    files = {
        "scans/store.json": json.dumps({"last_full_scan": {"finished_at": finished}}),
        "scans/pages.jsonl": "{}\n",
        "index/embeddings.npz": "npz",
        "index/images.json": json.dumps({"images": [{"url": "a"}, {"url": "b"}]}),
        "cache/ab/abc.jpg": "jpeg",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def test_make_and_install_round_trip(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _data(src)
    info = bundle.make(tmp_path / "b.zip", src)
    assert info["images"] == 2 and info["scanned"].startswith("2026-11-18")
    with zipfile.ZipFile(tmp_path / "b.zip") as zf:
        assert "cache/ab/abc.jpg" not in zf.namelist()  # never ships site images

    assert bundle.local_scan_date(dst) == ""
    bundle.install(tmp_path / "b.zip", dst)
    assert bundle.local_scan_date(dst).startswith("2026-11-18")
    assert (dst / "index/embeddings.npz").read_text() == "npz"


def test_make_refuses_without_data(tmp_path):
    with pytest.raises(bundle.BundleError):
        bundle.make(tmp_path / "b.zip", tmp_path)


@pytest.mark.parametrize("names", [
    ["../evil.txt", *bundle.FILES[1:]],
    [*bundle.FILES, "app.py"],
    list(bundle.FILES[:2]),
])
def test_read_rejects_unexpected_contents(tmp_path, names):
    path = tmp_path / "b.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for n in names:
            zf.writestr(n, "x")
    with pytest.raises(bundle.BundleError):
        bundle.read(path)


def test_read_rejects_non_zip(tmp_path):
    (tmp_path / "b.zip").write_text("not a zip")
    with pytest.raises(bundle.BundleError):
        bundle.read(tmp_path / "b.zip")


def test_get_data_keeps_newer_local_data(tmp_path, capsys):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "get_data", Path(__file__).resolve().parent.parent / "scripts/get_data.py")
    get_data = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(get_data)

    old, local = tmp_path / "old", tmp_path / "local"
    _data(old, "2026-11-01T00:00:00+00:00")
    bundle.make(tmp_path / "old.zip", old)
    _data(local, "2026-11-18T00:00:00+00:00")

    assert get_data.main(["--file", str(tmp_path / "old.zip"), "--data-dir", str(local)]) == 0
    assert "up to date" in capsys.readouterr().out
    assert bundle.local_scan_date(local).startswith("2026-11-18")

    assert get_data.main(["--file", str(tmp_path / "old.zip"), "--data-dir", str(local), "--force"]) == 0
    assert bundle.local_scan_date(local).startswith("2026-11-01")
