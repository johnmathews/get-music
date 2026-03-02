"""SQLite import log for tracking music imports."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from gm.ui import bold, cyan, dim

DB_PATH = Path("~/.local/share/gm/imports.db").expanduser()

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    artist TEXT NOT NULL DEFAULT '',
    album TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    destination TEXT NOT NULL DEFAULT '',
    file_hash TEXT NOT NULL DEFAULT '',
    video_id TEXT NOT NULL DEFAULT '',
    genre TEXT NOT NULL DEFAULT ''
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_video_id ON imports (video_id)",
    "CREATE INDEX IF NOT EXISTS idx_file_hash ON imports (file_hash)",
    "CREATE INDEX IF NOT EXISTS idx_destination ON imports (destination)",
    "CREATE INDEX IF NOT EXISTS idx_artist ON imports (artist)",
]

_MIGRATIONS = [
    "ALTER TABLE imports ADD COLUMN genre TEXT NOT NULL DEFAULT ''",
]


@dataclass
class ImportRecord:
    timestamp: str = ""
    source: str = ""
    artist: str = ""
    album: str = ""
    title: str = ""
    destination: str = ""
    file_hash: str = ""
    video_id: str = ""
    genre: str = ""


def _get_connection() -> sqlite3.Connection:
    """Open (and initialize if needed) the import database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(_CREATE_TABLE)
    for migration in _MIGRATIONS:
        try:
            conn.execute(migration)
        except sqlite3.OperationalError:
            pass  # Column/index already exists
    for idx in _CREATE_INDEXES:
        conn.execute(idx)
    conn.commit()
    return conn


def record_import(record: ImportRecord) -> None:
    """Insert an import record into the database."""
    if not record.timestamp:
        record.timestamp = datetime.now(timezone.utc).isoformat()
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO imports (timestamp, source, artist, album, title, "
            "destination, file_hash, video_id, genre) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.timestamp,
                record.source,
                record.artist,
                record.album,
                record.title,
                record.destination,
                record.file_hash,
                record.video_id,
                record.genre,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def find_by_video_id(video_id: str) -> list[ImportRecord]:
    """Look up imports by YouTube video ID."""
    if not video_id:
        return []
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT timestamp, source, artist, album, title, destination, "
            "file_hash, video_id, genre FROM imports WHERE video_id = ?",
            (video_id,),
        ).fetchall()
        return [_row_to_record(r) for r in rows]
    finally:
        conn.close()


def find_by_hash(file_hash: str) -> list[ImportRecord]:
    """Look up imports by file hash."""
    if not file_hash:
        return []
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT timestamp, source, artist, album, title, destination, "
            "file_hash, video_id, genre FROM imports WHERE file_hash = ?",
            (file_hash,),
        ).fetchall()
        return [_row_to_record(r) for r in rows]
    finally:
        conn.close()


def delete_import(destination: str) -> None:
    """Delete an import record by destination path."""
    if not destination:
        return
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM imports WHERE destination = ?", (destination,))
        conn.commit()
    finally:
        conn.close()


def all_imports() -> list[ImportRecord]:
    """Return all import records."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT timestamp, source, artist, album, title, destination, "
            "file_hash, video_id, genre FROM imports ORDER BY id"
        ).fetchall()
        return [_row_to_record(r) for r in rows]
    finally:
        conn.close()


def find_by_destination(dest: str) -> list[ImportRecord]:
    """Look up imports by destination path."""
    if not dest:
        return []
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT timestamp, source, artist, album, title, destination, "
            "file_hash, video_id, genre FROM imports WHERE destination = ?",
            (dest,),
        ).fetchall()
        return [_row_to_record(r) for r in rows]
    finally:
        conn.close()


def recent_imports(limit: int = 20) -> list[ImportRecord]:
    """Return recent imports, newest first."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT timestamp, source, artist, album, title, destination, "
            "file_hash, video_id, genre FROM imports ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_record(r) for r in rows]
    finally:
        conn.close()


def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hash of a local file."""
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()


def format_log(records: list[ImportRecord]) -> str:
    """Format import records for display."""
    if not records:
        return "No imports found."
    lines: list[str] = []
    for r in records:
        parts = [dim(r.timestamp[:19])]
        if r.artist:
            parts.append(bold(r.artist))
        if r.album:
            parts.append(f"/ {r.album}")
        if r.title:
            parts.append(f"- {cyan(r.title)}")
        if r.video_id:
            parts.append(dim(f"[{r.video_id}]"))
        lines.append("  ".join(parts))
    return "\n".join(lines)


def find_genre_by_artist(artist: str) -> str:
    """Look up the most recent genre used for an artist."""
    if not artist:
        return ""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT genre FROM imports WHERE artist = ? AND genre != '' "
            "ORDER BY id DESC LIMIT 1",
            (artist,),
        ).fetchone()
        if not row:
            return ""
        genre = row[0]
        if genre.lower() == "music":
            return ""
        return genre
    finally:
        conn.close()


def _row_to_record(row: tuple[str, ...]) -> ImportRecord:
    """Convert a database row to an ImportRecord."""
    return ImportRecord(
        timestamp=row[0],
        source=row[1],
        artist=row[2],
        album=row[3],
        title=row[4],
        destination=row[5],
        file_hash=row[6],
        video_id=row[7],
        genre=row[8] if len(row) > 8 else "",
    )
