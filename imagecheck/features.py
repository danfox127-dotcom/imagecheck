"""Image features: exact hashes, DINOv2 embeddings, and SIFT geometric verification.

Layering (cheap and certain first):
  1. SHA-256 of the file bytes -> byte-identical file.
  2. pHash -> same image resized/recompressed (not cropped).
  3. DINOv2 embedding (several crops/flip) -> shortlist of candidates,
     robust to crops, resizing, color tweaks.
  4. SIFT + RANSAC -> confirms the candidate is literally the same photograph
     (not just a similar-looking one or another frame from the same shoot).
"""

from __future__ import annotations

import hashlib
import io
import threading
from dataclasses import dataclass

import cv2
import imagehash
import numpy as np
from PIL import Image, ImageOps

from .config import EmbeddingConfig

Image.MAX_IMAGE_PIXELS = 200_000_000  # big stock originals are fine

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_PATCH = 14


# ---------------------------------------------------------------- loading


def load_image(data: bytes | io.IOBase) -> Image.Image:
    """Decode to RGB, honoring EXIF orientation and flattening transparency onto white."""
    im = Image.open(io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    return im.convert("RGB")


def downscale(img: Image.Image, max_side: int) -> Image.Image:
    w, h = img.size
    s = max_side / max(w, h)
    if s >= 1:
        return img
    return img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def phash(img: Image.Image) -> str:
    return str(imagehash.phash(img))


def phash_distance(a: str, b: str) -> int:
    return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)


# ---------------------------------------------------------------- views


def center_crop_aspect(img: Image.Image, aspect: float, position: float = 0.5) -> Image.Image:
    """Largest crop with width/height == aspect.

    `position` slides the crop along the axis being cut: 0 = left/top,
    0.5 = centered, 1 = right/bottom.
    """
    w, h = img.size
    if w / h > aspect:
        nw = round(h * aspect)
        x = round((w - nw) * position)
        return img.crop((x, 0, x + nw, h))
    nh = round(w / aspect)
    y = round((h - nh) * position)
    return img.crop((0, y, w, y + nh))


# A crop keeping less than this fraction of the frame also gets start- and
# end-aligned versions, since an off-center crop of it shares little with the center.
OFFCENTER_BELOW = 0.62


def views(img: Image.Image, aspects: tuple[float, ...], flip: bool = False,
          offcenter: bool = True) -> list[tuple[str, Image.Image]]:
    """The full image plus crops at the given aspect ratios.

    Crops that would barely differ from the full frame are skipped. Narrow crops
    are taken at the start, center and end of the frame when `offcenter` is set.
    """
    out = [("full", img)]
    a0 = img.width / img.height
    for a in aspects:
        ratio = max(a, a0) / min(a, a0)
        if ratio <= 1.15:
            continue
        positions = (0.5, 0.0, 1.0) if offcenter and 1 / ratio < OFFCENTER_BELOW else (0.5,)
        for pos in positions:
            out.append((f"crop{a:.2f}@{pos:g}", center_crop_aspect(img, a, pos)))
    if flip:
        out += [(f"flip-{name}", ImageOps.mirror(v)) for name, v in out]
    return out


def views_signature(cfg: EmbeddingConfig) -> str:
    """Identifies how index views are generated; a change means re-embedding."""
    return f"{cfg.model_name}|{cfg.short_side}x{cfg.long_side_max}|{list(cfg.crop_aspects)}|offcenter<{OFFCENTER_BELOW}"


# ---------------------------------------------------------------- embeddings


class Embedder:
    """DINOv2 global descriptors (Apache-2.0, runs on CPU)."""

    def __init__(self, cfg: EmbeddingConfig | None = None, device: str | None = None):
        import torch
        from transformers import AutoModel

        self.cfg = cfg or EmbeddingConfig()
        self.torch = torch
        if device is None:
            device = "cpu"
        self.device = device
        self.model = AutoModel.from_pretrained(self.cfg.model_name).to(device).eval()
        self.dim = self.model.config.hidden_size

    def _prep(self, img: Image.Image) -> np.ndarray:
        w, h = img.size
        s = self.cfg.short_side / min(w, h)
        if max(w, h) * s > self.cfg.long_side_max:
            s = self.cfg.long_side_max / max(w, h)
        nw = max(_PATCH, round(w * s / _PATCH) * _PATCH)
        nh = max(_PATCH, round(h * s / _PATCH) * _PATCH)
        arr = np.asarray(img.resize((nw, nh), Image.BICUBIC), dtype=np.float32) / 255.0
        return ((arr - _MEAN) / _STD).transpose(2, 0, 1)

    def embed(self, images: list[Image.Image]) -> np.ndarray:
        """L2-normalised descriptors, one row per image."""
        torch = self.torch
        out = np.zeros((len(images), self.dim), dtype=np.float32)
        # Group same-sized inputs into batches (crops of the same aspect share a size).
        prepped = [self._prep(im) for im in images]
        groups: dict[tuple[int, int], list[int]] = {}
        for i, p in enumerate(prepped):
            groups.setdefault(p.shape[1:], []).append(i)
        with torch.inference_mode():
            for idxs in groups.values():
                for start in range(0, len(idxs), 32):
                    chunk = idxs[start:start + 32]
                    x = torch.from_numpy(np.stack([prepped[i] for i in chunk])).to(self.device)
                    feats = self.model(pixel_values=x).pooler_output
                    feats = torch.nn.functional.normalize(feats, dim=-1)
                    out[chunk] = feats.cpu().numpy()
        return out


# ---------------------------------------------------------------- geometric verification


@dataclass
class LocalFeatures:
    points: np.ndarray  # (N, 2) float32 keypoint coords
    desc: np.ndarray | None  # (N, 128) float32
    size: tuple[int, int]  # (w, h) of the image the points live in
    gray: np.ndarray  # (h, w) uint8 grayscale at that size, for pixel comparison


# OpenCV detector/matcher objects aren't safe to share across threads (Streamlit
# serves sessions on separate threads), so keep one per thread.
_tls = threading.local()


def _sift():
    if not hasattr(_tls, "sift"):
        _tls.sift = cv2.SIFT_create(nfeatures=2000)
    return _tls.sift


def _matcher():
    if not hasattr(_tls, "matcher"):
        _tls.matcher = cv2.BFMatcher(cv2.NORM_L2)
    return _tls.matcher


def local_features(img: Image.Image, max_side: int = 800) -> LocalFeatures:
    small = downscale(img, max_side)
    gray = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2GRAY)
    # Contrast-normalise so brightness/colour edits don't starve the detector.
    eq = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    kps, desc = _sift().detectAndCompute(eq, None)
    pts = np.array([k.pt for k in kps], dtype=np.float32).reshape(-1, 2)
    return LocalFeatures(pts, desc, small.size, gray)


@dataclass
class Verification:
    inliers: int
    coverage: float  # fraction of one image's area spanned by inlier keypoints (0-1)
    agreement: float = 0.0  # fraction of textured overlapping patches that match pixel-wise (0-1)
    patches: int = 0  # how many patches the agreement was measured on

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.inliers > 0


def _hull_fraction(pts: np.ndarray, size: tuple[int, int]) -> float:
    if len(pts) < 3:
        return 0.0
    hull = cv2.convexHull(pts.astype(np.float32))
    return float(cv2.contourArea(hull)) / float(size[0] * size[1])


def photometric_agreement(a: LocalFeatures, b: LocalFeatures, M: np.ndarray,
                          ncc_min: float = 0.6, flat_std: float = 6.0) -> tuple[float, int]:
    """Warp `a` onto `b` with similarity transform M and compare patch by patch.

    A crop/resize/recompression/colour edit of the same photograph agrees almost
    everywhere (per-patch normalised cross-correlation is invariant to brightness
    and contrast). A *different frame* from the same shoot shares the static
    background but not the subject, so many textured patches disagree.
    Returns (fraction of informative patches that agree, number of patches).
    """
    h, w = b.gray.shape
    scale = float(np.hypot(M[0, 0], M[1, 0]))
    src = a.gray.astype(np.float32)
    if scale < 1:  # a is being shrunk: low-pass first to avoid aliasing
        src = cv2.GaussianBlur(src, (0, 0), 0.5 / scale)
    warped = cv2.warpAffine(src, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)
    valid = cv2.warpAffine(np.ones(a.gray.shape, np.uint8), M, (w, h), flags=cv2.INTER_NEAREST) > 0
    # Compare at the coarser of the two resolutions.
    sigma = max(1.0, 0.7 * scale)
    warped = cv2.GaussianBlur(warped, (0, 0), sigma)
    ref = cv2.GaussianBlur(b.gray.astype(np.float32), (0, 0), sigma)

    tile = max(16, min(h, w) // 10)
    agree = total = 0
    for y in range(0, h - tile + 1, tile):
        for x in range(0, w - tile + 1, tile):
            if valid[y:y + tile, x:x + tile].mean() < 0.95:
                continue
            pa = warped[y:y + tile, x:x + tile]
            pb = ref[y:y + tile, x:x + tile]
            sa, sb = float(pa.std()), float(pb.std())
            if sa < flat_std or sb < flat_std:
                # Flat on either side (sky, wall, bokeh, or a solid banner/overlay
                # added on top of the photo): says nothing either way.
                continue
            total += 1
            ncc = float(((pa - pa.mean()) * (pb - pb.mean())).mean() / (sa * sb))
            agree += ncc >= ncc_min
    return (agree / total if total else 0.0), total


def verify(a: LocalFeatures, b: LocalFeatures, ratio: float = 0.75) -> Verification:
    """Is `a` the same photograph as `b` (possibly cropped/resized/edited)?

    1. SIFT matches + RANSAC under a similarity transform (scale+rotation+shift):
       edits of one photograph keep a consistent transform between keypoints.
    2. Pixel agreement after alignment: rules out different frames of the same
       scene, whose static background also fits a single transform.
    """
    none = Verification(0, 0.0)
    if a.desc is None or b.desc is None or len(a.points) < 4 or len(b.points) < 4:
        return none
    knn = _matcher().knnMatch(a.desc, b.desc, k=2)
    good = [m[0] for m in knn if len(m) == 2 and m[0].distance < ratio * m[1].distance]
    if len(good) < 4:
        return none
    pa = a.points[[m.queryIdx for m in good]]
    pb = b.points[[m.trainIdx for m in good]]
    thresh = max(3.0, 0.008 * max(a.size + b.size))
    M, mask = cv2.estimateAffinePartial2D(
        pa, pb, method=cv2.RANSAC, ransacReprojThreshold=thresh, maxIters=4000, confidence=0.995
    )
    if M is None or mask is None:
        return none
    scale = float(np.hypot(M[0, 0], M[1, 0]))
    if not (0.05 < scale < 20):
        return none
    inl = mask.ravel().astype(bool)
    n = int(inl.sum())
    cov = max(_hull_fraction(pa[inl], a.size), _hull_fraction(pb[inl], b.size))
    agreement, patches = photometric_agreement(a, b, M) if n >= 8 else (0.0, 0)
    return Verification(n, cov, agreement, patches)
