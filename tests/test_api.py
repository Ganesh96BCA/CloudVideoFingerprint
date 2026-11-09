import pytest
from fastapi.testclient import TestClient
import sqlite3

from app.main import app


client = TestClient(app)
VALID = "a" * 64
INVALID = "not-a-sha-hash"


@pytest.fixture(autouse=True)
def isolate_tests_from_configured_cloud(monkeypatch, tmp_path) -> None:
    from app import main

    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_KEY", "")
    main.store = main.FingerprintStore(tmp_path / "isolated-test.db")


def test_validate_reports_valid_and_invalid_lines() -> None:
    response = client.post("/api/validate", json={"fingerprints": [VALID, INVALID]})
    assert response.status_code == 200
    assert response.json()["valid"] == 1
    assert response.json()["invalid"] == 1


def test_upload_requires_text_file() -> None:
    response = client.post("/api/upload", files={"file": ("evidence.csv", b"abc", "text/csv")})
    assert response.status_code == 415


def test_live_frame_endpoint_stores_one_fingerprint(tmp_path) -> None:
    from app import main

    main.store = main.FingerprintStore(tmp_path / "live.db")
    response = client.post(
        "/api/fingerprints",
        json={"customer_id": "CUS-001", "fingerprint": VALID, "source": "live browser dashcam"},
    )
    assert response.status_code == 200
    assert response.json()["algorithm"] == "SHA-256"
    assert response.json()["source"] == "live browser dashcam"
    live_response = client.post(
        "/api/live-fingerprints",
        json={"customer_id": "CUS-001", "fingerprint": VALID},
    )
    assert live_response.status_code == 200
    assert live_response.json()["algorithm"] == "SHA-256"


def test_stream_persists_only_valid_hashes(tmp_path, monkeypatch) -> None:
    from app import main

    main.store = main.FingerprintStore(tmp_path / "fingerprints.db")
    response = client.post("/api/stream", json={"customer_id": "CUS-001", "fingerprints": [VALID, INVALID], "delay_ms": 0})
    assert response.status_code == 200
    assert response.json()["accepted"] == 1
    assert len(main.store.list_recent()) == 1
    stats = client.get("/api/fingerprint-stats?customer_id=CUS-001")
    assert stats.json()["total"] == 1


def test_verify_matches_only_selected_customer(tmp_path) -> None:
    from app import main

    main.store = main.FingerprintStore(tmp_path / "verify.db")
    client.post("/api/fingerprints", json={"customer_id": "CUS-001", "fingerprint": VALID})
    client.post("/api/fingerprints", json={"customer_id": "CUS-002", "fingerprint": "b" * 64})
    response = client.post("/api/verify", json={"customer_id": "CUS-001", "fingerprints": [VALID]})
    assert response.status_code == 200
    assert response.json()["integrity_verified"] is True
    assert response.json()["matches"] == 1


def test_media_upload_is_listed_for_customer(tmp_path) -> None:
    from app import main

    main.store = main.FingerprintStore(tmp_path / "media.db")
    response = client.post(
        "/api/media",
        data={"customer_id": "CUS-001", "fingerprints": f'["{VALID}"]'},
        files={"file": ("drive.webm", b"recorded-media", "video/webm")},
    )
    assert response.status_code == 200
    assert response.json()["filename"] == "drive.webm"
    media = main.store.list_media("CUS-001")[0]
    assert media["filename"] == "drive.webm"
    integrity = client.get(f"/api/media/{media['media_id']}/integrity")
    assert integrity.status_code == 200
    assert integrity.json()["integrity_verified"] is True
    assert integrity.json()["cryptographic_algorithm"] == "SHA-256"
    assert len(integrity.json()["stored_file_sha256"]) == 64
    assert integrity.json()["verified_frames"] == 1
    assert integrity.json()["unverified_frames"] == 0
    assert integrity.json()["integrity_rate"] == 100.0
    assert integrity.json()["media_type"] == "video/webm"
    assert integrity.json()["size_bytes"] == len(b"recorded-media")
    assert integrity.json()["frames"][0]["status"] == "VERIFIED"


def test_video_upload_accepts_generic_mime_by_extension(tmp_path) -> None:
    from app import main

    main.store = main.FingerprintStore(tmp_path / "generic-video.db")
    response = client.post(
        "/api/media",
        data={"customer_id": "CUS-VIDEO", "fingerprints": f'["{VALID}"]'},
        files={"file": ("dashcam.mp4", b"video-bytes", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert response.json()["customer_id"] == "CUS-VIDEO"


def test_reuploaded_media_requires_integrity_review(tmp_path) -> None:
    from app import main

    main.store = main.FingerprintStore(tmp_path / "duplicate.db")
    payload = {"customer_id": "CUS-DUP", "fingerprints": f'["{VALID}"]'}
    first = client.post("/api/media", data=payload, files={"file": ("drive.mp4", b"same-video", "video/mp4")})
    second = client.post("/api/media", data=payload, files={"file": ("drive-copy.mp4", b"same-video", "video/mp4")})
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["reuploaded"] is True
    integrity = client.get(f"/api/media/{second.json()['media_id']}/integrity")
    assert integrity.json()["integrity_verified"] is False
    assert integrity.json()["verified_frames"] == 0
    assert integrity.json()["unverified_frames"] == 1


def test_matching_frame_from_different_video_is_unverified(tmp_path) -> None:
    from app import main

    main.store = main.FingerprintStore(tmp_path / "overlap.db")
    first = client.post(
        "/api/media",
        data={"customer_id": "CUS-OVERLAP", "fingerprints": f'["{VALID}"]'},
        files={"file": ("first.mp4", b"first-video", "video/mp4")},
    )
    second = client.post(
        "/api/media",
        data={"customer_id": "CUS-OVERLAP", "fingerprints": f'["{VALID}"]'},
        files={"file": ("second.mp4", b"different-video", "video/mp4")},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate_upload"] is False
    integrity = client.get(f"/api/media/{second.json()['media_id']}/integrity")
    assert integrity.json()["overlap_frame_count"] == 1
    assert integrity.json()["frames"][0]["status"] == "UNVERIFIED"


def test_manually_changed_fingerprint_is_unverified(tmp_path) -> None:
    from app import main

    main.store = main.FingerprintStore(tmp_path / "tampered-fingerprint.db")
    upload = client.post(
        "/api/media",
        data={"customer_id": "CUS-TAMPER", "fingerprints": f'["{VALID}"]'},
        files={"file": ("evidence.mp4", b"original-video", "video/mp4")},
    )
    assert upload.status_code == 200

    with sqlite3.connect(main.store.database_path) as connection:
        connection.execute(
            "UPDATE fingerprints SET fingerprint = ? WHERE media_id = ?",
            ("b" * 64, upload.json()["media_id"]),
        )

    integrity = client.get(f"/api/media/{upload.json()['media_id']}/integrity")
    report = integrity.json()
    assert report["integrity_verified"] is False
    assert report["verified_frames"] == 0
    assert report["unverified_frames"] == 1
    assert report["modified_fingerprint_count"] == 1
    assert report["integrity_rate"] == 0
    assert report["frames"][0]["status"] == "UNVERIFIED"
    assert report["frames"][0]["reason"] == "Fingerprint differs from the upload-time manifest"


def test_malformed_manual_fingerprint_is_unverified_even_for_legacy_baseline(tmp_path) -> None:
    from app import main

    main.store = main.FingerprintStore(tmp_path / "malformed-fingerprint.db")
    upload = client.post(
        "/api/media",
        data={"customer_id": "CUS-INVALID-FP", "fingerprints": f'["{VALID}"]'},
        files={"file": ("evidence.mp4", b"original-video", "video/mp4")},
    )
    media_id = upload.json()["media_id"]
    with sqlite3.connect(main.store.database_path) as connection:
        connection.execute("UPDATE fingerprints SET fingerprint = '000' WHERE media_id = ?", (media_id,))
        connection.execute("UPDATE media_files SET fingerprint_manifest = '[\"000\"]' WHERE media_id = ?", (media_id,))

    report = client.get(f"/api/media/{media_id}/integrity").json()
    assert report["integrity_verified"] is False
    assert report["invalid_fingerprint_count"] == 1
    assert report["frames"][0]["status"] == "UNVERIFIED"


def test_live_fingerprint_retry_with_same_event_id_is_stored_once() -> None:
    payload = {"customer_id": "CUS-RETRY", "fingerprint": VALID, "event_id": "event-0001", "seq": 1,
               "captured_at": "2026-01-01T00:00:00+00:00"}
    first = client.post("/api/live-fingerprints", json=payload)
    retry = client.post("/api/live-fingerprints", json=payload)
    assert first.json()["duplicate"] is False
    assert retry.json()["duplicate"] is True
    assert first.json()["captured_at"] == "2026-01-01T00:00:00+00:00"
    from app import main
    with sqlite3.connect(main.store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM live_fingerprints").fetchone()[0] == 1


def test_live_fingerprint_rejects_future_capture_time() -> None:
    response = client.post("/api/live-fingerprints", json={"customer_id": "CUS-RETRY", "fingerprint": VALID,
                                                            "captured_at": "2999-01-01T00:00:00+00:00"})
    assert response.status_code == 422
