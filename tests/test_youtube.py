"""Tests for YouTube download via SSH + yt-dlp on LXC."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch, call, MagicMock

import pytest

from gm.metadata import AudioMetadata
from gm.youtube import (
    handle_youtube,
    build_ytdlp_command,
    parse_ytdlp_metadata,
    extract_video_id,
    update_ytdlp,
    verify_thumbnail_embedded,
    _cleanup_stale_temp_dirs,
    _detect_ytdlp_install_method,
    _make_temp_dir,
)
from gm.ssh import SSH_HOST

TEMP_DIR = "/tmp/gm-download-test123"


class TestBuildYtdlpCommand:
    """Test yt-dlp command construction."""

    def test_builds_audio_download_command(self) -> None:
        url = "https://www.youtube.com/watch?v=abc123"
        cmd = build_ytdlp_command(url, TEMP_DIR)
        assert "yt-dlp" in cmd
        assert url in cmd
        assert "--extract-audio" in cmd
        assert "--audio-quality" in cmd
        assert "--embed-metadata" in cmd
        assert "--embed-thumbnail" in cmd
        assert "--write-info-json" in cmd

    def test_does_not_force_audio_format(self) -> None:
        url = "https://www.youtube.com/watch?v=abc123"
        cmd = build_ytdlp_command(url, TEMP_DIR)
        assert "--audio-format" not in cmd

    def test_output_template_uses_temp_dir(self) -> None:
        url = "https://www.youtube.com/watch?v=abc123"
        cmd = build_ytdlp_command(url, TEMP_DIR)
        joined = " ".join(cmd)
        assert TEMP_DIR in joined

    def test_includes_no_playlist_flag(self) -> None:
        url = "https://www.youtube.com/watch?v=abc123&list=PLxyz"
        cmd = build_ytdlp_command(url, TEMP_DIR)
        assert "--no-playlist" in cmd


class TestExtractVideoId:
    """Test YouTube video ID extraction."""

    def test_standard_url(self) -> None:
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_short_url(self) -> None:
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_music_url(self) -> None:
        assert extract_video_id("https://music.youtube.com/watch?v=abc123") == "abc123"

    def test_shorts_url(self) -> None:
        assert extract_video_id("https://www.youtube.com/shorts/abc123") == "abc123"

    def test_url_with_extra_params(self) -> None:
        assert extract_video_id("https://www.youtube.com/watch?v=abc123&list=PLxyz") == "abc123"

    def test_unknown_url_returns_empty(self) -> None:
        assert extract_video_id("https://example.com/video") == ""


class TestParseYtdlpMetadata:
    """Test metadata parsing from yt-dlp JSON output."""

    def test_parses_full_metadata(self) -> None:
        data = {
            "uploader": "Artist Name",
            "title": "Song Title",
            "album": "Album Name",
            "artist": "Real Artist",
            "genre": "Rock",
            "upload_date": "20230415",
            "description": "Official music video",
        }
        meta = parse_ytdlp_metadata(json.dumps(data))
        assert meta.artist == "Real Artist"
        assert meta.album == ""
        assert meta.title == "Song Title"
        assert meta.genre == ""
        assert meta.date == "2023-04-15"
        assert meta.description == "Official music video"

    def test_ignores_genre_from_json(self) -> None:
        data = {"uploader": "Artist", "title": "Song", "genre": "Rock"}
        meta = parse_ytdlp_metadata(json.dumps(data))
        assert meta.genre == ""

    def test_falls_back_to_uploader(self) -> None:
        data = {
            "uploader": "Channel Name",
            "title": "Video Title",
        }
        meta = parse_ytdlp_metadata(json.dumps(data))
        assert meta.artist == "Channel Name"
        assert meta.title == "Video Title"

    def test_does_not_extract_album(self) -> None:
        data = {"uploader": "Artist", "title": "Song", "album": "Some Album"}
        meta = parse_ytdlp_metadata(json.dumps(data))
        assert meta.album == ""

    def test_handles_empty_json(self) -> None:
        meta = parse_ytdlp_metadata("{}")
        assert meta.artist == ""
        assert meta.album == ""
        assert meta.title == ""

    def test_strips_topic_suffix_from_uploader(self) -> None:
        data = {"uploader": "Artist Name - Topic", "title": "Song"}
        meta = parse_ytdlp_metadata(json.dumps(data))
        assert meta.artist == "Artist Name"

    def test_prefers_release_date_over_upload_date(self) -> None:
        data = {
            "uploader": "Artist",
            "title": "Song",
            "release_date": "19690815",
            "upload_date": "20200101",
        }
        meta = parse_ytdlp_metadata(json.dumps(data))
        assert meta.date == "1969-08-15"

    def test_extracts_track_number(self) -> None:
        data = {
            "uploader": "Artist",
            "title": "Song",
            "track_number": 5,
        }
        meta = parse_ytdlp_metadata(json.dumps(data))
        assert meta.track_number == "5"

    def test_handles_invalid_json(self) -> None:
        meta = parse_ytdlp_metadata("not valid json {{{")
        assert meta.album == ""
        assert meta.artist == ""
        assert meta.title == ""


class TestMakeTempDir:
    """Test unique temp directory generation."""

    def test_make_temp_dir_unique(self) -> None:
        dir1 = _make_temp_dir()
        dir2 = _make_temp_dir()
        assert dir1 != dir2
        assert dir1.startswith("/tmp/gm-download-")
        assert dir2.startswith("/tmp/gm-download-")


class TestCleanupStaleTempDirs:
    """Test orphaned temp directory cleanup."""

    @patch("gm.youtube.ssh_run")
    def test_runs_cleanup_command(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 0, "", "")
        _cleanup_stale_temp_dirs()
        mock_ssh.assert_called_once()
        cmd = mock_ssh.call_args[0][0]
        assert "gm-download-" in cmd
        assert "-mmin +30" in cmd
        assert "rm -rf" in cmd


@patch("gm.youtube._cleanup_stale_temp_dirs")
@patch("gm.youtube.verify_thumbnail_embedded", return_value=True)
@patch("gm.youtube.write_metadata_ssh")
@patch("gm.youtube._make_temp_dir", return_value=TEMP_DIR)
class TestHandleYoutube:
    """Test the full YouTube download flow."""

    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=False)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id", return_value=[])
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_metadata")
    def test_full_flow(
        self,
        mock_prompt: MagicMock,
        mock_ssh: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_verify_thumb: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        from gm.metadata import AudioMetadata

        # Mock the SSH calls in sequence:
        # 1. mkdir temp  2. yt-dlp  3. cat info.json  4. find audio
        # 5. find thumbnail  6. mkdir dest  7. mv audio  8. rm -rf temp
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 0, "", ""),  # yt-dlp
            subprocess.CompletedProcess([], 0, json.dumps({
                "uploader": "Channel", "title": "Song", "artist": "Real Artist",
            }), ""),  # cat info.json
            subprocess.CompletedProcess([], 0, f"{TEMP_DIR}/Song.opus\n", ""),  # find audio
            subprocess.CompletedProcess([], 0, "", ""),  # find thumbnail
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir dest
            subprocess.CompletedProcess([], 0, "", ""),  # mv audio
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp
        ]
        mock_prompt.return_value = AudioMetadata(
            artist="Real Artist", album="Song", title="Song"
        )

        handle_youtube("https://www.youtube.com/watch?v=abc123")

        # Verify yt-dlp was called with --embed-metadata
        ytdlp_call_cmd = mock_ssh.call_args_list[1][0][0]
        assert "yt-dlp" in ytdlp_call_cmd
        assert "--embed-metadata" in ytdlp_call_cmd

        # Verify file was moved with video ID in brackets, native extension
        mv_call_cmd = mock_ssh.call_args_list[6][0][0]
        assert "/mnt/nfs/music/youtube/Real Artist/Song/Song-[abc123].opus" in mv_call_cmd

        # Verify metadata was written back to the audio file
        mock_write_meta.assert_called_once()
        write_dest, write_meta = mock_write_meta.call_args[0]
        assert "/mnt/nfs/music/youtube/Real Artist/Song/Song-[abc123].opus" == write_dest
        assert write_meta.album == "Song"

        # Verify import was logged
        mock_record.assert_called_once()
        record = mock_record.call_args[0][0]
        assert record.video_id == "abc123"
        assert record.artist == "Real Artist"

    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=False)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id")
    @patch("gm.youtube.delete_import")
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_metadata")
    def test_stale_video_id_hit_continues(
        self,
        mock_prompt: MagicMock,
        mock_ssh: MagicMock,
        mock_delete: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_verify_thumb: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        from gm.metadata import AudioMetadata
        from gm.history import ImportRecord

        stale_dest = "/mnt/nfs/music/youtube/Artist/Song/Song-[abc123].opus"
        mock_find_vid.return_value = [ImportRecord(destination=stale_dest)]
        # check_destination_exists returns False (file gone) — default from class decorator

        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 0, "", ""),  # yt-dlp
            subprocess.CompletedProcess([], 0, json.dumps({
                "uploader": "Artist", "title": "Song",
            }), ""),  # cat info.json
            subprocess.CompletedProcess([], 0, f"{TEMP_DIR}/Song.opus\n", ""),  # find audio
            subprocess.CompletedProcess([], 0, "", ""),  # find thumbnail
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir dest
            subprocess.CompletedProcess([], 0, "", ""),  # mv audio
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp
        ]
        mock_prompt.return_value = AudioMetadata(
            artist="Artist", album="Song", title="Song"
        )

        handle_youtube("https://www.youtube.com/watch?v=abc123")

        # Stale record deleted, download continues
        mock_delete.assert_called_once_with(stale_dest)
        mock_record.assert_called_once()

    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=True)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id")
    @patch("gm.youtube.prompt_duplicate_action", return_value="skip")
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_metadata")
    def test_skips_duplicate_by_video_id(
        self,
        mock_prompt: MagicMock,
        mock_ssh: MagicMock,
        mock_dup_action: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_verify_thumb: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        from gm.history import ImportRecord

        mock_find_vid.return_value = [
            ImportRecord(destination="/mnt/nfs/music/youtube/Real-Artist/Song/Song-[abc123].opus"),
        ]

        handle_youtube("https://www.youtube.com/watch?v=abc123")

        # Early dup check skips before download — no SSH calls, no metadata prompt
        mock_dup_action.assert_called_once()
        mock_ssh.assert_not_called()
        mock_prompt.assert_not_called()
        mock_record.assert_not_called()

    @patch("gm.youtube.update_ytdlp", return_value=False)
    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=False)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id", return_value=[])
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_metadata")
    def test_exits_on_download_failure(
        self,
        mock_prompt: MagicMock,
        mock_ssh: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_update: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_verify_thumb: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 1, "", ""),  # yt-dlp fails
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp (cleanup)
        ]

        with pytest.raises(SystemExit):
            handle_youtube("https://www.youtube.com/watch?v=abc123")

        # update_ytdlp was attempted
        mock_update.assert_called_once()
        # Temp dir cleaned up
        cleanup_cmd = mock_ssh.call_args_list[2][0][0]
        assert "rm -rf" in cleanup_cmd
        mock_prompt.assert_not_called()

    @patch("gm.youtube.update_ytdlp", return_value=True)
    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=False)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id", return_value=[])
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_metadata")
    def test_retries_after_ytdlp_update(
        self,
        mock_prompt: MagicMock,
        mock_ssh: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_update: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_verify_thumb: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 1, "", ""),  # yt-dlp fails
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp (cleanup)
            # After update_ytdlp succeeds, retry:
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 0, "", ""),  # yt-dlp succeeds
            subprocess.CompletedProcess([], 0, json.dumps({
                "uploader": "Artist", "title": "Song",
            }), ""),  # cat info.json
            subprocess.CompletedProcess([], 0, f"{TEMP_DIR}/Song.opus\n", ""),  # find audio
            subprocess.CompletedProcess([], 0, "", ""),  # find thumbnail
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir dest
            subprocess.CompletedProcess([], 0, "", ""),  # mv audio
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp
        ]
        mock_prompt.return_value = AudioMetadata(
            artist="Artist", album="Song", title="Song"
        )

        handle_youtube("https://www.youtube.com/watch?v=abc123")

        mock_update.assert_called_once()
        mock_record.assert_called_once()

    @patch("gm.youtube.update_ytdlp", return_value=True)
    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=False)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id", return_value=[])
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_metadata")
    def test_exits_when_retry_also_fails(
        self,
        mock_prompt: MagicMock,
        mock_ssh: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_update: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_verify_thumb: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 1, "", ""),  # yt-dlp fails
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp (cleanup)
            # After update_ytdlp succeeds, retry also fails:
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 1, "", ""),  # yt-dlp fails again
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp (cleanup)
        ]

        with pytest.raises(SystemExit):
            handle_youtube("https://www.youtube.com/watch?v=abc123")

        mock_update.assert_called_once()
        mock_prompt.assert_not_called()

    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=False)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id", return_value=[])
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_metadata")
    def test_raises_when_no_audio_found(
        self,
        mock_prompt: MagicMock,
        mock_ssh: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_verify_thumb: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 0, "", ""),  # yt-dlp
            subprocess.CompletedProcess([], 0, json.dumps({
                "uploader": "Channel", "title": "Song",
            }), ""),  # cat info.json
            subprocess.CompletedProcess([], 0, "\n", ""),  # find audio — empty
        ]
        mock_prompt.return_value = AudioMetadata(
            artist="Channel", album="Song", title="Song"
        )

        with pytest.raises(RuntimeError, match="No audio file found"):
            handle_youtube("https://www.youtube.com/watch?v=abc123")

    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=False)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id", return_value=[])
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_metadata")
    def test_moves_thumbnail(
        self,
        mock_prompt: MagicMock,
        mock_ssh: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_verify_thumb: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 0, "", ""),  # yt-dlp
            subprocess.CompletedProcess([], 0, json.dumps({
                "uploader": "Artist", "title": "Song",
            }), ""),  # cat info.json
            subprocess.CompletedProcess([], 0, f"{TEMP_DIR}/Song.opus\n", ""),  # find audio
            subprocess.CompletedProcess([], 0, f"{TEMP_DIR}/Song.jpg\n", ""),  # find thumbnail
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir dest
            subprocess.CompletedProcess([], 0, "", ""),  # mv audio
            subprocess.CompletedProcess([], 0, "", ""),  # mv thumbnail
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp
        ]
        mock_prompt.return_value = AudioMetadata(
            artist="Artist", album="Song", title="Song"
        )

        handle_youtube("https://www.youtube.com/watch?v=abc123")

        # Verify thumbnail was moved to cover.jpg in dest dir
        mv_thumb_cmd = mock_ssh.call_args_list[7][0][0]
        assert "cover.jpg" in mv_thumb_cmd
        assert f"{TEMP_DIR}/Song.jpg" in mv_thumb_cmd

    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=True)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id")
    @patch("gm.youtube.prompt_duplicate_action", return_value="overwrite")
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_metadata")
    def test_early_dup_overwrite_proceeds(
        self,
        mock_prompt: MagicMock,
        mock_ssh: MagicMock,
        mock_dup_action: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_verify_thumb: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Early video_id dup found, user overwrites — download proceeds."""
        from gm.history import ImportRecord

        mock_find_vid.return_value = [
            ImportRecord(destination="/mnt/nfs/music/youtube/Artist/Song/Song-[abc123].opus"),
        ]
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 0, "", ""),  # yt-dlp
            subprocess.CompletedProcess([], 0, json.dumps({
                "uploader": "Artist", "title": "Song",
            }), ""),  # cat info.json
            subprocess.CompletedProcess([], 0, f"{TEMP_DIR}/Song.opus\n", ""),  # find audio
            subprocess.CompletedProcess([], 0, "", ""),  # find thumbnail
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir dest
            subprocess.CompletedProcess([], 0, "", ""),  # mv audio
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp
        ]
        mock_prompt.return_value = AudioMetadata(
            artist="Artist", album="Song", title="Song"
        )

        handle_youtube("https://www.youtube.com/watch?v=abc123")

        mock_dup_action.assert_called_once()
        mock_record.assert_called_once()

    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=True)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id", return_value=[])
    @patch("gm.youtube.prompt_duplicate_action", return_value="skip")
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_metadata")
    def test_late_dest_skip(
        self,
        mock_prompt: MagicMock,
        mock_ssh: MagicMock,
        mock_dup_action: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_verify_thumb: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """No early dup, but dest exists after download — user skips."""
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 0, "", ""),  # yt-dlp
            subprocess.CompletedProcess([], 0, json.dumps({
                "uploader": "Artist", "title": "Song",
            }), ""),  # cat info.json
            subprocess.CompletedProcess([], 0, f"{TEMP_DIR}/Song.opus\n", ""),  # find audio
            subprocess.CompletedProcess([], 0, "", ""),  # find thumbnail
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp (cleanup on skip)
        ]
        mock_prompt.return_value = AudioMetadata(
            artist="Artist", album="Song", title="Song"
        )

        handle_youtube("https://www.youtube.com/watch?v=abc123")

        mock_dup_action.assert_called_once()
        mock_record.assert_not_called()

    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=True)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id", return_value=[])
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_duplicate_action", return_value="overwrite")
    @patch("gm.youtube.prompt_metadata")
    def test_overwrites_duplicate_by_dest(
        self,
        mock_prompt: MagicMock,
        mock_dup_action: MagicMock,
        mock_ssh: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_verify_thumb: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 0, "", ""),  # yt-dlp
            subprocess.CompletedProcess([], 0, json.dumps({
                "uploader": "Artist", "title": "Song",
            }), ""),  # cat info.json
            subprocess.CompletedProcess([], 0, f"{TEMP_DIR}/Song.opus\n", ""),  # find audio
            subprocess.CompletedProcess([], 0, "", ""),  # find thumbnail
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir dest
            subprocess.CompletedProcess([], 0, "", ""),  # mv audio
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp
        ]
        mock_prompt.return_value = AudioMetadata(
            artist="Artist", album="Song", title="Song"
        )

        handle_youtube("https://www.youtube.com/watch?v=abc123")

        mock_dup_action.assert_called_once()
        mock_record.assert_called_once()

    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=True)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id", return_value=[])
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_duplicate_action", return_value="rename")
    @patch("gm.youtube.prompt_metadata")
    def test_rename_reprompts_metadata(
        self,
        mock_prompt: MagicMock,
        mock_dup_action: MagicMock,
        mock_ssh: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_verify_thumb: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        first_meta = AudioMetadata(
            artist="Artist", album="Song", title="Song"
        )
        renamed_meta = AudioMetadata(
            artist="Artist", album="New-Song", title="New-Song"
        )
        mock_prompt.side_effect = [first_meta, renamed_meta]

        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 0, "", ""),  # yt-dlp
            subprocess.CompletedProcess([], 0, json.dumps({
                "uploader": "Artist", "title": "Song",
            }), ""),  # cat info.json
            subprocess.CompletedProcess([], 0, f"{TEMP_DIR}/Song.opus\n", ""),  # find audio
            subprocess.CompletedProcess([], 0, "", ""),  # find thumbnail
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir dest
            subprocess.CompletedProcess([], 0, "", ""),  # mv audio
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp
        ]

        handle_youtube("https://www.youtube.com/watch?v=abc123")

        # prompt_metadata called twice: initial + rename re-prompt
        assert mock_prompt.call_count == 2
        # Final destination should use renamed metadata (album = title for singles)
        record = mock_record.call_args[0][0]
        assert record.title == "New-Song"
        assert record.album == "New-Song"
        assert "New-Song" in record.destination


class TestVerifyThumbnailEmbedded:
    """Test ffprobe-based thumbnail verification."""

    @patch("gm.youtube.ssh_run")
    def test_returns_true_when_video_stream_found(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess(
            [], 0, "audio\nvideo\n", "",
        )
        assert verify_thumbnail_embedded("/tmp/song.opus") is True

    @patch("gm.youtube.ssh_run")
    def test_returns_false_when_no_video_stream(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess(
            [], 0, "audio\n", "",
        )
        assert verify_thumbnail_embedded("/tmp/song.opus") is False

    @patch("gm.youtube.ssh_run")
    def test_returns_false_on_empty_output(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 1, "", "")
        assert verify_thumbnail_embedded("/tmp/song.opus") is False


@patch("gm.youtube._cleanup_stale_temp_dirs")
@patch("gm.youtube.write_metadata_ssh")
@patch("gm.youtube._make_temp_dir", return_value=TEMP_DIR)
class TestHandleYoutubeThumbnailFailure:
    """Test thumbnail verification failure paths in handle_youtube."""

    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=False)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id", return_value=[])
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_metadata")
    def test_fails_when_no_thumbnail_url(
        self,
        mock_prompt: MagicMock,
        mock_ssh: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Fail with diagnostic when YouTube provides no thumbnail URL."""
        info_json = json.dumps({"uploader": "Artist", "title": "Song"})
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 0, "", ""),  # yt-dlp
            subprocess.CompletedProcess([], 0, info_json, ""),  # cat info.json
            subprocess.CompletedProcess([], 0, f"{TEMP_DIR}/Song.opus\n", ""),  # find audio
            subprocess.CompletedProcess([], 0, "audio\n", ""),  # ffprobe (no video stream)
            subprocess.CompletedProcess([], 0, "", ""),  # find loose thumbnail
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp
        ]

        with pytest.raises(SystemExit):
            handle_youtube("https://www.youtube.com/watch?v=abc123")

        mock_prompt.assert_not_called()
        mock_record.assert_not_called()

    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=False)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id", return_value=[])
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_metadata")
    def test_fails_when_thumbnail_downloaded_but_not_embedded(
        self,
        mock_prompt: MagicMock,
        mock_ssh: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Fail with diagnostic when thumbnail exists on disk but wasn't embedded."""
        info_json = json.dumps({
            "uploader": "Artist", "title": "Song",
            "thumbnail": "https://i.ytimg.com/vi/abc123/maxresdefault.jpg",
        })
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 0, "", ""),  # yt-dlp
            subprocess.CompletedProcess([], 0, info_json, ""),  # cat info.json
            subprocess.CompletedProcess([], 0, f"{TEMP_DIR}/Song.opus\n", ""),  # find audio
            subprocess.CompletedProcess([], 0, "audio\n", ""),  # ffprobe (no video stream)
            subprocess.CompletedProcess([], 0, f"{TEMP_DIR}/Song.jpg\n", ""),  # loose thumbnail found
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp
        ]

        with pytest.raises(SystemExit):
            handle_youtube("https://www.youtube.com/watch?v=abc123")

        mock_prompt.assert_not_called()

    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=False)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id", return_value=[])
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_metadata")
    def test_fails_when_thumbnail_url_available_but_not_downloaded(
        self,
        mock_prompt: MagicMock,
        mock_ssh: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Fail with diagnostic when thumbnail URL exists but file wasn't downloaded."""
        info_json = json.dumps({
            "uploader": "Artist", "title": "Song",
            "thumbnail": "https://i.ytimg.com/vi/abc123/maxresdefault.jpg",
        })
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 0, "", ""),  # yt-dlp
            subprocess.CompletedProcess([], 0, info_json, ""),  # cat info.json
            subprocess.CompletedProcess([], 0, f"{TEMP_DIR}/Song.opus\n", ""),  # find audio
            subprocess.CompletedProcess([], 0, "audio\n", ""),  # ffprobe (no video stream)
            subprocess.CompletedProcess([], 0, "", ""),  # no loose thumbnail
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp
        ]

        with pytest.raises(SystemExit):
            handle_youtube("https://www.youtube.com/watch?v=abc123")

        mock_prompt.assert_not_called()

    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=False)
    @patch("gm.youtube.check_video_id_exists", return_value="")
    @patch("gm.youtube.find_by_video_id", return_value=[])
    @patch("gm.youtube.ssh_run")
    @patch("gm.youtube.prompt_metadata")
    def test_cleans_up_temp_dir_on_thumbnail_failure(
        self,
        mock_prompt: MagicMock,
        mock_ssh: MagicMock,
        mock_find_vid: MagicMock,
        mock_check_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_temp_dir: MagicMock,
        mock_write_meta: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Temp directory is cleaned up when thumbnail verification fails."""
        info_json = json.dumps({"uploader": "Artist", "title": "Song"})
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 0, "", ""),  # yt-dlp
            subprocess.CompletedProcess([], 0, info_json, ""),  # cat info.json
            subprocess.CompletedProcess([], 0, f"{TEMP_DIR}/Song.opus\n", ""),  # find audio
            subprocess.CompletedProcess([], 0, "audio\n", ""),  # ffprobe (no video stream)
            subprocess.CompletedProcess([], 0, "", ""),  # find loose thumbnail
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp
        ]

        with pytest.raises(SystemExit):
            handle_youtube("https://www.youtube.com/watch?v=abc123")

        cleanup_cmd = mock_ssh.call_args_list[-1][0][0]
        assert "rm -rf" in cleanup_cmd
        assert TEMP_DIR in cleanup_cmd


class TestDetectYtdlpInstallMethod:
    """Test yt-dlp install method detection on the LXC."""

    @patch("gm.youtube.ssh_run")
    def test_detects_uv(self, mock_ssh: MagicMock) -> None:
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "/root/.local/bin/yt-dlp\n", ""),  # which
            subprocess.CompletedProcess([], 0, "yt-dlp v2025.6.1\n", ""),  # uv tool list
        ]
        assert _detect_ytdlp_install_method() == "uv"

    @patch("gm.youtube.ssh_run")
    def test_detects_pipx(self, mock_ssh: MagicMock) -> None:
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "/usr/local/bin/yt-dlp\n", ""),  # which
            subprocess.CompletedProcess([], 1, "", ""),  # uv tool list fails
            subprocess.CompletedProcess([], 0, "  - yt-dlp\n", ""),  # pipx list
        ]
        assert _detect_ytdlp_install_method() == "pipx"

    @patch("gm.youtube.ssh_run")
    def test_detects_pip(self, mock_ssh: MagicMock) -> None:
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "/usr/local/bin/yt-dlp\n", ""),  # which
            subprocess.CompletedProcess([], 1, "", ""),  # uv tool list fails
            subprocess.CompletedProcess([], 1, "", ""),  # pipx list fails
            subprocess.CompletedProcess([], 1, "", ""),  # dpkg -S fails
            subprocess.CompletedProcess([], 0, "Name: yt-dlp\n", ""),  # pip show
        ]
        assert _detect_ytdlp_install_method() == "pip"

    @patch("gm.youtube.ssh_run")
    def test_detects_brew(self, mock_ssh: MagicMock) -> None:
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "/opt/homebrew/bin/yt-dlp\n", ""),  # which
            subprocess.CompletedProcess([], 1, "", ""),  # uv tool list fails
            subprocess.CompletedProcess([], 1, "", ""),  # pipx list fails
            subprocess.CompletedProcess([], 1, "", ""),  # dpkg -S fails
            subprocess.CompletedProcess([], 1, "", ""),  # pip show fails
            subprocess.CompletedProcess([], 0, "", ""),  # brew list succeeds
        ]
        assert _detect_ytdlp_install_method() == "brew"

    @patch("gm.youtube.ssh_run")
    def test_detects_standalone(self, mock_ssh: MagicMock) -> None:
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "/usr/local/bin/yt-dlp\n", ""),  # which
            subprocess.CompletedProcess([], 1, "", ""),  # uv tool list fails
            subprocess.CompletedProcess([], 1, "", ""),  # pipx list fails
            subprocess.CompletedProcess([], 1, "", ""),  # dpkg -S fails
            subprocess.CompletedProcess([], 1, "", ""),  # pip show fails
            subprocess.CompletedProcess([], 1, "", ""),  # brew list fails
        ]
        assert _detect_ytdlp_install_method() == "standalone"

    @patch("gm.youtube.ssh_run")
    def test_returns_unknown_when_not_found(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 1, "", "")  # which fails
        assert _detect_ytdlp_install_method() == "unknown"

    @patch("gm.youtube.ssh_run")
    def test_apt_returns_unknown(self, mock_ssh: MagicMock) -> None:
        """apt-installed yt-dlp is too stale — report as unknown so user gets manual hint."""
        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "/usr/bin/yt-dlp\n", ""),  # which
            subprocess.CompletedProcess([], 1, "", ""),  # uv tool list fails
            subprocess.CompletedProcess([], 1, "", ""),  # pipx list fails
            subprocess.CompletedProcess([], 0, "yt-dlp: /usr/bin/yt-dlp\n", ""),  # dpkg -S
        ]
        assert _detect_ytdlp_install_method() == "unknown"


class TestUpdateYtdlp:
    """Test yt-dlp auto-update."""

    @patch("gm.youtube._detect_ytdlp_install_method", return_value="uv")
    @patch("gm.youtube.ssh_run")
    def test_updates_via_uv(self, mock_ssh: MagicMock, mock_detect: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 0, "", "")
        assert update_ytdlp() is True
        cmd = mock_ssh.call_args[0][0]
        assert cmd == "uv tool upgrade yt-dlp"

    @patch("gm.youtube._detect_ytdlp_install_method", return_value="pip")
    @patch("gm.youtube.ssh_run")
    def test_updates_via_pip(self, mock_ssh: MagicMock, mock_detect: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 0, "", "")
        assert update_ytdlp() is True
        cmd = mock_ssh.call_args[0][0]
        assert cmd == "pip install -U yt-dlp"

    @patch("gm.youtube._detect_ytdlp_install_method", return_value="pipx")
    @patch("gm.youtube.ssh_run")
    def test_updates_via_pipx(self, mock_ssh: MagicMock, mock_detect: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 0, "", "")
        assert update_ytdlp() is True
        cmd = mock_ssh.call_args[0][0]
        assert cmd == "pipx upgrade yt-dlp"

    @patch("gm.youtube._detect_ytdlp_install_method", return_value="standalone")
    @patch("gm.youtube.ssh_run")
    def test_updates_standalone(self, mock_ssh: MagicMock, mock_detect: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 0, "", "")
        assert update_ytdlp() is True
        cmd = mock_ssh.call_args[0][0]
        assert cmd == "yt-dlp -U"

    @patch("gm.youtube._detect_ytdlp_install_method", return_value="unknown")
    @patch("gm.youtube.ssh_run")
    def test_returns_false_when_unknown(self, mock_ssh: MagicMock, mock_detect: MagicMock) -> None:
        assert update_ytdlp() is False
        mock_ssh.assert_not_called()

    @patch("gm.youtube._detect_ytdlp_install_method", return_value="pip")
    @patch("gm.youtube.ssh_run")
    def test_returns_false_on_update_failure(self, mock_ssh: MagicMock, mock_detect: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 1, "", "error")
        assert update_ytdlp() is False
