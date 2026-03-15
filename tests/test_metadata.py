"""Tests for audio metadata extraction and user prompts."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from gm.metadata import (
    AudioMetadata,
    _normalized_prefix_end,
    _strip_artist_prefix,
    check_destination_exists,
    check_video_id_exists,
    extract_video_id_from_filename,
    humanize_name,
    list_existing_albums,
    list_existing_artists,
    normalize_date,
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


class TestExtractVideoIdFromFilename:
    """Test extracting YouTube video ID from filename stems."""

    def test_youtube_style_filename(self) -> None:
        assert extract_video_id_from_filename("Artist-Title-[dQw4w9WgXcQ]") == "dQw4w9WgXcQ"

    def test_non_youtube_filename(self) -> None:
        assert extract_video_id_from_filename("Artist-Title") == ""

    def test_plain_filename(self) -> None:
        assert extract_video_id_from_filename("song") == ""


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

    def test_preserves_spaced_dash_separator(self) -> None:
        assert humanize_name("Classical Music for Reading - Mozart, Chopin") == \
            "Classical Music for Reading - Mozart, Chopin"


class TestNormalizeDate:
    """Test date normalization."""

    def test_converts_yyyymmdd(self) -> None:
        assert normalize_date("20230415") == "2023-04-15"

    def test_preserves_yyyy_mm_dd(self) -> None:
        assert normalize_date("2023-04-15") == "2023-04-15"

    def test_preserves_bare_year(self) -> None:
        assert normalize_date("1971") == "1971"

    def test_strips_whitespace(self) -> None:
        assert normalize_date("  20230415  ") == "2023-04-15"

    def test_empty_string(self) -> None:
        assert normalize_date("") == ""

    def test_pads_single_digit_month_and_day(self) -> None:
        assert normalize_date("2023-4-5") == "2023-04-05"

    def test_pads_single_digit_month(self) -> None:
        assert normalize_date("2023-4-15") == "2023-04-15"

    def test_pads_single_digit_day(self) -> None:
        assert normalize_date("2023-12-5") == "2023-12-05"

    def test_passes_through_unknown_format(self) -> None:
        assert normalize_date("April 2023") == "April 2023"


class TestSanitizeFilename:
    """Test filename sanitization."""

    def test_removes_slashes(self) -> None:
        assert sanitize_filename("AC/DC") == "AC-DC"

    def test_removes_backslashes(self) -> None:
        assert sanitize_filename("back\\slash") == "back-slash"

    def test_removes_colons(self) -> None:
        assert sanitize_filename("Title: Subtitle") == "Title- Subtitle"

    def test_strips_whitespace(self) -> None:
        assert sanitize_filename("  hello  ") == "hello"

    def test_preserves_spaces(self) -> None:
        assert sanitize_filename("Good Song Name") == "Good Song Name"

    def test_collapses_multiple_hyphens(self) -> None:
        assert sanitize_filename("a - b") == "a - b"

    def test_replaces_null_bytes(self) -> None:
        assert sanitize_filename("bad\x00name") == "bad-name"

    def test_preserves_hyphenated_names(self) -> None:
        assert sanitize_filename("Good-Song-Name") == "Good-Song-Name"

    def test_replaces_dots_only_name(self) -> None:
        assert sanitize_filename("...") == "_"

    def test_removes_single_quotes(self) -> None:
        assert sanitize_filename("It's a Song") == "It-s a Song"

    def test_removes_double_quotes(self) -> None:
        assert sanitize_filename('Say "Hello"') == "Say -Hello"

    def test_removes_backticks(self) -> None:
        assert sanitize_filename("Song `Live`") == "Song -Live"

    def test_removes_dollar_sign(self) -> None:
        assert sanitize_filename("Ca$h Money") == "Ca-h Money"

    def test_removes_question_mark(self) -> None:
        assert sanitize_filename("Why?") == "Why"

    def test_removes_asterisk(self) -> None:
        assert sanitize_filename("Best*Of") == "Best-Of"

    def test_collapses_multiple_spaces(self) -> None:
        assert sanitize_filename("Hello   World") == "Hello World"

    def test_removes_angle_brackets(self) -> None:
        assert sanitize_filename("<Title>") == "Title"

    def test_removes_pipe(self) -> None:
        assert sanitize_filename("A|B") == "A-B"

    def test_removes_semicolons(self) -> None:
        assert sanitize_filename("A;B") == "A-B"

    def test_removes_ampersand(self) -> None:
        assert sanitize_filename("Tom & Jerry") == "Tom - Jerry"

    def test_removes_parentheses(self) -> None:
        assert sanitize_filename("Song (Live)") == "Song -Live"

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

    def test_spaces_preserved_in_path(self) -> None:
        meta = AudioMetadata(artist="Led Zeppelin", album="Led Zeppelin IV", title="Stairway To Heaven")
        result = build_destination_path(meta, ".flac")
        assert result == "/mnt/nfs/music/Led Zeppelin/Led Zeppelin IV/Stairway To Heaven.flac"

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
        assert meta.genre == ""
        assert meta.date == "1971"
        assert meta.description == "Classic rock track"
        assert meta.track_number == "4"

    @patch("gm.metadata.mutagen.File")
    def test_does_not_read_genre_tag(self, mock_file: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "song.mp3"
        f.write_bytes(b"\x00")

        mock_audio = MagicMock()
        mock_audio.tags = {
            "artist": ["Artist"],
            "title": ["Song"],
            "genre": ["Rock"],
        }
        mock_file.return_value = mock_audio

        meta = read_metadata(f)
        assert meta.genre == ""

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

    def test_falls_back_to_file_creation_date(self, tmp_path: Path) -> None:
        import re
        f = tmp_path / "no-date.mp3"
        f.write_bytes(b"\x00" * 100)
        meta = read_metadata(f)
        # No date tag in file → should use file creation date (YYYY-MM-DD)
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", meta.date)

    @patch("gm.metadata.mutagen.File")
    def test_preserves_tag_date_over_file_date(self, mock_file: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "song.mp3"
        f.write_bytes(b"\x00")

        mock_audio = MagicMock()
        mock_audio.tags = {
            "date": ["1971"],
            "title": ["Song"],
        }
        mock_file.return_value = mock_audio

        meta = read_metadata(f)
        assert meta.date == "1971"

    @patch("gm.metadata.mutagen.File")
    def test_normalizes_yyyymmdd_date_tag(self, mock_file: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "song.opus"
        f.write_bytes(b"\x00")

        mock_audio = MagicMock()
        mock_audio.tags = {
            "date": ["20230415"],
            "title": ["Song"],
        }
        mock_file.return_value = mock_audio

        meta = read_metadata(f)
        assert meta.date == "2023-04-15"

    def test_youtube_filename_fills_artist_album_title(self, tmp_path: Path) -> None:
        f = tmp_path / "Adam_Barrett-Jigsaw_Falling_Into_Place-[c99GmhBt7GM].mp3"
        f.write_bytes(b"\x00" * 100)
        meta = read_metadata(f)
        assert meta.artist == "Adam Barrett"
        assert meta.album == "Jigsaw Falling Into Place"
        assert meta.title == "Jigsaw Falling Into Place"

    @patch("gm.metadata.mutagen.File")
    def test_tag_metadata_takes_priority_over_youtube_filename(self, mock_file: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "SomeChannel-Some_Video-[dQw4w9WgXcQ].opus"
        f.write_bytes(b"\x00")

        mock_audio = MagicMock()
        mock_audio.tags = {
            "artist": ["Real Artist"],
            "album": ["Real Album"],
            "title": ["Real Title"],
            "date": ["2020-03-15"],
        }
        mock_file.return_value = mock_audio

        meta = read_metadata(f)
        assert meta.artist == "Real Artist"
        assert meta.album == "Real Album"
        assert meta.title == "Real Title"
        assert meta.date == "2020-03-15"

    def test_youtube_3part_channel_artist_title(self, tmp_path: Path) -> None:
        f = tmp_path / "BBC_Radio_6_Music-Ex_-Re_-_Romance_6_Music_Live_Room-[6pNhrlPUxfA].mp3"
        f.write_bytes(b"\x00" * 100)
        meta = read_metadata(f)
        assert meta.artist == "Ex -Re"
        assert meta.title == "Romance 6 Music Live Room"
        assert meta.album == "Romance 6 Music Live Room"

    def test_youtube_3part_not_applied_when_artist_too_long(self, tmp_path: Path) -> None:
        # "Jigsaw Falling Into Place" is 4 words — too long for artist, stays 2-part
        f = tmp_path / "Adam_Barrett-Jigsaw_Falling_Into_Place_-_Radiohead_cover-[c99GmhBt7GM].mp3"
        f.write_bytes(b"\x00" * 100)
        meta = read_metadata(f)
        assert meta.artist == "Adam Barrett"
        assert meta.title == "Jigsaw Falling Into Place - Radiohead cover"

    @patch("gm.metadata.mutagen.File")
    def test_youtube_3part_overrides_channel_artist_tag(self, mock_file: MagicMock, tmp_path: Path) -> None:
        """When embedded artist matches the channel, override with filename artist."""
        f = tmp_path / "Chelsea_Baker-Long_Beard_-_Someplace-[Wx1sZzslqC0].mp3"
        f.write_bytes(b"\x00")

        mock_audio = MagicMock()
        mock_audio.tags = {
            "artist": ["Chelsea Baker"],
            "title": ["Long Beard - Someplace"],
        }
        mock_file.return_value = mock_audio

        meta = read_metadata(f)
        assert meta.artist == "Long Beard"
        assert meta.title == "Long Beard - Someplace"  # tags take priority for title

    def test_non_youtube_filename_not_affected(self, tmp_path: Path) -> None:
        f = tmp_path / "normal-song.mp3"
        f.write_bytes(b"\x00" * 100)
        meta = read_metadata(f)
        assert meta.artist == ""
        assert meta.album == ""
        assert meta.title == "normal-song"

    @patch("gm.metadata.mutagen.File")
    def test_youtube_filename_fills_gaps_in_tags(self, mock_file: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "Adam_Barrett-Cool_Song-[c99GmhBt7GM].mp3"
        f.write_bytes(b"\x00")

        mock_audio = MagicMock()
        mock_audio.tags = {
            "date": ["2023-04-15"],
        }
        mock_file.return_value = mock_audio

        meta = read_metadata(f)
        # Artist/album/title filled from filename; date from tags
        assert meta.artist == "Adam Barrett"
        assert meta.album == "Cool Song"
        assert meta.title == "Cool Song"
        assert meta.date == "2023-04-15"


@patch("gm.metadata.list_existing_albums", return_value=[])
@patch("gm.metadata.list_existing_artists", return_value=[])
class TestPromptMetadata:
    """Test interactive metadata prompting."""

    @patch("builtins.input", side_effect=["", "", "", ""])
    def test_accepts_defaults(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(
            artist="Default Artist", album="Default Album", title="Default Title",
            date="2024",
        )
        result = prompt_metadata(defaults)
        assert result.artist == "Default Artist"
        assert result.title == "Default Title"
        assert result.album == "Default Title"  # album defaults to title
        assert result.date == "2024"

    @patch("builtins.input", side_effect=["Custom Artist", "Custom Title", "Custom Album", "1965"])
    def test_overrides_defaults(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(artist="Default", album="Default", title="Default")
        result = prompt_metadata(defaults)
        assert result.artist == "Custom Artist"
        assert result.title == "Custom Title"
        assert result.album == "Custom Album"
        assert result.date == "1965"

    @patch("builtins.input", side_effect=["", "", "My Album", ""])
    def test_partial_override(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(artist="Artist", album="Singles", title="Song")
        result = prompt_metadata(defaults)
        assert result.artist == "Artist"
        assert result.title == "Song"
        assert result.album == "My Album"

    @patch("builtins.input", side_effect=["Artist", "Title", "Album", "2023"])
    def test_fills_empty_defaults(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(artist="", album="", title="")
        result = prompt_metadata(defaults)
        assert result.artist == "Artist"
        assert result.title == "Title"
        assert result.album == "Album"
        assert result.date == "2023"

    @patch("builtins.input", side_effect=["", "", "", ""])
    def test_preserves_description_and_track(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(
            artist="Artist", album="Album", title="Song",
            description="A live recording from 1969", track_number="3",
        )
        result = prompt_metadata(defaults)
        assert result.description == "A live recording from 1969"
        assert result.track_number == "3"

    @patch("builtins.input", side_effect=["", "", "", "-"])
    def test_hyphen_clears_default(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(
            artist="Artist", album="Album", title="Song",
            date="2024",
        )
        result = prompt_metadata(defaults)
        assert result.artist == "Artist"
        assert result.date == ""

    @patch("builtins.input", side_effect=["", "", "", "  "])
    def test_space_clears_default(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(
            artist="Artist", album="Album", title="Song",
            date="2024",
        )
        result = prompt_metadata(defaults)
        assert result.artist == "Artist"
        assert result.date == ""

    @patch("builtins.input", side_effect=["", "", "", ""])
    def test_album_defaults_to_title(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(artist="Artist", title="My Song")
        result = prompt_metadata(defaults)
        assert result.title == "My Song"
        assert result.album == "My Song"


@patch("gm.metadata.list_existing_artists", return_value=[])
class TestPromptMetadataSingle:
    """Test prompt_metadata in single mode (album = title)."""

    @patch("builtins.input", side_effect=["", "", ""])
    def test_single_sets_album_to_title(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(artist="Artist", title="My Song", date="2024")
        result = prompt_metadata(defaults, single=True)
        assert result.artist == "Artist"
        assert result.album == "My Song"
        assert result.title == "My Song"
        assert result.date == "2024"

    @patch("builtins.input", side_effect=["", "Custom Title", ""])
    def test_single_album_follows_overridden_title(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(artist="Artist", title="Default Title")
        result = prompt_metadata(defaults, single=True)
        assert result.album == "Custom Title"
        assert result.title == "Custom Title"

    @patch("builtins.input", side_effect=["", "", ""])
    def test_single_skips_album_prompt(self, mock_input: object, *_mocks: object) -> None:
        """Single mode prompts 3 fields (artist, title, date), not 4."""
        defaults = AudioMetadata(artist="Artist", title="Song", date="2024")
        prompt_metadata(defaults, single=True)
        assert mock_input.call_count == 3

    @patch("builtins.input", side_effect=["", "", ""])
    def test_single_preserves_description(self, mock_input: object, *_mocks: object) -> None:
        defaults = AudioMetadata(
            artist="Artist", title="Song",
            description="Live recording", track_number="1",
        )
        result = prompt_metadata(defaults, single=True)
        assert result.description == "Live recording"
        assert result.track_number == "1"


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
        # "Led Zeppelin" normalizes to match "Led-Zeppelin" via space/hyphen equivalence
        assert suggest_match("Led Zeppelin", ["Led-Zeppelin", "Pink-Floyd"]) == "Led-Zeppelin"

    def test_normalized_match_spaces_to_hyphens(self) -> None:
        # "Forrest Frank" matches existing "Forrest-Frank" via normalization
        assert suggest_match("Forrest Frank", ["Forrest-Frank"]) == "Forrest-Frank"

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
    @patch("gm.metadata.list_existing_artists", return_value=["Led Zeppelin"])
    @patch("builtins.input", side_effect=[
        "led zeppelin",  # artist prompt — case-insensitive match, no suggestion needed
        "Stairway",      # title prompt
        "IV",            # album prompt
        "1971",          # date prompt
    ])
    def test_silent_match_when_case_insensitive_equals_input(
        self, mock_input: MagicMock, mock_artists: MagicMock, mock_albums: MagicMock,
    ) -> None:
        defaults = AudioMetadata()
        result = prompt_metadata(defaults)
        assert result.artist == "led zeppelin"

    @patch("gm.metadata.list_existing_albums", return_value=[])
    @patch("gm.metadata.list_existing_artists", return_value=["Led-Zeppelin"])
    @patch("builtins.input", side_effect=[
        "Led Zeplin",    # artist prompt — typo, fuzzy matches Led-Zeppelin
        "y",             # "Did you mean 'Led-Zeppelin'?"
        "Stairway",      # title prompt
        "IV",            # album prompt
        "1971",          # date prompt
    ])
    def test_suggests_directory_name_for_typo(
        self, mock_input: MagicMock, mock_artists: MagicMock, mock_albums: MagicMock,
    ) -> None:
        defaults = AudioMetadata()
        result = prompt_metadata(defaults)
        assert result.artist == "Led-Zeppelin"

    @patch("gm.metadata.list_existing_albums", return_value=[])
    @patch("gm.metadata.list_existing_artists", return_value=["Led-Zeppelin"])
    @patch("builtins.input", side_effect=[
        "Led Zeplin",    # artist prompt — typo
        "n",             # reject suggestion
        "Stairway",      # title prompt
        "IV",            # album prompt
        "1971",          # date prompt
    ])
    def test_rejects_artist_suggestion(
        self, mock_input: MagicMock, mock_artists: MagicMock, mock_albums: MagicMock,
    ) -> None:
        defaults = AudioMetadata()
        result = prompt_metadata(defaults)
        assert result.artist == "Led Zeplin"

    @patch("gm.metadata.list_existing_albums", return_value=[])
    @patch("gm.metadata.list_existing_artists", return_value=["Ex-Re"])
    @patch("builtins.input", side_effect=[
        "Ex:Re",         # artist prompt — colon sanitizes to same dir, keep as-is
        "Romance",       # title prompt
        "Ex:Re",         # album prompt
        "2019",          # date prompt
    ])
    def test_keeps_special_chars_when_sanitized_form_matches(
        self, mock_input: MagicMock, mock_artists: MagicMock, mock_albums: MagicMock,
    ) -> None:
        defaults = AudioMetadata()
        result = prompt_metadata(defaults)
        # "Ex:Re" sanitizes to "Ex-Re" which matches — keep the user's "Ex:Re"
        assert result.artist == "Ex:Re"

    @patch("gm.metadata.list_existing_albums", return_value=[])
    @patch("gm.metadata.list_existing_artists", return_value=["Jay-Z"])
    @patch("builtins.input", side_effect=[
        "jay z",         # artist prompt — fuzzy matches Jay-Z
        "y",             # "Did you mean 'Jay-Z'?"
        "99 Problems",   # title prompt
        "The Black Album",  # album prompt
        "2003",          # date prompt
    ])
    def test_preserves_hyphen_in_artist_name(
        self, mock_input: MagicMock, mock_artists: MagicMock, mock_albums: MagicMock,
    ) -> None:
        defaults = AudioMetadata()
        result = prompt_metadata(defaults)
        assert result.artist == "Jay-Z"

    @patch("gm.metadata.list_existing_albums", return_value=[])
    @patch("gm.metadata.list_existing_artists", return_value=["AC-DC"])
    @patch("builtins.input", side_effect=[
        "AC/DC",         # artist prompt — slash sanitizes to same dir
        "Hells Bells",   # title prompt
        "Back-In-Black", # album prompt
        "1980",          # date prompt
    ])
    def test_keeps_slash_when_sanitized_form_matches(
        self, mock_input: MagicMock, mock_artists: MagicMock, mock_albums: MagicMock,
    ) -> None:
        defaults = AudioMetadata()
        result = prompt_metadata(defaults)
        assert result.artist == "AC/DC"


class TestPromptBatchMetadata:
    """Test batch metadata prompting."""

    @patch("gm.metadata.list_existing_albums", return_value=[])
    @patch("gm.metadata.list_existing_artists", return_value=[])
    @patch("builtins.input", side_effect=["Led Zeppelin", "IV", "1971"])
    def test_prompts_shared_fields(
        self, mock_input: MagicMock, mock_artists: MagicMock, mock_albums: MagicMock,
    ) -> None:
        result = prompt_batch_metadata()
        assert result.artist == "Led Zeppelin"
        assert result.album == "IV"
        assert result.date == "1971"
        # Title and track_number should be empty
        assert result.title == ""
        assert result.track_number == ""


class TestPromptTitleOnly:
    """Test per-file title-only prompting with batch merge."""

    @patch("builtins.input", return_value="")
    def test_accepts_default_title(self, mock_input: MagicMock) -> None:
        defaults = AudioMetadata(artist="File Artist", album="File Album", title="File Song")
        batch = AudioMetadata(artist="Batch Artist", album="Batch Album", date="1971")
        result = prompt_title_only(defaults, batch, track_number=3)
        assert result.artist == "Batch Artist"
        assert result.album == "Batch Album"
        assert result.title == "File Song"
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
        defaults = AudioMetadata(artist="File Artist", date="1960")
        batch = AudioMetadata()
        result = prompt_title_only(defaults, batch, track_number=0)
        assert result.artist == "File Artist"
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
    def test_clears_empty_tags(self, mock_file: MagicMock, tmp_path: Path) -> None:
        mock_audio = MagicMock()
        mock_file.return_value = mock_audio

        meta = AudioMetadata(artist="Artist", album="", title="Song")
        write_metadata(tmp_path / "song.mp3", meta)

        # album is empty — should be deleted, not set
        set_calls = [c[0][0] for c in mock_audio.__setitem__.call_args_list]
        assert "album" not in set_calls
        assert "artist" in set_calls
        del_calls = [c[0][0] for c in mock_audio.__delitem__.call_args_list]
        assert "album" in del_calls

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
        meta = AudioMetadata(artist="Artist", album="Song", title="Song")
        write_metadata_ssh("/mnt/nfs/music/Artist/Song/Song.opus", meta)

        cmd = mock_ssh.call_args_list[0][0][0]
        assert "ffmpeg" in cmd
        assert "-metadata artist=Artist" in cmd
        assert "-metadata album=Song" in cmd
        assert "-metadata title=Song" in cmd
        assert "-map 0:a" in cmd
        assert "-c copy" in cmd

    @patch("gm.metadata.ssh_run")
    def test_skips_empty_metadata(self, mock_ssh: MagicMock) -> None:
        meta = AudioMetadata()
        write_metadata_ssh("/mnt/nfs/music/A/B/C.opus", meta)
        mock_ssh.assert_not_called()

    @patch("gm.metadata.ssh_run")
    def test_cleans_up_on_failure(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 1, "", "error")
        meta = AudioMetadata(artist="Artist", album="Song")
        write_metadata_ssh("/mnt/nfs/music/Artist/Song/Song.opus", meta)

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

    @patch("gm.metadata.ssh_run")
    def test_clears_empty_fields_via_ffmpeg(self, mock_ssh: MagicMock) -> None:
        mock_ssh.return_value = subprocess.CompletedProcess([], 0, "", "")
        meta = AudioMetadata(artist="Artist", title="Song")
        write_metadata_ssh("/mnt/nfs/music/Artist/Song/Song.opus", meta)

        cmd = mock_ssh.call_args_list[0][0][0]
        assert "-metadata artist=Artist" in cmd
        assert "-metadata title=Song" in cmd
        # Empty fields should be explicitly cleared
        assert "-metadata genre=" in cmd
        assert "-metadata date=" in cmd


class TestNormalizedPrefixEnd:
    """Test alphanumeric-only prefix matching."""

    def test_exact_match(self) -> None:
        assert _normalized_prefix_end("Joe Bloggs - Song", "Joe Bloggs") == 10

    def test_punctuation_differences(self) -> None:
        # "Ex:Re" matches "Ex -Re" (only alphanumeric chars compared)
        assert _normalized_prefix_end("Ex -Re - Romance", "Ex:Re") == 6

    def test_case_insensitive(self) -> None:
        assert _normalized_prefix_end("joe bloggs - Song", "Joe Bloggs") == 10

    def test_no_match(self) -> None:
        assert _normalized_prefix_end("Other Artist", "Joe Bloggs") == -1

    def test_prefix_longer_than_text(self) -> None:
        assert _normalized_prefix_end("Ex", "Ex:Re") == -1

    def test_trailing_punctuation_in_prefix(self) -> None:
        # Trailing non-alnum in prefix is OK; returns position after consuming
        # non-alnum chars in both strings (the ":" in prefix, " " in text)
        assert _normalized_prefix_end("ABC song", "ABC:") == 4

    def test_empty_prefix(self) -> None:
        assert _normalized_prefix_end("anything", "") == 0

    def test_empty_text(self) -> None:
        assert _normalized_prefix_end("", "artist") == -1


class TestStripArtistPrefix:
    """Test stripping artist name from title suggestions."""

    def test_strips_hyphen_separator(self) -> None:
        assert _strip_artist_prefix("Joe Bloggs - My Song", "Joe Bloggs") == "My Song"

    def test_strips_en_dash_separator(self) -> None:
        assert _strip_artist_prefix("Joe Bloggs \u2013 My Song", "Joe Bloggs") == "My Song"

    def test_strips_em_dash_separator(self) -> None:
        assert _strip_artist_prefix("Joe Bloggs \u2014 My Song", "Joe Bloggs") == "My Song"

    def test_case_insensitive(self) -> None:
        assert _strip_artist_prefix("joe bloggs - My Song", "Joe Bloggs") == "My Song"

    def test_strips_colon_separator(self) -> None:
        assert _strip_artist_prefix("Joe Bloggs: My Song", "Joe Bloggs") == "My Song"

    def test_strips_space_separator(self) -> None:
        assert _strip_artist_prefix("Joe Bloggs My Song", "Joe Bloggs") == "My Song"

    def test_artist_not_at_start(self) -> None:
        assert _strip_artist_prefix("My Song by Joe Bloggs", "Joe Bloggs") == "My Song by Joe Bloggs"

    def test_empty_artist(self) -> None:
        assert _strip_artist_prefix("Some Title", "") == "Some Title"

    def test_empty_title(self) -> None:
        assert _strip_artist_prefix("", "Joe Bloggs") == ""

    def test_no_match(self) -> None:
        assert _strip_artist_prefix("Other Artist - Song", "Joe Bloggs") == "Other Artist - Song"

    def test_preserves_title_with_internal_separator(self) -> None:
        assert _strip_artist_prefix("Joe Bloggs - My Song - Live", "Joe Bloggs") == "My Song - Live"

    def test_normalized_punctuation_match(self) -> None:
        # "Ex:Re" should match "Ex -Re" in the title via alphanumeric comparison
        assert _strip_artist_prefix("Ex -Re - Romance 6 Music Live Room", "Ex:Re") == "Romance 6 Music Live Room"

    def test_title_equals_artist(self) -> None:
        # When title is just the artist, return unchanged
        assert _strip_artist_prefix("Joe Bloggs", "Joe Bloggs") == "Joe Bloggs"

    def test_possessive_not_stripped(self) -> None:
        # "Ben Howard's ..." should NOT be stripped — apostrophe continues the word
        title = "Ben Howard's breathtaking performance of End of the Affair"
        assert _strip_artist_prefix(title, "Ben Howard") == title

    def test_word_boundary_required(self) -> None:
        # "BenHowardLive" should NOT be stripped — no word boundary
        assert _strip_artist_prefix("BenHowardLive Session", "BenHoward") == "BenHowardLive Session"


@patch("gm.metadata.list_existing_albums", return_value=[])
@patch("gm.metadata.list_existing_artists", return_value=[])
class TestArtistStrippingInPrompt:
    """Test that artist prefix is stripped from title and album suggestions."""

    @patch("builtins.input", side_effect=["Joe Bloggs", "", "", "2024"])
    def test_prompt_metadata_strips_artist_from_title(
        self, mock_input: MagicMock, *_mocks: object,
    ) -> None:
        defaults = AudioMetadata(
            artist="Joe Bloggs", album="Album", title="Joe Bloggs - My Song",
        )
        result = prompt_metadata(defaults)
        assert result.title == "My Song"
        assert result.album == "My Song"  # album defaults to title

    @patch("builtins.input", side_effect=["Joe Bloggs", "", "", "2024"])
    def test_prompt_metadata_no_strip_when_no_match(
        self, mock_input: MagicMock, *_mocks: object,
    ) -> None:
        defaults = AudioMetadata(
            artist="Joe Bloggs", album="Album", title="Unrelated Title",
        )
        result = prompt_metadata(defaults)
        assert result.title == "Unrelated Title"

    @patch("builtins.input", side_effect=["Ex:Re", "", "", "2019"])
    def test_prompt_metadata_strips_artist_from_title_and_album_follows(
        self, mock_input: MagicMock, *_mocks: object,
    ) -> None:
        defaults = AudioMetadata(
            artist="BBC Radio 6 Music",
            album="Ex -Re - Romance 6 Music Live Room",
            title="Ex -Re - Romance 6 Music Live Room",
        )
        result = prompt_metadata(defaults)
        assert result.title == "Romance 6 Music Live Room"
        assert result.album == "Romance 6 Music Live Room"

    @patch("builtins.input", side_effect=["Adam Barrett", "", "", "2023"])
    def test_prompt_metadata_album_defaults_to_title(
        self, mock_input: MagicMock, *_mocks: object,
    ) -> None:
        defaults = AudioMetadata(
            artist="Adam Barrett",
            album="Jigsaw Falling Into Place",
            title="Jigsaw Falling Into Place",
        )
        result = prompt_metadata(defaults)
        assert result.title == "Jigsaw Falling Into Place"
        assert result.album == "Jigsaw Falling Into Place"


class TestTitleStrippingBatch:
    """Test artist prefix stripping in batch (title-only) prompts."""

    @patch("builtins.input", return_value="")
    def test_prompt_title_only_strips_artist(self, mock_input: MagicMock) -> None:
        defaults = AudioMetadata(title="Joe Bloggs - My Song")
        batch = AudioMetadata(artist="Joe Bloggs", album="Album")
        result = prompt_title_only(defaults, batch, track_number=1)
        assert result.title == "My Song"


@patch("gm.metadata.list_existing_albums", return_value=[])
@patch("gm.metadata.list_existing_artists", return_value=[])
class TestGoBack:
    """Test '<' input goes back to the previous field."""

    @patch("builtins.input", side_effect=["Typo Artist", "<", "Good Artist", "", "", "2024"])
    def test_back_from_title_to_artist(
        self, mock_input: MagicMock, *_mocks: object,
    ) -> None:
        defaults = AudioMetadata(artist="Def Artist", title="Song")
        result = prompt_metadata(defaults)
        assert result.artist == "Good Artist"

    @patch("builtins.input", side_effect=["Artist", "Title", "<", "Better Title", "", "2024"])
    def test_back_from_album_to_title(
        self, mock_input: MagicMock, *_mocks: object,
    ) -> None:
        defaults = AudioMetadata(artist="Artist", title="Song")
        result = prompt_metadata(defaults)
        assert result.title == "Better Title"
        assert result.album == "Better Title"  # album defaults to title

    @patch("builtins.input", side_effect=["Artist", "Title", "Album", "<", "New Album", "2024"])
    def test_back_from_date_to_album(
        self, mock_input: MagicMock, *_mocks: object,
    ) -> None:
        defaults = AudioMetadata(artist="Artist", title="Song")
        result = prompt_metadata(defaults)
        assert result.album == "New Album"

    @patch("builtins.input", side_effect=["<", "Artist", "", "", "2024"])
    def test_back_at_first_field_stays(
        self, mock_input: MagicMock, *_mocks: object,
    ) -> None:
        """'<' at the first field just re-prompts the same field."""
        defaults = AudioMetadata(artist="Def", title="Song")
        result = prompt_metadata(defaults)
        assert result.artist == "Artist"

    @patch("builtins.input", side_effect=["Artist", "<", "Fixed", "", "2024"])
    def test_back_from_title_single_mode(
        self, mock_input: MagicMock, *_mocks: object,
    ) -> None:
        """In single mode (YouTube), back from title goes to artist."""
        defaults = AudioMetadata(artist="Artist", title="Song")
        result = prompt_metadata(defaults, single=True)
        assert result.artist == "Fixed"
        assert result.album == result.title  # single mode sets album=title


@patch("gm.metadata.list_existing_albums", return_value=[])
@patch("gm.metadata.list_existing_artists", return_value=[])
class TestGoBackBatch:
    """Test '<' in batch metadata prompts."""

    @patch("builtins.input", side_effect=["Artist", "<", "Better Artist", "", "2024"])
    def test_back_from_album_to_artist(
        self, mock_input: MagicMock, *_mocks: object,
    ) -> None:
        result = prompt_batch_metadata()
        assert result.artist == "Better Artist"

    @patch("builtins.input", side_effect=["Artist", "Album", "<", "New Album", "2024"])
    def test_back_from_date_to_album(
        self, mock_input: MagicMock, *_mocks: object,
    ) -> None:
        result = prompt_batch_metadata()
        assert result.album == "New Album"
