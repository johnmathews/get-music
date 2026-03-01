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
        assert meta.album == "Album Name"
        assert meta.title == "Song Title"
        assert meta.genre == "Rock"
        assert meta.date == "2023-04-15"
        assert meta.description == "Official music video"

    def test_falls_back_to_uploader(self) -> None:
        data = {
            "uploader": "Channel Name",
            "title": "Video Title",
        }
        meta = parse_ytdlp_metadata(json.dumps(data))
        assert meta.artist == "Channel Name"
        assert meta.title == "Video Title"

    def test_defaults_album_to_singles(self) -> None:
        data = {"uploader": "Artist", "title": "Song"}
        meta = parse_ytdlp_metadata(json.dumps(data))
        assert meta.album == "Singles"

    def test_handles_empty_json(self) -> None:
        meta = parse_ytdlp_metadata("{}")
        assert meta.artist == ""
        assert meta.album == "Singles"
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
        assert meta.album == "Singles"
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
            artist="Real Artist", album="Singles", title="Song"
        )

        handle_youtube("https://www.youtube.com/watch?v=abc123")

        # Verify yt-dlp was called with --embed-metadata
        ytdlp_call_cmd = mock_ssh.call_args_list[1][0][0]
        assert "yt-dlp" in ytdlp_call_cmd
        assert "--embed-metadata" in ytdlp_call_cmd

        # Verify file was moved with video ID in brackets, no spaces, native extension
        mv_call_cmd = mock_ssh.call_args_list[6][0][0]
        assert "/mnt/nfs/music/Real-Artist/Singles/Song-[abc123].opus" in mv_call_cmd

        # Verify import was logged
        mock_record.assert_called_once()
        record = mock_record.call_args[0][0]
        assert record.video_id == "abc123"
        assert record.artist == "Real Artist"

    @patch("gm.youtube.record_import")
    @patch("gm.youtube.check_destination_exists", return_value=False)
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
    ) -> None:
        from gm.metadata import AudioMetadata
        from gm.history import ImportRecord

        mock_ssh.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),  # mkdir -p temp
            subprocess.CompletedProcess([], 0, "", ""),  # yt-dlp
            subprocess.CompletedProcess([], 0, json.dumps({
                "uploader": "Channel", "title": "Song", "artist": "Real Artist",
            }), ""),  # cat info.json
            subprocess.CompletedProcess([], 0, f"{TEMP_DIR}/Song.opus\n", ""),  # find audio
            subprocess.CompletedProcess([], 0, "", ""),  # find thumbnail
            subprocess.CompletedProcess([], 0, "", ""),  # rm -rf temp (cleanup on skip)
        ]
        mock_prompt.return_value = AudioMetadata(
            artist="Real Artist", album="Singles", title="Song"
        )
        mock_find_vid.return_value = [
            ImportRecord(destination="/mnt/nfs/music/Real-Artist/Singles/Song-[abc123].opus"),
        ]

        handle_youtube("https://www.youtube.com/watch?v=abc123")

        mock_dup_action.assert_called_once()
        mock_record.assert_not_called()

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
            artist="Channel", album="Singles", title="Song"
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
            artist="Artist", album="Singles", title="Song"
        )

        handle_youtube("https://www.youtube.com/watch?v=abc123")

        # Verify thumbnail was moved to cover.jpg in dest dir
        mv_thumb_cmd = mock_ssh.call_args_list[7][0][0]
        assert "cover.jpg" in mv_thumb_cmd
        assert f"{TEMP_DIR}/Song.jpg" in mv_thumb_cmd

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
            artist="Artist", album="Singles", title="Song"
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
    ) -> None:
        first_meta = AudioMetadata(
            artist="Artist", album="Singles", title="Song"
        )
        renamed_meta = AudioMetadata(
            artist="Artist", album="Other-Album", title="New-Song"
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
        # Final destination should use renamed metadata
        record = mock_record.call_args[0][0]
        assert record.title == "New-Song"
        assert record.album == "Other-Album"
        assert "Other-Album" in record.destination
