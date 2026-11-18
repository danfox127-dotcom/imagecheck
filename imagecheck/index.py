"""Build, store and load the site image index.

Layout of an index directory:
  images.json      metadata: one record per canonical site image, incl. failures
  embeddings.npz   `emb` (n_views, dim) float32, `owner` (n_views,) int32 -> images row
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from . import features
from .config import EmbeddingConfig
from .fetch import FetchError, ImageCache, fetch_image, make_session
from .ingest import SiteImage, clean_pages

CACHE_MAX_SIDE = 1024
SCHEMA_VERSION = 1


@dataclass
class Index:
    images: list[dict]
    emb: np.ndarray
    owner: np.ndarray
    meta: dict = field(default_factory=dict)

    # ------------------------------------------------------------ io

    @classmethod
    def empty(cls, dim: int = 384) -> "Index":
        return cls([], np.zeros((0, dim), np.float32), np.zeros(0, np.int32), {})

    @classmethod
    def load(cls, index_dir: Path) -> "Index":
        index_dir = Path(index_dir)
        doc = json.loads((index_dir / "images.json").read_text())
        arrs = np.load(index_dir / "embeddings.npz")
        return cls(doc["images"], arrs["emb"].astype(np.float32), arrs["owner"].astype(np.int32), doc.get("meta", {}))

    def save(self, index_dir: Path) -> None:
        index_dir = Path(index_dir)
        index_dir.mkdir(parents=True, exist_ok=True)
        tmp = index_dir / "images.json.tmp"
        tmp.write_text(json.dumps({"meta": self.meta, "images": self.images}, indent=1))
        np.savez_compressed(index_dir / "embeddings.tmp.npz", emb=self.emb.astype(np.float32), owner=self.owner)
        tmp.replace(index_dir / "images.json")
        (index_dir / "embeddings.tmp.npz").replace(index_dir / "embeddings.npz")

    # ------------------------------------------------------------ queries

    @property
    def ok_images(self) -> list[dict]:
        return [r for r in self.images if r["status"] == "ok"]

    @property
    def failed_images(self) -> list[dict]:
        return [r for r in self.images if r["status"] != "ok"]

    def coverage(self) -> dict:
        return {
            "total": len(self.images),
            "indexed": len(self.ok_images),
            "failed": [{"url": r["url"], "error": r.get("error", "")} for r in self.failed_images],
            "built_at": self.meta.get("built_at"),
        }


def _record(item: SiteImage) -> dict:
    return {
        "url": item.url,
        "getty_id": item.getty_id,
        "pages": clean_pages(item.pages),
        "alt_texts": sorted(a for a in item.alt_texts if a),
        "variants": sorted(item.variants),
        "status": "pending",
    }


def build_index(
    inventory: dict[str, SiteImage],
    index_dir: Path,
    cache_dir: Path,
    embedder: "features.Embedder",
    *,
    refresh: bool = False,
    workers: int = 8,
    retry_failed: bool = True,
    crawl_files: list[str] | None = None,
    log: Callable[[str], None] = print,
    checkpoint_every: int = 200,
) -> Index:
    """(Re)build the index for `inventory`, reusing prior work where possible.

    Incremental: images already indexed successfully are not re-downloaded,
    and not re-embedded unless the model or view settings changed (then they're
    re-embedded from the local cache). Images no longer in the crawl are dropped.
    """
    index_dir, cache_dir = Path(index_dir), Path(cache_dir)
    cache = ImageCache(cache_dir)
    cfg: EmbeddingConfig = embedder.cfg
    signature = features.views_signature(cfg)

    old = None
    if not refresh and (index_dir / "images.json").exists():
        old = Index.load(index_dir)
        if old.meta.get("schema") != SCHEMA_VERSION:
            log("Index format changed since last build; rebuilding from scratch.")
            old = None
    reembed = old is not None and old.meta.get("views") != signature
    if reembed:
        log("Embedding model/view settings changed; re-embedding cached images (no re-download).")

    reuse: dict[str, tuple[dict, np.ndarray | None]] = {}
    if old is not None:
        for i, r in enumerate(old.images):
            if r["status"] == "ok" and cache.has(r["url"]):
                reuse[r["url"]] = (r, None if reembed else old.emb[old.owner == i])
            elif r["status"] != "ok" and not retry_failed:
                reuse[r["url"]] = (r, old.emb[:0])

    images: list[dict] = []
    emb_parts: list[np.ndarray] = []
    owner_parts: list[np.ndarray] = []

    def add(rec: dict, vecs: np.ndarray) -> None:
        images.append(rec)
        emb_parts.append(vecs)
        owner_parts.append(np.full(len(vecs), len(images) - 1, np.int32))

    todo: list[dict] = []
    from_cache: list[dict] = []
    for url in sorted(inventory):
        rec = _record(inventory[url])
        if url in reuse:
            prev, vecs = reuse[url]
            for k in ("status", "error", "source_url", "sha256", "phash", "width", "height"):
                if k in prev:
                    rec[k] = prev[k]
            if vecs is None:
                from_cache.append(rec)
            else:
                add(rec, vecs)
        else:
            todo.append(rec)

    log(f"{len(inventory)} site images in crawl: {len(images)} already indexed, "
        f"{len(from_cache)} to re-embed from cache, {len(todo)} to fetch.")

    meta = {
        "schema": SCHEMA_VERSION,
        "model": cfg.model_name,
        "views": signature,
        "crop_aspects": list(cfg.crop_aspects),
        "crawl_files": crawl_files or [],
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    def snapshot() -> Index:
        dim = embedder.dim
        emb = np.concatenate(emb_parts) if emb_parts else np.zeros((0, dim), np.float32)
        owner = np.concatenate(owner_parts) if owner_parts else np.zeros(0, np.int32)
        return Index(list(images), emb, owner, dict(meta))

    session = make_session(pool_size=workers)
    t0 = time.time()
    done = 0
    pending_imgs: list[tuple[dict, features.Image.Image]] = []

    def flush() -> None:
        """Embed buffered images (batched across images for speed)."""
        if not pending_imgs:
            return
        all_views, spans = [], []
        for rec, img in pending_imgs:
            vs = [v for _, v in features.views(img, cfg.crop_aspects)]
            spans.append((len(all_views), len(all_views) + len(vs)))
            all_views += vs
        vecs = embedder.embed(all_views)
        for (rec, _), (a, b) in zip(pending_imgs, spans):
            add(rec, vecs[a:b])
        pending_imgs.clear()

    for k, rec in enumerate(from_cache, 1):
        pending_imgs.append((rec, cache.get(rec["url"])))
        if len(pending_imgs) >= 16:
            flush()
        if k % 200 == 0 or k == len(from_cache):
            log(f"  {k}/{len(from_cache)} re-embedded")
    flush()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_image, rec["url"], rec["variants"], session): rec for rec in todo
        }
        for fut in as_completed(futures):
            rec = futures[fut]
            try:
                fetched = fut.result()
                img = features.load_image(fetched.data)
                rec["sha256"] = features.sha256(fetched.data)
                rec["width"], rec["height"] = img.info.get("original_size", img.size)
                small = features.downscale(img, CACHE_MAX_SIDE)
                rec["phash"] = features.phash(small)
                rec["source_url"] = fetched.source_url
                cache.put(rec["url"], small)
                rec["status"] = "ok"
                rec.pop("error", None)
                pending_imgs.append((rec, small))
                if len(pending_imgs) >= 16:
                    flush()
            except (FetchError, OSError, ValueError) as e:
                rec["status"] = "failed"
                rec["error"] = str(e)[:500]
                add(rec, np.zeros((0, embedder.dim), np.float32))
            done += 1
            if done % 50 == 0 or done == len(todo):
                rate = done / max(time.time() - t0, 1e-6)
                log(f"  {done}/{len(todo)} fetched ({rate:.1f}/s)")
            if checkpoint_every and done % checkpoint_every == 0:
                flush()
                snapshot().save(index_dir)
    flush()

    # Keep a stable order (by URL) so diffs/inspection are easy.
    idx = snapshot()
    order = sorted(range(len(idx.images)), key=lambda i: idx.images[i]["url"])
    remap = np.empty(len(order), np.int32)
    remap[order] = np.arange(len(order), dtype=np.int32)
    idx.images = [idx.images[i] for i in order]
    idx.owner = remap[idx.owner] if len(idx.owner) else idx.owner
    idx.save(index_dir)

    cov = idx.coverage()
    log(f"Index saved to {index_dir}: {cov['indexed']}/{cov['total']} images indexed, "
        f"{len(cov['failed'])} failed, {len(idx.emb)} embedding views.")
    return idx
