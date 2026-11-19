"""Answer "is this image already on the site?" against a prebuilt index."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
from PIL import Image, ImageOps

from . import features
from .config import EmbeddingConfig, Thresholds
from .fetch import FetchError, ImageCache, fetch_image, make_session
from .index import Index
from .ingest import getty_id

SAME = "same_photo"
REVIEW = "review"
NO_MATCH = "no_match"


@dataclass
class Candidate:
    row: int  # index into Index.images
    url: str
    similarity: float = 0.0  # best DINOv2 cosine over view pairs
    inliers: int = 0
    coverage: float = 0.0
    agreement: float = 0.0
    phash_distance: int | None = None
    reasons: list[str] = field(default_factory=list)  # why it's a match, strongest first
    verdict: str = NO_MATCH
    error: str | None = None  # e.g. couldn't load image for verification


@dataclass
class SearchResult:
    verdict: str
    matches: list[Candidate]  # SAME first, then REVIEW, best first
    candidates: list[Candidate]  # every candidate examined (for debugging/eval)
    coverage: dict
    timings: dict

    @property
    def same(self) -> list[Candidate]:
        return [c for c in self.matches if c.verdict == SAME]

    @property
    def review(self) -> list[Candidate]:
        return [c for c in self.matches if c.verdict == REVIEW]


class Searcher:
    def __init__(
        self,
        index: Index,
        embedder: "features.Embedder",
        cache: ImageCache | None = None,
        thresholds: Thresholds | None = None,
        embed_cfg: EmbeddingConfig | None = None,
    ):
        self.index = index
        self.embedder = embedder
        self.cache = cache
        self.t = thresholds or Thresholds()
        self.cfg = embed_cfg or embedder.cfg
        self._session = None
        self._local: dict[int, features.LocalFeatures] = {}
        self._sha = {r["sha256"]: i for i, r in enumerate(index.images) if r.get("sha256")}
        self._getty: dict[str, list[int]] = {}
        for i, r in enumerate(index.images):
            if r.get("getty_id"):
                self._getty.setdefault(r["getty_id"], []).append(i)

    # ------------------------------------------------------------ helpers

    def site_image(self, row: int) -> Image.Image:
        """Site image for verification: local cache first, live fetch as fallback."""
        rec = self.index.images[row]
        if self.cache is not None:
            img = self.cache.get(rec["url"])
            if img is not None:
                return img
        if self._session is None:
            self._session = make_session(4)
        fetched = fetch_image(rec["url"], rec.get("variants", []), self._session)
        img = features.downscale(features.load_image(fetched.data), 1024)
        if self.cache is not None:
            self.cache.put(rec["url"], img)
        return img

    def _site_local(self, row: int) -> features.LocalFeatures:
        if row not in self._local:
            self._local[row] = features.local_features(self.site_image(row))
        return self._local[row]

    # ------------------------------------------------------------ main entry

    def search(
        self,
        data: bytes | None = None,
        filename: str | None = None,
        *,
        image: Image.Image | None = None,
        exclude: Iterable[int] = (),
    ) -> SearchResult:
        """Search by file bytes (preferred: enables exact-file check) or a PIL image.

        `exclude` drops index rows from consideration (used by leave-one-out eval).
        """
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        exclude = set(exclude)
        if image is None:
            if data is None:
                raise ValueError("pass data or image")
            image = features.load_image(data)
        query = features.downscale(image, 1024)
        cands: dict[int, Candidate] = {}

        def cand(row: int) -> Candidate:
            if row not in cands:
                cands[row] = Candidate(row=row, url=self.index.images[row]["url"])
            return cands[row]

        # 1. Getty asset ID in the filename.
        gid = getty_id(filename)
        if gid:
            for row in self._getty.get(gid, []):
                if row not in exclude:
                    cand(row).reasons.append("getty_id")

        # 2. Byte-identical file.
        if data is not None:
            row = self._sha.get(features.sha256(data))
            if row is not None and row not in exclude:
                cand(row).reasons.append("exact_file")
        timings["ids"] = time.perf_counter() - t0

        # 3. Embedding shortlist (all query views vs all index views, max per image).
        t1 = time.perf_counter()
        qviews = features.views(query, self.cfg.crop_aspects, flip=True)
        qemb = self.embedder.embed([v for _, v in qviews])
        n_rows = len(self.index.images)
        best = np.full(n_rows, -1.0, dtype=np.float32)
        if len(self.index.emb):
            sims = (self.index.emb @ qemb.T).max(axis=1)
            np.maximum.at(best, self.index.owner, sims)
        if exclude:
            best[list(exclude)] = -1.0
        top = [int(r) for r in np.argsort(-best)[: self.t.top_k] if best[r] > -1.0]
        for row in top:
            cand(row)
        for row, c in cands.items():
            c.similarity = float(best[row])
        timings["embed"] = time.perf_counter() - t1

        # 4. pHash + geometric verification on every candidate.
        t2 = time.perf_counter()
        qhash = features.phash(query)
        qlocal = features.local_features(query)
        qlocal_flip = features.local_features(ImageOps.mirror(query))
        for row, c in cands.items():
            rec = self.index.images[row]
            if rec.get("phash"):
                c.phash_distance = features.phash_distance(qhash, rec["phash"])
                if c.phash_distance <= self.t.phash_exact:
                    c.reasons.append("near_exact")
            try:
                site = self._site_local(row)
            except (FetchError, OSError) as e:
                c.error = f"could not load site image for verification: {e}"
                continue
            v = max(features.verify(qlocal, site), features.verify(qlocal_flip, site),
                    key=lambda v: (self.t.is_same_photo(v.inliers, v.coverage, v.agreement),
                                   v.inliers * v.agreement, v.inliers))
            c.inliers, c.coverage, c.agreement = v.inliers, v.coverage, v.agreement
            if self.t.is_same_photo(v.inliers, v.coverage, v.agreement):
                c.reasons.append("geometric")
        timings["verify"] = time.perf_counter() - t2

        # 5. Verdicts.
        for c in cands.values():
            if c.reasons:
                c.verdict = SAME
            elif c.similarity >= self.t.review_similarity or c.inliers >= self.t.min_inliers:
                # Looks alike, or shares a lot of aligned detail (typically another
                # frame from the same shoot): a human should look.
                c.verdict = REVIEW
        order = sorted(cands.values(), key=lambda c: (c.verdict != SAME, -(c.agreement * c.inliers), -c.similarity))
        same = [c for c in order if c.verdict == SAME]
        review = [c for c in order if c.verdict == REVIEW][: self.t.max_review]
        verdict = SAME if same else REVIEW if review else NO_MATCH
        timings["total"] = time.perf_counter() - t0
        return SearchResult(verdict, same + review, order, self.index.coverage(), timings)
