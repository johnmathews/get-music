"""Tests for import log (SQLite history)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import gm.history as history
from gm.history import (
    ImportRecord,
    record_import,
    find_by_video_id,
    find_by_hash,
    find_by_destination,
    find_genre_by_artist,
    recent_imports,
    compute_file_hash,
    format_log,
    delete_import,
    all_imports,
)


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point DB_PATH to a temp directory for every test."""
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "imports.db")


class TestRecordAndFind:
    """Test recording and querying imports."""

    def test_record_and_find_by_video_id(self) -> None:
        record_import(ImportRecord(
            source="https://youtube.com/watch?v=abc123",
            artist="Artist", album="Album", title="Song",
            destination="/mnt/nfs/music/Artist/Album/Song-[abc123].opus",
            video_id="abc123",
        ))
        results = find_by_video_id("abc123")
        assert len(results) == 1
        assert results[0].artist == "Artist"
        assert results[0].video_id == "abc123"

    def test_find_by_video_id_empty_returns_empty(self) -> None:
        assert find_by_video_id("") == []

    def test_find_by_video_id_no_match(self) -> None:
        assert find_by_video_id("nonexistent") == []

    def test_record_and_find_by_hash(self) -> None:
        record_import(ImportRecord(
            source="/local/song.mp3",
            artist="Artist", album="Album", title="Song",
            destination="/mnt/nfs/music/Artist/Album/Song.mp3",
            file_hash="abc123hash",
        ))
        results = find_by_hash("abc123hash")
        assert len(results) == 1
        assert results[0].file_hash == "abc123hash"

    def test_find_by_hash_empty_returns_empty(self) -> None:
        assert find_by_hash("") == []

    def test_record_and_find_by_destination(self) -> None:
        dest = "/mnt/nfs/music/Artist/Album/Song.mp3"
        record_import(ImportRecord(
            source="/local/song.mp3", destination=dest,
        ))
        results = find_by_destination(dest)
        assert len(results) == 1
        assert results[0].destination == dest

    def test_find_by_destination_empty_returns_empty(self) -> None:
        assert find_by_destination("") == []


class TestDeleteImport:
    """Test deleting import records by destination."""

    def test_deletes_existing_record(self) -> None:
        dest = "/mnt/nfs/music/Artist/Album/Song.mp3"
        record_import(ImportRecord(source="/local/song.mp3", destination=dest))
        assert len(find_by_destination(dest)) == 1
        delete_import(dest)
        assert find_by_destination(dest) == []

    def test_noop_for_empty_destination(self) -> None:
        record_import(ImportRecord(source="s1", destination="/some/path"))
        delete_import("")
        assert len(all_imports()) == 1

    def test_noop_for_nonexistent_destination(self) -> None:
        record_import(ImportRecord(source="s1", destination="/some/path"))
        delete_import("/nonexistent")
        assert len(all_imports()) == 1


class TestAllImports:
    """Test retrieving all import records."""

    def test_returns_all_records_in_order(self) -> None:
        for i in range(3):
            record_import(ImportRecord(source=f"s{i}", title=f"Song-{i}"))
        results = all_imports()
        assert len(results) == 3
        assert results[0].title == "Song-0"
        assert results[2].title == "Song-2"

    def test_empty_db(self) -> None:
        assert all_imports() == []


class TestFindGenreByArtist:
    """Test genre lookup by artist."""

    def test_returns_genre_for_known_artist(self) -> None:
        record_import(ImportRecord(
            source="s1", artist="Miles Davis", title="So What", genre="Jazz",
        ))
        assert find_genre_by_artist("Miles Davis") == "Jazz"

    def test_returns_most_recent_genre(self) -> None:
        record_import(ImportRecord(
            source="s1", artist="Artist", title="Old", genre="Rock",
        ))
        record_import(ImportRecord(
            source="s2", artist="Artist", title="New", genre="Electronic",
        ))
        assert find_genre_by_artist("Artist") == "Electronic"

    def test_skips_empty_genre(self) -> None:
        record_import(ImportRecord(
            source="s1", artist="Artist", title="With Genre", genre="Jazz",
        ))
        record_import(ImportRecord(
            source="s2", artist="Artist", title="No Genre", genre="",
        ))
        assert find_genre_by_artist("Artist") == "Jazz"

    def test_skips_generic_music_genre(self) -> None:
        record_import(ImportRecord(
            source="s1", artist="Artist", title="Song", genre="Music",
        ))
        assert find_genre_by_artist("Artist") == ""

    def test_returns_empty_for_unknown_artist(self) -> None:
        assert find_genre_by_artist("Unknown") == ""

    def test_returns_empty_for_empty_artist(self) -> None:
        assert find_genre_by_artist("") == ""


class TestRecentImports:
    """Test recent imports query."""

    def test_returns_newest_first(self) -> None:
        for i in range(3):
            record_import(ImportRecord(
                timestamp=f"2024-01-0{i + 1}T00:00:00",
                source=f"source-{i}", title=f"Song-{i}",
            ))
        results = recent_imports(limit=3)
        assert len(results) == 3
        assert results[0].title == "Song-2"
        assert results[2].title == "Song-0"

    def test_respects_limit(self) -> None:
        for i in range(5):
            record_import(ImportRecord(source=f"s-{i}", title=f"Song-{i}"))
        results = recent_imports(limit=2)
        assert len(results) == 2

    def test_empty_db(self) -> None:
        assert recent_imports() == []


class TestComputeFileHash:
    """Test file hashing."""

    def test_computes_sha256(self, tmp_path: Path) -> None:
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        h = compute_file_hash(f)
        assert len(h) == 64  # SHA-256 hex digest
        # Known SHA-256 of "hello world"
        assert h == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"content A")
        f2.write_bytes(b"content B")
        assert compute_file_hash(f1) != compute_file_hash(f2)


class TestFormatLog:
    """Test log formatting."""

    def test_empty_list(self) -> None:
        assert format_log([]) == "No imports found."

    def test_formats_records(self) -> None:
        records = [
            ImportRecord(
                timestamp="2024-01-15T10:30:00+00:00",
                artist="Artist", album="Album", title="Song",
                video_id="abc123",
            ),
        ]
        output = format_log(records)
        assert "2024-01-15T10:30:00" in output
        assert "Artist" in output
        assert "Album" in output
        assert "Song" in output
        assert "[abc123]" in output

    def test_formats_without_video_id(self) -> None:
        records = [
            ImportRecord(
                timestamp="2024-01-15T10:30:00+00:00",
                artist="Artist", title="Song",
            ),
        ]
        output = format_log(records)
        assert "[" not in output


class TestTimestampAutoFill:
    """Test that timestamp is auto-filled when empty."""

    def test_auto_fills_timestamp(self) -> None:
        record_import(ImportRecord(source="test", title="Song"))
        results = recent_imports(limit=1)
        assert results[0].timestamp != ""
        assert "T" in results[0].timestamp  # ISO format
