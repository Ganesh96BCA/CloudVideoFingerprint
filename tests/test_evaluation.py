import imagehash
import numpy as np

from evaluate_video_scenarios import compare, confusion, threshold_sweep


def make_frame(hash_value: int, time_seconds: float = 0.0) -> dict:
    bits = np.array([(hash_value >> index) & 1 for index in range(64)], dtype=bool).reshape((8, 8))
    perceptual_hash = imagehash.ImageHash(bits)
    return {
        "time": time_seconds,
        "hashes": {name: perceptual_hash for name in ("ahash", "dhash", "phash", "whash")},
        "pixels": np.zeros(256, dtype=np.float32),
        "sha256": "same-frame",
        "ssdeep": None,
        "tlsh": None,
    }


def test_compare_identical_frames_reports_full_matches() -> None:
    reference = [make_frame(15, 1.0), make_frame(31, 2.0)]
    result = compare(reference, reference)
    assert result["phash"]["matched_frame_percent"] == 100
    assert result["phash"]["mean_normalized_hamming"] == 0
    assert result["sha256_exact_frame"]["matched_frame_percent"] == 100


def test_threshold_sweep_reports_classification_metrics() -> None:
    rows = [
        {"expected_match": True, "metrics": {"phash": {"mean_normalized_hamming": 0.01}}},
        {"expected_match": False, "metrics": {"phash": {"mean_normalized_hamming": 0.40}}},
    ]
    sweep = threshold_sweep(rows, "phash")
    best = max(sweep, key=lambda item: item["f1"])
    assert best["tp"] == 1
    assert best["tn"] == 1
    assert best["fp"] == 0
    assert best["fn"] == 0
    assert confusion([], "phash", 70)["precision"] == 0