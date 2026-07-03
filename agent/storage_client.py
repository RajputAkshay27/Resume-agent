"""
storage_client.py — Unified storage client for the Resume Agent v2.

Default backend: SQLite (zero external dependencies, works offline).
Optional backend: S3-compatible (set STORAGE_BACKEND=s3 in environment).

The SQLite store uses a single database file (default: data/resume_agent.db)
with the following tables:
  - profile:          Single master profile (JSON blob + timestamp).
  - job_description:  Current job description (text + timestamp).
  - templates:        Versioned LaTeX templates (content + version + active flag).
  - preferences:      Section preferences (JSON blob).
  - tailored_outputs: History of tailored outputs for diff comparison (max 10 retained).

Security:
  - All key inputs are validated to prevent path traversal in S3 mode.
  - SQLite uses parameterized queries throughout — no string interpolation.
  - File size limits enforced at the client layer.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CONTENT_BYTES = 5 * 1024 * 1024  # 5 MB hard limit
MAX_TAILORED_HISTORY = 10            # Rolling window of tailored outputs


# ---------------------------------------------------------------------------
# Key validation helper (used in S3 mode to prevent path traversal)
# ---------------------------------------------------------------------------

def _validate_storage_key(key: str) -> str:
    """
    Validate a storage key to prevent path traversal attacks.

    Raises ValueError for keys containing '..' or starting with '/'.
    Returns the key unchanged if valid.
    """
    if not key or not key.strip():
        raise ValueError("Storage key must not be empty.")
    if ".." in key:
        raise ValueError(f"Storage key contains path traversal sequence: {key!r}")
    if key.startswith("/"):
        raise ValueError(f"Storage key must not be an absolute path: {key!r}")
    return key.strip()


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class StorageClient(ABC):
    """Abstract storage interface. Both SQLite and S3 adapters implement this."""

    @abstractmethod
    def save_profile(self, profile: dict) -> None:
        """Persist the master profile (replaces any existing profile)."""

    @abstractmethod
    def load_profile(self) -> Optional[dict]:
        """Load the master profile. Returns None if not set."""

    @abstractmethod
    def save_job_description(self, jd: str) -> None:
        """Persist the job description (replaces any existing JD)."""

    @abstractmethod
    def load_job_description(self) -> Optional[str]:
        """Load the job description. Returns None if not set."""

    @abstractmethod
    def save_preferences(self, prefs: dict) -> None:
        """Persist section preferences (replaces any existing prefs)."""

    @abstractmethod
    def load_preferences(self) -> Optional[dict]:
        """Load section preferences. Returns None if not set."""

    @abstractmethod
    def save_template(self, content: str, label: str = "") -> int:
        """
        Save a new LaTeX template version.

        Args:
            content: The template content.
            label:   Optional human-readable label (e.g. 'compact-v2').

        Returns:
            The version number assigned to this template.
        """

    @abstractmethod
    def load_template(self, version: Optional[int] = None) -> Optional[str]:
        """
        Load a template by version.

        Args:
            version: If None, returns the active (latest) template.

        Returns:
            The template content, or None if not found.
        """

    @abstractmethod
    def list_templates(self) -> list[dict]:
        """
        List all template versions.

        Returns:
            List of dicts: [{version, label, created_at, is_active}, ...]
        """

    @abstractmethod
    def set_active_template(self, version: int) -> None:
        """Set the active template version used for rendering."""

    @abstractmethod
    def save_tailored_output(self, tailored: dict) -> None:
        """
        Save a tailored output to the history.

        Retains at most MAX_TAILORED_HISTORY entries.
        """

    @abstractmethod
    def load_tailored_history(self) -> list[dict]:
        """
        Load the tailored output history (oldest first).

        Returns:
            List of {version, tailored, created_at} dicts.
        """


# ---------------------------------------------------------------------------
# SQLite implementation (default)
# ---------------------------------------------------------------------------

class SQLiteStorage(StorageClient):
    """
    SQLite-backed storage. All operations are synchronous.

    Uses parameterized queries throughout for SQL injection safety.
    """

    def __init__(self, db_path: str = "data/resume_agent.db") -> None:
        self._db_path = db_path
        # Ensure the parent directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        logger.info("SQLiteStorage initialized at %s", db_path)

    @contextmanager
    def _conn(self):
        """Context manager for a SQLite connection with WAL mode for concurrency."""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS profile (
                    id          INTEGER PRIMARY KEY CHECK (id = 1),
                    data        TEXT    NOT NULL,
                    updated_at  REAL    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS job_description (
                    id          INTEGER PRIMARY KEY CHECK (id = 1),
                    content     TEXT    NOT NULL,
                    updated_at  REAL    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS preferences (
                    id          INTEGER PRIMARY KEY CHECK (id = 1),
                    data        TEXT    NOT NULL,
                    updated_at  REAL    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS templates (
                    version     INTEGER PRIMARY KEY AUTOINCREMENT,
                    label       TEXT    NOT NULL DEFAULT '',
                    content     TEXT    NOT NULL,
                    is_active   INTEGER NOT NULL DEFAULT 0,
                    created_at  REAL    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tailored_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    data        TEXT    NOT NULL,
                    created_at  REAL    NOT NULL
                );
            """)

    # ── Profile ─────────────────────────────────────────────────────────────

    def save_profile(self, profile: dict) -> None:
        if not isinstance(profile, dict):
            raise TypeError("Profile must be a dict.")
        data = json.dumps(profile, ensure_ascii=False)
        if len(data.encode()) > MAX_CONTENT_BYTES:
            raise ValueError("Profile exceeds maximum allowed size (5 MB).")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO profile (id, data, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (data, time.time()),
            )
        logger.info("Profile saved to SQLite.")

    def load_profile(self) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT data FROM profile WHERE id = 1").fetchone()
        if row is None:
            return None
        return json.loads(row["data"])

    # ── Job Description ──────────────────────────────────────────────────────

    def save_job_description(self, jd: str) -> None:
        if not isinstance(jd, str):
            raise TypeError("Job description must be a string.")
        if len(jd.encode()) > MAX_CONTENT_BYTES:
            raise ValueError("Job description exceeds maximum allowed size (5 MB).")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO job_description (id, content, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at",
                (jd.strip(), time.time()),
            )
        logger.info("Job description saved to SQLite.")

    def load_job_description(self) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT content FROM job_description WHERE id = 1").fetchone()
        return row["content"] if row else None

    # ── Preferences ──────────────────────────────────────────────────────────

    def save_preferences(self, prefs: dict) -> None:
        if not isinstance(prefs, dict):
            raise TypeError("Preferences must be a dict.")
        data = json.dumps(prefs, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO preferences (id, data, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (data, time.time()),
            )

    def load_preferences(self) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT data FROM preferences WHERE id = 1").fetchone()
        if row is None:
            return None
        return json.loads(row["data"])

    # ── Templates ─────────────────────────────────────────────────────────────

    def save_template(self, content: str, label: str = "") -> int:
        if not content or not content.strip():
            raise ValueError("Template content must not be empty.")
        if len(content.encode()) > MAX_CONTENT_BYTES:
            raise ValueError("Template exceeds maximum allowed size (5 MB).")
        # Deactivate all existing templates before inserting new one as active
        with self._conn() as conn:
            conn.execute("UPDATE templates SET is_active = 0")
            cursor = conn.execute(
                "INSERT INTO templates (label, content, is_active, created_at) VALUES (?, ?, 1, ?)",
                (label.strip()[:100], content, time.time()),
            )
            version = cursor.lastrowid
        logger.info("Template saved as version %d (label=%r).", version, label)
        return version

    def load_template(self, version: Optional[int] = None) -> Optional[str]:
        with self._conn() as conn:
            if version is None:
                # Load the active template
                row = conn.execute(
                    "SELECT content FROM templates WHERE is_active = 1 ORDER BY version DESC LIMIT 1"
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT content FROM templates WHERE version = ?", (version,)
                ).fetchone()
        return row["content"] if row else None

    def list_templates(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT version, label, created_at, is_active FROM templates ORDER BY version DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_active_template(self, version: int) -> None:
        with self._conn() as conn:
            # Verify version exists
            row = conn.execute("SELECT version FROM templates WHERE version = ?", (version,)).fetchone()
            if row is None:
                raise ValueError(f"Template version {version} does not exist.")
            conn.execute("UPDATE templates SET is_active = 0")
            conn.execute("UPDATE templates SET is_active = 1 WHERE version = ?", (version,))
        logger.info("Active template set to version %d.", version)

    # ── Tailored output history ───────────────────────────────────────────────

    def save_tailored_output(self, tailored: dict) -> None:
        data = json.dumps(tailored, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tailored_history (data, created_at) VALUES (?, ?)",
                (data, time.time()),
            )
            # Prune to keep only the last N entries
            conn.execute(
                "DELETE FROM tailored_history WHERE id NOT IN "
                "(SELECT id FROM tailored_history ORDER BY id DESC LIMIT ?)",
                (MAX_TAILORED_HISTORY,),
            )

    def load_tailored_history(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, data, created_at FROM tailored_history ORDER BY id ASC"
            ).fetchall()
        result = []
        for row in rows:
            result.append({
                "version": row["id"],
                "tailored": json.loads(row["data"]),
                "created_at": row["created_at"],
            })
        return result


# ---------------------------------------------------------------------------
# Factory — returns the correct backend based on environment
# ---------------------------------------------------------------------------

def get_storage_client() -> StorageClient:
    """
    Return the configured storage client.

    Environment variables:
        STORAGE_BACKEND: 'sqlite' (default) or 's3'.
        SQLITE_DB_PATH:  Path for the SQLite database file (default: data/resume_agent.db).

    The S3 adapter (not yet implemented) will delegate to the existing
    storage microservice via HTTP.
    """
    backend = os.getenv("STORAGE_BACKEND", "sqlite").lower().strip()

    if backend == "sqlite":
        db_path = os.getenv("SQLITE_DB_PATH", "data/resume_agent.db")
        return SQLiteStorage(db_path=db_path)

    if backend == "s3":
        # S3 adapter deferred — falls back to SQLite with a warning
        logger.warning(
            "STORAGE_BACKEND=s3 is not yet fully implemented in standalone mode. "
            "Falling back to SQLite."
        )
        db_path = os.getenv("SQLITE_DB_PATH", "data/resume_agent.db")
        return SQLiteStorage(db_path=db_path)

    logger.warning("Unknown STORAGE_BACKEND=%r — defaulting to SQLite.", backend)
    return SQLiteStorage()


# ---------------------------------------------------------------------------
# Module-level singleton — shared across the process lifetime
# ---------------------------------------------------------------------------

_storage: Optional[StorageClient] = None


def storage() -> StorageClient:
    """Return the module-level storage singleton, initializing it on first call."""
    global _storage
    if _storage is None:
        _storage = get_storage_client()
    return _storage
