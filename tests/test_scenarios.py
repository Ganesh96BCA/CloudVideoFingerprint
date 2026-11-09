"""Scenario test cases for the professor's video-integrity evaluation.

Each test case = one scenario row: reference video, candidate video, expected
decision and the evidence that must be reported (pHash matched %, segment,
anomalies). Reads the output of evaluate_video_scenarios.py /
evaluate_network_scenarios.py, so run those first.
"""
import csv
import json
from pathlib import Path

import pytest

RESULTS = Path(__file__).resolve().parents[1] / "evaluation-results"
CSV_FILE = RESULTS / "scenario_results.csv"
NET_FILE = RESULTS / "network_results.json"

# case_id: (expected outcome, minimum pHash matched %, maximum pHash matched %, anomaly keyword or "")
VIDEO_CASES = {
    "AUTH-001": ("true match", 99, 100, ""),
    "TEMP-001": ("true match", 95, 100, ""),
    "TEMP-002": ("true match", 95, 100, ""),
    "TEMP-003": ("true match", 95, 100, ""),
    "TEMP-004": ("true match", 70, 100, "unmatched candidate section"),
    "TEMP-005": ("true match", 70, 100, ""),
    "TEMP-006": ("true match", 70, 100, ""),
    "TEMP-007": ("true match", 70, 100, ""),
    "TEMP-008": ("true match", 95, 100, ""),
    "TEMP-009": ("true match", 70, 100, "not present in candidate"),
    "TEMP-010": ("true match", 70, 100, ""),
    "TEMP-011": ("correct rejection", 0, 100, "order violation"),
    "TRANS-001": ("true match", 95, 100, ""),
    "TRANS-002": ("true match", 95, 100, ""),
    "TRANS-003": ("true match", 95, 100, ""),
    "TRANS-004": ("true match", 95, 100, ""),
    "TRANS-005": ("true match", 95, 100, ""),
    "TRANS-006": ("true match", 95, 100, ""),
    "TRANS-007": ("true match", 95, 100, ""),
    "TRANS-008": ("true match", 95, 100, ""),
    "TRANS-009": ("true match", 70, 100, ""),
    # Documented limitation: whole-frame hashes fail on cropping.
    "TRANS-010": ("missed match", 0, 40, ""),
    "AUTH-002": ("correct rejection", 0, 10, ""),
    "AUTH-003": ("true match", 70, 90, "15.0-24.5s"),
    "AUTH-004": ("correct rejection", 0, 10, ""),
    "AUTH-005": ("correct rejection", 0, 100, "order violation"),
}


@pytest.fixture(scope="module")
def rows():
    if not CSV_FILE.exists():
        pytest.skip("run evaluate_video_scenarios.py first")
    with CSV_FILE.open(encoding="utf-8", newline="") as f:
        return {r["case_id"]: r for r in csv.DictReader(f)}


@pytest.mark.parametrize("case_id", sorted(VIDEO_CASES))
def test_video_scenario(rows, case_id):
    outcome, lo, hi, keyword = VIDEO_CASES[case_id]
    row = rows[case_id]
    assert row["outcome"] == outcome
    assert lo <= float(row["phash_matched_percent"]) <= hi
    if keyword:
        assert keyword in row["anomalies"]
    assert float(row["processing_time_ms"]) > 0  # processing time is reported


def test_no_false_matches(rows):
    assert not [c for c, r in rows.items() if r["outcome"] == "false match"]


def test_only_known_missed_match(rows):
    assert [c for c, r in rows.items() if r["outcome"] == "missed match"] == ["TRANS-010"]


# N-xx: client with outbox must lose nothing and store no duplicates.
NETWORK_CASES = [f"N-{i:02d}" for i in range(1, 10)]


@pytest.fixture(scope="module")
def network():
    if not NET_FILE.exists():
        pytest.skip("run evaluate_network_scenarios.py first")
    return {r["case_id"]: r for r in json.loads(NET_FILE.read_text(encoding="utf-8"))}


@pytest.mark.parametrize("case_id", NETWORK_CASES)
def test_network_scenario_no_loss(network, case_id):
    r = network[case_id]
    assert r["lost"] == 0
    assert r["duplicate_rows_stored"] == 0
    assert r["stored_on_server"] == r["generated"]
    assert r["order_restorable_by_seq"]


def test_network_legacy_client_loses_data(network):
    assert network["N-10"]["lost"] > 0
