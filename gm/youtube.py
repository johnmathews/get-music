"""YouTube download via SSH + yt-dlp on LXC."""

from __future__ import annotations

import json
import shlex
import uuid
from pathlib import PurePosixPath

from gm.metadata import (
    AudioMetadata,
    build_destination_path,
    check_destination_exists,
    check_video_id_exists,
    prompt_duplicate_action,
    prompt_metadata,
    sanitize_filename,
    write_metadata_ssh,
    MUSIC_ROOT,
)
from gm.history import ImportRecord, record_import, find_by_video_id
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
    genre = data.get("genre", "") or ""
    description = data.get("description", "") or ""
    track_number = str(data.get("track_number", "")) if data.get("track_number") else ""

    # yt-dlp uses upload_date as YYYYMMDD, convert to YYYY-MM-DD
    upload_date = data.get("release_date", "") or data.get("upload_date", "") or ""
    if len(upload_date) == 8 and upload_date.isdigit():
        date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
    else:
        date = upload_date

    return AudioMetadata(
        artist=artist,
        album=album,
        title=title,
        genre=genre,
        date=date,
        description=description,
        track_number=track_number,
    )


def handle_youtube(url: str) -> None:
    """Download audio from YouTube URL via SSH + yt-dlp on LXC."""
    print(f"Downloading from YouTube: {url}")

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
    video_id = extract_video_id(url)
    extension = PurePosixPath(audio_file).suffix
    dest = build_destination_path(meta, extension, video_id=video_id)
    dest_dir = str(PurePosixPath(dest).parent)
    artist_dir = f"{MUSIC_ROOT}/{sanitize_filename(meta.artist)}"

    # Check for duplicates: local log first, then filesystem
    existing = ""
    if video_id:
        log_hits = find_by_video_id(video_id)
        if log_hits:
            existing = log_hits[0].destination
        if not existing:
            existing = check_video_id_exists(artist_dir, video_id)
    if not existing and check_destination_exists(dest):
        existing = dest

    if existing:
        action = prompt_duplicate_action(existing)
        if action == "skip":
            ssh_run(f"rm -rf {temp_dir}")
            print("Skipped.")
            return
        if action == "rename":
            meta = prompt_metadata(meta)
            dest = build_destination_path(meta, extension, video_id=video_id)
            dest_dir = str(PurePosixPath(dest).parent)
            artist_dir = f"{MUSIC_ROOT}/{sanitize_filename(meta.artist)}"

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

    print(f"Done! Saved to: {dest}")
