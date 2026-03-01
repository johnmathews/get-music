"""Tests for audio metadata extraction and user prompts."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from gm.metadata import (
    AudioMetadata,
    check_destination_exists,
    check_video_id_exists,
    humanize_name,
    list_existing_albums,
    list_existing_artists,
    prompt_batch_metadata,
    prompt_duplicate_action,
    prompt_title_only,
    read_metadata,
    write_metadata,
    write_metadata_ssh,
    prompt_metadata,
    sanitize_filename,
    suggest_match,
    build_destination_path,
)


class TestHumanizeName:
    """Test converting hyphenated names back to spaces."""

    def test_converts_hyphens_to_spaces(self) -> None:
        assert humanize_name("Yussef-Dayes") == "Yussef Dayes"

    def test_converts_multiple_hyphens(self) -> None:
        assert humanize_name("Led-Zeppelin-IV") == "Led Zeppelin IV"

    def test_preserves_plain_name(self) -> None:
        assert humanize_name("Radiohead") == "Radiohead"

    def test_preserves_spaces(self) -> None:
        assert humanize_name("Led Zeppelin") == "Led Zeppelin"

    def test_empty_string(self) -> None:
        assert humanize_name("") == ""


class TestSanitizeFilename:
    """Test filename sanitization."""

    def test_removes_slashes(self) -> None:
        assert sanitize_filename("AC/DC") == "AC-DC"

    def test_removes_backslashes(self) -> None:
        assert sanitize_filename("back\\slash") == "back-slash"

    def test_removes_colons(self) -> None:
        assert sanitize_filename("Title: Subtitle") == "Title-Subtitle"

    def test_strips_whitespace(self) -> None:
        assert sanitize_filename("  hello  ") == "hello"

    def test_replaces_spaces_with_hyphens(self) -> None:
        assert sanitize_filename("Good Song Name") == "Good-Song-Name"

    def test_collapses_multiple_hyphens(self) -> None:
        assert sanitize_filename("a - b") == "a-b"

    def test_replaces_null_bytes(self) -> None:
        assert sanitize_filename("bad\x00name") == "bad-name"

    def test_preserves_hyphenated_names(self) -> None:
        assert sanitize_filename("Good-Song-Name") == "Good-Song-Name"

    def test_replaces_dots_only_name(self) -> None:
        assert sanitize_filename("...") == "_"

    def test_removes_single_quotes(self) -> None:
        assert sanitize_filename("It's a Song") == "It-s-a-Song"

    def test_removes_double_quotes(self) -> None:
        assert sanitize_filename('Say "Hello"') == "Say-Hello"

    def test_removes_backticks(self) -> None:
        assert sanitize_filename("Song `Live`") == "Song-Live"

    def test_removes_dollar_sign(self) -> None:
        assert sanitize_filename("Ca$h Money") == "Ca-h-Money"

    def test_removes_question_mark(self) -> None:
        assert sanitize_filename("Why?") == "Why"

    def test_removes_asterisk(self) -> None:
        assert sanitize_filename("Best*Of") == "Best-Of"

    def test_removes_angle_brackets(self) -> None:
        assert sanitize_filename("<Title>") == "Title"

    def test_removes_pipe(self) -> None:
        assert sanitize_filename("A|B") == "A-B"

    def test_removes_semicolons(self) -> None:
        assert sanitize_filename("A;B") == "A-B"

    def test_removes_ampersand(self) -> None:
        assert sanitize_filename("Tom & Jerry") == "Tom-Jerry"

    def test_removes_parentheses(self) -> None:
        assert sanitize_filename("Song (Live)") == "Song-Live"

    def test_removes_newlines_and_tabs(self) -> None:
        assert sanitize_filename("Line1\nLine2\tLine3") == "Line1-Line2-Line3"


class TestBuildDestinationPath:
    """Test destination path construction."""

    def test_builds_artist_album_title(self) -> None:
        meta = AudioMetadata(artist="Artist", album="Album", title="Song")
        result = build_destination_path(meta, ".mp3")
        assert result == "/mnt/nfs/music/Artist/Album/Song.mp3"

    def test_builds_path_with_video_id(self) -> None:
        meta = AudioMetadata(artist="Artist", album="Album", title="Song")
        result = build_destination_path(meta, ".opus", video_id="dQw4w9WgXcQ")
        assert result == "/mnt/nfs/music/Artist/Album/Song-[dQw4w9WgXcQ].opus"

    def test_no_video_id_no_brackets(self) -> None:
        meta = AudioMetadata(artist="Artist", album="Album", title="Song")
        result = build_destination_path(meta, ".mp3")
        assert "[" not in result

    def test_no_spaces_in_path(self) -> None:
        meta = AudioMetadata(artist="Led Zeppelin", album="Led Zeppelin IV", title="Stairway To Heaven")
        result = build_destination_path(meta, ".flac")
        assert " " not in result
        assert result == "/mnt/nfs/music/Led-Zeppelin/Led-Zeppelin-IV/Stairway-To-Heaven.flac"

    def test_sanitizes_components(self) -> None:
        meta = AudioMetadata(artist="AC/DC", album="Back: In Black", title="Hells Bells")
        result = build_destination_path(meta, ".flac")
        assert "/" not in result.split("/mnt/nfs/music/")[1].split("/")[0]  # artist part
        assert ":" not in result


class TestReadMetadata:
    """Test metadata reading from audio files."""

    def test_returns_empty_for_nonexistent(self, tmp_path: Path) -> None:
        meta = read_metadata(tmp_path / "nonexistent.mp3")
        assert meta.artist == ""
        assert meta.album == ""
        assert meta.title == ""

    def test_returns_filename_title_for_invalid_file(self, tmp_path: Path) -> None:
        f = tmp_path / "not_audio.mp3"
        f.write_bytes(b"\x00" * 100)
        meta = read_metadata(f)
        assert meta.artist == ""
        assert meta.album == ""
        assert meta.title == "not_audio"

    def test_derives_title_from_filename(self, tmp_path: Path) -> None:
        f = tmp_path / "Cool Song.mp3"
        f.write_bytes(b"\x00" * 100)
        meta = read_metadata(f)
        assert meta.title == "Cool Song"

    @patch("gm.metadata.mutagen.File")
    def test_reads_tags_from_audio(self, mock_file: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "song.mp3"
        f.write_bytes(b"\x00")

        mock_audio = MagicMock()
        mock_audio.tags = {
            "artist": ["Led Zeppelin"],
            "album": ["IV"],
            "title": ["Stairway To Heaven"],
            "genre": ["Rock"],
            "date": ["1971"],
            "description": ["Classic rock track"],
            "tracknumber": ["4"],
        }
        mock_file.return_value = mock_audio

        meta = read_metadata(f)
        assert meta.artist == "Led Zeppelin"
        assert meta.album == "IV"
        assert meta.title == "Stairway To Heaven"
        assert meta.genre == "Rock"
        assert meta.date == "1971"
        assert meta.description == "Classic rock track"
        assert meta.track_number == "4"

    @patch("gm.metadata.mutagen.File")
    def test_handles_scalar_tag_values(self, mock_file: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "song.mp3"
        f.write_bytes(b"\x00")

        tag_data = {"artist": "Single Value", "title": "Song"}
        mock_tags = MagicMock()
        mock_tags.__bool__ = lambda self: True
        mock_tags.get = lambda k, d=None: tag_data.get(k, d)

        mock_audio = MagicMock()
        mock_audio.tags = mock_tags
        mock_file.return_value = mock_audio

        meta = read_metadata(f)
        assert meta.artist == "Single Value"
        assert meta.title == "Song"

    @patch("gm.metadata.mutagen.File")
    def test_handles_empty_tag_values(self, mock_file: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "song.mp3"
        f.write_bytes(b"\x00")

        mock_tags = MagicMock()
        mock_tags.__bool__ = lambda self: True
        mock_tags.get = lambda k, d=None: None

        mock_audio = MagicMock()
        mock_audio.tags = mock_tags
        mock_file.return_value = mock_audio

        meta = read_metadata(f)
        assert meta.artist == ""
        assert meta.title == "song"  # falls back to filename stem


@patch("gm.metadata.list_existing_albums", return_value=[])
@patch("gm.metadata.list_existing_artists", return_value=[])
class TestPromptMetadata:
    """Test interactive metadata prompting."""

    @patch("builtins.input", side_effect=["", "", "", "", ""])
    def test_accepts_defaults(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(
            artist="Default Artist", album="Default Album", title="Default Title",
            genre="Rock", date="2024",
        )
        result = prompt_metadata(defaults)
        assert result.artist == "Default Artist"
        assert result.album == "Default Album"
        assert result.title == "Default Title"
        assert result.genre == "Rock"
        assert result.date == "2024"

    @patch("builtins.input", side_effect=["Custom Artist", "Custom Album", "Custom Title", "Jazz", "1965"])
    def test_overrides_defaults(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(artist="Default", album="Default", title="Default")
        result = prompt_metadata(defaults)
        assert result.artist == "Custom Artist"
        assert result.album == "Custom Album"
        assert result.title == "Custom Title"
        assert result.genre == "Jazz"
        assert result.date == "1965"

    @patch("builtins.input", side_effect=["", "My Album", "", "", ""])
    def test_partial_override(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(artist="Artist", album="Singles", title="Song")
        result = prompt_metadata(defaults)
        assert result.artist == "Artist"
        assert result.album == "My Album"
        assert result.title == "Song"

    @patch("builtins.input", side_effect=["Artist", "Album", "Title", "Live", "2023"])
    def test_fills_empty_defaults(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(artist="", album="", title="")
        result = prompt_metadata(defaults)
        assert result.artist == "Artist"
        assert result.album == "Album"
        assert result.title == "Title"
        assert result.genre == "Live"
        assert result.date == "2023"

    @patch("builtins.input", side_effect=["", "", "", "", ""])
    def test_preserves_description_and_track(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(
            artist="Artist", album="Album", title="Song",
            description="A live recording from 1969", track_number="3",
        )
        result = prompt_metadata(defaults)
        assert result.description == "A live recording from 1969"
        assert result.track_number == "3"


class TestCheckDestinationExists:
    """Test SSH-based destination file existence check."""

    @patch("gm.metadata.ssh_run")
    def test_returns_true_when_exists(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 0, "", "")
        assert check_destination_exists("/mnt/nfs/music/A/B/C.opus") is True

    @patch("gm.metadata.ssh_run")
    def test_returns_false_when_missing(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 1, "", "")
        assert check_destination_exists("/mnt/nfs/music/A/B/C.opus") is False


class TestCheckVideoIdExists:
    """Test SSH-based video ID filename scan."""

    @patch("gm.metadata.ssh_run")
    def test_returns_path_when_found(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess(
            [], 0, "/mnt/nfs/music/Artist/Album/Song-[abc123].opus\n", ""
        )
        result = check_video_id_exists("/mnt/nfs/music/Artist", "abc123")
        assert result == "/mnt/nfs/music/Artist/Album/Song-[abc123].opus"

    @patch("gm.metadata.ssh_run")
    def test_returns_empty_when_not_found(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 0, "", "")
        assert check_video_id_exists("/mnt/nfs/music/Artist", "abc123") == ""

    def test_returns_empty_for_empty_video_id(self) -> None:
        assert check_video_id_exists("/mnt/nfs/music/Artist", "") == ""


class TestPromptDuplicateAction:
    """Test duplicate action prompt."""

    @patch("builtins.input", return_value="s")
    def test_skip(self, mock_input: MagicMock) -> None:
        assert prompt_duplicate_action("/some/path") == "skip"

    @patch("builtins.input", return_value="o")
    def test_overwrite(self, mock_input: MagicMock) -> None:
        assert prompt_duplicate_action("/some/path") == "overwrite"

    @patch("builtins.input", return_value="r")
    def test_rename(self, mock_input: MagicMock) -> None:
        assert prompt_duplicate_action("/some/path") == "rename"

    @patch("builtins.input", return_value="")
    def test_default_is_skip(self, mock_input: MagicMock) -> None:
        assert prompt_duplicate_action("/some/path") == "skip"

    @patch("builtins.input", return_value="overwrite")
    def test_full_word_overwrite(self, mock_input: MagicMock) -> None:
        assert prompt_duplicate_action("/some/path") == "overwrite"


class TestListExistingArtists:
    """Test SSH-based artist directory listing."""

    @patch("gm.metadata.ssh_run")
    def test_returns_list(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess(
            [], 0, "Led-Zeppelin\nPink-Floyd\nThe-Beatles\n", ""
        )
        result = list_existing_artists()
        assert result == ["Led-Zeppelin", "Pink-Floyd", "The-Beatles"]

    @patch("gm.metadata.ssh_run")
    def test_returns_empty_on_failure(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 1, "", "error")
        assert list_existing_artists() == []

    @patch("gm.metadata.ssh_run")
    def test_returns_empty_for_empty_dir(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 0, "", "")
        assert list_existing_artists() == []


class TestListExistingAlbums:
    """Test SSH-based album directory listing."""

    @patch("gm.metadata.ssh_run")
    def test_returns_list(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess(
            [], 0, "Led-Zeppelin-IV\nPhysical-Graffiti\n", ""
        )
        result = list_existing_albums("Led-Zeppelin")
        assert result == ["Led-Zeppelin-IV", "Physical-Graffiti"]

    @patch("gm.metadata.ssh_run")
    def test_returns_empty_on_failure(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 1, "", "error")
        assert list_existing_albums("Unknown") == []


class TestSuggestMatch:
    """Test fuzzy matching of user input against existing directories."""

    def test_case_insensitive_exact_match(self) -> None:
        assert suggest_match("led zeppelin", ["Led-Zeppelin", "Pink-Floyd"]) == "Led-Zeppelin"

    def test_sanitized_form_match(self) -> None:
        # "Led Zeppelin" sanitizes to "Led-Zeppelin" which matches exactly
        assert suggest_match("Led Zeppelin", ["Led-Zeppelin", "Pink-Floyd"]) == "Led-Zeppelin"

    def test_fuzzy_match(self) -> None:
        # Typo: "Led Zeplin" should fuzzy match "Led-Zeppelin"
        result = suggest_match("Led Zeplin", ["Led-Zeppelin", "Pink-Floyd"])
        assert result == "Led-Zeppelin"

    def test_no_match(self) -> None:
        assert suggest_match("Metallica", ["Led-Zeppelin", "Pink-Floyd"]) == ""

    def test_empty_input(self) -> None:
        assert suggest_match("", ["Led-Zeppelin"]) == ""

    def test_empty_existing(self) -> None:
        assert suggest_match("Led Zeppelin", []) == ""

    def test_exact_match_returns_original(self) -> None:
        # If user typed the exact dir name, return it
        assert suggest_match("Led-Zeppelin", ["Led-Zeppelin"]) == "Led-Zeppelin"


class TestPromptMetadataWithSuggestion:
    """Test prompt_metadata with artist/album suggestion flow."""

    @patch("gm.metadata.list_existing_albums", return_value=[])
    @patch("gm.metadata.list_existing_artists", return_value=["Led-Zeppelin"])
    @patch("builtins.input", side_effect=[
        "Led Zeppelin",  # artist prompt — matches Led-Zeppelin, no suggestion needed
        "IV",            # album prompt
        "Stairway",      # title prompt
        "Rock",          # genre prompt
        "1971",          # date prompt
    ])
    def test_silent_match_when_humanized_equals_input(
        self, mock_input: MagicMock, mock_artists: MagicMock, mock_albums: MagicMock,
    ) -> None:
        defaults = AudioMetadata()
        result = prompt_metadata(defaults)
        assert result.artist == "Led Zeppelin"

    @patch("gm.metadata.list_existing_albums", return_value=[])
    @patch("gm.metadata.list_existing_artists", return_value=["Led-Zeppelin"])
    @patch("builtins.input", side_effect=[
        "Led Zeplin",    # artist prompt — typo, fuzzy matches Led-Zeppelin
        "y",             # "Did you mean 'Led Zeppelin'?"
        "IV",            # album prompt
        "Stairway",      # title prompt
        "Rock",          # genre prompt
        "1971",          # date prompt
    ])
    def test_suggests_humanized_match_for_typo(
        self, mock_input: MagicMock, mock_artists: MagicMock, mock_albums: MagicMock,
    ) -> None:
        defaults = AudioMetadata()
        result = prompt_metadata(defaults)
        assert result.artist == "Led Zeppelin"

    @patch("gm.metadata.list_existing_albums", return_value=[])
    @patch("gm.metadata.list_existing_artists", return_value=["Led-Zeppelin"])
    @patch("builtins.input", side_effect=[
        "Led Zeplin",    # artist prompt — typo
        "n",             # reject suggestion
        "IV",            # album prompt
        "Stairway",      # title prompt
        "Rock",          # genre prompt
        "1971",          # date prompt
    ])
    def test_rejects_artist_suggestion(
        self, mock_input: MagicMock, mock_artists: MagicMock, mock_albums: MagicMock,
    ) -> None:
        defaults = AudioMetadata()
        result = prompt_metadata(defaults)
        assert result.artist == "Led Zeplin"


class TestPromptBatchMetadata:
    """Test batch metadata prompting."""

    @patch("gm.metadata.list_existing_albums", return_value=[])
    @patch("gm.metadata.list_existing_artists", return_value=[])
    @patch("builtins.input", side_effect=["Led Zeppelin", "IV", "Rock", "1971"])
    def test_prompts_shared_fields(
        self, mock_input: MagicMock, mock_artists: MagicMock, mock_albums: MagicMock,
    ) -> None:
        result = prompt_batch_metadata()
        assert result.artist == "Led Zeppelin"
        assert result.album == "IV"
        assert result.genre == "Rock"
        assert result.date == "1971"
        # Title and track_number should be empty
        assert result.title == ""
        assert result.track_number == ""


class TestPromptTitleOnly:
    """Test per-file title-only prompting with batch merge."""

    @patch("builtins.input", return_value="")
    def test_accepts_default_title(self, mock_input: MagicMock) -> None:
        defaults = AudioMetadata(artist="File Artist", album="File Album", title="File Song")
        batch = AudioMetadata(artist="Batch Artist", album="Batch Album", genre="Rock", date="1971")
        result = prompt_title_only(defaults, batch, track_number=3)
        assert result.artist == "Batch Artist"
        assert result.album == "Batch Album"
        assert result.title == "File Song"
        assert result.genre == "Rock"
        assert result.date == "1971"
        assert result.track_number == "3"

    @patch("builtins.input", return_value="Custom Title")
    def test_overrides_title(self, mock_input: MagicMock) -> None:
        defaults = AudioMetadata(title="Default Title")
        batch = AudioMetadata(artist="Artist", album="Album")
        result = prompt_title_only(defaults, batch, track_number=1)
        assert result.title == "Custom Title"

    @patch("builtins.input", return_value="")
    def test_falls_back_to_defaults_when_batch_empty(self, mock_input: MagicMock) -> None:
        defaults = AudioMetadata(artist="File Artist", genre="Jazz", date="1960")
        batch = AudioMetadata()
        result = prompt_title_only(defaults, batch, track_number=0)
        assert result.artist == "File Artist"
        assert result.genre == "Jazz"
        assert result.date == "1960"
        assert result.track_number == ""

    @patch("builtins.input", return_value="")
    def test_preserves_description(self, mock_input: MagicMock) -> None:
        defaults = AudioMetadata(title="Song", description="A live recording")
        batch = AudioMetadata(artist="Artist", album="Album")
        result = prompt_title_only(defaults, batch, track_number=1)
        assert result.description == "A live recording"


class TestWriteMetadata:
    """Test metadata write-back to audio files."""

    @patch("gm.metadata.mutagen.File")
    def test_writes_all_tags(self, mock_file: MagicMock, tmp_path: Path) -> None:
        mock_audio = MagicMock()
        mock_file.return_value = mock_audio

        meta = AudioMetadata(
            artist="Artist", album="Album", title="Song",
            genre="Rock", date="2024", description="Desc", track_number="3",
        )
        write_metadata(tmp_path / "song.mp3", meta)

        mock_audio.__setitem__.assert_any_call("artist", "Artist")
        mock_audio.__setitem__.assert_any_call("album", "Album")
        mock_audio.__setitem__.assert_any_call("title", "Song")
        mock_audio.__setitem__.assert_any_call("genre", "Rock")
        mock_audio.__setitem__.assert_any_call("date", "2024")
        mock_audio.__setitem__.assert_any_call("tracknumber", "3")
        mock_audio.save.assert_called_once()

    @patch("gm.metadata.mutagen.File")
    def test_skips_empty_tags(self, mock_file: MagicMock, tmp_path: Path) -> None:
        mock_audio = MagicMock()
        mock_file.return_value = mock_audio

        meta = AudioMetadata(artist="Artist", album="", title="Song")
        write_metadata(tmp_path / "song.mp3", meta)

        # album is empty, should not be set
        calls = [c[0][0] for c in mock_audio.__setitem__.call_args_list]
        assert "album" not in calls
        assert "artist" in calls

    @patch("gm.metadata.mutagen.File", return_value=None)
    def test_handles_none_file(self, mock_file: MagicMock, tmp_path: Path) -> None:
        meta = AudioMetadata(artist="Artist", title="Song")
        # Should not raise
        write_metadata(tmp_path / "song.mp3", meta)

    @patch("gm.metadata.mutagen.File", side_effect=Exception("bad file"))
    def test_handles_exception(self, mock_file: MagicMock, tmp_path: Path) -> None:
        meta = AudioMetadata(artist="Artist", title="Song")
        # Should not raise
        write_metadata(tmp_path / "song.mp3", meta)

    @patch("gm.metadata.mutagen.File")
    def test_handles_unsupported_tag(self, mock_file: MagicMock, tmp_path: Path) -> None:
        mock_audio = MagicMock()
        mock_audio.__setitem__ = MagicMock(side_effect=KeyError("unsupported"))
        mock_file.return_value = mock_audio

        meta = AudioMetadata(artist="Artist", title="Song")
        # Should not raise even if tags fail
        write_metadata(tmp_path / "song.mp3", meta)

    @patch("gm.metadata.mutagen.File")
    def test_handles_save_failure(self, mock_file: MagicMock, tmp_path: Path) -> None:
        mock_audio = MagicMock()
        mock_audio.save.side_effect = Exception("save failed")
        mock_file.return_value = mock_audio

        meta = AudioMetadata(artist="Artist", title="Song")
        # Should not raise even if save fails
        write_metadata(tmp_path / "song.mp3", meta)


class TestWriteMetadataSsh:
    """Test SSH-based metadata writing via ffmpeg."""

    @patch("gm.metadata.ssh_run")
    def test_writes_metadata_via_ffmpeg(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 0, "", "")
        meta = AudioMetadata(artist="Artist", album="YouTube", title="Song")
        write_metadata_ssh("/mnt/nfs/music/Artist/YouTube/Song.opus", meta)

        cmd = mock_ssh.call_args_list[0][0][0]
        assert "ffmpeg" in cmd
        assert "-metadata artist=Artist" in cmd
        assert "-metadata album=YouTube" in cmd
        assert "-metadata title=Song" in cmd
        assert "-c copy" in cmd
        assert "Song.gm-tmp.opus" in cmd

    @patch("gm.metadata.ssh_run")
    def test_skips_empty_metadata(self, mock_ssh: MagicMock) -> None:
        meta = AudioMetadata()
        write_metadata_ssh("/mnt/nfs/music/A/B/C.opus", meta)
        mock_ssh.assert_not_called()

    @patch("gm.metadata.ssh_run")
    def test_cleans_up_on_failure(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 1, "", "error")
        meta = AudioMetadata(artist="Artist", album="YouTube")
        write_metadata_ssh("/mnt/nfs/music/Artist/YouTube/Song.opus", meta)

        # First call is ffmpeg, second is rm cleanup
        assert mock_ssh.call_count == 2
        cleanup_cmd = mock_ssh.call_args_list[1][0][0]
        assert "rm -f" in cleanup_cmd
        assert "Song.gm-tmp.opus" in cleanup_cmd

    @patch("gm.metadata.ssh_run")
    def test_includes_all_fields(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 0, "", "")
        meta = AudioMetadata(
            artist="A", album="B", title="C", genre="Rock", date="2024", track_number="5",
        )
        write_metadata_ssh("/mnt/nfs/music/A/B/C.opus", meta)

        cmd = mock_ssh.call_args_list[0][0][0]
        assert "-metadata artist=A" in cmd
        assert "-metadata album=B" in cmd
        assert "-metadata title=C" in cmd
        assert "-metadata genre=Rock" in cmd
        assert "-metadata date=2024" in cmd
        assert "-metadata track=5" in cmd
