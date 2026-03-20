"""Audio metadata extraction and user prompts."""

from __future__ import annotations

import difflib
import os
import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import mutagen

from gm.ui import E_WARN, E_ERROR, bold, bold_cyan, bold_yellow, cyan, dim, yellow
from gm.ssh import ssh_run, quote_path


MUSIC_ROOT = "/mnt/nfs/music"
YOUTUBE_ROOT = "/mnt/nfs/music/youtube"


@dataclass
class AudioMetadata:
    artist: str = ""
    album: str = ""
    title: str = ""
    genre: str = ""
    date: str = ""
    description: str = ""
    track_number: str = ""


_UNSAFE_CHARS = re.compile(r"""[/\\:\x00'"`\$\?\*<>\|;&\(\)\n\t\r]""")


def humanize_name(name: str) -> str:
    """Convert a hyphenated filename-style name back to spaces for metadata.

    Only replaces hyphens that join words (no surrounding spaces), so
    "Led-Zeppelin" becomes "Led Zeppelin" but " - " separators are
    preserved.  Filesystem paths use sanitize_filename for the reverse.
    """
    return re.sub(r"(?<!\s)-(?!\s)", " ", name)


def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename or directory name.

    Replaces shell/filesystem-unsafe characters with hyphens. Spaces are
    preserved to match Lidarr's directory naming convention.
    """
    name = name.strip()
    name = _UNSAFE_CHARS.sub("-", name)
    # Collapse multiple consecutive hyphens
    while "--" in name:
        name = name.replace("--", "-")
    # Collapse multiple consecutive spaces
    while "  " in name:
        name = name.replace("  ", " ")
    name = name.strip("- ")
    if not name or all(c == "." for c in name):
        return "_"
    return name


def build_destination_path(
    meta: AudioMetadata, extension: str, *, video_id: str = "",
    music_root: str = MUSIC_ROOT,
) -> str:
    """Build the full destination path on the LXC from metadata."""
    artist = sanitize_filename(meta.artist)
    album = sanitize_filename(meta.album)
    title = sanitize_filename(meta.title)
    if video_id:
        return f"{music_root}/{artist}/{album}/{title}-[{video_id}]{extension}"
    return f"{music_root}/{artist}/{album}/{title}{extension}"


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
        meta.date = _first_tag(audio, "date")
        meta.description = _first_tag(audio, "description")
        meta.track_number = _first_tag(audio, "tracknumber")

    # Fill gaps from YouTube-style filename (Artist_Name-Title-[videoID])
    yt = _parse_youtube_filename(path.stem)
    if yt:
        channel = yt.get("channel", "")
        # Override artist if empty or if it matches the channel name
        # (embedded tags from yt-dlp often set artist to the channel)
        if not meta.artist or (channel and meta.artist == channel):
            meta.artist = yt["artist"]
        if not meta.album:
            meta.album = yt["album"]
        if not meta.title:
            meta.title = yt["title"]

    if not meta.title:
        meta.title = path.stem

    if not meta.date:
        meta.date = _file_creation_date(path)

    meta.date = normalize_date(meta.date)
    return meta


_YT_FILENAME_RE = re.compile(r"^([^-]+)-(.+)-\[([a-zA-Z0-9_-]{9,12})\]$")


def extract_video_id_from_filename(stem: str) -> str:
    """Extract YouTube video ID from a filename stem like 'Artist-Title-[videoID]'."""
    match = _YT_FILENAME_RE.match(stem)
    return match.group(3) if match else ""


_MAX_ARTIST_WORDS = 3


def _parse_youtube_filename(stem: str) -> dict[str, str] | None:
    """Parse artist and title from a YouTube-style filename.

    Detects filenames like 'Artist_Name-Song_Title-[videoID]' and returns
    extracted metadata, or None if the pattern doesn't match.

    Also handles 3-part filenames 'Channel-Artist-Title-[videoID]' where
    the title portion contains ' - ' (from _-_ in restricted filenames).
    If the segment before the first ' - ' is short (≤3 words), it's
    treated as the artist and the first group becomes a channel (discarded).
    """
    match = _YT_FILENAME_RE.match(stem)
    if not match:
        return None
    first_raw, rest_raw, _video_id = match.groups()
    rest = rest_raw.replace("_", " ").strip()

    # Check for channel-artist-title structure
    if " - " in rest:
        candidate_artist, _, candidate_title = rest.partition(" - ")
        candidate_artist = candidate_artist.strip()
        candidate_title = candidate_title.strip()
        if (
            candidate_artist
            and candidate_title
            and len(candidate_artist.split()) <= _MAX_ARTIST_WORDS
        ):
            return {
                "artist": candidate_artist,
                "title": candidate_title,
                "album": candidate_title,
                "channel": first_raw.replace("_", " ").strip(),
            }

    # Default 2-part: first segment is artist, rest is title
    title = rest
    return {
        "artist": first_raw.replace("_", " ").strip(),
        "title": title,
        "album": title,
    }


_DATE_DASH_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


def normalize_date(date: str) -> str:
    """Normalize a date string to YYYY-MM-DD where possible.

    Handles YYYYMMDD (from yt-dlp), YYYY-M-D (unpadded), and passes through
    bare years (YYYY) unchanged.  Returns the original string for anything else.
    """
    date = date.strip()
    if len(date) == 8 and date.isdigit():
        return f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    m = _DATE_DASH_RE.match(date)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return date


def _file_creation_date(path: Path) -> str:
    """Return the file's creation date as YYYY-MM-DD, or empty string on failure."""
    try:
        stat = path.stat()
        # Use birth time (st_birthtime) on macOS, fall back to mtime
        ts = getattr(stat, "st_birthtime", None) or stat.st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except OSError:
        return ""


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
        try:
            if value:
                audio[tag_key] = value
            else:
                del audio[tag_key]
        except (KeyError, mutagen.MutagenError):
            pass

    try:
        audio.save()
    except Exception:
        pass


def reembed_thumbnail_ssh(audio_path: str, thumb_path: str) -> bool:
    """Re-embed a thumbnail into an opus/ogg file on the LXC using mutagen.

    ffmpeg cannot write picture streams into opus/ogg containers, so we
    use mutagen (OggOpus + FLAC Picture) — the same approach yt-dlp uses.
    Returns True on success.
    """
    ext = PurePosixPath(thumb_path).suffix.lower()
    mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    mime = mime_map.get(ext, "image/jpeg")

    script = (
        "from mutagen.oggopus import OggOpus;"
        "from mutagen.flac import Picture;"
        "import base64;"
        f"a=OggOpus({audio_path!r});"
        "p=Picture();"
        f"p.data=open({thumb_path!r},'rb').read();"
        "p.type=3;"
        f"p.mime={mime!r};"
        "a['metadata_block_picture']=[base64.b64encode(p.write()).decode('ascii')];"
        "a.save()"
    )
    result = ssh_run(f"python3 -c {shlex.quote(script)}")
    if result.returncode != 0:
        print(f"{E_WARN}{yellow('Thumbnail re-embed failed')}")
        if result.stderr.strip():
            print(f"  {dim(result.stderr.strip().splitlines()[-1])}")
        return False
    return True


def _write_metadata_mutagen_ssh(dest: str, meta: AudioMetadata) -> None:
    """Write metadata tags into an opus/ogg file on the LXC using mutagen.

    Mutagen writes tags directly into OGG metadata without touching the
    embedded artwork (metadata_block_picture), unlike ffmpeg which strips
    video streams from opus containers.
    """
    _MUTAGEN_FIELDS = [
        ("artist", "artist"),
        ("album", "album"),
        ("title", "title"),
        ("genre", "genre"),
        ("date", "date"),
        ("track_number", "tracknumber"),
    ]
    statements: list[str] = []
    for attr, tag in _MUTAGEN_FIELDS:
        value = getattr(meta, attr, "")
        if value:
            statements.append(f"a[{tag!r}]=[{value!r}]")
        else:
            statements.append(f"a.pop({tag!r},None)")

    script = (
        "from mutagen.oggopus import OggOpus;"
        f"a=OggOpus({dest!r});"
        + ";".join(statements)
        + ";a.save()"
    )
    result = ssh_run(f"python3 -c {shlex.quote(script)}")
    if result.returncode != 0:
        print(f"{E_ERROR}{yellow('Metadata rewrite failed — tags from yt-dlp may be stale')}")
        if result.stderr.strip():
            print(f"  {dim(result.stderr.strip().splitlines()[-1])}")


def write_metadata_ssh(dest: str, meta: AudioMetadata, *, thumb_file: str = "") -> None:
    """Write metadata tags into an audio file on the LXC.

    Best-effort: failures are silently ignored since the file is already
    in place with yt-dlp's embedded metadata.

    For opus/ogg files, uses mutagen directly (preserves embedded artwork).
    For other formats, uses ffmpeg with ``-map 0 -c copy``.
    """
    fields = [
        ("artist", "artist"),
        ("album", "album"),
        ("title", "title"),
        ("genre", "genre"),
        ("date", "date"),
        ("track_number", "track"),
    ]
    if not any(getattr(meta, attr, "") for attr, _ in fields):
        return

    ext = PurePosixPath(dest).suffix.lower()
    if ext in (".opus", ".ogg"):
        _write_metadata_mutagen_ssh(dest, meta)
        return

    metadata_args: list[str] = []
    for attr, tag in fields:
        value = getattr(meta, attr, "")
        metadata_args.extend(["-metadata", f"{tag}={value}"])

    p = PurePosixPath(dest)
    tmp = str(p.parent / f"{p.stem}.gm-tmp{p.suffix}")
    ffmpeg_cmd = shlex.join(
        ["ffmpeg", "-y", "-i", dest, "-map", "0", "-c", "copy"]
        + metadata_args
        + [tmp]
    )
    result = ssh_run(f"{ffmpeg_cmd} && mv {quote_path(tmp)} {quote_path(dest)}")
    if result.returncode != 0:
        ssh_run(f"rm -f {quote_path(tmp)}")
        print(f"{E_ERROR}{yellow('Metadata rewrite failed — tags from yt-dlp may be stale')}")
        if result.stderr.strip():
            print(f"  {dim(result.stderr.strip().splitlines()[-1])}")


def list_existing_artists(music_root: str = MUSIC_ROOT) -> list[str]:
    """List artist directories on the LXC."""
    result = ssh_run(f"ls -1 {quote_path(music_root)} 2>/dev/null")
    if result.returncode != 0 or not result.stdout.strip():
        return []
    return [line for line in result.stdout.strip().split("\n") if line]


def list_existing_albums(artist: str, music_root: str = MUSIC_ROOT) -> list[str]:
    """List album directories for an artist on the LXC."""
    safe_artist = sanitize_filename(artist)
    result = ssh_run(f"ls -1 {quote_path(f'{music_root}/{safe_artist}')} 2>/dev/null")
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

    # Normalized match (spaces <-> hyphens)
    normalized = user_input.replace("-", " ").lower()
    for e in existing:
        if e.replace("-", " ").lower() == normalized:
            return e

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
    if not match or match == user_input:
        return user_input
    # User's input sanitizes to the same directory — keep their spelling.
    # e.g. "Ex:Re" → "Ex-Re" matches existing "Ex-Re", keep "Ex:Re" for metadata.
    if sanitize_filename(user_input) == match:
        return user_input
    # Skip prompt if input differs only by case
    if user_input.lower() == match.lower():
        return user_input
    confirm = input(f"  Did you mean {bold_cyan(match)}? [Y/n]: ").strip().lower()
    if confirm != "n":
        return match
    return user_input


def _normalized_prefix_end(text: str, prefix: str) -> int:
    """Find where *prefix* ends in *text*, comparing only alphanumeric chars.

    Returns the index in *text* just past the last character that matched
    the prefix, or -1 if the prefix doesn't match.
    """
    ti, pi = 0, 0
    while ti < len(text) and pi < len(prefix):
        tc, pc = text[ti], prefix[pi]
        if not tc.isalnum():
            ti += 1
            continue
        if not pc.isalnum():
            pi += 1
            continue
        if tc.lower() != pc.lower():
            return -1
        ti += 1
        pi += 1
    # Remaining prefix chars must all be non-alnum (trailing punctuation)
    while pi < len(prefix):
        if prefix[pi].isalnum():
            return -1
        pi += 1
    return ti


_LEADING_PUNCTUATION = re.compile(r"^[\s\-\u2013\u2014:,]+")


def _strip_artist_prefix(title: str, artist: str) -> str:
    """Strip the artist name from the beginning of a title.

    Matches the artist as a prefix using alphanumeric-only comparison so
    that punctuation differences (e.g. "Ex:Re" vs "Ex -Re") are ignored.
    Requires a word boundary after the match — won't strip "Ben Howard"
    from "Ben Howard's ..." (apostrophe continues the word).
    Strips leading separators/whitespace from the remainder.
    """
    if not artist or not title:
        return title
    end = _normalized_prefix_end(title, artist)
    if end < 0 or end >= len(title):
        return title
    # Word boundary check: next char must not continue the word
    next_char = title[end]
    if next_char.isalnum() or next_char == "'":
        return title
    remainder = _LEADING_PUNCTUATION.sub("", title[end:])
    return remainder if remainder else title


def prompt_metadata(
    defaults: AudioMetadata, *, single: bool = False,
    music_root: str = MUSIC_ROOT,
) -> AudioMetadata:
    """Prompt the user to confirm or override metadata fields.

    When *single* is True (YouTube tracks), the album defaults to the title
    but is still prompted so the user can override it.
    Type '<' at any prompt to go back to the previous field.
    """
    print(f"\n{bold('Metadata')} {dim('(press Enter to accept default, < to go back):')}")

    artist = title = album = date = ""
    step = 0
    # Steps: 0=artist, 1=title, 2=album, 3=date
    while True:
        if step == 0:
            val = _prompt_field("Artist", defaults.artist)
            if val is _BACK:
                continue  # already at first field
            artist = val  # type: ignore[assignment]
            artist = _apply_suggestion(artist, list_existing_artists(music_root))
            step = 1
        elif step == 1:
            val = _prompt_field("Title", _strip_artist_prefix(defaults.title, artist))
            if val is _BACK:
                step = 0
                continue
            title = val  # type: ignore[assignment]
            step = 2
        elif step == 2:
            album_default = title
            val = _prompt_field("Album", album_default)
            if val is _BACK:
                step = 1
                continue
            album = val  # type: ignore[assignment]
            album = _apply_suggestion(album, list_existing_albums(artist, music_root))
            step = 3
        elif step == 3:
            val = _prompt_field("Date", defaults.date)
            if val is _BACK:
                step = 2
                continue
            date = val  # type: ignore[assignment]
            break

    return AudioMetadata(
        artist=artist,
        album=album,
        title=title,
        date=date,
        description=defaults.description,
        track_number=defaults.track_number,
    )


_BACK = object()  # sentinel for "go back to previous field"


def _prompt_field(label: str, default: str) -> str | object:
    """Prompt for a single metadata field with a default value.

    Type '-' or a blank space to clear a default value (set it to empty string).
    Type '<' to go back to the previous field.
    """
    if default:
        raw = input(f"  {bold(label)} [{dim(default)}]: ")
        value = raw.strip()
        if value == "<":
            return _BACK
        if value == "-" or (raw and not value):
            return ""
        return value if value else default
    value = input(f"  {bold(label)}: ").strip()
    if value == "<":
        return _BACK
    return value


def prompt_batch_metadata() -> AudioMetadata:
    """Prompt for shared metadata fields (artist, album, date) once for a batch."""
    print(f"\n{bold('Shared metadata for all files')} {dim('(press Enter to leave empty, < to go back):')}")

    artist = album = date = ""
    step = 0
    while True:
        if step == 0:
            val = _prompt_field("Artist", "")
            if val is _BACK:
                continue
            artist = val  # type: ignore[assignment]
            artist = _apply_suggestion(artist, list_existing_artists())
            step = 1
        elif step == 1:
            val = _prompt_field("Album", "")
            if val is _BACK:
                step = 0
                continue
            album = val  # type: ignore[assignment]
            album = _apply_suggestion(album, list_existing_albums(artist))
            step = 2
        elif step == 2:
            val = _prompt_field("Date", "")
            if val is _BACK:
                step = 1
                continue
            date = val  # type: ignore[assignment]
            break

    return AudioMetadata(artist=artist, album=album, date=date)


def prompt_title_only(
    defaults: AudioMetadata, batch: AudioMetadata, track_number: int,
) -> AudioMetadata:
    """Merge batch metadata with per-file defaults, prompt only for title."""
    artist = batch.artist or defaults.artist
    album = batch.album or defaults.album
    date = batch.date or defaults.date

    while True:
        val = _prompt_field("Title", _strip_artist_prefix(defaults.title, artist))
        if val is not _BACK:
            title: str = val  # type: ignore[assignment]
            break

    return AudioMetadata(
        artist=artist,
        album=album,
        title=title,
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
    print(f"\n  {E_WARN}{bold_yellow('Duplicate found:')} {cyan(existing_path)}")
    choice = input(f"  Action — [{bold('s')}]kip / [{bold('o')}]verwrite / [{bold('r')}]ename: ").strip().lower()
    if choice in ("o", "overwrite"):
        return "overwrite"
    if choice in ("r", "rename"):
        return "rename"
    return "skip"
