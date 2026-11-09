from __future__ import annotations

import os
import sqlite3
import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


def load_local_environment() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_local_environment()


@dataclass(frozen=True)
class Fingerprint:
    customer_id: str
    fingerprint: str
    algorithm: str
    captured_at: str
    source: str
    integrity_valid: bool
    media_id: str | None = None


class FingerprintStore:
    """Persistent storage with Supabase as the cloud target and SQLite fallback."""

    def __init__(self, database_path: str | Path = "data/fingerprints.db") -> None:
        self.supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.supabase_key = os.getenv("SUPABASE_KEY", "")
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS fingerprints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id TEXT NOT NULL DEFAULT 'legacy-customer',
                    fingerprint TEXT NOT NULL,
                    algorithm TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    integrity_valid INTEGER NOT NULL,
                    media_id TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS media_files (
                    media_id TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    fingerprint_count INTEGER NOT NULL,
                    file_sha256 TEXT NOT NULL DEFAULT '',
                    content BLOB NOT NULL DEFAULT X'',
                    reuploaded INTEGER NOT NULL DEFAULT 0,
                    duplicate_upload INTEGER NOT NULL DEFAULT 0,
                    overlap_fingerprints TEXT NOT NULL DEFAULT '[]',
                    overlap_media_ids TEXT NOT NULL DEFAULT '[]',
                    fingerprint_manifest TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS live_fingerprints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    algorithm TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    source TEXT NOT NULL
                )
                """
            )
            media_columns = {row[1] for row in connection.execute("PRAGMA table_info(media_files)")}
            live_columns = {row[1] for row in connection.execute("PRAGMA table_info(live_fingerprints)")}
            if "event_id" not in live_columns:
                connection.execute("ALTER TABLE live_fingerprints ADD COLUMN event_id TEXT")
            if "seq" not in live_columns:
                connection.execute("ALTER TABLE live_fingerprints ADD COLUMN seq INTEGER")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS live_fingerprints_event_idx ON live_fingerprints (customer_id, event_id)"
            )
            if "file_sha256" not in media_columns:
                connection.execute("ALTER TABLE media_files ADD COLUMN file_sha256 TEXT NOT NULL DEFAULT ''")
            if "content" not in media_columns:
                connection.execute("ALTER TABLE media_files ADD COLUMN content BLOB NOT NULL DEFAULT X''")
            if "reuploaded" not in media_columns:
                connection.execute("ALTER TABLE media_files ADD COLUMN reuploaded INTEGER NOT NULL DEFAULT 0")
            if "duplicate_upload" not in media_columns:
                connection.execute("ALTER TABLE media_files ADD COLUMN duplicate_upload INTEGER NOT NULL DEFAULT 0")
            if "overlap_fingerprints" not in media_columns:
                connection.execute("ALTER TABLE media_files ADD COLUMN overlap_fingerprints TEXT NOT NULL DEFAULT '[]'")
            if "overlap_media_ids" not in media_columns:
                connection.execute("ALTER TABLE media_files ADD COLUMN overlap_media_ids TEXT NOT NULL DEFAULT '[]'")
            if "fingerprint_manifest" not in media_columns:
                connection.execute("ALTER TABLE media_files ADD COLUMN fingerprint_manifest TEXT NOT NULL DEFAULT '[]'")
            legacy_media = connection.execute(
                "SELECT media_id FROM media_files WHERE fingerprint_manifest = '[]'"
            ).fetchall()
            for (media_id,) in legacy_media:
                fingerprints = connection.execute(
                    "SELECT fingerprint FROM fingerprints WHERE media_id = ? ORDER BY id",
                    (media_id,),
                ).fetchall()
                if fingerprints:
                    connection.execute(
                        "UPDATE media_files SET fingerprint_manifest = ? WHERE media_id = ?",
                        (json.dumps([row[0] for row in fingerprints]), media_id),
                    )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(fingerprints)")}
            if "customer_id" not in columns:
                connection.execute(
                    "ALTER TABLE fingerprints ADD COLUMN customer_id TEXT NOT NULL DEFAULT 'legacy-customer'"
                )
            if "media_id" not in columns:
                connection.execute("ALTER TABLE fingerprints ADD COLUMN media_id TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS media_files (
                    media_id TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    fingerprint_count INTEGER NOT NULL,
                    file_sha256 TEXT NOT NULL DEFAULT '',
                    content BLOB NOT NULL DEFAULT X''
                )
                """
            )

    @property
    def backend_name(self) -> str:
        return "Supabase" if self.supabase_url and self.supabase_key else "SQLite local fallback"

    def add(self, item: Fingerprint) -> Fingerprint:
        if self.supabase_url and self.supabase_key:
            self._add_supabase(item)
        else:
            self._add_sqlite(item)
        return item

    def add_live_fingerprint(self, item: Fingerprint, event_id: str | None = None, seq: int | None = None) -> bool:
        """Store one live fingerprint; returns False when event_id was already stored (idempotent retry)."""
        if self.supabase_url and self.supabase_key:
            payload: dict[str, Any] = {
                "customer_id": item.customer_id,
                "fingerprint": item.fingerprint,
                "algorithm": item.algorithm,
                "captured_at": item.captured_at,
                "source": item.source,
            }
            headers = {**self._supabase_headers(), "Prefer": "return=minimal"}
            params = None
            if event_id:
                payload["event_id"] = event_id
                payload["seq"] = seq
                headers["Prefer"] = "resolution=ignore-duplicates,return=representation"
                params = {"on_conflict": "customer_id,event_id"}
            response = httpx.post(
                f"{self.supabase_url}/rest/v1/live_fingerprints",
                headers=headers,
                params=params,
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
            return bool(response.json()) if event_id else True
        with sqlite3.connect(self.database_path) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO live_fingerprints (customer_id, fingerprint, algorithm, captured_at, source, event_id, seq) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (item.customer_id, item.fingerprint, item.algorithm, item.captured_at, item.source, event_id, seq),
            )
            return cursor.rowcount > 0

    def list_recent(self, limit: int = 100, customer_id: str | None = None, media_id: str | None = None) -> list[dict[str, Any]]:
        if self.supabase_url and self.supabase_key:
            params = {"select": "*", "order": "captured_at.desc", "limit": str(limit)}
            if customer_id:
                params["customer_id"] = f"eq.{customer_id}"
            if media_id:
                params["media_id"] = f"eq.{media_id}"
            response = httpx.get(
                f"{self.supabase_url}/rest/v1/fingerprints",
                headers=self._supabase_headers(),
                params=params,
                timeout=10,
            )
            response.raise_for_status()
            return response.json()

        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT customer_id, fingerprint, algorithm, captured_at, source, integrity_valid, media_id "
                "FROM fingerprints "
                + ("WHERE customer_id = ? " if customer_id else "")
                + ("AND media_id = ? " if customer_id and media_id else "WHERE media_id = ? " if media_id else "")
                + "ORDER BY id DESC LIMIT ?",
                tuple(value for value in (customer_id, media_id, limit) if value is not None),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_fingerprints(self, customer_id: str | None = None) -> int:
        if self.supabase_url and self.supabase_key:
            params = {"select": "id", "limit": "1"}
            if customer_id:
                params["customer_id"] = f"eq.{customer_id}"
            response = httpx.get(
                f"{self.supabase_url}/rest/v1/fingerprints",
                headers={**self._supabase_headers(), "Prefer": "count=exact"},
                params=params,
                timeout=10,
            )
            response.raise_for_status()
            content_range = response.headers.get("content-range", "")
            if "/" in content_range:
                return int(content_range.rsplit("/", 1)[1])
            return len(response.json())

        with sqlite3.connect(self.database_path) as connection:
            if customer_id:
                row = connection.execute(
                    "SELECT COUNT(*) FROM fingerprints WHERE customer_id = ?", (customer_id,)
                ).fetchone()
            else:
                row = connection.execute("SELECT COUNT(*) FROM fingerprints").fetchone()
        return int(row[0])

    def _add_sqlite(self, item: Fingerprint) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO fingerprints "
                "(customer_id, fingerprint, algorithm, captured_at, source, integrity_valid, media_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (item.customer_id, item.fingerprint, item.algorithm, item.captured_at, item.source, int(item.integrity_valid), item.media_id),
            )

    def _add_supabase(self, item: Fingerprint) -> None:
        response = httpx.post(
            f"{self.supabase_url}/rest/v1/fingerprints",
            headers={**self._supabase_headers(), "Prefer": "return=minimal"},
            json={
                "customer_id": item.customer_id,
                "fingerprint": item.fingerprint,
                "algorithm": item.algorithm,
                "captured_at": item.captured_at,
                "source": item.source,
                "integrity_valid": item.integrity_valid,
                "media_id": item.media_id,
            },
            timeout=10,
        )
        response.raise_for_status()

    def add_media(self, media: dict[str, Any]) -> dict[str, Any]:
        if self.supabase_url and self.supabase_key:
            payload = {key: value for key, value in media.items() if key != "content"}
            payload["content_base64"] = base64.b64encode(media["content"]).decode("ascii")
            response = httpx.post(
                f"{self.supabase_url}/rest/v1/media_files",
                headers={**self._supabase_headers(), "Prefer": "return=minimal"},
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
        else:
            with sqlite3.connect(self.database_path) as connection:
                connection.execute(
                    "INSERT INTO media_files "
                    "(media_id, customer_id, filename, media_type, stored_path, uploaded_at, size_bytes, fingerprint_count, file_sha256, content, reuploaded, duplicate_upload, overlap_fingerprints, overlap_media_ids, fingerprint_manifest) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (media["media_id"], media["customer_id"], media["filename"], media["media_type"], media["stored_path"], media["uploaded_at"], media["size_bytes"], media["fingerprint_count"], media["file_sha256"], media["content"], int(media["reuploaded"]), int(media["duplicate_upload"]), media["overlap_fingerprints"], media["overlap_media_ids"], media["fingerprint_manifest"]),
                )
        return media

    def find_media_by_hash(self, customer_id: str, file_sha256: str) -> dict[str, Any] | None:
        if self.supabase_url and self.supabase_key:
            response = httpx.get(
                f"{self.supabase_url}/rest/v1/media_files",
                headers=self._supabase_headers(),
                params={"select": "media_id,customer_id,filename,file_sha256", "customer_id": f"eq.{customer_id}", "file_sha256": f"eq.{file_sha256}", "limit": "1"},
                timeout=10,
            )
            response.raise_for_status()
            rows = response.json()
            return rows[0] if rows else None
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT media_id, customer_id, filename, file_sha256 FROM media_files WHERE customer_id = ? AND file_sha256 = ? LIMIT 1",
                (customer_id, file_sha256),
            ).fetchone()
        return dict(row) if row else None

    def list_media(self, customer_id: str | None = None) -> list[dict[str, Any]]:
        if self.supabase_url and self.supabase_key:
            params = {
                "select": "media_id,customer_id,filename,media_type,stored_path,uploaded_at,size_bytes,fingerprint_count,file_sha256",
                "order": "uploaded_at.desc",
                "limit": "100",
            }
            if customer_id:
                params["customer_id"] = f"eq.{customer_id}"
            response = httpx.get(
                f"{self.supabase_url}/rest/v1/media_files",
                headers=self._supabase_headers(),
                params=params,
                timeout=10,
            )
            response.raise_for_status()
            return response.json()

        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT media_id, customer_id, filename, media_type, stored_path, uploaded_at, size_bytes, fingerprint_count, file_sha256 "
                "FROM media_files "
                + ("WHERE customer_id = ? " if customer_id else "")
                + "ORDER BY uploaded_at DESC LIMIT 100",
                ((customer_id,) if customer_id else ()),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_media(self, media_id: str) -> dict[str, Any] | None:
        if self.supabase_url and self.supabase_key:
            response = httpx.get(
                f"{self.supabase_url}/rest/v1/media_files",
                headers=self._supabase_headers(),
                params={"select": "*", "media_id": f"eq.{media_id}", "limit": "1"},
                timeout=10,
            )
            response.raise_for_status()
            rows = response.json()
            return rows[0] if rows else None
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM media_files WHERE media_id = ?", (media_id,)).fetchone()
        return dict(row) if row else None

    def _supabase_headers(self) -> dict[str, str]:
        return {"apikey": self.supabase_key, "Authorization": f"Bearer {self.supabase_key}"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")








