from __future__ import annotations

import re
import json
import hashlib
import base64
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .storage import Fingerprint, FingerprintStore, utc_now


BASE_DIR = Path(__file__).resolve().parent.parent
app = FastAPI(title="Cloud Video Fingerprint Collector", version="1.0.0")
store = FingerprintStore(BASE_DIR / "data" / "fingerprints.db")
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
SHA512_PATTERN = re.compile(r"^[0-9a-fA-F]{128}$")


class FingerprintRequest(BaseModel):
    customer_id: str
    fingerprint: str
    source: str = "live camera simulation"


class LiveFingerprintRequest(BaseModel):
    customer_id: str
    fingerprint: str
    event_id: str | None = None
    seq: int | None = None
    captured_at: str | None = None


class StreamRequest(BaseModel):
    customer_id: str
    fingerprints: list[str]
    delay_ms: int = 250
    source: str = "uploaded camera log"


class ValidationRequest(BaseModel):
    fingerprints: list[str]


class VerificationRequest(BaseModel):
    customer_id: str
    fingerprints: list[str]


def identify_algorithm(value: str) -> str | None:
    if SHA256_PATTERN.fullmatch(value):
        return "SHA-256"
    if SHA512_PATTERN.fullmatch(value):
        return "SHA-512"
    return None


def validate_fingerprint(value: str) -> tuple[str, str]:
    normalized = value.strip()
    algorithm = identify_algorithm(normalized)
    if algorithm is None:
        raise HTTPException(status_code=422, detail="Fingerprint must be a 64 or 128 character hexadecimal SHA-256/SHA-512 hash")
    return normalized.lower(), algorithm


def validate_customer_id(value: str) -> str:
    normalized = value.strip()
    if len(normalized) < 3 or len(normalized) > 64:
        raise HTTPException(status_code=422, detail="Customer ID must contain between 3 and 64 characters")
    return normalized


@app.get("/", response_class=FileResponse)
def dashboard() -> str:
    return str(BASE_DIR / "web" / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "storage_backend": store.backend_name}


@app.get("/api/fingerprints")
def fingerprints(customer_id: str | None = None) -> list[dict[str, object]]:
    return store.list_recent(customer_id=validate_customer_id(customer_id) if customer_id else None)


@app.get("/api/fingerprint-stats")
def fingerprint_stats(customer_id: str | None = None) -> dict[str, object]:
    normalized_customer_id = validate_customer_id(customer_id) if customer_id else None
    return {"total": store.count_fingerprints(customer_id=normalized_customer_id)}


@app.get("/api/media")
def media_files(customer_id: str | None = None) -> list[dict[str, object]]:
    return store.list_media(customer_id=validate_customer_id(customer_id) if customer_id else None)


@app.post("/api/fingerprints")
def add_fingerprint(request: FingerprintRequest) -> dict[str, object]:
    fingerprint, algorithm = validate_fingerprint(request.fingerprint)
    customer_id = validate_customer_id(request.customer_id)
    item = Fingerprint(customer_id, fingerprint, algorithm, utc_now(), request.source, True)
    store.add(item)
    return {"accepted": True, **item.__dict__}


@app.post("/api/live-fingerprints")
def add_live_fingerprint(request: LiveFingerprintRequest) -> dict[str, object]:
    customer_id = validate_customer_id(request.customer_id)
    fingerprint, algorithm = validate_fingerprint(request.fingerprint)
    if request.event_id is not None and not 8 <= len(request.event_id) <= 64:
        raise HTTPException(status_code=422, detail="event_id must contain 8 to 64 characters")
    captured_at = utc_now()
    if request.captured_at:
        # Offline-buffered fingerprints keep their capture time; future timestamps are rejected.
        try:
            parsed = datetime.fromisoformat(request.captured_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail="captured_at must be an ISO-8601 timestamp")
        if parsed.tzinfo is None or parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise HTTPException(status_code=422, detail="captured_at must be a UTC timestamp that is not in the future")
        captured_at = parsed.astimezone(timezone.utc).isoformat()
    item = Fingerprint(customer_id, fingerprint, algorithm, captured_at, "real-time camera fingerprint", True)
    created = store.add_live_fingerprint(item, event_id=request.event_id, seq=request.seq)
    return {"accepted": True, "duplicate": not created, "event_id": request.event_id, "customer_id": customer_id, "fingerprint": fingerprint, "algorithm": algorithm, "captured_at": item.captured_at}


@app.post("/api/stream")
def stream_fingerprints(request: StreamRequest) -> dict[str, object]:
    if not request.fingerprints:
        raise HTTPException(status_code=422, detail="At least one fingerprint is required")
    customer_id = validate_customer_id(request.customer_id)
    if request.delay_ms < 0 or request.delay_ms > 5000:
        raise HTTPException(status_code=422, detail="delay_ms must be between 0 and 5000")

    accepted = 0
    rejected: list[dict[str, object]] = []
    for line_number, value in enumerate(request.fingerprints, start=1):
        try:
            fingerprint, algorithm = validate_fingerprint(value)
            store.add(Fingerprint(customer_id, fingerprint, algorithm, utc_now(), request.source, True))
            accepted += 1
        except HTTPException as error:
            rejected.append({"line": line_number, "value": value, "reason": error.detail})
        if request.delay_ms and line_number < len(request.fingerprints):
            time.sleep(request.delay_ms / 1000)
    return {"accepted": accepted, "rejected": rejected, "total": len(request.fingerprints)}


@app.post("/api/upload")
async def upload_fingerprints(file: UploadFile = File(...)) -> dict[str, object]:
    if not file.filename or not file.filename.lower().endswith(".txt"):
        raise HTTPException(status_code=415, detail="Upload a .txt fingerprint file")
    content = (await file.read()).decode("utf-8-sig")
    values = [line.strip() for line in content.splitlines() if line.strip()]
    if not values:
        raise HTTPException(status_code=422, detail="The text file contains no fingerprints")
    return {"filename": file.filename, "fingerprints": values, "count": len(values)}


@app.post("/api/media")
async def upload_media(
    customer_id: str = Form(...),
    fingerprints: str = Form(...),
    file: UploadFile = File(...),
) -> dict[str, object]:
    customer_id = validate_customer_id(customer_id)
    media_suffix = Path(file.filename).suffix.lower()
    supported_suffixes = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".jpg", ".jpeg", ".png", ".webp", ".gif"}
    if not file.filename or not ((file.content_type or "").startswith(("video/", "image/")) or media_suffix in supported_suffixes):
        raise HTTPException(status_code=415, detail="Upload an image or video file")
    try:
        submitted = json.loads(fingerprints)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=422, detail="fingerprints must be a JSON array") from error
    if not isinstance(submitted, list) or not submitted:
        raise HTTPException(status_code=422, detail="At least one fingerprint is required")
    valid_fingerprints: list[tuple[str, str]] = []
    for value in submitted:
        fingerprint, algorithm = validate_fingerprint(str(value))
        valid_fingerprints.append((fingerprint, algorithm))

    media_id = uuid.uuid4().hex
    safe_name = Path(file.filename).name
    content = await file.read()
    uploaded_at = utc_now()
    file_sha256 = hashlib.sha256(content).hexdigest()
    duplicate_upload = store.find_media_by_hash(customer_id, file_sha256) is not None
    existing_frames = store.list_recent(limit=100000, customer_id=customer_id)
    submitted_fingerprints = {fingerprint for fingerprint, _ in valid_fingerprints}
    overlap_records = [row for row in existing_frames if row["fingerprint"] in submitted_fingerprints]
    overlap_fingerprints = sorted({row["fingerprint"] for row in overlap_records})
    overlap_media_ids = sorted({row["media_id"] for row in overlap_records if row.get("media_id")})
    reuploaded = duplicate_upload or bool(overlap_fingerprints)
    media = {
        "media_id": media_id,
        "customer_id": customer_id,
        "filename": safe_name,
        "media_type": file.content_type or ("video/" + media_suffix.lstrip(".")),
        "stored_path": "database",
        "uploaded_at": uploaded_at,
        "size_bytes": len(content),
        "fingerprint_count": len(valid_fingerprints),
        "file_sha256": file_sha256,
        "content": content,
        "reuploaded": reuploaded,
        "duplicate_upload": duplicate_upload,
        "overlap_fingerprints": json.dumps(overlap_fingerprints),
        "overlap_media_ids": json.dumps(overlap_media_ids),
        "fingerprint_manifest": json.dumps([fingerprint for fingerprint, _ in valid_fingerprints]),
    }
    store.add_media(media)
    for fingerprint, algorithm in valid_fingerprints:
        store.add(Fingerprint(customer_id, fingerprint, algorithm, uploaded_at, f"uploaded media: {safe_name}", True, media_id))
    return {"uploaded": True, **{key: value for key, value in media.items() if key != "content"}}


@app.get("/api/media/{media_id}/integrity")
def media_integrity(media_id: str) -> dict[str, object]:
    media = store.get_media(media_id)
    if not media:
        raise HTTPException(status_code=404, detail="Media record not found")
    content = media.get("content")
    if content is None and media.get("content_base64"):
        content = base64.b64decode(media["content_base64"])
    records = store.list_recent(limit=10000, customer_id=media["customer_id"], media_id=media_id)
    bytes_match = bool(content) and hashlib.sha256(content).hexdigest() == media["file_sha256"]
    reuploaded = bool(media.get("reuploaded"))
    duplicate_upload = bool(media.get("duplicate_upload"))
    overlap_fingerprints = set(json.loads(media.get("overlap_fingerprints") or "[]"))
    overlap_media_ids = json.loads(media.get("overlap_media_ids") or "[]")
    original_manifest = json.loads(media.get("fingerprint_manifest") or "[]")
    manifest_counts: dict[str, int] = {}
    for fingerprint in original_manifest:
        manifest_counts[fingerprint] = manifest_counts.get(fingerprint, 0) + 1
    manifest_available = bool(original_manifest) and len(original_manifest) == media["fingerprint_count"]
    frames = []
    for row in reversed(records):
        fingerprint_count = manifest_counts.get(row["fingerprint"], 0)
        manifest_value_match = manifest_available and fingerprint_count > 0
        if manifest_value_match:
            manifest_counts[row["fingerprint"]] -= 1
        fingerprint_format_valid = identify_algorithm(row["fingerprint"]) is not None
        manifest_match = manifest_value_match and fingerprint_format_valid
        if not fingerprint_format_valid:
            reason = "Fingerprint value is not a valid SHA-256 or SHA-512 hash"
        elif not manifest_value_match:
            reason = "Fingerprint differs from the upload-time manifest"
        elif duplicate_upload:
            reason = "Video file was uploaded before"
        elif row["fingerprint"] in overlap_fingerprints:
            reason = "Frame fingerprint overlaps another video"
        elif not bytes_match:
            reason = "Stored video bytes do not match the original file hash"
        elif not row["integrity_valid"]:
            reason = "Fingerprint record failed validation"
        else:
            reason = "Matches upload-time manifest"
        status = (
            "VERIFIED"
            if manifest_match and bytes_match and not duplicate_upload
            and row["fingerprint"] not in overlap_fingerprints and row["integrity_valid"]
            else "UNVERIFIED"
        )
        frames.append({
            "fingerprint": row["fingerprint"],
            "captured_at": row["captured_at"],
            "status": status,
            "reason": reason,
            "modified": not manifest_value_match,
        })
    # Rows deleted or unlinked from this media: report as many manifest entries as are missing.
    missing_rows = max(media["fingerprint_count"] - len(records), 0) if manifest_available else 0
    for fingerprint, remaining in manifest_counts.items():
        for _ in range(remaining):
            if missing_rows <= 0:
                break
            missing_rows -= 1
            frames.append({
                "fingerprint": fingerprint,
                "captured_at": media["uploaded_at"],
                "status": "UNVERIFIED",
                "reason": "Stored fingerprint missing or edited: upload-time manifest entry has no matching record",
                "modified": True,
            })
    fingerprints_match = (
        manifest_available
        and len(records) == media["fingerprint_count"]
        and all(frame["status"] == "VERIFIED" for frame in frames)
    )
    return {
        "media_id": media_id,
        "customer_id": media["customer_id"],
        "filename": media["filename"],
        "integrity_verified": bytes_match and not reuploaded and fingerprints_match,
        "reuploaded": reuploaded,
        "duplicate_upload": duplicate_upload,
        "fingerprint_manifest_available": manifest_available,
        "overlap_frame_count": len(overlap_fingerprints),
        "overlap_media_ids": overlap_media_ids,
        "file_bytes_verified": bytes_match,
        "fingerprints_verified": fingerprints_match,
        "cryptographic_algorithm": "SHA-256",
        "stored_file_sha256": media["file_sha256"],
        "fingerprint_count": len(records),
        "verified_frames": sum(frame["status"] == "VERIFIED" for frame in frames),
        "unverified_frames": sum(frame["status"] == "UNVERIFIED" for frame in frames),
        "modified_fingerprint_count": sum(frame["modified"] for frame in frames),
        "invalid_fingerprint_count": sum("not a valid SHA-256" in frame["reason"] for frame in frames),
        "integrity_rate": round((sum(frame["status"] == "VERIFIED" for frame in frames) / len(frames)) * 100, 2) if frames else 0,
        "media_type": media["media_type"],
        "size_bytes": media["size_bytes"],
        "uploaded_at": media["uploaded_at"],
        "frames": frames,
    }


def integrity_report(values: list[str]) -> dict[str, object]:
    valid = 0
    invalid: list[dict[str, object]] = []
    for index, value in enumerate(values, start=1):
        normalized = value.strip().lower()
        algorithm = identify_algorithm(normalized)
        if algorithm:
            valid += 1
        else:
            invalid.append({"line": index, "value": value, "reason": "Unsupported or malformed hash"})
    return {"valid": valid, "invalid": len(invalid), "total": len(values), "details": invalid}


@app.post("/api/validate")
def validate_values(request: ValidationRequest) -> dict[str, object]:
    return integrity_report(request.fingerprints)


@app.post("/api/verify")
def verify_values(request: VerificationRequest) -> dict[str, object]:
    customer_id = validate_customer_id(request.customer_id)
    stored = store.list_recent(limit=10000, customer_id=customer_id)
    stored_hashes = {str(row["fingerprint"]) for row in stored}
    submitted = {value.strip().lower() for value in request.fingerprints if identify_algorithm(value.strip())}
    matches = sorted(submitted & stored_hashes)
    missing = sorted(submitted - stored_hashes)
    return {
        "customer_id": customer_id,
        "submitted": len(submitted),
        "matches": len(matches),
        "unmatched": len(missing),
        "integrity_verified": bool(matches) and not missing,
        "matched_fingerprints": matches,
        "unmatched_fingerprints": missing,
    }
