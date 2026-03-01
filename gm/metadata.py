"""Audio metadata extraction and user prompts."""

from __future__ import annotations

import difflib
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

import mutagen

from gm.ssh import ssh_run, quote_path


MUSIC_ROOT = "/mnt/nfs/music"


@dataclass
class AudioMetadata:
    artist: str = ""
    album: str = ""
    title: str = ""
    genre: str = ""
    date: str = ""
    description: str = ""
    track_number: str = ""


_UNSAFE_CHARS = re.compile(r"""[/\\:\x00 '"` \$\?\*<>\|;&\(\)\n\t\r]""")


def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename or directory name.

    Replaces spaces and shell/filesystem-unsafe characters with hyphens.
    """
    name = name.strip()
    name = _UNSAFE_CHARS.sub("-", name)
    # Collapse multiple consecutive hyphens
    while "--" in name:
        name = name.replace("--", "-")
    name = name.strip("-")
    if not name or all(c == "." for c in name):
        return "_"
    return name


def build_destination_path(
    meta: AudioMetadata, extension: str, *, video_id: str = ""
) -> str:
    """Build the full destination path on the LXC from metadata."""
    artist = sanitize_filename(meta.artist)
    album = sanitize_filename(meta.album)
    title = sanitize_filename(meta.title)
    if video_id:
        return f"{MUSIC_ROOT}/{artist}/{album}/{title}-[{video_id}]{extension}"
    return f"{MUSIC_ROOT}/{artist}/{album}/{title}{extension}"


def read_metadata(path: Path) -> AudioMetadata:
    """Read metadata from an audio file using mutagen."""
    meta = AudioMetadata()

    if not path.exists():
        return meta

    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        audio = None

    if audio is not None and audio.tags:
        meta.artist = _first_tag(audio, "artist")
        meta.album = _first_tag(audio, "album")
        meta.title = _first_tag(audio, "title")
        meta.genre = _first_tag(audio, "genre")
        meta.date = _first_tag(audio, "date")
        meta.description = _first_tag(audio, "description")
        meta.track_number = _first_tag(audio, "tracknumber")

    if not meta.title:
        meta.title = path.stem

    return meta


def _first_tag(audio: mutagen.FileType, key: str) -> str:
    """Extract the first value of a tag, or empty string."""
    values = audio.tags.get(key)
    if values and isinstance(values, list):
        return str(values[0])
    if values:
        return str(values)
    return ""


_TAG_MAP = {
    "artist": "artist",
    "album": "album",
    "title": "title",
    "genre": "genre",
    "date": "date",
    "description": "description",
    "track_number": "tracknumber",
}


def write_metadata(path: Path, meta: AudioMetadata) -> None:
    """Write metadata tags back into an audio file. Best-effort."""
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        return
    if audio is None:
        return

    for attr, tag_key in _TAG_MAP.items():
        value = getattr(meta, attr, "")
        if not value:
            continue
        try:
            audio[tag_key] = value
        except (KeyError, mutagen.MutagenError):
            pass

    try:
        audio.save()
    except Exception:
        pass


def write_metadata_ssh(dest: str, meta: AudioMetadata) -> None:
    """Write metadata tags into an audio file on the LXC using ffmpeg.

    Best-effort: failures are silently ignored since the file is already
    in place with yt-dlp's embedded metadata.
    """
    metadata_args: list[str] = []
    for attr, tag in [
        ("artist", "artist"),
        ("album", "album"),
        ("title", "title"),
        ("genre", "genre"),
        ("date", "date"),
        ("track_number", "track"),
    ]:
        value = getattr(meta, attr, "")
        if value:
            metadata_args.extend(["-metadata", f"{tag}={value}"])
    if not metadata_args:
        return

    tmp = f"{dest}.gm-tmp"
    ffmpeg_cmd = shlex.join(
        ["ffmpeg", "-y", "-i", dest, "-map", "0", "-c", "copy"]
        + metadata_args
        + [tmp]
    )
    result = ssh_run(f"{ffmpeg_cmd} && mv {quote_path(tmp)} {quote_path(dest)}")
    if result.returncode != 0:
        ssh_run(f"rm -f {quote_path(tmp)}")


def list_existing_artists() -> list[str]:
    """List artist directories on the LXC."""
    result = ssh_run(f"ls -1 '{MUSIC_ROOT}' 2>/dev/null")
    if result.returncode != 0 or not result.stdout.strip():
        return []
    return [line for line in result.stdout.strip().split("\n") if line]


def list_existing_albums(artist: str) -> list[str]:
    """List album directories for an artist on the LXC."""
    safe_artist = sanitize_filename(artist)
    result = ssh_run(f"ls -1 {quote_path(f'{MUSIC_ROOT}/{safe_artist}')} 2>/dev/null")
    if result.returncode != 0 or not result.stdout.strip():
        return []
    return [line for line in result.stdout.strip().split("\n") if line]


def suggest_match(user_input: str, existing: list[str]) -> str:
    """Suggest an existing directory name matching user input.

    Checks (in order): case-insensitive exact, sanitized form match, then fuzzy.
    Returns the suggested name or empty string.
    """
    if not user_input or not existing:
        return ""

    # Case-insensitive exact match
    lower_map = {e.lower(): e for e in existing}
    if user_input.lower() in lower_map:
        return lower_map[user_input.lower()]

    # Sanitized form match (e.g. "Led Zeppelin" -> "Led-Zeppelin")
    sanitized = sanitize_filename(user_input)
    sanitized_map = {e.lower(): e for e in existing}
    if sanitized.lower() in sanitized_map:
        return sanitized_map[sanitized.lower()]

    # Fuzzy match
    matches = difflib.get_close_matches(
        user_input.lower(),
        [e.lower() for e in existing],
        n=1,
        cutoff=0.6,
    )
    if matches:
        return lower_map[matches[0]]

    return ""


def _apply_suggestion(user_input: str, existing: list[str]) -> str:
    """Check for a match and prompt the user if found. Returns final value."""
    match = suggest_match(user_input, existing)
    if match and match != user_input:
        confirm = input(f"  Did you mean '{match}'? [Y/n]: ").strip().lower()
        if confirm != "n":
            return match
    return user_input


def prompt_metadata(defaults: AudioMetadata) -> AudioMetadata:
    """Prompt the user to confirm or override metadata fields."""
    print("\nMetadata (press Enter to accept default):")

    artist = _prompt_field("Artist", defaults.artist)
    artist = _apply_suggestion(artist, list_existing_artists())

    album = _prompt_field("Album", defaults.album)
    album = _apply_suggestion(album, list_existing_albums(artist))

    title = _prompt_field("Title", defaults.title)
    genre = _prompt_field("Genre", defaults.genre)
    date = _prompt_field("Date", defaults.date)

    return AudioMetadata(
        artist=artist,
        album=album,
        title=title,
        genre=genre,
        date=date,
        description=defaults.description,
        track_number=defaults.track_number,
    )


def _prompt_field(label: str, default: str) -> str:
    """Prompt for a single metadata field with a default value."""
    if default:
        value = input(f"  {label} [{default}]: ")
        return value.strip() if value.strip() else default
    return input(f"  {label}: ").strip()


def prompt_batch_metadata() -> AudioMetadata:
    """Prompt for shared metadata fields (artist, album, genre, date) once for a batch."""
    print("\nShared metadata for all files (press Enter to leave empty):")

    artist = _prompt_field("Artist", "")
    artist = _apply_suggestion(artist, list_existing_artists())

    album = _prompt_field("Album", "")
    album = _apply_suggestion(album, list_existing_albums(artist))

    genre = _prompt_field("Genre", "")
    date = _prompt_field("Date", "")

    return AudioMetadata(artist=artist, album=album, genre=genre, date=date)


def prompt_title_only(
    defaults: AudioMetadata, batch: AudioMetadata, track_number: int,
) -> AudioMetadata:
    """Merge batch metadata with per-file defaults, prompt only for title."""
    artist = batch.artist or defaults.artist
    album = batch.album or defaults.album
    genre = batch.genre or defaults.genre
    date = batch.date or defaults.date

    title = _prompt_field("Title", defaults.title)

    return AudioMetadata(
        artist=artist,
        album=album,
        title=title,
        genre=genre,
        date=date,
        description=defaults.description,
        track_number=str(track_number) if track_number else defaults.track_number,
    )


def check_destination_exists(dest: str) -> bool:
    """Check whether a file already exists at dest on the LXC."""
    result = ssh_run(f"test -e {quote_path(dest)}")
    return result.returncode == 0


def check_video_id_exists(artist_dir: str, video_id: str) -> str:
    """Check if a file containing [video_id] already exists under artist_dir.

    Returns the path of the existing file, or empty string.
    """
    if not video_id:
        return ""
    result = ssh_run(f"find {quote_path(artist_dir)} -name '*\\[{video_id}\\]*' -type f 2>/dev/null | head -1")
    return result.stdout.strip()


def prompt_duplicate_action(existing_path: str) -> str:
    """Prompt the user to choose how to handle a duplicate.

    Returns "skip", "overwrite", or "rename".
    """
    print(f"\n  Duplicate found: {existing_path}")
    choice = input("  Action — [s]kip / [o]verwrite / [r]ename: ").strip().lower()
    if choice in ("o", "overwrite"):
        return "overwrite"
    if choice in ("r", "rename"):
        return "rename"
    return "skip"
