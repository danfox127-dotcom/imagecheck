# imagecheck accuracy report

- Index: 1534 site images (2026-11-18T22:02:45+00:00), model `facebook/dinov2-small`
- Positives: 420 queries from 30 site images; external negatives: 40
- Chosen thresholds: **min_inliers=10, min_coverage=0.0, min_agreement=0.5** -> recall **99.8%**, false-match rate **3.04%** (targets: recall >= 98%, false-match <= 1%)
- At the app's current thresholds (min_inliers=15, min_coverage=0.1, min_agreement=0.75): recall **99.3%**, false-match rate **3.04%**

## Recall by edit

| edit | n | in shortlist | confirmed same photo | median inliers |
|---|---|---|---|---|
| crop_16x9_center | 30 | 100% | 100% | 751 |
| crop_3x1_hero | 30 | 100% | 100% | 600 |
| crop_21x9_offcenter | 30 | 100% | 100% | 715 |
| crop_square_left | 30 | 100% | 100% | 475 |
| crop_tight_50pct | 30 | 100% | 100% | 454 |
| thumb_260px | 30 | 100% | 100% | 197 |
| jpeg_q40 | 30 | 100% | 100% | 803 |
| brighter_15pct | 30 | 100% | 100% | 959 |
| desaturated | 30 | 100% | 100% | 1122 |
| mirrored | 30 | 100% | 100% | 1242 |
| text_overlay | 30 | 100% | 100% | 866 |
| hero_combo | 30 | 100% | 100% | 512 |
| live_drupal_crop | 60 | 100% | 98% | 413 |

## Score separation

- Same photo (positives), inliers: min 0, 5th pct 132, median 600
- Different photo (best wrong candidate per query), inliers: max 787, 99th pct 672, median 4

## Missed positives

- live_drupal_crop: https://www.columbiadoctors.org/sites/default/files/media/images/2024-07/genetic_hero.jpg (inliers=0 cov=0.00 agree=0.00 sim=0.53)

## Confirmed matches to a *different* site URL

These count as false matches above, but some may be real duplicates on the site (the same photo uploaded twice under different names). Check them by eye.

- https://www.columbiadoctors.org/sites/default/files/media/images/2021-08/adult_dermatology-1.jpg <-> https://www.columbiadoctors.org/sites/default/files/media/images/2021-06/880_dermatology_featured_imeage.jpg (inliers=502, cov=0.86, agree=1.00, via crop_16x9_center)

Median search time: 1.87s

## Threshold sweep

| min_inliers | min_coverage | min_agreement | recall | false-match rate |
|---|---|---|---|---|
| 10 | 0.0 | 0.5 | 99.8% | 3.04% |
| 10 | 0.0 | 0.6 | 99.8% | 3.04% |
| 10 | 0.0 | 0.65 | 99.8% | 3.04% |
| 10 | 0.0 | 0.7 | 99.5% | 3.04% |
| 10 | 0.0 | 0.75 | 99.3% | 3.04% |
| 10 | 0.0 | 0.8 | 97.6% | 3.04% |
| 10 | 0.0 | 0.85 | 94.3% | 2.83% |
| 10 | 0.0 | 0.9 | 93.1% | 2.83% |
| 10 | 0.1 | 0.5 | 99.8% | 3.04% |
| 10 | 0.1 | 0.6 | 99.8% | 3.04% |
| 10 | 0.1 | 0.65 | 99.8% | 3.04% |
| 10 | 0.1 | 0.7 | 99.5% | 3.04% |
| 10 | 0.1 | 0.75 | 99.3% | 3.04% |
| 10 | 0.1 | 0.8 | 97.6% | 3.04% |
| 10 | 0.1 | 0.85 | 94.3% | 2.83% |
| 10 | 0.1 | 0.9 | 93.1% | 2.83% |
| 15 | 0.0 | 0.5 | 99.8% | 3.04% |
| 15 | 0.0 | 0.6 | 99.8% | 3.04% |
| 15 | 0.0 | 0.65 | 99.8% | 3.04% |
| 15 | 0.0 | 0.7 | 99.5% | 3.04% |
| 15 | 0.0 | 0.75 | 99.3% | 3.04% |
| 15 | 0.0 | 0.8 | 97.6% | 3.04% |
| 15 | 0.0 | 0.85 | 94.3% | 2.83% |
| 15 | 0.0 | 0.9 | 93.1% | 2.83% |
| 15 | 0.1 | 0.5 | 99.8% | 3.04% |
| 15 | 0.1 | 0.6 | 99.8% | 3.04% |
| 15 | 0.1 | 0.65 | 99.8% | 3.04% |
| 15 | 0.1 | 0.7 | 99.5% | 3.04% |
| 15 | 0.1 | 0.75 | 99.3% | 3.04% |
| 15 | 0.1 | 0.8 | 97.6% | 3.04% |
| 15 | 0.1 | 0.85 | 94.3% | 2.83% |
| 15 | 0.1 | 0.9 | 93.1% | 2.83% |
| 20 | 0.0 | 0.5 | 99.8% | 3.04% |
| 20 | 0.0 | 0.6 | 99.8% | 3.04% |
| 20 | 0.0 | 0.65 | 99.8% | 3.04% |
| 20 | 0.0 | 0.7 | 99.5% | 3.04% |
| 20 | 0.0 | 0.75 | 99.3% | 3.04% |
| 20 | 0.0 | 0.8 | 97.6% | 3.04% |
| 20 | 0.0 | 0.85 | 94.3% | 2.83% |
| 20 | 0.0 | 0.9 | 93.1% | 2.83% |
| 20 | 0.1 | 0.5 | 99.8% | 3.04% |
| 20 | 0.1 | 0.6 | 99.8% | 3.04% |
| 20 | 0.1 | 0.65 | 99.8% | 3.04% |
| 20 | 0.1 | 0.7 | 99.5% | 3.04% |
| 20 | 0.1 | 0.75 | 99.3% | 3.04% |
| 20 | 0.1 | 0.8 | 97.6% | 3.04% |
| 20 | 0.1 | 0.85 | 94.3% | 2.83% |
| 20 | 0.1 | 0.9 | 93.1% | 2.83% |
| 30 | 0.0 | 0.5 | 99.5% | 3.04% |
| 30 | 0.0 | 0.6 | 99.5% | 3.04% |
| 30 | 0.0 | 0.65 | 99.5% | 3.04% |
| 30 | 0.0 | 0.7 | 99.3% | 3.04% |
| 30 | 0.0 | 0.75 | 99.0% | 3.04% |
| 30 | 0.0 | 0.8 | 97.4% | 3.04% |
| 30 | 0.0 | 0.85 | 94.0% | 2.83% |
| 30 | 0.0 | 0.9 | 92.9% | 2.83% |
| 30 | 0.1 | 0.5 | 99.5% | 3.04% |
| 30 | 0.1 | 0.6 | 99.5% | 3.04% |
| 30 | 0.1 | 0.65 | 99.5% | 3.04% |
| 30 | 0.1 | 0.7 | 99.3% | 3.04% |
| 30 | 0.1 | 0.75 | 99.0% | 3.04% |
| 30 | 0.1 | 0.8 | 97.4% | 3.04% |
| 30 | 0.1 | 0.85 | 94.0% | 2.83% |
| 30 | 0.1 | 0.9 | 92.9% | 2.83% |
| 50 | 0.0 | 0.5 | 99.5% | 3.04% |
| 50 | 0.0 | 0.6 | 99.5% | 3.04% |
| 50 | 0.0 | 0.65 | 99.5% | 3.04% |
| 50 | 0.0 | 0.7 | 99.3% | 3.04% |
| 50 | 0.0 | 0.75 | 99.0% | 3.04% |
| 50 | 0.0 | 0.8 | 97.4% | 3.04% |
| 50 | 0.0 | 0.85 | 94.0% | 2.83% |
| 50 | 0.0 | 0.9 | 92.9% | 2.83% |
| 50 | 0.1 | 0.5 | 99.5% | 3.04% |
| 50 | 0.1 | 0.6 | 99.5% | 3.04% |
| 50 | 0.1 | 0.65 | 99.5% | 3.04% |
| 50 | 0.1 | 0.7 | 99.3% | 3.04% |
| 50 | 0.1 | 0.75 | 99.0% | 3.04% |
| 50 | 0.1 | 0.8 | 97.4% | 3.04% |
| 50 | 0.1 | 0.85 | 94.0% | 2.83% |
| 50 | 0.1 | 0.9 | 92.9% | 2.83% |
