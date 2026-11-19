# imagecheck

Check whether an image is **already published on the website** before you use it, even if the
copy on the site has been cropped, resized, recompressed, color-adjusted, mirrored, or had text
put on top.

Everything is open source and runs on a laptop. No paid APIs.

## Install (for the team)

Needs a Mac with Apple silicon (M1 or later) or Linux, and about 2 GB of disk space. No admin
rights needed.

1. Open **Terminal** (press ⌘-Space, type *Terminal*, press Return).
2. Paste this line and press Return:

   ```bash
   curl -LsSf https://raw.githubusercontent.com/danfox127-dotcom/imagecheck/main/install.sh | sh
   ```

   The first time takes about 5 minutes: it sets up Python, downloads the app and the latest site
   data, then opens the app in your browser.
3. From then on, double-click **Image checker** on your Desktop. A Terminal window opens alongside
   the app; keep it open while you use it, and close it when you're done.

**To update** the app and the site data, run the same line again. Your own scans are kept.

What gets installed: the app and its Python libraries in `~/imagecheck` (mostly PyTorch, which runs
the image model), and the shared site data: an index of the site's images and the list of pages
they appear on. No copies of the photos are included; the app downloads site images from
columbiadoctors.org as it needs them. To uninstall, delete `~/imagecheck` and the Desktop launcher.

**Publishing fresh site data (maintainers):** after a full scan, run
`python scripts/publish_data.py`. It attaches the index to a GitHub release (`data-YYYY-MM-DD`);
everyone's next install or update picks it up.

## How it works

1. **Scan (in the app, or on a schedule).** The built-in scanner crawls the site with
   [Columbia-Crawler](https://github.com/danfox127-dotcom/Columbia-Crawler), finds every image on
   every page, including lazy-loaded ones, and records which page shows which image. It collapses
   every Drupal crop and size variant (`/styles/cola_media_320_16_9/...`) back to the one original
   file, downloads each new original once, and stores a few fingerprints for it.
2. **Search (a few seconds).** For an uploaded image it checks, in order:
   | Layer | Catches | Certainty |
   |---|---|---|
   | Getty ID in the filename (`GettyImages-1234567.jpg`) | the same stock asset, even a watermarked comp | certain |
   | SHA-256 of the file | byte-identical file | certain |
   | pHash | same image resized or recompressed | very high |
   | DINOv2 embedding ([`facebook/dinov2-small`](https://huggingface.co/facebook/dinov2-small), Apache-2.0) | builds the shortlist; robust to crops and edits | candidate only |
   | SIFT keypoints + RANSAC, then a patch-by-patch pixel comparison | confirms a shortlisted image is *literally the same photograph*, not another frame of the same shoot | very high |
3. **Verdicts:**
   - **Already on the site:** confirmed by one of the certain or very-high layers.
   - **Similar images, review:** looks alike but isn't confirmed. Often a different frame from
     the same photo shoot.
   - **Not found:** no match. If some site images couldn't be downloaded when the index was
     built, the app says so. It never reports "not found" without telling you it skipped images.

## Development setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

The first run downloads the DINOv2 model (~90 MB) from Hugging Face. The crawler comes from
GitHub through `requirements.txt`. To work on both repos at once, use
`pip install -e ../Columbia-Crawler` instead.

## Run the app

```bash
streamlit run app.py
```

It has two pages:
- **Check an image**: upload a photo and get "Already on the site", "Similar images, review", or
  "Not found".
- **Site scan**: crawl the site to update the index.

The sidebar on both pages shows how fresh the data is. It turns into a warning once the last full
scan is more than 30 days old.

## Keep the index current: scanning the site

On the **Site scan** page:
- **Whole site** takes about 3,200 pages at 4 pages/s, roughly **13 minutes**. The scan runs in the
  background, so you can close the tab. It finds new images and drops ones no longer on any page.
- **Only these folders**, e.g. `news, childrens-health`, takes a few minutes. It's the quick way to
  pick up newly published articles.
- **Skip these folders** defaults to `health-library/*`. The Healthwise health library is ~6,700
  pages, 70% of the site, and its images are licensed third-party art that isn't indexed. The `/*`
  means "skip what's under the folder but still check the folder's own page"; the library's landing
  page shows 5 site-owned images. Pages in skipped folders keep the images earlier scans found.
- **Pace:** 0.25 s between requests (4/s). It was load-tested on this site (Fastly CDN, not
  Cloudflare): no throttling, no blocking, flat response times. The crawler also slows itself down
  automatically if it ever gets 429/503 responses. You can change this under *Advanced settings*.
- **Cancel** keeps the pages fetched so far. Then either **Update index** (under *More options*) or
  run another scan.

A scan follows links from the home page. It also starts from every page earlier scans or imports
found, and from the site's sitemaps, because pages nothing links to are otherwise invisible. The
2021–22 annual report is one example. A known page only drops out once it stops loading, for
example with a 404.

The page shows an estimated time, based on the pages earlier scans found. Re-scans take about as
long as the first one: the crawler asks whether each page changed since last time, but this site's
CDN only sometimes answers "304 Not Modified".

The same is available from the command line, for example from cron or a scheduled CI job:

```bash
python scripts/scan_site.py                   # whole site (minus health-library/*)
python scripts/scan_site.py --include news    # one section
python scripts/scan_site.py --no-skip         # really everything (~2.5x longer)
python scripts/scan_site.py --reindex         # rebuild the index from saved pages only
```

Safety rules:
- A scan that's capped, cancelled, or limited to sections never removes images it didn't re-check.
- A "complete" scan that reaches fewer than half the known pages is treated as suspect, for
  example when it's blocked or the site is down, and doesn't remove anything.
- Only one scan runs at a time, whether started from the app or the CLI. Ctrl-C in the CLI, or
  **Cancel** in the app, stops a scan wherever it was started.
- Scans only start on the configured site (`Scope.hosts`, https) and stop if a redirect points
  elsewhere.

Crawling rules: robots.txt `Disallow` is obeyed. Its `Crawl-delay: 10` is not, by default, which
matches Screaming Frog's behavior; there's a toggle in the app's *Advanced settings* and
`--honor-crawl-delay` on the CLI.

What gets indexed is set in `imagecheck/config.py` (`Scope`): images on `www.columbiadoctors.org`,
excluding generated map tiles and theme icons. Healthwise health-library art and YouTube or Vimeo
thumbnails are third-party content and are skipped. Logos in the page header or footer are skipped.
Mega-menu photos are kept.

### Screaming Frog exports (optional)

Screaming Frog is no longer needed, but exports can still be imported: **Bulk Export → Links →
All Image Inlinks**. Use *More options → Import* in the app, or put the files in `data/crawls/` and
run `python scripts/build_index.py`.
- Into an empty store, the export becomes the whole picture.
- Otherwise it only fills in pages the scans haven't seen.
- The crawl date is read from the export, so it never makes the data look fresher than it is.

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

**Re-measured after the first site scan** (1,534-image index, 420 held-out queries): 99.3% recall,
with 0 wrong "same photo" verdicts once a newly found on-site duplicate was recorded. The one miss
is a known hard case: a tight, upscaled Drupal crop that's mostly blurry, repetitive texture
(DNA-gel stripes), where keypoints can't align. A copy like that can come back "Not found".

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
app.py                  Streamlit entry point: navigation + freshness sidebar
app_pages/check.py      "Check an image" page
app_pages/scan.py       "Site scan" page
imagecheck/scan.py      scan job: crawl -> page store -> index; status, lock, cancel
imagecheck/pages.py     page store (which page shows which image) and its merge rules
imagecheck/ingest.py    page records / Screaming Frog exports -> canonical originals + pages
imagecheck/fetch.py     downloads with retries, on-disk cache
imagecheck/features.py  hashes, DINOv2 embeddings, SIFT + pixel verification
imagecheck/index.py     build/load the index (data/index/)
imagecheck/search.py    layered search + verdicts
imagecheck/webapp.py    shared Streamlit state (cached model, index, scan job)
scripts/                scan_site.py, build_index.py (imports), evaluate.py
data/crawls/            Screaming Frog exports (optional)
data/scans/             page store, scan runs and status (git-ignored)
data/index/             built index; embeddings and images.json are git-ignored
data/cache/             downloaded site images (git-ignored)
```

## Hosting later

The built index (`data/index/`, about 17 MB) and page store (`data/scans/`) are git-ignored, so
they're rebuilt locally rather than committed.
- **Hosting on a free service** (Hugging Face Spaces, Streamlit Community Cloud; use the CPU
  build of torch): run a scheduled `scripts/scan_site.py` somewhere persistent and publish its
  `data/index/`. Hour-long scans don't survive well on hosts that sleep.
- **On the host,** the app downloads the ~15 shortlisted site images live for each search if
  `data/cache/` is missing.
