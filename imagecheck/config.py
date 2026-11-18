"""Central configuration: scope of what gets indexed and match thresholds."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DEFAULT_INDEX_DIR = DATA_DIR / "index"
DEFAULT_CACHE_DIR = DATA_DIR / "cache"
DEFAULT_CRAWL_DIR = DATA_DIR / "crawls"

USER_AGENT = "imagecheck/2.0 (duplicate image checker)"


@dataclass
class Scope:
    """Which crawled image URLs are worth indexing."""

    # Only images served from these hosts are indexed. Third-party content
    # (Healthwise health-library art, YouTube/Vimeo thumbnails, maps) is not
    # something editors upload, so it's out of scope by default.
    hosts: tuple[str, ...] = ("www.columbiadoctors.org", "columbiadoctors.org")
    extensions: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".webp")
    # Path fragments for structural/generated assets that are never editorial photos.
    excluded_path_fragments: tuple[str, ...] = (
        "/staticmaps/",  # generated Google map tiles
        "/themes/",  # theme chrome: map markers, icons
    )
    # If an image ONLY ever appears in these Screaming Frog "Link Position"s, skip it.
    chrome_positions: tuple[str, ...] = ("Header", "Footer")


@dataclass
class Thresholds:
    """Match decision thresholds. Tuned by scripts/evaluate.py."""

    # pHash Hamming distance at or below which two images are the same file
    # (resized/recompressed but not cropped).
    phash_exact: int = 4
    # How many embedding-nearest site images get the geometric check.
    top_k: int = 15
    # Same photo = enough RANSAC-consistent keypoint matches...
    min_inliers: int = 20
    # ...spanning at least this fraction of one image's area (so a shared logo
    # or banner on two different graphics doesn't count)...
    min_coverage: float = 0.0
    # ...AND, after aligning the two images, at least this fraction of textured
    # patches agree pixel-wise. Rejects other frames from the same photo shoot,
    # whose static background also aligns.
    min_agreement: float = 0.75
    # Embedding cosine similarity at or above which an unconfirmed candidate is
    # surfaced for human review. Other frames from the same shoot measured
    # 0.82-0.90; merely similar subjects (e.g. two different tulip photos) ~0.75.
    review_similarity: float = 0.80
    # Max number of "review" candidates to show.
    max_review: int = 5

    def is_same_photo(self, inliers: int, coverage: float, agreement: float) -> bool:
        return (inliers >= self.min_inliers and coverage >= self.min_coverage
                and agreement >= self.min_agreement)

    @classmethod
    def load(cls, path: Path) -> "Thresholds":
        if path.exists():
            data = json.loads(path.read_text())
            known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            return cls(**known)
        return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")


@dataclass
class EmbeddingConfig:
    model_name: str = "facebook/dinov2-small"
    # Shorter image side fed to the model (must be a multiple of the 14px patch).
    short_side: int = 224
    long_side_max: int = 448
    # Center-crop aspect ratios embedded in addition to the full image, so a
    # site crop (e.g. a 3:1 hero) can match a full stock photo and vice versa.
    crop_aspects: tuple[float, ...] = field(default=(16 / 9, 3.0, 21 / 9, 1.0))
