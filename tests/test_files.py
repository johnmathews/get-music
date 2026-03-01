"""Tests for local file/directory processing and scp transfer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock, call
import subprocess

import pytest

from gm.files import (
    find_audio_files,
    find_video_files,
    is_video_file,
    is_audio_file,
    build_scp_command,
    extract_audio_from_video,
    handle_file,
    handle_directory,
    scp_transfer,
    ssh_mkdir,
    SCP_HOST,
)


class TestFileDetection:
    """Test audio/video file type detection."""

    @pytest.mark.parametrize("name", [
        "song.mp3", "track.flac", "audio.ogg", "music.m4a",
        "sound.wav", "tune.opus", "audio.aac", "song.wma",
    ])
    def test_detects_audio_files(self, name: str) -> None:
        assert is_audio_file(Path(name))

    @pytest.mark.parametrize("name", [
        "video.mp4", "clip.mkv", "movie.avi", "vid.webm", "clip.mov",
    ])
    def test_detects_video_files(self, name: str) -> None:
        assert is_video_file(Path(name))

    def test_non_media_file(self) -> None:
        assert not is_audio_file(Path("readme.txt"))
        assert not is_video_file(Path("readme.txt"))


class TestFindFiles:
    """Test file discovery in directories."""

    def test_finds_audio_files(self, tmp_path: Path) -> None:
        (tmp_path / "song.mp3").write_bytes(b"\x00")
        (tmp_path / "track.flac").write_bytes(b"\x00")
        (tmp_path / "readme.txt").write_bytes(b"\x00")
        files = find_audio_files(tmp_path, recursive=False)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"song.mp3", "track.flac"}

    def test_finds_video_files(self, tmp_path: Path) -> None:
        (tmp_path / "clip.mp4").write_bytes(b"\x00")
        (tmp_path / "readme.txt").write_bytes(b"\x00")
        files = find_video_files(tmp_path, recursive=False)
        assert len(files) == 1
        assert files[0].name == "clip.mp4"

    def test_recursive_search(self, tmp_path: Path) -> None:
        sub = tmp_path / "subdir"
        sub.mkdir()
        (tmp_path / "top.mp3").write_bytes(b"\x00")
        (sub / "nested.flac").write_bytes(b"\x00")
        files = find_audio_files(tmp_path, recursive=True)
        assert len(files) == 2

    def test_non_recursive_skips_subdirs(self, tmp_path: Path) -> None:
        sub = tmp_path / "subdir"
        sub.mkdir()
        (tmp_path / "top.mp3").write_bytes(b"\x00")
        (sub / "nested.flac").write_bytes(b"\x00")
        files = find_audio_files(tmp_path, recursive=False)
        assert len(files) == 1
        assert files[0].name == "top.mp3"


class TestBuildScpCommand:
    """Test scp command construction."""

    def test_builds_scp_command(self) -> None:
        cmd = build_scp_command(
            Path("/local/song.mp3"),
            "/mnt/nfs/music/Artist/Album/Song.mp3",
        )
        assert cmd[0] == "scp"
        assert str(Path("/local/song.mp3")) in cmd
        assert f"{SCP_HOST}:/mnt/nfs/music/Artist/Album/Song.mp3" in cmd


class TestScpTransfer:
    """Test scp file transfer."""

    @patch("gm.files.subprocess.run")
    def test_successful_transfer(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        scp_transfer(tmp_path / "song.mp3", "/mnt/nfs/music/A/B/C.mp3")
        mock_run.assert_called_once()

    @patch("gm.files.subprocess.run")
    def test_raises_on_failure(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", "permission denied")
        with pytest.raises(RuntimeError, match="scp failed: permission denied"):
            scp_transfer(tmp_path / "song.mp3", "/mnt/nfs/music/A/B/C.mp3")


class TestSshMkdir:
    """Test remote directory creation."""

    @patch("gm.files.ssh_run")
    def test_calls_mkdir(self, mock_ssh: MagicMock) -> None:
        ssh_mkdir("/mnt/nfs/music/Artist/Album")
        mock_ssh.assert_called_once_with("mkdir -p /mnt/nfs/music/Artist/Album", check=True)

    @patch("gm.files.ssh_run")
    def test_calls_mkdir_with_special_chars(self, mock_ssh: MagicMock) -> None:
        ssh_mkdir("/mnt/nfs/music/It's-Art/Album")
        # quote_path should safely escape the single quote
        call_cmd = mock_ssh.call_args[0][0]
        assert "mkdir -p" in call_cmd
        assert "It" in call_cmd


class TestExtractAudio:
    """Test audio extraction from video files."""

    @patch("gm.files.subprocess.run")
    def test_extracts_audio(self, mock_run: MagicMock, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00")
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")

        result = extract_audio_from_video(video)
        assert result.suffix == ".mp3"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "ffmpeg" in cmd

    @patch("gm.files.subprocess.run")
    def test_raises_on_failure(self, mock_run: MagicMock, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00")
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", "codec error")
        with pytest.raises(RuntimeError, match="ffmpeg failed: codec error"):
            extract_audio_from_video(video)


@patch("gm.files.find_genre_by_artist", return_value="")
@patch("gm.files.write_metadata")
class TestHandleFile:
    """Test the full single-file processing flow."""

    @patch("gm.files.record_import")
    @patch("gm.files.check_destination_exists", return_value=False)
    @patch("gm.files.find_by_hash", return_value=[])
    @patch("gm.files.compute_file_hash", return_value="fakehash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_metadata")
    @patch("gm.files.read_metadata")
    def test_handles_audio_file(
        self,
        mock_read: MagicMock,
        mock_prompt: MagicMock,
        mock_mkdir: MagicMock,
        mock_scp: MagicMock,
        mock_hash: MagicMock,
        mock_find_hash: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata

        f = tmp_path / "song.mp3"
        f.write_bytes(b"\x00")

        mock_read.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")
        mock_prompt.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")

        handle_file(f)

        mock_read.assert_called_once_with(f)
        mock_prompt.assert_called_once()
        mock_mkdir.assert_called_once()
        mock_scp.assert_called_once()
        mock_record.assert_called_once()
        record = mock_record.call_args[0][0]
        assert record.file_hash == "fakehash"
        assert record.artist == "Artist"

    @patch("gm.files.record_import")
    @patch("gm.files.check_destination_exists", return_value=False)
    @patch("gm.files.find_by_hash", return_value=[])
    @patch("gm.files.compute_file_hash", return_value="fakehash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_metadata")
    @patch("gm.files.read_metadata")
    @patch("gm.files.extract_audio_from_video")
    def test_handles_video_file(
        self,
        mock_extract: MagicMock,
        mock_read: MagicMock,
        mock_prompt: MagicMock,
        mock_mkdir: MagicMock,
        mock_scp: MagicMock,
        mock_hash: MagicMock,
        mock_find_hash: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata

        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00")
        extracted = tmp_path / "video.mp3"
        extracted.write_bytes(b"\x00")

        mock_extract.return_value = extracted
        mock_read.return_value = AudioMetadata(artist="Artist", album="Album", title="Video")
        mock_prompt.return_value = AudioMetadata(artist="Artist", album="Album", title="Video")

        handle_file(f)

        mock_extract.assert_called_once_with(f)
        mock_scp.assert_called_once()
        mock_record.assert_called_once()

    @patch("gm.files.record_import")
    @patch("gm.files.check_destination_exists", return_value=False)
    @patch("gm.files.find_by_hash")
    @patch("gm.files.compute_file_hash", return_value="duphash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_duplicate_action", return_value="skip")
    @patch("gm.files.prompt_metadata")
    @patch("gm.files.read_metadata")
    def test_skips_duplicate_by_hash(
        self,
        mock_read: MagicMock,
        mock_prompt: MagicMock,
        mock_dup_action: MagicMock,
        mock_mkdir: MagicMock,
        mock_scp: MagicMock,
        mock_hash: MagicMock,
        mock_find_hash: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata
        from gm.history import ImportRecord

        f = tmp_path / "song.mp3"
        f.write_bytes(b"\x00")

        mock_read.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")
        mock_prompt.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")
        mock_find_hash.return_value = [ImportRecord(destination="/mnt/nfs/music/Artist/Album/Song.mp3")]

        handle_file(f)

        mock_dup_action.assert_called_once()
        mock_scp.assert_not_called()
        mock_record.assert_not_called()

    def test_skips_unsupported_file(self, mock_write_meta: MagicMock, mock_find_genre: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        f = tmp_path / "readme.txt"
        f.write_bytes(b"\x00")
        handle_file(f)
        captured = capsys.readouterr()
        assert "Skipping unsupported file" in captured.out

    @patch("gm.files.record_import")
    @patch("gm.files.check_destination_exists", return_value=False)
    @patch("gm.files.find_by_hash", return_value=[])
    @patch("gm.files.compute_file_hash", return_value="fakehash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_title_only")
    @patch("gm.files.read_metadata")
    def test_handles_batch_meta(
        self,
        mock_read: MagicMock,
        mock_title: MagicMock,
        mock_mkdir: MagicMock,
        mock_scp: MagicMock,
        mock_hash: MagicMock,
        mock_find_hash: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata

        f = tmp_path / "song.mp3"
        f.write_bytes(b"\x00")

        batch = AudioMetadata(artist="Artist", album="Album", genre="Rock")
        mock_read.return_value = AudioMetadata(title="Song")
        mock_title.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")

        handle_file(f, batch_meta=batch, track_number=3)

        mock_title.assert_called_once_with(mock_read.return_value, batch, 3)
        mock_scp.assert_called_once()

    @patch("gm.files.record_import")
    @patch("gm.files.check_destination_exists", return_value=True)
    @patch("gm.files.find_by_hash", return_value=[])
    @patch("gm.files.compute_file_hash", return_value="newhash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_duplicate_action", return_value="overwrite")
    @patch("gm.files.prompt_metadata")
    @patch("gm.files.read_metadata")
    def test_overwrites_duplicate_by_dest(
        self,
        mock_read: MagicMock,
        mock_prompt: MagicMock,
        mock_dup_action: MagicMock,
        mock_mkdir: MagicMock,
        mock_scp: MagicMock,
        mock_hash: MagicMock,
        mock_find_hash: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata

        f = tmp_path / "song.mp3"
        f.write_bytes(b"\x00")

        mock_read.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")
        mock_prompt.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")

        handle_file(f)

        mock_dup_action.assert_called_once()
        mock_scp.assert_called_once()
        mock_record.assert_called_once()

    @patch("gm.files.record_import")
    @patch("gm.files.check_destination_exists", return_value=True)
    @patch("gm.files.find_by_hash", return_value=[])
    @patch("gm.files.compute_file_hash", return_value="newhash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_duplicate_action", return_value="rename")
    @patch("gm.files.prompt_metadata")
    @patch("gm.files.read_metadata")
    def test_rename_reprompts_metadata(
        self,
        mock_read: MagicMock,
        mock_prompt: MagicMock,
        mock_dup_action: MagicMock,
        mock_mkdir: MagicMock,
        mock_scp: MagicMock,
        mock_hash: MagicMock,
        mock_find_hash: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata

        f = tmp_path / "song.mp3"
        f.write_bytes(b"\x00")

        first_meta = AudioMetadata(artist="Artist", album="Album", title="Song")
        renamed_meta = AudioMetadata(artist="Artist", album="Other-Album", title="New-Song")
        mock_read.return_value = first_meta
        mock_prompt.side_effect = [first_meta, renamed_meta]

        handle_file(f)

        # prompt_metadata called twice: initial + rename re-prompt
        assert mock_prompt.call_count == 2
        # Final destination should use renamed metadata
        record = mock_record.call_args[0][0]
        assert record.title == "New-Song"
        assert record.album == "Other-Album"


class TestHandleDirectory:
    """Test directory processing."""

    @patch("gm.files.handle_file")
    @patch("gm.files.prompt_batch_metadata")
    @patch("builtins.input", return_value="n")
    def test_non_recursive(
        self,
        mock_input: MagicMock,
        mock_batch: MagicMock,
        mock_handle: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata

        mock_batch.return_value = AudioMetadata(artist="Artist", album="Album")
        (tmp_path / "song.mp3").write_bytes(b"\x00")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.flac").write_bytes(b"\x00")

        handle_directory(tmp_path)

        mock_batch.assert_called_once()
        assert mock_handle.call_count == 1
        # Verify batch_meta and track_number were passed
        _, kwargs = mock_handle.call_args
        assert kwargs["batch_meta"].artist == "Artist"
        assert kwargs["track_number"] == 1

    @patch("gm.files.handle_file")
    @patch("gm.files.prompt_batch_metadata")
    @patch("builtins.input", return_value="y")
    def test_recursive(
        self,
        mock_input: MagicMock,
        mock_batch: MagicMock,
        mock_handle: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata

        mock_batch.return_value = AudioMetadata(artist="Artist", album="Album")
        (tmp_path / "song.mp3").write_bytes(b"\x00")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.flac").write_bytes(b"\x00")

        handle_directory(tmp_path)

        mock_batch.assert_called_once()
        assert mock_handle.call_count == 2
        # Second file should have track_number=2
        assert mock_handle.call_args_list[1].kwargs["track_number"] == 2

    @patch("gm.files.handle_file")
    @patch("builtins.input", return_value="n")
    def test_no_files_found(
        self,
        mock_input: MagicMock,
        mock_handle: MagicMock,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "readme.txt").write_bytes(b"\x00")
        handle_directory(tmp_path)
        mock_handle.assert_not_called()

    @patch("gm.files.handle_file")
    @patch("gm.files.prompt_batch_metadata")
    @patch("builtins.input", return_value="n")
    def test_continues_after_file_error(
        self,
        mock_input: MagicMock,
        mock_batch: MagicMock,
        mock_handle: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from gm.metadata import AudioMetadata

        mock_batch.return_value = AudioMetadata(artist="Artist", album="Album")
        (tmp_path / "a.mp3").write_bytes(b"\x00")
        (tmp_path / "b.mp3").write_bytes(b"\x00")
        (tmp_path / "c.mp3").write_bytes(b"\x00")

        mock_handle.side_effect = [None, RuntimeError("scp failed"), None]
        handle_directory(tmp_path)

        assert mock_handle.call_count == 3
        captured = capsys.readouterr()
        assert "scp failed" in captured.out
        assert "1 file(s) failed" in captured.out
        assert "2/3 file(s) processed" in captured.out

    @patch("gm.files.handle_file")
    @patch("gm.files.prompt_batch_metadata")
    @patch("builtins.input", return_value="n")
    def test_reports_all_failures(
        self,
        mock_input: MagicMock,
        mock_batch: MagicMock,
        mock_handle: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from gm.metadata import AudioMetadata

        mock_batch.return_value = AudioMetadata(artist="Artist", album="Album")
        (tmp_path / "a.mp3").write_bytes(b"\x00")
        (tmp_path / "b.mp3").write_bytes(b"\x00")

        mock_handle.side_effect = RuntimeError("boom")
        handle_directory(tmp_path)

        assert mock_handle.call_count == 2
        captured = capsys.readouterr()
        assert "2 file(s) failed" in captured.out
        assert "0/2 file(s) processed" in captured.out
