"""Tests for CLI argument parsing and input routing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from gm.cli import (
    InputType,
    detect_input_type,
    get_help_text,
    main,
)


class TestDetectInputType:
    """Test input type detection from CLI arguments."""

    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/abc123",
        "https://music.youtube.com/watch?v=abc123",
        "http://youtube.com/watch?v=abc123",
    ])
    def test_detects_youtube_urls(self, url: str) -> None:
        assert detect_input_type(url) == InputType.YOUTUBE_URL

    def test_detects_file(self, tmp_path: Path) -> None:
        f = tmp_path / "song.mp3"
        f.write_bytes(b"\x00")
        assert detect_input_type(str(f)) == InputType.FILE

    def test_detects_directory(self, tmp_path: Path) -> None:
        assert detect_input_type(str(tmp_path)) == InputType.DIRECTORY

    def test_nonexistent_path_raises(self) -> None:
        with pytest.raises(SystemExit):
            detect_input_type("/nonexistent/path/to/file.mp3")

    def test_non_youtube_url_raises(self) -> None:
        with pytest.raises(SystemExit):
            detect_input_type("https://example.com/video")


class TestHelp:
    """Test help text output."""

    def test_help_text_contains_usage(self) -> None:
        text = get_help_text()
        assert "gm" in text
        assert "youtube" in text.lower() or "url" in text.lower()
        assert "directory" in text.lower()
        assert "file" in text.lower()
        assert "log" in text.lower()

    def test_main_help_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "gm" in captured.out


class TestMainRouting:
    """Test that main routes to the correct handler."""

    def test_no_args_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "gm" in captured.out

    @patch("gm.cli.handle_youtube")
    def test_routes_youtube_url(self, mock_handler: MagicMock) -> None:
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        main([url])
        mock_handler.assert_called_once_with(url)

    @patch("gm.cli.handle_file")
    def test_routes_file(self, mock_handler: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "song.mp3"
        f.write_bytes(b"\x00")
        main([str(f)])
        mock_handler.assert_called_once_with(Path(str(f)))

    @patch("gm.cli.handle_directory")
    def test_routes_directory(self, mock_handler: MagicMock, tmp_path: Path) -> None:
        main([str(tmp_path)])
        mock_handler.assert_called_once_with(Path(str(tmp_path)))


class TestLogSubcommand:
    """Test gm log subcommand."""

    @patch("gm.history.recent_imports", return_value=[])
    def test_log_default(
        self,
        mock_recent: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        main(["log"])
        mock_recent.assert_called_once_with(limit=20)
        captured = capsys.readouterr()
        assert "No imports found" in captured.out

    @patch("gm.history.recent_imports", return_value=[])
    def test_log_with_limit(
        self,
        mock_recent: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        main(["log", "5"])
        mock_recent.assert_called_once_with(limit=5)


class TestPruneSubcommand:
    """Test gm prune subcommand."""

    @patch("gm.metadata.check_destination_exists", return_value=True)
    @patch("gm.history.all_imports")
    def test_prune_no_stale_records(
        self,
        mock_all: MagicMock,
        mock_check: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from gm.history import ImportRecord

        mock_all.return_value = [
            ImportRecord(destination="/mnt/nfs/music/Artist/Album/Song.mp3"),
        ]

        main(["prune"])

        captured = capsys.readouterr()
        assert "Pruned 0 stale record(s) out of 1 total." in captured.out

    @patch("gm.history.delete_import")
    @patch("gm.metadata.check_destination_exists", return_value=False)
    @patch("gm.history.all_imports")
    def test_prune_deletes_stale_records(
        self,
        mock_all: MagicMock,
        mock_check: MagicMock,
        mock_delete: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from gm.history import ImportRecord

        mock_all.return_value = [
            ImportRecord(destination="/mnt/nfs/music/Artist/Album/Song.mp3"),
            ImportRecord(destination="/mnt/nfs/music/Artist/Album/Song2.mp3"),
        ]

        main(["prune"])

        assert mock_delete.call_count == 2
        captured = capsys.readouterr()
        assert "Stale: /mnt/nfs/music/Artist/Album/Song.mp3" in captured.out
        assert "Stale: /mnt/nfs/music/Artist/Album/Song2.mp3" in captured.out
        assert "Pruned 2 stale record(s) out of 2 total." in captured.out

    @patch("gm.metadata.check_destination_exists")
    @patch("gm.history.all_imports")
    def test_prune_skips_empty_destination(
        self,
        mock_all: MagicMock,
        mock_check: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from gm.history import ImportRecord

        mock_all.return_value = [
            ImportRecord(destination=""),
        ]

        main(["prune"])

        mock_check.assert_not_called()
        captured = capsys.readouterr()
        assert "Pruned 0 stale record(s) out of 1 total." in captured.out
