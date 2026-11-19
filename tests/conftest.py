import io
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imagecheck.config import EmbeddingConfig  # noqa: E402


def synthetic_photo(seed: int, size=(900, 600)) -> Image.Image:
    """A textured image with lots of distinctive keypoints (stand-in for a photo)."""
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (size[1] // 20, size[0] // 20, 3), dtype=np.uint8)
    img = Image.fromarray(base).resize(size, Image.BICUBIC)
    d = ImageDraw.Draw(img)
    for _ in range(60):
        x, y = rng.integers(0, size[0]), rng.integers(0, size[1])
        r = int(rng.integers(8, 60))
        color = tuple(int(c) for c in rng.integers(0, 255, 3))
        if rng.random() < 0.5:
            d.ellipse((x - r, y - r, x + r, y + r), fill=color)
        else:
            d.rectangle((x - r, y - r // 2, x + r, y + r // 2), fill=color)
    return img


def to_jpeg(img: Image.Image, quality=90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


class FakeEmbedder:
    """Cheap deterministic stand-in for DINOv2: normalised 16x16 colour thumbnail."""

    def __init__(self):
        self.cfg = EmbeddingConfig()
        self.dim = 16 * 16 * 3

    def embed(self, images):
        out = []
        for im in images:
            v = np.asarray(im.resize((16, 16)), dtype=np.float32).ravel()
            v -= v.mean()
            out.append(v / (np.linalg.norm(v) + 1e-6))
        return np.stack(out).astype(np.float32)


@pytest.fixture
def fake_embedder():
    return FakeEmbedder()
