"""Tests for local file/directory processing and scp transfer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock, call
import subprocess

import pytest

from gm.files import (
    CODEC_EXTENSION_MAP,
    _BAR_WIDTH,
    _MIN_THUMBNAIL_SIZE,
    embed_cover_art,
    fetch_youtube_thumbnail,
    find_audio_files,
    find_video_files,
    get_media_duration,
    is_video_file,
    is_audio_file,
    build_scp_command,
    detect_audio_codec,
    extract_thumbnail,
    extract_audio_from_video,
    handle_file,
    handle_directory,
    run_ffmpeg,
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

    def test_skips_macos_resource_forks(self) -> None:
        assert not is_audio_file(Path("._song.mp3"))
        assert not is_video_file(Path("._video.mp4"))


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


class TestDetectAudioCodec:
    """Test audio codec detection via ffprobe."""

    @patch("gm.files.subprocess.run")
    def test_detects_known_codecs(self, mock_run: MagicMock, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        for codec, ext in CODEC_EXTENSION_MAP.items():
            mock_run.return_value = subprocess.CompletedProcess([], 0, f"{codec}\n", "")
            result = detect_audio_codec(video)
            assert result == codec

    @patch("gm.files.subprocess.run")
    def test_returns_unknown_codec(self, mock_run: MagicMock, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ac3\n", "")
        result = detect_audio_codec(video)
        assert result == "ac3"

    @patch("gm.files.subprocess.run")
    def test_returns_empty_on_failure(self, mock_run: MagicMock, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", "error")
        result = detect_audio_codec(video)
        assert result == ""


class TestExtractThumbnail:
    """Test thumbnail extraction from video files."""

    @patch("gm.files.subprocess.run")
    def test_extracts_thumbnail(self, mock_run: MagicMock, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00")
        thumb = tmp_path / "video.jpg"
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        # Simulate ffmpeg creating the thumbnail
        thumb.write_bytes(b"\xff\xd8")

        result = extract_thumbnail(video)
        assert result == thumb
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "-map" in cmd
        assert "0:v:t" in cmd
        assert "-q:v" in cmd

    @patch("gm.files.subprocess.run")
    def test_returns_none_on_failure(self, mock_run: MagicMock, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00")
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", "no thumbnail")

        result = extract_thumbnail(video)
        assert result is None
        mock_run.assert_called_once()

    @patch("gm.files.subprocess.run")
    def test_returns_none_when_file_missing(self, mock_run: MagicMock, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00")
        # ffmpeg returns 0 but doesn't create the file
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")

        result = extract_thumbnail(video)
        assert result is None


class TestFetchYoutubeThumbnail:
    """Test YouTube thumbnail downloading."""

    @patch("gm.files.urllib.request.urlretrieve")
    def test_downloads_maxresdefault(self, mock_retrieve: MagicMock, tmp_path: Path) -> None:
        thumb = tmp_path / "cover.jpg"

        def fake_retrieve(url: str, path: str) -> None:
            Path(path).write_bytes(b"\xff" * 10000)

        mock_retrieve.side_effect = fake_retrieve

        result = fetch_youtube_thumbnail("dQw4w9WgXcQ", thumb)
        assert result == thumb
        mock_retrieve.assert_called_once()
        assert "maxresdefault" in mock_retrieve.call_args[0][0]

    @patch("gm.files.urllib.request.urlretrieve")
    def test_falls_back_to_hqdefault(self, mock_retrieve: MagicMock, tmp_path: Path) -> None:
        thumb = tmp_path / "cover.jpg"
        import urllib.error

        def fake_retrieve(url: str, path: str) -> None:
            if "maxresdefault" in url:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
            Path(path).write_bytes(b"\xff" * 10000)

        mock_retrieve.side_effect = fake_retrieve

        result = fetch_youtube_thumbnail("dQw4w9WgXcQ", thumb)
        assert result == thumb
        assert mock_retrieve.call_count == 2
        assert "hqdefault" in mock_retrieve.call_args[0][0]

    @patch("gm.files.urllib.request.urlretrieve")
    def test_skips_placeholder_image(self, mock_retrieve: MagicMock, tmp_path: Path) -> None:
        thumb = tmp_path / "cover.jpg"

        def fake_retrieve(url: str, path: str) -> None:
            if "maxresdefault" in url:
                # YouTube returns a tiny placeholder for missing maxres
                Path(path).write_bytes(b"\xff" * 1097)
            else:
                Path(path).write_bytes(b"\xff" * 10000)

        mock_retrieve.side_effect = fake_retrieve

        result = fetch_youtube_thumbnail("dQw4w9WgXcQ", thumb)
        assert result == thumb
        # maxresdefault was too small, fell through to hqdefault
        assert mock_retrieve.call_count == 2

    @patch("gm.files.urllib.request.urlretrieve")
    def test_returns_none_on_network_failure(self, mock_retrieve: MagicMock, tmp_path: Path) -> None:
        import urllib.error
        thumb = tmp_path / "cover.jpg"
        mock_retrieve.side_effect = urllib.error.URLError("network error")

        result = fetch_youtube_thumbnail("dQw4w9WgXcQ", thumb)
        assert result is None
        assert not thumb.exists()

    def test_returns_none_for_empty_video_id(self, tmp_path: Path) -> None:
        result = fetch_youtube_thumbnail("", tmp_path / "cover.jpg")
        assert result is None

    @patch("gm.files.urllib.request.urlretrieve")
    def test_cleans_up_placeholder_on_total_failure(self, mock_retrieve: MagicMock, tmp_path: Path) -> None:
        thumb = tmp_path / "cover.jpg"

        def fake_retrieve(url: str, path: str) -> None:
            # Both URLs return tiny placeholders
            Path(path).write_bytes(b"\xff" * 500)

        mock_retrieve.side_effect = fake_retrieve

        result = fetch_youtube_thumbnail("dQw4w9WgXcQ", thumb)
        assert result is None
        assert not thumb.exists()


class TestEmbedCoverArt:
    """Test cover art embedding into audio files."""

    @patch("gm.files._embed_mp3")
    def test_embeds_mp3(self, mock_embed: MagicMock, tmp_path: Path) -> None:
        audio = tmp_path / "song.mp3"
        audio.write_bytes(b"\x00")
        image = tmp_path / "cover.jpg"
        image.write_bytes(b"\xff\xd8")

        embed_cover_art(audio, image)
        mock_embed.assert_called_once_with(audio, b"\xff\xd8", "image/jpeg")

    @patch("gm.files._embed_mp4")
    def test_embeds_m4a(self, mock_embed: MagicMock, tmp_path: Path) -> None:
        audio = tmp_path / "song.m4a"
        audio.write_bytes(b"\x00")
        image = tmp_path / "cover.jpg"
        image.write_bytes(b"\xff\xd8")

        embed_cover_art(audio, image)
        mock_embed.assert_called_once_with(audio, b"\xff\xd8", "image/jpeg")

    @patch("gm.files._embed_vorbis")
    def test_embeds_opus(self, mock_embed: MagicMock, tmp_path: Path) -> None:
        audio = tmp_path / "song.opus"
        audio.write_bytes(b"\x00")
        image = tmp_path / "cover.png"
        image.write_bytes(b"\x89PNG")

        embed_cover_art(audio, image)
        mock_embed.assert_called_once_with(audio, b"\x89PNG", "image/png")

    @patch("gm.files._embed_vorbis")
    def test_embeds_ogg(self, mock_embed: MagicMock, tmp_path: Path) -> None:
        audio = tmp_path / "song.ogg"
        audio.write_bytes(b"\x00")
        image = tmp_path / "cover.jpg"
        image.write_bytes(b"\xff\xd8")

        embed_cover_art(audio, image)
        mock_embed.assert_called_once_with(audio, b"\xff\xd8", "image/jpeg")

    @patch("gm.files._embed_flac")
    def test_embeds_flac(self, mock_embed: MagicMock, tmp_path: Path) -> None:
        audio = tmp_path / "song.flac"
        audio.write_bytes(b"\x00")
        image = tmp_path / "cover.jpg"
        image.write_bytes(b"\xff\xd8")

        embed_cover_art(audio, image)
        mock_embed.assert_called_once_with(audio, b"\xff\xd8", "image/jpeg")

    def test_handles_missing_image(self, tmp_path: Path) -> None:
        audio = tmp_path / "song.mp3"
        audio.write_bytes(b"\x00")
        missing = tmp_path / "no-such-file.jpg"

        # Should not raise
        embed_cover_art(audio, missing)

    @patch("gm.files._embed_mp3", side_effect=Exception("mutagen broke"))
    def test_handles_mutagen_failure(self, mock_embed: MagicMock, tmp_path: Path) -> None:
        audio = tmp_path / "song.mp3"
        audio.write_bytes(b"\x00")
        image = tmp_path / "cover.jpg"
        image.write_bytes(b"\xff\xd8")

        # Should not raise
        embed_cover_art(audio, image)

    def test_ignores_unsupported_format(self, tmp_path: Path) -> None:
        audio = tmp_path / "song.wav"
        audio.write_bytes(b"\x00")
        image = tmp_path / "cover.jpg"
        image.write_bytes(b"\xff\xd8")

        # Should not raise, just silently do nothing
        embed_cover_art(audio, image)


class TestGetMediaDuration:
    """Test media duration detection via ffprobe."""

    @patch("gm.files.subprocess.run")
    def test_returns_duration(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "123.456\n", "")
        result = get_media_duration(tmp_path / "video.mp4")
        assert result == 123.456

    @patch("gm.files.subprocess.run")
    def test_returns_zero_on_failure(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", "error")
        result = get_media_duration(tmp_path / "video.mp4")
        assert result == 0.0

    @patch("gm.files.subprocess.run")
    def test_returns_zero_on_invalid_output(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "N/A\n", "")
        result = get_media_duration(tmp_path / "video.mp4")
        assert result == 0.0


class TestRunFfmpeg:
    """Test ffmpeg progress bar runner."""

    def _make_progress_output(self, *, out_time_us: str = "5000000",
                              total_size: str = "3498000",
                              bitrate: str = "135.8kb/s",
                              speed: str = "74.7x") -> str:
        """Build ffmpeg -progress style output."""
        lines = [
            f"out_time_us={out_time_us}",
            f"total_size={total_size}",
            f"bitrate={bitrate}",
            f"speed={speed}",
            "progress=continue",
            f"out_time_us={out_time_us}",
            f"total_size={total_size}",
            f"bitrate={bitrate}",
            f"speed={speed}",
            "progress=end",
        ]
        return "\n".join(lines) + "\n"

    @patch("gm.files.subprocess.Popen")
    def test_successful_run(self, mock_popen: MagicMock) -> None:
        import io
        output = self._make_progress_output()
        proc = MagicMock()
        proc.stdout = io.StringIO(output)
        proc.stderr = io.StringIO("")
        proc.returncode = 0
        proc.wait.return_value = 0
        mock_popen.return_value = proc

        # Should not raise
        run_ffmpeg(["ffmpeg", "-i", "in.mp4", "-vn", "-y", "out.opus"], duration=10.0)

        # Verify progress flags appended
        cmd = mock_popen.call_args[0][0]
        assert "-progress" in cmd
        assert "pipe:1" in cmd
        assert "-nostats" in cmd

    @patch("gm.files.subprocess.Popen")
    def test_raises_on_failure(self, mock_popen: MagicMock) -> None:
        import io
        proc = MagicMock()
        proc.stdout = io.StringIO("")
        proc.stderr = io.StringIO("codec not found")
        proc.returncode = 1
        proc.wait.return_value = 1
        mock_popen.return_value = proc

        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            run_ffmpeg(["ffmpeg", "-i", "in.mp4", "-y", "out.opus"])

    @patch("gm.files.subprocess.Popen")
    def test_zero_duration_shows_stats_only(self, mock_popen: MagicMock) -> None:
        import io
        output = self._make_progress_output()
        proc = MagicMock()
        proc.stdout = io.StringIO(output)
        proc.stderr = io.StringIO("")
        proc.returncode = 0
        proc.wait.return_value = 0
        mock_popen.return_value = proc

        # duration=0 means no percentage bar
        run_ffmpeg(["ffmpeg", "-i", "in.mp4", "-y", "out.opus"], duration=0.0)
        mock_popen.assert_called_once()

    @patch("gm.files.subprocess.Popen")
    def test_handles_lines_without_equals(self, mock_popen: MagicMock) -> None:
        import io
        output = "some garbage line\nout_time_us=5000000\nprogress=end\n"
        proc = MagicMock()
        proc.stdout = io.StringIO(output)
        proc.stderr = io.StringIO("")
        proc.returncode = 0
        proc.wait.return_value = 0
        mock_popen.return_value = proc

        run_ffmpeg(["ffmpeg", "-i", "in.mp4", "-y", "out.opus"], duration=10.0)

    @patch("gm.files.subprocess.Popen")
    def test_handles_invalid_out_time_us(self, mock_popen: MagicMock) -> None:
        import io
        lines = [
            "out_time_us=not_a_number",
            "total_size=3498000",
            "progress=end",
        ]
        proc = MagicMock()
        proc.stdout = io.StringIO("\n".join(lines) + "\n")
        proc.stderr = io.StringIO("")
        proc.returncode = 0
        proc.wait.return_value = 0
        mock_popen.return_value = proc

        run_ffmpeg(["ffmpeg", "-i", "in.mp4", "-y", "out.opus"], duration=10.0)

    @patch("gm.files.subprocess.Popen")
    def test_handles_invalid_total_size(self, mock_popen: MagicMock) -> None:
        import io
        lines = [
            "out_time_us=5000000",
            "total_size=not_a_number",
            "progress=end",
        ]
        proc = MagicMock()
        proc.stdout = io.StringIO("\n".join(lines) + "\n")
        proc.stderr = io.StringIO("")
        proc.returncode = 0
        proc.wait.return_value = 0
        mock_popen.return_value = proc

        run_ffmpeg(["ffmpeg", "-i", "in.mp4", "-y", "out.opus"], duration=10.0)


class TestExtractAudio:
    """Test audio extraction from video files."""

    @patch("gm.files.extract_thumbnail")
    @patch("gm.files.detect_audio_codec", return_value="opus")
    @patch("gm.files.run_ffmpeg")
    @patch("gm.files.get_media_duration", return_value=120.0)
    def test_extracts_audio_opus(self, mock_dur: MagicMock, mock_ffmpeg: MagicMock, mock_codec: MagicMock, mock_thumb: MagicMock, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00")
        thumb = tmp_path / "video.jpg"
        mock_thumb.return_value = thumb

        audio, thumbnail = extract_audio_from_video(video)
        assert audio.suffix == ".opus"
        assert thumbnail == thumb
        mock_thumb.assert_called_once_with(video)
        mock_dur.assert_called_once_with(video)
        mock_ffmpeg.assert_called_once()
        cmd = mock_ffmpeg.call_args[0][0]
        assert "-c:a" in cmd
        assert "copy" in cmd
        assert mock_ffmpeg.call_args[1] == {"duration": 120.0} or mock_ffmpeg.call_args[0][1] == 120.0

    @patch("gm.files.extract_thumbnail")
    @patch("gm.files.detect_audio_codec", return_value="aac")
    @patch("gm.files.run_ffmpeg")
    @patch("gm.files.get_media_duration", return_value=60.0)
    def test_extracts_audio_aac(self, mock_dur: MagicMock, mock_ffmpeg: MagicMock, mock_codec: MagicMock, mock_thumb: MagicMock, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00")
        mock_thumb.return_value = None

        audio, thumbnail = extract_audio_from_video(video)
        assert audio.suffix == ".m4a"
        assert thumbnail is None

    @patch("gm.files.extract_thumbnail")
    @patch("gm.files.detect_audio_codec", return_value="unknown_codec")
    @patch("gm.files.run_ffmpeg")
    @patch("gm.files.get_media_duration", return_value=0.0)
    def test_falls_back_to_opus_for_unknown_codec(self, mock_dur: MagicMock, mock_ffmpeg: MagicMock, mock_codec: MagicMock, mock_thumb: MagicMock, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00")
        mock_thumb.return_value = None

        audio, thumbnail = extract_audio_from_video(video)
        assert audio.suffix == ".opus"

    @patch("gm.files.extract_thumbnail", return_value=None)
    @patch("gm.files.detect_audio_codec", return_value="opus")
    @patch("gm.files.run_ffmpeg", side_effect=RuntimeError("ffmpeg failed (exit 1): codec error"))
    @patch("gm.files.get_media_duration", return_value=0.0)
    def test_raises_on_failure(self, mock_dur: MagicMock, mock_ffmpeg: MagicMock, mock_codec: MagicMock, mock_thumb: MagicMock, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00")
        with pytest.raises(RuntimeError, match="ffmpeg failed"):
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
        capsys: pytest.CaptureFixture[str],
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

        captured = capsys.readouterr()
        assert "song.mp3" in captured.out  # filename printed for standalone import
        assert "Checking for duplicates..." in captured.out
        assert "Writing metadata..." in captured.out
        assert "Transferring..." in captured.out

    @patch("gm.files.embed_cover_art")
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
        mock_embed_art: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata

        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00")
        extracted = tmp_path / "video.opus"
        extracted.write_bytes(b"\x00")
        thumb = tmp_path / "video.jpg"
        thumb.write_bytes(b"\xff\xd8")

        mock_extract.return_value = (extracted, thumb)
        mock_read.return_value = AudioMetadata(artist="Artist", album="Album", title="Video")
        mock_prompt.return_value = AudioMetadata(artist="Artist", album="Album", title="Video")

        handle_file(f)

        mock_extract.assert_called_once_with(f)
        mock_embed_art.assert_called_once_with(extracted, thumb)
        assert mock_scp.call_count == 2
        # First call: audio file
        audio_call = mock_scp.call_args_list[0]
        assert audio_call[0][0] == extracted
        # Second call: cover.jpg
        cover_call = mock_scp.call_args_list[1]
        assert cover_call[0][0] == thumb
        assert cover_call[0][1].endswith("/cover.jpg")
        mock_record.assert_called_once()

    @patch("gm.files.fetch_youtube_thumbnail", return_value=None)
    @patch("gm.files.record_import")
    @patch("gm.files.check_destination_exists", return_value=False)
    @patch("gm.files.find_by_hash", return_value=[])
    @patch("gm.files.compute_file_hash", return_value="fakehash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_metadata")
    @patch("gm.files.read_metadata")
    @patch("gm.files.extract_audio_from_video")
    def test_handles_video_file_no_thumbnail(
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
        mock_fetch_yt: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata

        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00")
        extracted = tmp_path / "video.opus"
        extracted.write_bytes(b"\x00")

        mock_extract.return_value = (extracted, None)
        mock_read.return_value = AudioMetadata(artist="Artist", album="Album", title="Video")
        mock_prompt.return_value = AudioMetadata(artist="Artist", album="Album", title="Video")

        handle_file(f)

        mock_extract.assert_called_once_with(f)
        # No video ID in filename, so fetch_youtube_thumbnail not called
        mock_fetch_yt.assert_not_called()
        # Only audio scp, no cover scp
        mock_scp.assert_called_once()
        mock_record.assert_called_once()

    @patch("gm.files.embed_cover_art")
    @patch("gm.files.fetch_youtube_thumbnail")
    @patch("gm.files.record_import")
    @patch("gm.files.check_destination_exists", return_value=False)
    @patch("gm.files.find_by_hash", return_value=[])
    @patch("gm.files.compute_file_hash", return_value="fakehash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_metadata")
    @patch("gm.files.read_metadata")
    @patch("gm.files.extract_audio_from_video")
    def test_fetches_youtube_thumbnail_when_no_embedded(
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
        mock_fetch_yt: MagicMock,
        mock_embed_art: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata

        # Filename has YouTube video ID (artist-title-[id] pattern)
        f = tmp_path / "Artist-Song-[dQw4w9WgXcQ].mp4"
        f.write_bytes(b"\x00")
        extracted = tmp_path / "Artist-Song-[dQw4w9WgXcQ].opus"
        extracted.write_bytes(b"\x00")
        yt_thumb = tmp_path / "Artist-Song-[dQw4w9WgXcQ].jpg"
        yt_thumb.write_bytes(b"\xff\xd8")

        mock_extract.return_value = (extracted, None)  # No attached picture
        mock_fetch_yt.return_value = yt_thumb  # YouTube download succeeds
        mock_read.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")
        mock_prompt.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")

        handle_file(f)

        # YouTube thumbnail fetched as fallback
        mock_fetch_yt.assert_called_once_with(
            "dQw4w9WgXcQ", f.with_suffix(".jpg"),
        )
        # Thumbnail embedded and transferred
        mock_embed_art.assert_called_once_with(extracted, yt_thumb)
        assert mock_scp.call_count == 2
        cover_call = mock_scp.call_args_list[1]
        assert cover_call[0][0] == yt_thumb
        assert cover_call[0][1].endswith("/cover.jpg")

    @patch("gm.files.record_import")
    @patch("gm.files.check_destination_exists", return_value=True)
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
    @patch("gm.files.check_destination_exists", return_value=False)
    @patch("gm.files.find_by_hash", return_value=[])
    @patch("gm.files.compute_file_hash", return_value="fakehash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_title_only")
    @patch("gm.files.read_metadata")
    def test_batch_mode_does_not_print_filename(
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
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from gm.metadata import AudioMetadata

        f = tmp_path / "song.mp3"
        f.write_bytes(b"\x00")

        batch = AudioMetadata(artist="Artist", album="Album", genre="Rock")
        mock_read.return_value = AudioMetadata(title="Song")
        mock_title.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")

        handle_file(f, batch_meta=batch, track_number=3)

        captured = capsys.readouterr()
        # In batch mode, handle_file should NOT print the filename
        # (handle_directory prints it instead)
        lines = captured.out.split("\n")
        assert not any(line.strip() == "song.mp3" for line in lines)

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


    @patch("gm.files.fetch_youtube_thumbnail", return_value=None)
    @patch("gm.files.record_import")
    @patch("gm.files.check_destination_exists", return_value=False)
    @patch("gm.files.find_by_hash", return_value=[])
    @patch("gm.files.compute_file_hash", return_value="fakehash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_metadata")
    @patch("gm.files.read_metadata")
    def test_passes_video_id_to_destination(
        self,
        mock_read: MagicMock,
        mock_prompt: MagicMock,
        mock_mkdir: MagicMock,
        mock_scp: MagicMock,
        mock_hash: MagicMock,
        mock_find_hash: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_fetch_yt: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata

        f = tmp_path / "Artist-Song-[dQw4w9WgXcQ].mp3"
        f.write_bytes(b"\x00")

        mock_read.return_value = AudioMetadata(artist="Artist", album="YouTube", title="Song")
        mock_prompt.return_value = AudioMetadata(artist="Artist", album="YouTube", title="Song")

        handle_file(f)

        # Destination should contain the video ID
        dest = mock_scp.call_args[0][1]
        assert "[dQw4w9WgXcQ]" in dest

    @patch("gm.files.fetch_youtube_thumbnail", return_value=None)
    @patch("gm.files.record_import")
    @patch("gm.files.check_destination_exists", return_value=False)
    @patch("gm.files.find_by_hash", return_value=[])
    @patch("gm.files.compute_file_hash", return_value="fakehash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_metadata")
    @patch("gm.files.read_metadata")
    def test_logs_video_id_in_import_record(
        self,
        mock_read: MagicMock,
        mock_prompt: MagicMock,
        mock_mkdir: MagicMock,
        mock_scp: MagicMock,
        mock_hash: MagicMock,
        mock_find_hash: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_fetch_yt: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata

        f = tmp_path / "Artist-Song-[dQw4w9WgXcQ].mp3"
        f.write_bytes(b"\x00")

        mock_read.return_value = AudioMetadata(artist="Artist", album="YouTube", title="Song")
        mock_prompt.return_value = AudioMetadata(artist="Artist", album="YouTube", title="Song")

        handle_file(f)

        record = mock_record.call_args[0][0]
        assert record.video_id == "dQw4w9WgXcQ"

    @patch("gm.files.fetch_youtube_thumbnail", return_value=None)
    @patch("gm.files.record_import")
    @patch("gm.files.check_destination_exists", return_value=True)
    @patch("gm.files.find_by_hash", return_value=[])
    @patch("gm.files.compute_file_hash", return_value="fakehash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_duplicate_action", return_value="skip")
    @patch("gm.files.prompt_metadata")
    @patch("gm.files.read_metadata")
    @patch("gm.files.find_by_video_id")
    def test_video_id_duplicate_skip(
        self,
        mock_find_vid: MagicMock,
        mock_read: MagicMock,
        mock_prompt: MagicMock,
        mock_dup_action: MagicMock,
        mock_mkdir: MagicMock,
        mock_scp: MagicMock,
        mock_hash: MagicMock,
        mock_find_hash: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_fetch_yt: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from gm.history import ImportRecord
        from gm.metadata import AudioMetadata

        f = tmp_path / "Artist-Song-[dQw4w9WgXcQ].mp3"
        f.write_bytes(b"\x00")

        mock_find_vid.return_value = [ImportRecord(destination="/mnt/nfs/music/Artist/Album/Song-[dQw4w9WgXcQ].opus")]
        mock_read.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")
        mock_prompt.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")

        handle_file(f)

        mock_find_vid.assert_called_once_with("dQw4w9WgXcQ")
        mock_dup_action.assert_called_once_with("/mnt/nfs/music/Artist/Album/Song-[dQw4w9WgXcQ].opus")
        mock_scp.assert_not_called()
        mock_record.assert_not_called()
        captured = capsys.readouterr()
        assert "Skipped." in captured.out

    @patch("gm.files.fetch_youtube_thumbnail", return_value=None)
    @patch("gm.files.record_import")
    @patch("gm.files.check_destination_exists", side_effect=[True, False])
    @patch("gm.files.find_by_hash", return_value=[])
    @patch("gm.files.compute_file_hash", return_value="fakehash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_metadata")
    @patch("gm.files.read_metadata")
    @patch("gm.files.extract_audio_from_video")
    @patch("gm.files.find_by_video_id")
    @patch("gm.files.prompt_duplicate_action", return_value="overwrite")
    def test_video_id_duplicate_overwrite_proceeds(
        self,
        mock_dup_action: MagicMock,
        mock_find_vid: MagicMock,
        mock_extract: MagicMock,
        mock_read: MagicMock,
        mock_prompt: MagicMock,
        mock_mkdir: MagicMock,
        mock_scp: MagicMock,
        mock_hash: MagicMock,
        mock_find_hash: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_fetch_yt: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata
        from gm.history import ImportRecord

        f = tmp_path / "Artist-Song-[dQw4w9WgXcQ].mp4"
        f.write_bytes(b"\x00")
        extracted = tmp_path / "Artist-Song-[dQw4w9WgXcQ].opus"
        extracted.write_bytes(b"\x00")

        mock_find_vid.return_value = [ImportRecord(destination="/mnt/nfs/music/Artist/Album/Song-[dQw4w9WgXcQ].opus")]
        mock_extract.return_value = (extracted, None)
        mock_read.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")
        mock_prompt.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")

        handle_file(f)

        mock_find_vid.assert_called_once_with("dQw4w9WgXcQ")
        mock_dup_action.assert_called_once()
        mock_extract.assert_called_once_with(f)
        mock_scp.assert_called_once()
        mock_record.assert_called_once()

    @patch("gm.files.fetch_youtube_thumbnail", return_value=None)
    @patch("gm.files.record_import")
    @patch("gm.files.find_by_hash", return_value=[])
    @patch("gm.files.compute_file_hash", return_value="fakehash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_metadata")
    @patch("gm.files.read_metadata")
    @patch("gm.files.delete_import")
    @patch("gm.files.check_destination_exists", return_value=False)
    @patch("gm.files.find_by_video_id")
    def test_stale_video_id_hit_skips_prompt(
        self,
        mock_find_vid: MagicMock,
        mock_check_dest: MagicMock,
        mock_delete: MagicMock,
        mock_read: MagicMock,
        mock_prompt: MagicMock,
        mock_mkdir: MagicMock,
        mock_scp: MagicMock,
        mock_hash: MagicMock,
        mock_find_hash: MagicMock,
        mock_record: MagicMock,
        mock_fetch_yt: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata
        from gm.history import ImportRecord

        f = tmp_path / "Artist-Song-[dQw4w9WgXcQ].mp3"
        f.write_bytes(b"\x00")

        stale_dest = "/mnt/nfs/music/Artist/Album/Song-[dQw4w9WgXcQ].opus"
        mock_find_vid.return_value = [ImportRecord(destination=stale_dest)]
        # check_destination_exists returns False (file gone)
        mock_read.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")
        mock_prompt.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")

        handle_file(f)

        # Stale record deleted, no duplicate prompt, extraction proceeds
        mock_delete.assert_called_once_with(stale_dest)
        mock_scp.assert_called_once()
        mock_record.assert_called_once()

    @patch("gm.files.record_import")
    @patch("gm.files.find_by_hash")
    @patch("gm.files.compute_file_hash", return_value="duphash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_metadata")
    @patch("gm.files.read_metadata")
    @patch("gm.files.delete_import")
    @patch("gm.files.check_destination_exists", return_value=False)
    def test_stale_hash_hit_skips_prompt(
        self,
        mock_check_dest: MagicMock,
        mock_delete: MagicMock,
        mock_read: MagicMock,
        mock_prompt: MagicMock,
        mock_mkdir: MagicMock,
        mock_scp: MagicMock,
        mock_hash: MagicMock,
        mock_find_hash: MagicMock,
        mock_record: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata
        from gm.history import ImportRecord

        f = tmp_path / "song.mp3"
        f.write_bytes(b"\x00")

        stale_dest = "/mnt/nfs/music/Artist/Album/Song.mp3"
        mock_find_hash.return_value = [ImportRecord(destination=stale_dest)]
        # check_destination_exists returns False (file gone)
        mock_read.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")
        mock_prompt.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")

        handle_file(f)

        # Stale record deleted, no duplicate prompt, transfer proceeds
        mock_delete.assert_called_once_with(stale_dest)
        mock_scp.assert_called_once()
        mock_record.assert_called_once()

    @patch("gm.files.fetch_youtube_thumbnail", return_value=None)
    @patch("gm.files.record_import")
    @patch("gm.files.check_destination_exists", return_value=True)
    @patch("gm.files.find_by_hash")
    @patch("gm.files.compute_file_hash", return_value="duphash")
    @patch("gm.files.scp_transfer")
    @patch("gm.files.ssh_mkdir")
    @patch("gm.files.prompt_duplicate_action", return_value="skip")
    @patch("gm.files.prompt_metadata")
    @patch("gm.files.read_metadata")
    @patch("gm.files.find_by_video_id")
    def test_video_id_duplicate_takes_priority_over_hash(
        self,
        mock_find_vid: MagicMock,
        mock_read: MagicMock,
        mock_prompt: MagicMock,
        mock_dup_action: MagicMock,
        mock_mkdir: MagicMock,
        mock_scp: MagicMock,
        mock_hash: MagicMock,
        mock_find_hash: MagicMock,
        mock_check_dest: MagicMock,
        mock_record: MagicMock,
        mock_fetch_yt: MagicMock,
        mock_write_meta: MagicMock,
        mock_find_genre: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When both video_id and hash match, the video_id hit is used."""
        from gm.history import ImportRecord
        from gm.metadata import AudioMetadata

        f = tmp_path / "Artist-Song-[dQw4w9WgXcQ].mp3"
        f.write_bytes(b"\x00")

        vid_dest = "/mnt/nfs/music/Artist/Album/Song-[dQw4w9WgXcQ].opus"
        hash_dest = "/mnt/nfs/music/Artist/Other/Song.mp3"
        mock_find_vid.return_value = [ImportRecord(destination=vid_dest)]
        mock_find_hash.return_value = [ImportRecord(destination=hash_dest)]
        mock_read.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")
        mock_prompt.return_value = AudioMetadata(artist="Artist", album="Album", title="Song")

        handle_file(f)

        # Video ID hit used, hash check skipped
        mock_dup_action.assert_called_once_with(vid_dest)
        mock_find_hash.assert_not_called()


class TestHandleDirectory:
    """Test directory processing."""

    @patch("gm.files.handle_file")
    @patch("gm.files.prompt_batch_metadata")
    @patch("builtins.input", side_effect=["n", "y"])
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
    @patch("builtins.input", side_effect=["y", "y"])
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
    @patch("builtins.input", side_effect=["n", "y"])
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
    @patch("builtins.input", side_effect=["n", "y"])
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

    @patch("gm.files.handle_file")
    @patch("gm.files.prompt_batch_metadata")
    @patch("builtins.input", side_effect=["n", "n"])
    def test_per_file_metadata_mode(
        self,
        mock_input: MagicMock,
        mock_batch: MagicMock,
        mock_handle: MagicMock,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "a.mp3").write_bytes(b"\x00")
        (tmp_path / "b.mp3").write_bytes(b"\x00")

        handle_directory(tmp_path)

        mock_batch.assert_not_called()
        assert mock_handle.call_count == 2
        for c in mock_handle.call_args_list:
            assert c.kwargs["batch_meta"] is None
            assert c.kwargs["track_number"] == 0

    @patch("gm.files.handle_file")
    @patch("gm.files.prompt_batch_metadata")
    @patch("builtins.input", side_effect=["n", "y"])
    def test_deduplicates_audio_and_video_with_same_stem(
        self,
        mock_input: MagicMock,
        mock_batch: MagicMock,
        mock_handle: MagicMock,
        tmp_path: Path,
    ) -> None:
        from gm.metadata import AudioMetadata

        mock_batch.return_value = AudioMetadata(artist="Artist", album="Album")
        # Same stem, different extensions — video should win
        (tmp_path / "song-[abc123DEF].mp3").write_bytes(b"\x00")
        (tmp_path / "song-[abc123DEF].mp4").write_bytes(b"\x00")

        handle_directory(tmp_path)

        assert mock_handle.call_count == 1
        processed = mock_handle.call_args[0][0]
        assert processed.suffix == ".mp4"
