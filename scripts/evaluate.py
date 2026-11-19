"""Measure matching accuracy against the real site index, and tune thresholds.

Positives: sampled site images put through realistic edits (crops, thumbnails,
recompression, colour tweaks, mirroring, text overlays), plus the actual crops
Drupal serves on the live site. Each must come back as "same photo" pointing at
its original.

Negatives:
  * in-index hard negatives: for every query, all the *other* nearest site
    images (similar medical/stock photos) must NOT be confirmed as the same photo.
  * optional external photos (--negatives DIR, --picsum N) that aren't on the site.

    python scripts/evaluate.py                      # ~50 images x 12 edits + live crops
    python scripts/evaluate.py --n 100 --picsum 100 --write-thresholds
"""

from __future__ import annotations

import argparse
import io
import json
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageEnhance, ImageOps  # noqa: E402

from imagecheck import features  # noqa: E402
from imagecheck.config import DEFAULT_CACHE_DIR, DEFAULT_INDEX_DIR, Thresholds  # noqa: E402
from imagecheck.fetch import FetchError, ImageCache, fetch_bytes, make_session  # noqa: E402
from imagecheck.index import Index  # noqa: E402
from imagecheck.search import Searcher  # noqa: E402

# ------------------------------------------------------------------ edits


def _jpeg(img: Image.Image, q: int) -> Image.Image:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=q)
    return features.load_image(buf.getvalue())


def _crop_frac(img: Image.Image, aspect: float, fx: float, fy: float, scale: float = 1.0) -> Image.Image:
    """Crop with given aspect, `scale` of the max size, positioned at (fx, fy) in [0,1]."""
    w, h = img.size
    cw, ch = (w, round(w / aspect)) if w / h < aspect else (round(h * aspect), h)
    cw, ch = max(16, round(cw * scale)), max(16, round(ch * scale))
    x, y = round((w - cw) * fx), round((h - ch) * fy)
    return img.crop((x, y, x + cw, y + ch))


def _overlay(img: Image.Image) -> Image.Image:
    out = img.copy()
    d = ImageDraw.Draw(out)
    w, h = out.size
    d.rectangle((0, int(h * 0.72), w, int(h * 0.92)), fill=(20, 40, 90))
    d.text((int(w * 0.05), int(h * 0.76)), "Schedule your appointment today", fill=(255, 255, 255))
    return out


EDITS = {
    "crop_16x9_center": lambda im: features.center_crop_aspect(im, 16 / 9),
    "crop_3x1_hero": lambda im: _crop_frac(im, 3.0, 0.5, 0.3),
    "crop_21x9_offcenter": lambda im: _crop_frac(im, 21 / 9, 0.5, 0.15),
    "crop_square_left": lambda im: _crop_frac(im, 1.0, 0.1, 0.5),
    "crop_tight_50pct": lambda im: _crop_frac(im, im.width / im.height, 0.7, 0.4, scale=0.7),
    "thumb_260px": lambda im: im.resize((260, max(1, round(260 * im.height / im.width)))),
    "jpeg_q40": lambda im: _jpeg(im, 40),
    "brighter_15pct": lambda im: ImageEnhance.Brightness(im).enhance(1.15),
    "desaturated": lambda im: ImageEnhance.Color(im).enhance(0.4),
    "mirrored": ImageOps.mirror,
    "text_overlay": _overlay,
    "hero_combo": lambda im: _jpeg(_crop_frac(im, 3.0, 0.4, 0.4).resize((900, 300)), 60),
}


# ------------------------------------------------------------------ scoring


def passes(c: dict, t: dict) -> bool:
    return c["near_exact"] or (
        c["inliers"] >= t["min_inliers"] and c["coverage"] >= t["min_coverage"]
        and c["agreement"] >= t["min_agreement"]
    )


def load_known_duplicates(path: Path) -> set[frozenset[str]]:
    """Pairs of site URLs confirmed by eye to be the same photo (real duplicates)."""
    pairs = set()
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                a, b = line.split()[:2]
                pairs.add(frozenset((a, b)))
    return pairs


def run_query(searcher: Searcher, img: Image.Image, true_row: int | None, dup_rows: set[int]) -> dict:
    r = searcher.search(image=img)
    cands = [
        {"row": c.row, "url": c.url, "inliers": c.inliers, "coverage": round(c.coverage, 4),
         "similarity": round(c.similarity, 4), "agreement": round(c.agreement, 4), "near_exact": "near_exact" in c.reasons}
        for c in r.candidates
    ]
    true = next((c for c in cands if c["row"] == true_row), None)
    others = [c for c in cands if c["row"] != true_row and c["row"] not in dup_rows]
    return {"true": true, "others": others, "seconds": r.timings["total"]}


def sweep(pos: list[dict], neg_sets: list[list[dict]], max_fp_rate: float,
          min_recall: float) -> tuple[list[dict], dict]:
    rows = []
    for mi in (10, 15, 20, 30, 50):
        for mc in (0.0, 0.1):
            for ma in (0.5, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9):
                t = {"min_inliers": mi, "min_coverage": mc, "min_agreement": ma}
                tp = sum(1 for p in pos if p["true"] and passes(p["true"], t))
                fp = sum(1 for ns in neg_sets if any(passes(c, t) for c in ns))
                rows.append({**t, "recall": tp / max(len(pos), 1), "fp_rate": fp / max(len(neg_sets), 1)})
    # A wrong "already on the site" (e.g. for another frame of the same shoot) is
    # worse than a miss, because misses with strong evidence still show up as
    # "review". So: lowest false-match rate while recall stays >= min_recall,
    # then best recall, then the strictest setting.
    strict = lambda r: (r["min_agreement"], r["min_inliers"], r["min_coverage"])  # noqa: E731
    ok = [r for r in rows if r["recall"] >= min_recall and r["fp_rate"] <= max_fp_rate]
    best = min(ok, key=lambda r: (round(r["fp_rate"], 4), -round(r["recall"], 4), tuple(-x for x in strict(r)))) \
        if ok else max(rows, key=lambda r: (r["recall"] - 5 * r["fp_rate"]))
    return rows, best


# ------------------------------------------------------------------ main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    ap.add_argument("--n", type=int, default=50, help="site images to sample")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--edits", nargs="*", default=list(EDITS), choices=list(EDITS))
    ap.add_argument("--no-live-variants", action="store_true", help="skip fetching real Drupal crops")
    ap.add_argument("--negatives", type=Path, help="folder of photos known NOT to be on the site")
    ap.add_argument("--picsum", type=int, default=0, help="download N random photos from picsum.photos as negatives")
    ap.add_argument("--max-fp-rate", type=float, default=0.01)
    ap.add_argument("--min-recall", type=float, default=0.98)
    ap.add_argument("--known-duplicates", type=Path, default=Path(__file__).resolve().parent.parent / "data" / "known_duplicates.txt",
                    help="site URL pairs confirmed to be the same photo; not counted as false matches")
    ap.add_argument("--report", type=Path, help="report path (default: <index-dir>/eval_report.md)")
    ap.add_argument("--write-thresholds", action="store_true", help="save tuned thresholds for the app")
    args = ap.parse_args(argv)

    index = Index.load(args.index_dir)
    cache = ImageCache(args.cache_dir)
    base = Thresholds.load(args.index_dir / "thresholds.json")
    # Verify everything; pass/fail is decided from raw scores in the sweep. The
    # report's headline uses the sweep's pick; compare with the saved thresholds:
    print(f"Current thresholds: {asdict(base)}")
    probe = Thresholds(**{**asdict(base), "min_inliers": 10**9})
    embedder = features.Embedder()
    searcher = Searcher(index, embedder, cache, probe)

    ok_rows = [i for i, r in enumerate(index.images) if r["status"] == "ok" and min(r["width"], r["height"]) >= 200]
    rng = random.Random(args.seed)
    sample = rng.sample(ok_rows, min(args.n, len(ok_rows)))
    by_sha: dict[str, set[int]] = {}
    for i, r in enumerate(index.images):
        if r.get("sha256"):
            by_sha.setdefault(r["sha256"], set()).add(i)
    row_of = {r["url"]: i for i, r in enumerate(index.images)}
    known = load_known_duplicates(args.known_duplicates)
    for pair in known:
        a, b = tuple(pair)
        if a in row_of and b in row_of:
            by_sha.setdefault(f"dup:{a}", set()).add(row_of[b])
            by_sha.setdefault(f"dup:{b}", set()).add(row_of[a])

    print(f"Evaluating {len(sample)} site images x {len(args.edits)} edits"
          f"{'' if args.no_live_variants else ' + live Drupal crops'}...")
    pos: list[dict] = []
    t0 = time.time()
    session = make_session(4)
    for k, row in enumerate(sample, 1):
        rec = index.images[row]
        img = cache.get(rec["url"])
        dups = (by_sha.get(rec.get("sha256"), set()) | by_sha.get(f"dup:{rec['url']}", set())) - {row}
        queries = [(name, EDITS[name](img)) for name in args.edits]
        if not args.no_live_variants:
            variants = [v for v in rec.get("variants", []) if "/styles/" in v]
            for v in rng.sample(variants, min(2, len(variants))):
                try:
                    queries.append(("live_drupal_crop", features.load_image(fetch_bytes(v, session))))
                except (FetchError, OSError):
                    pass
        for name, q in queries:
            res = run_query(searcher, q, row, dups)
            res.update(edit=name, url=rec["url"])
            pos.append(res)
        if k % 10 == 0 or k == len(sample):
            print(f"  {k}/{len(sample)} images ({time.time() - t0:.0f}s)")

    ext: list[dict] = []
    ext_imgs: list[tuple[str, Image.Image]] = []
    if args.negatives:
        for p in sorted(args.negatives.iterdir()):
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                ext_imgs.append((p.name, features.load_image(p.read_bytes())))
    for i in range(args.picsum):
        try:
            ext_imgs.append((f"picsum-{i}", features.load_image(
                fetch_bytes(f"https://picsum.photos/seed/imagecheck{i}/1200/800", session))))
        except (FetchError, OSError) as e:
            print(f"  (picsum {i} failed: {e})")
    for name, q in ext_imgs:
        res = run_query(searcher, q, None, set())
        res.update(edit="external_negative", url=name)
        ext.append(res)

    neg_sets = [p["others"] for p in pos] + [e["others"] for e in ext]
    rows, best = sweep(pos, neg_sets, args.max_fp_rate, args.min_recall)

    # ----- report
    mi, mc, ma = best["min_inliers"], best["min_coverage"], best["min_agreement"]
    lines = ["# imagecheck accuracy report", "",
             f"- Index: {len(index.ok_images)} site images ({index.meta.get('built_at')}), model `{index.meta.get('model')}`",
             f"- Positives: {len(pos)} queries from {len(sample)} site images; external negatives: {len(ext)}",
             f"- Chosen thresholds: **min_inliers={mi}, min_coverage={mc}, min_agreement={ma}** "
             f"-> recall **{best['recall']:.1%}**, false-match rate **{best['fp_rate']:.2%}** "
             f"(targets: recall >= {args.min_recall:.0%}, false-match <= {args.max_fp_rate:.0%})"]
    saved = asdict(base)
    s_rec = sum(1 for p in pos if p["true"] and passes(p["true"], saved)) / max(len(pos), 1)
    s_fp = sum(1 for ns in neg_sets if any(passes(c, saved) for c in ns)) / max(len(neg_sets), 1)
    lines += [f"- At the app's current thresholds (min_inliers={base.min_inliers}, min_coverage={base.min_coverage}, "
              f"min_agreement={base.min_agreement}): recall **{s_rec:.1%}**, false-match rate **{s_fp:.2%}**", ""]

    lines += ["## Recall by edit", "", "| edit | n | in shortlist | confirmed same photo | median inliers |",
              "|---|---|---|---|---|"]
    for name in list(dict.fromkeys(p["edit"] for p in pos)):
        ps = [p for p in pos if p["edit"] == name]
        shortlisted = sum(1 for p in ps if p["true"])
        confirmed = sum(1 for p in ps if p["true"] and passes(p["true"], best))
        med = int(np.median([p["true"]["inliers"] if p["true"] else 0 for p in ps]))
        lines.append(f"| {name} | {len(ps)} | {shortlisted / len(ps):.0%} | {confirmed / len(ps):.0%} | {med} |")

    neg_top = sorted((max((c["inliers"] for c in ns), default=0) for ns in neg_sets), reverse=True)
    pos_true = sorted(p["true"]["inliers"] if p["true"] else 0 for p in pos)
    lines += ["", "## Score separation", "",
              f"- Same photo (positives), inliers: min {pos_true[0]}, 5th pct {int(np.percentile(pos_true, 5))}, "
              f"median {int(np.median(pos_true))}",
              f"- Different photo (best wrong candidate per query), inliers: max {neg_top[0]}, "
              f"99th pct {int(np.percentile(neg_top, 99))}, median {int(np.median(neg_top))}",
              ""]

    misses = [p for p in pos if not (p["true"] and passes(p["true"], best))]
    if misses:
        lines += ["## Missed positives", ""]
        for p in misses[:40]:
            t = p["true"]
            detail = (f"inliers={t['inliers']} cov={t['coverage']:.2f} agree={t['agreement']:.2f} "
                      f"sim={t['similarity']:.2f}") if t else "not in shortlist"
            lines.append(f"- {p['edit']}: {p['url']} ({detail})")
        lines.append("")

    fps = []
    for p in pos + ext:
        for c in p["others"]:
            if passes(c, best):
                fps.append((p["url"], p["edit"], c))
    if fps:
        lines += ["## Confirmed matches to a *different* site URL",
                  "", "These count as false matches above, but some may be real duplicates on the site "
                  "(the same photo uploaded twice under different names). Check them by eye.", ""]
        seen = set()
        for url, edit, c in fps:
            key = tuple(sorted((url, c["url"])))
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- {url} <-> {c['url']} (inliers={c['inliers']}, cov={c['coverage']:.2f}, "
                         f"agree={c['agreement']:.2f}, via {edit})")
        lines.append("")

    secs = [p["seconds"] for p in pos + ext]
    lines += [f"Median search time: {np.median(secs):.2f}s", "",
              "## Threshold sweep", "",
              "| min_inliers | min_coverage | min_agreement | recall | false-match rate |", "|---|---|---|---|---|"]
    lines += [f"| {r['min_inliers']} | {r['min_coverage']} | {r['min_agreement']} | {r['recall']:.1%} | {r['fp_rate']:.2%} |"
              for r in rows]

    report = "\n".join(lines) + "\n"
    report_path = args.report or args.index_dir / "eval_report.md"
    report_path.write_text(report)
    (args.index_dir / "eval_raw.json").write_text(json.dumps({"positives": pos, "external": ext, "sweep": rows}))
    print("\n" + "\n".join(lines[:40]))
    print(f"\nFull report: {report_path}")

    if args.write_thresholds:
        tuned = Thresholds(**{**asdict(base), "min_inliers": mi, "min_coverage": mc, "min_agreement": ma})
        tuned.save(args.index_dir / "thresholds.json")
        print(f"Saved thresholds to {args.index_dir / 'thresholds.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
