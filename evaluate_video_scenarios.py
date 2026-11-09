"""Evaluate labeled reference/candidate videos using frame and vector metrics.

Manifest CSV columns: case_id,reference_video,candidate_video,scenario,expected_match
Video paths are relative to the manifest. Install optional requirements first.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from pathlib import Path
from statistics import median
from typing import Any

HASH_NAMES = ("ahash", "dhash", "phash", "whash")
FUZZY_NAMES = ("ssdeep", "tlsh")

try:  # ssdeep-compatible fuzzy hash, pure Python: https://github.com/elceef/ppdeep
    import ppdeep
except ImportError:
    ppdeep = None
try:  # TLSH needs a C++ build (https://github.com/trendmicro/tlsh); optional on Windows
    import tlsh
except ImportError:
    tlsh = None

def load_metric_dependencies() -> tuple[Any, Any, Any]:
    try:
        import imagehash
        import numpy as np
        from PIL import Image
    except (ImportError, OSError) as error:
        raise RuntimeError(
            "Metric evaluation requires ImageHash, NumPy, and Pillow. Install requirements.txt."
        ) from error
    return imagehash, np, Image


def load_video_dependencies() -> tuple[Any, Any, Any, Any]:
    imagehash, np, Image = load_metric_dependencies()
    try:
        import cv2
    except (ImportError, OSError) as error:
        raise RuntimeError(
            "Video evaluation requires OpenCV. Install requirements.txt; if Windows Application Control blocks the cv2 DLL, run on an approved environment or ask IT to allow it."
        ) from error
    return cv2, imagehash, np, Image


def read_video(path: Path, interval: float) -> tuple[list[dict[str, Any]], float, float]:
    cv2, imagehash, np, Image = load_video_dependencies()
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"Cannot decode video: {path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    frame_total = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frame_total / fps if fps > 0 else 0.0
    stride = max(1, round(fps * interval)) if fps > 0 else 1
    frames: list[dict[str, Any]] = []
    index = 0
    while True:
        ok, bgr = capture.read()
        if not ok:
            break
        if index % stride == 0:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            pixels = cv2.resize(gray, (16, 16), interpolation=cv2.INTER_AREA).astype(np.float32).ravel() / 255.0
            gray64 = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA).tobytes()
            frames.append({
                "time": index / fps if fps else float(index),
                "ssdeep": ppdeep.hash(gray64) if ppdeep else None,
                "tlsh": (tlsh.hash(gray64) or None) if tlsh else None,
                "hashes": {
                    "ahash": imagehash.average_hash(image),
                    "dhash": imagehash.dhash(image),
                    "phash": imagehash.phash(image),
                    "whash": imagehash.whash(image),
                },
                "pixels": pixels,
                "sha256": hashlib.sha256(gray.tobytes()).hexdigest(),
            })
        index += 1
    capture.release()
    if not frames:
        raise ValueError(f"Video has no decodable frames: {path}")
    return frames, fps, duration


def compare(reference: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> dict[str, Any]:
    _, np, _ = load_metric_dependencies()
    result: dict[str, Any] = {}
    for method in HASH_NAMES:
        distances: list[float] = []
        matched_offsets: list[float] = []
        matched_reference_times: list[float] = []
        per_frame: list[tuple[float, float | None]] = []
        for frame in candidate:
            best = min(reference, key=lambda original: frame["hashes"][method] - original["hashes"][method])
            distance = (frame["hashes"][method] - best["hashes"][method]) / 64.0
            distances.append(distance)
            if distance <= 0.20:
                matched_offsets.append(best["time"] - frame["time"])
                matched_reference_times.append(best["time"])
                per_frame.append((frame["time"], best["time"]))
            else:
                per_frame.append((frame["time"], None))
        if method == "phash":
            result["anomalies"] = find_anomalies(per_frame, reference)
        ref_sequence = [ref for _, ref in per_frame if ref is not None]
        step = reference[1]["time"] - reference[0]["time"] if len(reference) > 1 else 0.5
        backward = sum(after <= before - 0.9 * step for before, after in zip(ref_sequence, ref_sequence[1:]))
        result[method] = {
            "sequence_inconsistent": backward >= max(2, 0.05 * max(1, len(ref_sequence) - 1)),
            "backward_jumps": backward,
            "mean_normalized_hamming": round(float(np.mean(distances)), 5),
            "median_normalized_hamming": round(float(np.median(distances)), 5),
            "matched_frames": len(matched_offsets),
            "matched_frame_percent": round(100 * len(matched_offsets) / len(candidate), 2),
            "frame_threshold_normalized_hamming": 0.20,
            "median_time_offset_seconds": round(median(matched_offsets), 3) if matched_offsets else None,
            "matched_segment_start_seconds": round(min(matched_reference_times), 3) if matched_reference_times else None,
            "matched_segment_end_seconds": round(max(matched_reference_times), 3) if matched_reference_times else None,
        }

    vector_means = {"euclidean_l2": [], "manhattan_l1_mean": [], "cosine_similarity": []}
    sha_exact = 0
    for frame in candidate:
        closest = min(reference, key=lambda original: float(np.mean(np.abs(frame["pixels"] - original["pixels"]))))
        delta = frame["pixels"] - closest["pixels"]
        vector_means["euclidean_l2"].append(float(np.linalg.norm(delta) / np.sqrt(delta.size)))
        vector_means["manhattan_l1_mean"].append(float(np.mean(np.abs(delta))))
        norm_product = float(np.linalg.norm(frame["pixels"]) * np.linalg.norm(closest["pixels"]))
        vector_means["cosine_similarity"].append(float(np.dot(frame["pixels"], closest["pixels"]) / norm_product) if norm_product else 1.0)
        sha_exact += int(any(frame["sha256"] == original["sha256"] for original in reference))

    result["euclidean_l2"] = {"mean_distance": round(float(np.mean(vector_means["euclidean_l2"])), 5), "threshold": 0.12}
    result["manhattan_l1_mean"] = {"mean_distance": round(float(np.mean(vector_means["manhattan_l1_mean"])), 5), "threshold": 0.08}
    result["cosine_similarity"] = {"mean_similarity": round(float(np.mean(vector_means["cosine_similarity"])), 5), "threshold": 0.90}
    result["sha256_exact_frame"] = {"matched_frames": sha_exact, "matched_frame_percent": round(100 * sha_exact / len(candidate), 2)}

    if ppdeep and all(frame.get("ssdeep") for frame in [*reference, *candidate]):
        best_scores = [max(ppdeep.compare(frame["ssdeep"], original["ssdeep"]) for original in reference) for frame in candidate]
        result["ssdeep"] = {"mean_similarity": round(float(np.mean(best_scores)), 3), "threshold": 30.0, "available": True}
    else:
        result["ssdeep"] = {"available": False}
    if tlsh and all(frame.get("tlsh") for frame in [*reference, *candidate]):
        best_diffs = []
        for frame in candidate:
            diffs = [tlsh.diff(frame["tlsh"], original["tlsh"]) for original in reference if frame["tlsh"] and original["tlsh"]]
            best_diffs.append(min(diffs) if diffs else 1000)
        result["tlsh"] = {"mean_distance": round(float(np.mean(best_diffs)), 3), "threshold": 150.0, "available": True}
    else:
        result["tlsh"] = {"available": False}
    return result


def find_anomalies(per_frame: list[tuple[float, float | None]], reference: list[dict[str, Any]]) -> list[str]:
    """Report unmatched candidate sections, absent reference sections and order violations."""
    notes: list[str] = []
    step = reference[1]["time"] - reference[0]["time"] if len(reference) > 1 else 0.5
    run_start = run_end = None
    for time_value, matched in per_frame + [(float("inf"), 0.0)]:
        if matched is None:
            run_start = time_value if run_start is None else run_start
            run_end = time_value
        elif run_start is not None:
            if run_end - run_start >= step:
                notes.append(f"unmatched candidate section {run_start:.1f}-{run_end:.1f}s")
            run_start = None
    matched_times = [ref for _, ref in per_frame if ref is not None]
    for before, after in zip(matched_times, matched_times[1:]):
        if after <= before - 0.9 * step and len(notes) < 6:
            notes.append(f"order violation: reference {before:.1f}s then {after:.1f}s")
    seen = sorted(set(matched_times))
    for before, after in zip(seen, seen[1:]):
        if after - before > 2.5 * step:
            notes.append(f"reference section {before + step:.1f}-{after - step:.1f}s not present in candidate")
    return notes[:8]


def predict(metric: str, values: dict[str, Any], match_rate: float) -> bool:
    if metric in HASH_NAMES or metric == "sha256_exact_frame":
        return values["matched_frame_percent"] >= match_rate and not values.get("sequence_inconsistent", False)
    if not values.get("available", True):
        return False
    if metric in {"euclidean_l2", "manhattan_l1_mean", "tlsh"}:
        return values["mean_distance"] <= values["threshold"]
    return values["mean_similarity"] >= values["threshold"]


def confusion(rows: list[dict[str, Any]], metric: str, match_rate: float) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    for row in rows:
        predicted = predict(metric, row["metrics"][metric], match_rate)
        expected = row["expected_match"]
        if predicted and expected: tp += 1
        elif predicted: fp += 1
        elif expected: fn += 1
        else: tn += 1
    return {
        "true_matches": tp,
        "false_matches": fp,
        "true_rejections": tn,
        "missed_matches": fn,
        "precision": round(tp / (tp + fp), 4) if tp + fp else 0.0,
        "recall": round(tp / (tp + fn), 4) if tp + fn else 0.0,
    }


def threshold_sweep(rows: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    _, np, _ = load_metric_dependencies()
    if not rows[0]["metrics"][metric].get("available", True):
        return []
    if metric == "ssdeep":
        thresholds = list(range(0, 101, 5))
        higher_is_match = True
        score = lambda row: row["metrics"][metric]["mean_similarity"]
    elif metric == "tlsh":
        thresholds = list(range(0, 401, 25))
        higher_is_match = False
        score = lambda row: row["metrics"][metric]["mean_distance"]
    elif metric == "cosine_similarity":
        thresholds = [round(value, 2) for value in np.arange(0.50, 1.001, 0.05)]
        higher_is_match = True
        score = lambda row: row["metrics"][metric]["mean_similarity"]
    elif metric == "sha256_exact_frame":
        thresholds = list(range(0, 101, 5))
        higher_is_match = True
        score = lambda row: row["metrics"][metric]["matched_frame_percent"]
    elif metric in HASH_NAMES:
        thresholds = [round(value, 2) for value in np.arange(0.0, 0.501, 0.025)]
        higher_is_match = False
        score = lambda row: row["metrics"][metric]["mean_normalized_hamming"]
    else:
        thresholds = [round(value, 2) for value in np.arange(0.0, 0.501, 0.025)]
        higher_is_match = False
        score = lambda row: row["metrics"][metric]["mean_distance"]
    output = []
    for threshold in thresholds:
        tp = fp = tn = fn = 0
        for row in rows:
            value = score(row)
            predicted = value >= threshold if higher_is_match else value <= threshold
            if predicted and row["expected_match"]: tp += 1
            elif predicted: fp += 1
            elif row["expected_match"]: fn += 1
            else: tn += 1
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        output.append({"threshold": threshold, "tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)})
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--interval", type=float, default=0.5, help="Sample frames every N seconds")
    parser.add_argument("--match-rate", type=float, default=70.0, help="Candidate frames required for a video-level match")
    parser.add_argument("--output", type=Path, default=Path("evaluation-results"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.manifest.resolve().parent
    rows: list[dict[str, Any]] = []
    with args.manifest.open(newline="", encoding="utf-8-sig") as stream:
        for case in csv.DictReader(stream):
            started = time.perf_counter()
            reference_path = (root / case["reference_video"]).resolve()
            candidate_path = (root / case["candidate_video"]).resolve()
            reference, reference_fps, reference_duration = read_video(reference_path, args.interval)
            candidate, candidate_fps, candidate_duration = read_video(candidate_path, args.interval)
            metrics = compare(reference, candidate)
            rows.append({
                "case_id": case["case_id"],
                "scenario": case["scenario"],
                "expected_match": case["expected_match"].strip().lower() in {"1", "true", "yes"},
                "reference_video": case["reference_video"],
                "candidate_video": case["candidate_video"],
                "reference_fps": reference_fps,
                "candidate_fps": candidate_fps,
                "reference_duration_seconds": round(reference_duration, 3),
                "candidate_duration_seconds": round(candidate_duration, 3),
                "candidate_frames_analyzed": len(candidate),
                "processing_time_ms": round((time.perf_counter() - started) * 1000, 2),
                "metrics": metrics,
            })

    metric_names = [*HASH_NAMES, "euclidean_l2", "manhattan_l1_mean", "cosine_similarity", "ssdeep", "tlsh", "sha256_exact_frame"]
    summary = {metric: confusion(rows, metric, args.match_rate) for metric in metric_names}
    sweeps = {metric: threshold_sweep(rows, metric) for metric in metric_names}
    best = {}
    for metric, sweep in sweeps.items():
        if sweep:
            top = max(sweep, key=lambda item: (item["f1"], -item["fp"]))
            best[metric] = {key: top[key] for key in ("threshold", "tp", "fp", "tn", "fn", "precision", "recall", "f1")}
    payload = {"sampling_interval_seconds": args.interval, "video_match_rate_threshold_percent": args.match_rate, "cases": rows, "summary": summary, "best_threshold_by_f1": best, "threshold_sweeps": sweeps}
    (args.output / "scenario_results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (args.output / "scenario_results.csv").open("w", newline="", encoding="utf-8") as stream:
        columns = ["case_id", "scenario", "expected_match", "detected_match", "outcome", "reference_fps", "candidate_fps", "reference_duration_seconds", "candidate_duration_seconds", "candidate_frames_analyzed", "processing_time_ms"]
        columns += ["phash_matched_frames", "phash_matched_percent", "phash_segment_start_s", "phash_segment_end_s", "phash_time_offset_s", "anomalies"]
        columns += [f"{name}_matched_percent" for name in HASH_NAMES if name != "phash"]
        columns += [f"{name}_mean_normalized_hamming" for name in HASH_NAMES]
        columns += ["euclidean_l2_mean", "manhattan_l1_mean", "cosine_similarity_mean", "ssdeep_mean_similarity", "tlsh_mean_distance"]
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            metrics = row["metrics"]
            detected = predict("phash", metrics["phash"], args.match_rate)
            expected = row["expected_match"]
            row["detected_match"] = detected
            row["outcome"] = "true match" if detected and expected else "false match" if detected else "missed match" if expected else "correct rejection"
            flat = {key: row[key] for key in columns if key in row}
            flat.update({
                "phash_matched_frames": metrics["phash"]["matched_frames"],
                "phash_matched_percent": metrics["phash"]["matched_frame_percent"],
                "phash_segment_start_s": metrics["phash"]["matched_segment_start_seconds"],
                "phash_segment_end_s": metrics["phash"]["matched_segment_end_seconds"],
                "phash_time_offset_s": metrics["phash"]["median_time_offset_seconds"],
                "anomalies": "; ".join(metrics["anomalies"]),
                "euclidean_l2_mean": metrics["euclidean_l2"]["mean_distance"],
                "manhattan_l1_mean": metrics["manhattan_l1_mean"]["mean_distance"],
                "cosine_similarity_mean": metrics["cosine_similarity"]["mean_similarity"],
                "ssdeep_mean_similarity": metrics["ssdeep"].get("mean_similarity", "n/a"),
                "tlsh_mean_distance": metrics["tlsh"].get("mean_distance", "n/a"),
            })
            for name in HASH_NAMES:
                flat[f"{name}_matched_percent"] = metrics[name]["matched_frame_percent"]
                flat[f"{name}_mean_normalized_hamming"] = metrics[name]["mean_normalized_hamming"]
            writer.writerow(flat)
    (args.output / "scenario_results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Evaluated {len(rows)} cases. Results saved in {args.output.resolve()}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
