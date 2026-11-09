# Video Integrity Verification: Evaluation Protocol and Results

## Purpose

This document defines a repeatable evaluation of the project's video fingerprinting, temporal matching, integrity analysis, and transmission behavior. It is designed to be completed with the actual original trip recordings and transformed candidate videos. Do not report placeholder or invented measurements as experimental results.

## Current implementation and limitation

The production app currently computes SHA-256 over JPEG-encoded sampled frames. SHA-256 is useful for exact integrity and tamper detection, but it is intentionally sensitive: a small brightness, crop, resize, codec, or compression change produces a different digest. Therefore, transformation robustness must be measured with perceptual hashes separately from the production SHA-256 integrity result. A perceptual match is evidence of visual similarity, not cryptographic proof of authenticity.

The evaluation runner compares aHash, dHash, pHash, and wavelet hash using Hamming distance and normalized Hamming distance; it also compares normalized grayscale vectors using L2, mean L1, and cosine similarity, and includes exact frame SHA-256 as a baseline. ssdeep/TLSH are not currently used by production and are not included in the current runner. They may be added as a separate exploratory byte-level test, but should not replace visual frame comparison.

## Evaluation dataset

Use at least 3 independent trips if available. For each trip, retain the untouched source as the reference, then create candidate variants. Record the generation command and random seed in a lab log. Keep clips short enough for practical repeated testing, but include different lighting, scenes, camera motion, and road conditions. Split data by trip: use some trips to tune thresholds and different trips for final evaluation to avoid tuning and testing on the same footage.

Fill in `evaluation_manifest_template.csv` with paths relative to the CSV file. The template includes the requested scenarios and initial expected decisions. Revisit those decisions with your supervisor: for example, a tiny overlay may remain a match with an anomaly, while a large crop or extensive replaced section may require review. For partially replaced clips, assess both whole-video acceptance and identification of the affected segment.

### Example FFmpeg transformations

Install FFmpeg on Windows and make sure `ffmpeg.exe` is available in the terminal PATH. Run these from the folder containing `original_trip.mp4`; use the resulting filenames in the manifest. Preserve the command and parameters as experiment metadata.

```powershell
# H.265/HEVC re-encoding and changed bitrate
ffmpeg -i original_trip.mp4 -c:v libx265 -crf 28 -an h265_reencoded.mp4
ffmpeg -i original_trip.mp4 -b:v 500k -an bitrate_changed.mp4

# Resolution, frame rate, and playback speed
ffmpeg -i original_trip.mp4 -vf "scale=640:-2" -an resolution_changed.mp4
ffmpeg -i original_trip.mp4 -vf "fps=15" -an fps_15.mp4
ffmpeg -i original_trip.mp4 -vf "setpts=PTS/1.1" -an speed_1_1.mp4
ffmpeg -i original_trip.mp4 -vf "setpts=PTS/0.9" -an speed_0_9.mp4

# Brightness, contrast, moderate noise, and crop
ffmpeg -i original_trip.mp4 -vf "eq=brightness=0.08" -an brightness_changed.mp4
ffmpeg -i original_trip.mp4 -vf "eq=contrast=1.25" -an contrast_changed.mp4
ffmpeg -i original_trip.mp4 -vf "noise=alls=12:allf=t+u" -an moderate_noise.mp4
ffmpeg -i original_trip.mp4 -vf "crop=iw*0.85:ih*0.85" -an cropped.mp4

# Trim and remove every tenth frame
ffmpeg -ss 00:00:05 -i original_trip.mp4 -c:v libx264 -an trim_start.mp4
ffmpeg -i original_trip.mp4 -vf "select='not(mod(n,10))',setpts=N/FRAME_RATE/TB" -an missing_frames.mp4
```

Salt-and-pepper noise, frame duplication/reordering, logos, and partial replacement can be generated with a short OpenCV/Python transform script or FFmpeg filters. Keep transform strength modest and record exact parameters; include stronger versions as separate cases.

## Scenario matrix

| Group | Cases to generate | Expected analysis |
|---|---|---|
| Temporal/synchronization | Trim beginning/end, extracted segment, ±5 second shift, 0.9x/1.1x speed, different FPS, deleted frames, duplicated frames, reordered frames | Match rate, matched segment start/end, estimated offset, missing/extra/reordered frame anomalies |
| Re-encoding/quality | H.264→H.265, low/high bitrate, resolution changes, compression quality | Per-hash distance distributions and match rate |
| Image changes | Brightness, contrast, salt-and-pepper noise, moderate noise, watermark/overlay, crop | Per-hash distance and frame-level match rate; annotate affected regions when known |
| Authenticity classes | Original, transformed, trimmed, dropped-frame, modified, other trip, partly replaced | True/false acceptance, missed matches, partial-change detection |
| Network | Offline, intermittent loss, server outage, recovery, out-of-order, duplicate retry | Sent/received/lost/duplicate counts, recovery delay, order preservation |

## Run the video benchmark

1. Install project and evaluation dependencies:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Put the original and candidate videos in an evaluation folder. Create transformed clips with an instructor-approved tool such as FFmpeg. Record each command, parameters, codec, bitrate, FPS, and transformation intensity.
3. Copy `evaluation_manifest_template.csv` to `evaluation_manifest.csv` in that folder and update every candidate filename and expected label.
4. Run:

```powershell
python evaluate_video_scenarios.py .\evaluation_manifest.csv --interval 0.5 --match-rate 70 --output .\evaluation-results
```

The runner writes `scenario_results.csv` and `scenario_results.json`. Outputs include FPS, duration, analyzed frame count, processing time, matched percentage, temporal offset/segment, confusion matrices, and threshold sweeps (precision, recall, F1, TP, FP, TN, FN).

OpenCV may be blocked by Windows Application Control on some machines. If the runner reports a blocked `cv2` DLL, run it on an approved machine/environment with OpenCV allowed. The normal web application and API tests do not require running this video evaluator.

## Threshold selection

Do not treat the default thresholds as validated. Use only the calibration trips to choose a threshold that limits false matches while retaining allowed transformations. Report the selected threshold and evaluate it on held-out trips. For Hamming metrics, lower distances mean more similar; for cosine similarity, higher values mean more similar. Include the per-scenario results and the false-acceptance / missed-match tradeoff.

## Network/server failure protocol

This is a separate integration test because offline behavior cannot be established from video files alone. During a controlled recording, disable networking for a measured interval, restore it, and compare generated, buffered, transmitted, acknowledged, missing, and duplicate event IDs. Repeat with intermittent loss and a stopped backend. Record recovery time and ordering. The current frontend sends live fingerprints over individual HTTP requests and does not yet have a durable offline queue/retry protocol; report this as a limitation unless that feature is implemented and tested before submission.

Suggested network table:

| Run | Failure | Generated | Received | Lost | Duplicates | Recovery time | Order preserved |
|---|---|---:|---:|---:|---:|---:|---|
| N-01 | No failure | Fill after run | | | | | |
| N-02 | Internet disabled for 10 s | | | | | | |
| N-03 | Intermittent connection | | | | | | |
| N-04 | API unavailable for 10 s | | | | | | |
| N-05 | Retry after simulated timeout | | | | | | |

## Results to include in final submission

For every test case, report expected class, detected match, matched segment, temporal offset, matching score, anomalies, processing time, matched fingerprint count/percentage, and notes. Summarize true matches, false matches, missed matches, correctly detected modifications, and per-metric threshold performance. Include representative failures and explain why they occurred.

## Results (synthetic dataset)

Dataset: `make_test_dataset.py` generated a 40 s, 640x360, 30 fps synthetic "trip" (seed 11), a second trip (seed 97) and 24 transformed candidates (26 files in `dataset/`). Frames sampled every 0.5 s; a video matches when >= 70 % of sampled frames match and frame order is consistent. Full per-case data: `evaluation-results/scenario_results.csv/json` (match, segment, offset, scores, anomalies, time, matched %).

Main metric: pHash, per-frame normalized Hamming <= 0.20.

| Case | Scenario | Expected | Outcome | pHash matched | Segment (s) | Offset (s) | dHash % | Anomalies |
|---|---|---|---|---|---|---|---|---|
| AUTH-001 | authentic | match | true match | 100 % | 0-39.5 | 0 | 100 | none |
| TEMP-001 | trimmed start | match | true match | 100 % | 5-39.5 | 5 | 100 | none |
| TEMP-002 | trimmed end | match | true match | 100 % | 0-34.5 | 0 | 100 | none |
| TEMP-003 | extracted 10-25 s | match | true match | 100 % | 10-24.5 | 10 | 100 | none |
| TEMP-004 | shifted +5 s | match | true match | 88.9 % | 0-39.5 | -5 | 88.9 | unmatched head 0-4.5 s |
| TEMP-005 | speed 0.9x | match | true match | 98.8 % | 0-39.5 | -2.26 | 79.1 | one order violation |
| TEMP-006 | speed 1.1x | match | true match | 98.6 % | 0-39.5 | 1.92 | 74.7 | none |
| TEMP-007 | 15 fps | match | true match | 98.7 % | 0-39.5 | 0 | 70.7 | none |
| TEMP-008 | 60 fps | match | true match | 100 % | 0-39.5 | 0 | 100 | none |
| TEMP-009 | missing frames | match | true match | 97.2 % | 0-39.5 | 2 | 70.8 | missing 23.0-23.5 s detected |
| TEMP-010 | duplicated frames | match | true match | 98.9 % | 0-39.5 | -2 | 76.1 | none |
| TEMP-011 | reordered frames | reject | correct rejection | 100 % | 0-39.5 | 0 | 100 | 6+ order violations |
| TRANS-001 | H.264 -> H.265 | match | true match | 100 % | 0-39.5 | 0 | 100 | none |
| TRANS-002 | bitrate | match | true match | 100 % | 0-39.5 | 0 | 100 | none |
| TRANS-003 | resolution | match | true match | 100 % | 0-39.5 | 0 | 100 | none |
| TRANS-004 | compression | match | true match | 100 % | 0-39.5 | 0 | 100 | none |
| TRANS-005 | brightness | match | true match | 100 % | 0-39.5 | 0 | 100 | none |
| TRANS-006 | contrast | match | true match | 100 % | 0-39.5 | 0 | 100 | none |
| TRANS-007 | salt-and-pepper | match | true match | 100 % | 0-39.5 | 0 | 100 | none |
| TRANS-008 | moderate noise | match | true match | 100 % | 0-39.5 | 0 | 100 | none |
| TRANS-009 | watermark | match | true match | 81.3 % | 0-39.5 | 0 | 100 | 3 sections flagged |
| TRANS-010 | crop | match | **missed match** | 18.8 % | 2-39.5 | 0 | 15 | many unmatched sections |
| AUTH-002 | other trip 1 | reject | correct rejection | 1.3 % | - | - | 1.3 | whole video unmatched |
| AUTH-003 | partially replaced 15-25 s | flag | match with anomaly | 75 % | 0-39.5 | 0 | 75 | **15.0-24.5 s flagged** |
| AUTH-004 | other trip 2 | reject | correct rejection | 1.3 % | - | - | 0 | whole video unmatched |
| AUTH-005 | reversed playback | reject | correct rejection | 98.8 % | - | - | 98.8 | order violations |

Processing time was about 3.4-6.1 s per 40 s video (pHash, 80 frames); the first case is slower because of ssdeep initialisation.

Totals (pHash, 26 cases incl. expected-reject): true matches 21, false matches 0, true rejections 4, missed matches 1 (crop), correctly detected modifications: partial replacement, missing frames, reordering, reversal.

### Metric comparison and thresholds

Threshold = best F1 over the sweep (`threshold_sweep`) on this dataset; "can reject" means it separates other-trip videos.

| Metric | Chosen threshold | F1 | Precision | Recall | Verdict |
|---|---|---|---|---|---|
| dHash, normalized Hamming | 0.25 | 0.957 | 0.917 | 1.00 | Best; rejects other trips |
| pHash, normalized Hamming | 0.18-0.20 | 0.933 | 0.913 | 0.955 | Good; misses crop |
| aHash, normalized Hamming | 0.12 | 0.917 | 0.846 | 1.00 | Cannot reject other trips (all frames look alike) |
| wHash, normalized Hamming | 0.12 | 0.917 | 0.846 | 1.00 | Same weakness |
| Euclidean L2 (16x16 pixels) | 0.08 | 0.917 | 0.846 | 1.00 | Cannot reject |
| Manhattan L1 | 0.08 | 0.917 | 0.846 | 1.00 | Cannot reject |
| Cosine similarity | 0.50 | 0.917 | 0.846 | 1.00 | Cannot reject |
| ssdeep | 30 (default) | degenerate | - | - | Scores 0 for everything except exact copies |
| TLSH | not measured | - | - | - | Package could not be built (needs a C++ compiler) |
| SHA-256 per frame | exact | - | 1.00 | 0.05 | Only the exact original matches; no robustness |

Raw Hamming distance is the bit count; normalized Hamming divides it by the hash length (64 bits for 8x8 hashes), which makes the threshold independent of hash size.

## Network and server failures

`evaluate_network_scenarios.py` drives the real FastAPI app through scenarios N-01..N-10 (offline, intermittent, offline generation, server down, downtime mid-transmission, recovery, out-of-order, duplicates, combined failure, legacy client without buffer). Results: `evaluation-results/network_results.md/json`.

- With the browser outbox (localStorage, in-order flush, 3 s retry, `event_id` idempotency): 0 lost fingerprints and 0 duplicate rows in N-01..N-09; out-of-order arrival is repairable using `seq`; combined failure recovered in about 5.25 s of simulated time.
- Without a buffer (N-10): 40 of 240 fingerprints lost (16.67 %).
- Time in this simulation is simulated, not wall-clock.

## Limitations and failure cases

- The videos are synthetic with similar layouts, so aHash, wHash and pixel-vector metrics see all trips as similar; real footage will have more variation and should be re-tested (`make_test_dataset.py --original ... --other ...`).
- Cropping defeats whole-frame perceptual hashes (missed match). A crop-tolerant approach would need local features.
- Watermarks lower match rates (81 %) and speed/FPS changes lower dHash rates (about 70-79 %); near the 70 % limit.
- Hash matching ignores order, so a separate sequence check is needed to catch reordering/reversal.
- A partially replaced video still scores 75 %; the verdict relies on the flagged section.
- ssdeep is meant for byte-similar files and fails on pixel-derived digests; TLSH was not measured.
- The browser flow and Supabase path were not exercised in an automated end-to-end test.
