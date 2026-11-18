# imagecheck

Check whether an image is **already published on the website** before you use it, even if the
copy on the site has been cropped, resized, recompressed, color-adjusted, mirrored, or had text
put on top.

Everything is open source and runs on a laptop. No paid APIs.

## How it works

1. **Index (offline, once per crawl).** `scripts/build_index.py` reads Screaming Frog crawl exports.
   It collapses every Drupal crop and size variant (`/styles/cola_media_320_16_9/...`) back to the
   one original file, downloads each original once, and stores a few fingerprints for it.
2. **Search (a few seconds).** For an uploaded image it checks, in order:
   | Layer | Catches | Certainty |
   |---|---|---|
   | Getty ID in the filename (`GettyImages-1234567.jpg`) | the same stock asset, even a watermarked comp | certain |
   | SHA-256 of the file | byte-identical file | certain |
   | pHash | same image resized or recompressed | very high |
   | DINOv2 embedding ([`facebook/dinov2-small`](https://huggingface.co/facebook/dinov2-small), Apache-2.0) | builds the shortlist; robust to crops and edits | candidate only |
   | SIFT keypoints + RANSAC | confirms a shortlisted image is *literally the same photograph* | very high |
3. **Verdicts:**
   - **Already on the site:** confirmed by one of the certain or very-high layers.
   - **Similar images, review:** looks alike but isn't confirmed. Often a different frame from
     the same photo shoot.
   - **Not found:** no match. If some site images couldn't be downloaded when the index was
     built, the app says so. It never reports "not found" without telling you it skipped images.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

The first run downloads the DINOv2 model (~90 MB) from Hugging Face.

## Refresh the index after a new crawl

1. In Screaming Frog: **Bulk Export → Links → All Image Inlinks**. Save it into `data/crawls/`.
   Older or extra exports can stay; everything in the folder is merged.
2. Run:
   ```bash
   python scripts/build_index.py
   ```
   Only new images are downloaded. Images no longer in the crawl drop out. Failures are printed and
   listed in the app's sidebar. Use `--refresh` to rebuild from scratch.

What gets indexed is set in `imagecheck/config.py` (`Scope`): images on `www.columbiadoctors.org`,
excluding generated map tiles and theme icons. Healthwise health-library art and YouTube or Vimeo
thumbnails are third-party content and are skipped. Use `--host` to change this.

## Run the app

```bash
streamlit run app.py
```

## Accuracy (measured 2026-11-18, 1,376-image index)

| | tuning set (50 images) | held-out set (30 other images) |
|---|---|---|
| Edited copies found as "same photo" | 99.4% of 679 | **100% of 411** |
| …of which real Drupal crops fetched from the live site | 100% of 79 | 100% of 51 |
| Wrong "same photo" verdicts (other similar site images, random photos) | 0% | **0%** |
| Median search time (M1 laptop, CPU) | 1.0 s | 1.0 s |

The few tuning-set misses were text-overlay copies. They still show up as **review** because
their keypoints align; they are never silently reported as "not found". Frames from the same
photo shoot (e.g. `chef_keily_busby-00127` vs `-00135`) come back as **review**, not as matches.
Full reports: `data/index/eval_report.md`, `data/index/eval_report_holdout.md`.

The benchmark also found **real duplicates already on the site**, for example
`gettyimages-180777876.jpg` at three URLs (one renamed `03_04_day_highview.jpg`),
`ortho_-_spine_hero.jpg` = `congenital-scoliosis.jpg`, and `childrens-health-immuno.jpg` =
`eoe.jpeg` (mirrored). They're listed in `data/known_duplicates.txt` so the benchmark doesn't count
them as errors.

## Measure accuracy

```bash
python scripts/evaluate.py --n 50 --picsum 50 --write-thresholds
```

This takes sampled site images and puts them through realistic edits: hero and teaser crops,
260px thumbnails, JPEG quality 40, brightness and saturation changes, mirroring, and text
overlays. It also fetches the real Drupal crops from the live site. Each edited copy must match
its original. Every *other* similar site image must not match, which tests against hard negatives
like other medical stock photos. The script writes `data/index/eval_report.md`. With
`--write-thresholds` it also saves tuned thresholds to `data/index/thresholds.json`, which the app
loads.

Pairs it reports under "Confirmed matches to a *different* site URL" are either real duplicates
on the site or errors. Check them by eye, and add real duplicates to `data/known_duplicates.txt`.
Use `--report PATH` and a different `--seed` for a held-out run.

You can add a folder of known-not-on-site photos with `--negatives DIR`. Other frames from a
Getty shoot that *is* on the site are the best hard test.

## Tests

```bash
pytest
```

## Layout

```
app.py                  Streamlit UI
imagecheck/ingest.py    crawl CSVs -> canonical originals + pages that use them
imagecheck/fetch.py     downloads with retries, on-disk cache
imagecheck/features.py  hashes, DINOv2 embeddings, SIFT verification
imagecheck/index.py     build/load the index (data/index/)
imagecheck/search.py    layered search + verdicts
scripts/                build_index.py, evaluate.py
data/crawls/            Screaming Frog exports
data/index/             built index (small; commit it to host the app without rebuilding)
data/cache/             downloaded site images (git-ignored)
```

## Hosting later

`data/index/` is a few MB, so commit it and deploy `app.py` to Hugging Face Spaces or Streamlit
Community Cloud (both free; use the CPU build of torch). With no `data/cache/` on the host, the
app downloads the ~15 shortlisted site images live for each search.
