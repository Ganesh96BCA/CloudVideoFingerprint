"""Network / server failure evaluation for the live fingerprint pipeline.

Simulates a phone sending fingerprints (4 per second for 60 s) to the real FastAPI app
while the network or server fails. The client logic mirrors the browser outbox in
web/index.html: persist first, send in order, keep the head item until the server
acknowledges it, retry every 3 s. A "legacy" run (send once, drop on failure) is included
for comparison. Time is simulated (no sleeping) and seeded, so results are reproducible.

Usage: python evaluate_network_scenarios.py [--output evaluation-results]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sqlite3
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

CAPTURE_INTERVAL = 0.25
RETRY_INTERVAL = 3.0
DURATION = 60.0
CUSTOMER = "NET-TEST-001"


class Scenario:
    def __init__(self, case_id: str, title: str, outbox: bool = True, **faults) -> None:
        self.case_id, self.title, self.outbox = case_id, title, outbox
        self.network_down = faults.get("network_down", [])  # request never reaches the server
        self.server_down = faults.get("server_down", [])  # server answers 503, stores nothing
        self.loss_rate = faults.get("loss_rate", 0.0)  # request lost before reaching the server
        self.response_loss_rate = faults.get("response_loss_rate", 0.0)  # stored, but the ack is lost
        self.reorder_window = faults.get("reorder_window", 0)  # deliver in shuffled windows of N

    @staticmethod
    def inside(windows: list[tuple[float, float]], now: float) -> bool:
        return any(start <= now < end for start, end in windows)


SCENARIOS = [
    Scenario("N-01", "No failure (baseline)"),
    Scenario("N-02", "Internet lost for 10 s (20-30 s)", network_down=[(20, 30)]),
    Scenario("N-03", "Intermittent connectivity (35% of requests lost)", loss_rate=0.35),
    Scenario("N-04", "Server unavailable for 10 s (20-30 s, HTTP 503)", server_down=[(20, 30)]),
    Scenario("N-05", "Server downtime during transmission (two outages)", server_down=[(10, 18), (35, 45)]),
    Scenario("N-06", "Offline 30 s, fingerprints generated offline then sent on reconnect", network_down=[(15, 45)]),
    Scenario("N-07", "Acknowledgement lost, client retries (duplicate transmissions)", response_loss_rate=0.30),
    Scenario("N-08", "Fingerprints delivered out of order (shuffled windows of 8)", reorder_window=8),
    Scenario("N-09", "Combined: offline 8 s, 503 for 8 s, 20% loss, 20% lost acks", network_down=[(10, 18)], server_down=[(30, 38)], loss_rate=0.2, response_loss_rate=0.2),
    Scenario("N-10", "Legacy client (no buffer), Internet lost 10 s", outbox=False, network_down=[(20, 30)]),
]


def run(scenario: Scenario, client: TestClient, seed: int) -> dict:
    rng = random.Random(seed)
    generated: list[dict] = []
    queue: list[dict] = []
    attempts = 0
    last_failure_end = max([end for _, end in scenario.network_down + scenario.server_down], default=0.0)
    drained_at: float | None = None

    def send(item: dict, now: float) -> bool:
        nonlocal attempts
        attempts += 1
        if Scenario.inside(scenario.network_down, now) or rng.random() < scenario.loss_rate:
            return False
        if Scenario.inside(scenario.server_down, now):
            return False
        if client.post("/api/live-fingerprints", json=item).status_code >= 500:
            return False
        return rng.random() >= scenario.response_loss_rate

    def flush(now: float) -> None:
        if scenario.reorder_window:
            while queue:
                window = queue[: scenario.reorder_window]
                shuffled = window[:]
                rng.shuffle(shuffled)
                delivered = {id(item) for item in shuffled if send(item, now)}
                queue[:] = [item for item in window if id(item) not in delivered] + queue[scenario.reorder_window:]
                if len(delivered) < len(window):
                    return
            return
        while queue and send(queue[0], now):
            queue.pop(0)

    steps = int(DURATION / CAPTURE_INTERVAL)
    for step in range(steps):
        now = step * CAPTURE_INTERVAL
        item = {
            "event_id": uuid.UUID(int=rng.getrandbits(128)).hex,
            "seq": step + 1,
            "customer_id": CUSTOMER,
            "fingerprint": hashlib.sha256(f"frame-{scenario.case_id}-{step}".encode()).hexdigest(),
            "captured_at": f"2026-01-01T00:{int(now) // 60:02d}:{int(now) % 60:02d}.{int((now % 1) * 1000):03d}+00:00",
        }
        generated.append(item)
        if scenario.outbox:
            queue.append(item)
            if not scenario.reorder_window or len(queue) >= scenario.reorder_window or step == steps - 1:
                flush(now)
            if not queue and drained_at is None and now >= last_failure_end:
                drained_at = now
        else:
            send(item, now)  # legacy: one attempt, then forgotten

    now = DURATION
    while scenario.outbox and queue and now < DURATION + 300:
        now += RETRY_INTERVAL
        flush(now)
    if scenario.outbox and not queue and drained_at is None:
        drained_at = now

    with sqlite3.connect(main.store.database_path) as connection:
        rows = connection.execute(
            "SELECT event_id, seq, fingerprint, captured_at FROM live_fingerprints WHERE customer_id = ? ORDER BY id",
            (CUSTOMER,),
        ).fetchall()
    stored = [row[0] for row in rows]
    expected = {item["event_id"]: item for item in generated}
    missing = [event for event in expected if event not in set(stored)]
    arrival = [row[1] for row in rows]
    intact = all(
        row[2] == expected[row[0]]["fingerprint"]
        and row[3] == datetime.fromisoformat(expected[row[0]]["captured_at"]).isoformat()
        for row in rows
        if row[0] in expected
    )
    restorable = not missing and intact and sorted(arrival) == list(range(1, len(generated) + 1))
    recovery = round(max(0.0, (drained_at or now) - last_failure_end), 2) if scenario.outbox and last_failure_end else None
    return {
        "case_id": scenario.case_id,
        "scenario": scenario.title,
        "client": "outbox + retry" if scenario.outbox else "legacy (no buffer)",
        "generated": len(generated),
        "send_attempts": attempts,
        "stored_on_server": len(rows),
        "lost": len(missing),
        "loss_percent": round(100 * len(missing) / len(generated), 2),
        "duplicate_rows_stored": len(stored) - len(set(stored)),
        "recovery_time_s_after_last_failure": recovery,
        "arrival_order_preserved": arrival == sorted(arrival),
        "order_restorable_by_seq": restorable,
    }


def main_cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("evaluation-results"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        for index, scenario in enumerate(SCENARIOS):
            main.store = main.FingerprintStore(Path(folder) / f"net-{index}.db")
            results.append(run(scenario, TestClient(main.app), seed=100 + index))
    (args.output / "network_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    header = ["case_id", "client", "generated", "send_attempts", "stored_on_server", "lost", "loss_percent", "duplicate_rows_stored", "recovery_time_s_after_last_failure", "arrival_order_preserved", "order_restorable_by_seq", "scenario"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(row[key]) for key in header) + " |" for row in results]
    (args.output / "network_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main_cli()
