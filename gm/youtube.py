"""YouTube download via SSH + yt-dlp on LXC."""

from __future__ import annotations

import json
import shlex
import uuid
from pathlib import PurePosixPath

from gm.ui import (
    E_DONE, E_LINK, E_SEARCH, E_SKIP,
    bold_cyan, bold_green, cyan, yellow,
)
from gm.metadata import (
    AudioMetadata,
    build_destination_path,
    check_destination_exists,
    check_video_id_exists,
    humanize_name,
    normalize_date,
    prompt_duplicate_action,
    prompt_metadata,
    write_metadata_ssh,
    MUSIC_ROOT,
)
from gm.history import ImportRecord, record_import, delete_import, find_by_video_id
from gm.ssh import ssh_run, SSH_HOST, quote_path

def _make_temp_dir() -> str:
    """Generate a unique temp directory path for a download."""
    return f"/tmp/gm-download-{uuid.uuid4().hex[:12]}"


def extract_video_id(url: str) -> str:
    """Extract the video ID from a YouTube URL."""
    import re
    # youtu.be/ID
    match = re.search(r"youtu\.be/([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)
    # youtube.com/watch?v=ID or music.youtube.com/watch?v=ID
    match = re.search(r"[?&]v=([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)
    # youtube.com/shorts/ID
    match = re.search(r"/shorts/([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)
    return ""


def build_ytdlp_command(url: str, temp_dir: str) -> list[str]:
    """Build the yt-dlp command for best-quality audio with embedded metadata."""
    return [
        "yt-dlp",
        "--no-playlist",
        "--extract-audio",
        "--audio-quality", "0",
        "--embed-metadata",
        "--embed-thumbnail",
        "--write-info-json",
        "--output", f"{temp_dir}/%(title)s.%(ext)s",
        url,
    ]


def parse_ytdlp_metadata(json_str: str) -> AudioMetadata:
    """Parse metadata from yt-dlp's info.json output."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return AudioMetadata(album="YouTube")

    artist = data.get("artist", "") or data.get("uploader", "") or ""
    # Strip " - Topic" suffix from auto-generated YouTube Music channels
    if artist.endswith(" - Topic"):
        artist = artist[: -len(" - Topic")]

    title = data.get("title", "") or ""
    album = data.get("album", "") or "YouTube"
    description = data.get("description", "") or ""
    track_number = str(data.get("track_number", "")) if data.get("track_number") else ""

    date = normalize_date(
        data.get("release_date", "") or data.get("upload_date", "") or ""
    )

    return AudioMetadata(
        artist=humanize_name(artist),
        album=humanize_name(album),
        title=humanize_name(title),
        date=date,
        description=description,
        track_number=track_number,
    )


def handle_youtube(url: str) -> None:
    """Download audio from YouTube URL via SSH + yt-dlp on LXC."""
    print(f"{E_LINK}{bold_cyan('Downloading from YouTube:')} {url}")

    video_id = extract_video_id(url)

    # Early duplicate check: video_id in log + filesystem (before download)
    early_dup_handled = False
    if video_id:
        print(f"{E_SEARCH}Checking for duplicates...")
        existing = ""
        log_hits = find_by_video_id(video_id)
        if log_hits:
            hit_dest = log_hits[0].destination
            if hit_dest and not check_destination_exists(hit_dest):
                delete_import(hit_dest)
            else:
                existing = hit_dest
        if not existing:
            existing = check_video_id_exists(MUSIC_ROOT, video_id)
        if existing:
            action = prompt_duplicate_action(existing)
            if action == "skip":
                print(f"{E_SKIP}{yellow('Skipped.')}")
                return
            early_dup_handled = True

    temp_dir = _make_temp_dir()

    # Create temp directory and download
    ytdlp_cmd = shlex.join(build_ytdlp_command(url, temp_dir))
    ssh_run(f"mkdir -p {temp_dir}", check=True)
    ssh_run(ytdlp_cmd, check=True, stream=True)

    # Read metadata from info.json
    result = ssh_run(
        f"cat {temp_dir}/*.info.json", check=True
    )
    defaults = parse_ytdlp_metadata(result.stdout)

    # Find the downloaded audio file
    audio_result = ssh_run(
        f"find {temp_dir} -type f \\( -name '*.mp3' -o -name '*.opus' "
        f"-o -name '*.m4a' -o -name '*.flac' -o -name '*.ogg' \\) | head -1",
        check=True,
    )
    audio_file = audio_result.stdout.strip()
    if not audio_file:
        raise RuntimeError("No audio file found after download")

    # Find thumbnail if present
    thumb_result = ssh_run(
        f"find {temp_dir} -type f \\( -name '*.jpg' -o -name '*.png' "
        f"-o -name '*.webp' \\) | head -1"
    )
    thumb_file = thumb_result.stdout.strip()

    # Prompt user for metadata
    meta = prompt_metadata(defaults)
    extension = PurePosixPath(audio_file).suffix
    dest = build_destination_path(meta, extension, video_id=video_id)
    dest_dir = str(PurePosixPath(dest).parent)

    # Late duplicate check: destination path only (if not already handled)
    if not early_dup_handled and check_destination_exists(dest):
        action = prompt_duplicate_action(dest)
        if action == "skip":
            ssh_run(f"rm -rf {temp_dir}")
            print(f"{E_SKIP}{yellow('Skipped.')}")
            return
        if action == "rename":
            meta = prompt_metadata(meta)
            dest = build_destination_path(meta, extension, video_id=video_id)
            dest_dir = str(PurePosixPath(dest).parent)

    # Move file to final destination
    ssh_run(f"mkdir -p {quote_path(dest_dir)}", check=True)
    ssh_run(f"mv {quote_path(audio_file)} {quote_path(dest)}", check=True)

    # Write user-confirmed metadata into the audio file
    write_metadata_ssh(dest, meta)

    # Embed thumbnail if available
    if thumb_file:
        thumb_ext = PurePosixPath(thumb_file).suffix
        thumb_dest = str(PurePosixPath(dest_dir) / f"cover{thumb_ext}")
        ssh_run(f"mv {quote_path(thumb_file)} {quote_path(thumb_dest)}")

    # Clean up temp directory
    ssh_run(f"rm -rf {temp_dir}")

    # Log the import
    record_import(ImportRecord(
        source=url,
        artist=meta.artist,
        album=meta.album,
        title=meta.title,
        destination=dest,
        video_id=video_id,
    ))

    print(f"{E_DONE}{bold_green('Done!')} Saved to: {cyan(dest)}")
