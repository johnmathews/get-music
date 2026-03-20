"""YouTube download via SSH + yt-dlp on LXC."""

from __future__ import annotations

import json
import shlex
import uuid
from pathlib import PurePosixPath

from gm.ui import (
    E_CHECK, E_DONE, E_ERROR, E_LINK, E_SEARCH, E_SKIP, E_WARN,
    bold, bold_cyan, bold_green, bold_yellow, cyan, dim, yellow,
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
    reembed_thumbnail_ssh,
    write_metadata_ssh,
    YOUTUBE_ROOT,
)
from gm.history import ImportRecord, record_import, delete_import, find_by_video_id
from gm.ssh import ssh_run, SSH_HOST, quote_path


def _make_temp_dir() -> str:
    """Generate a unique temp directory path for a download."""
    return f"/tmp/gm-download-{uuid.uuid4().hex[:12]}"


def verify_thumbnail_embedded(audio_file: str) -> bool:
    """Check whether the audio file has an embedded thumbnail via ffprobe.

    Returns True if ffprobe finds a video stream (thumbnail) in the file.
    """
    result = ssh_run(
        f"ffprobe -v quiet -show_entries stream=codec_type -of csv=p=0 "
        f"{quote_path(audio_file)}"
    )
    return "video" in result.stdout


def _detect_ytdlp_install_method() -> str:
    """Detect how yt-dlp was installed on the LXC.

    Returns one of: "uv", "pipx", "pip", "brew", "standalone", "unknown".
    Checks uv first (uv tool installs put binaries in ~/.local/bin with a
    uv-managed venv), then pipx, pip, brew, and finally standalone.
    """
    result = ssh_run("which yt-dlp 2>/dev/null")
    if result.returncode != 0:
        return "unknown"
    path = result.stdout.strip()
    # uv tool: check if the binary is a uv-managed tool
    result = ssh_run("uv tool list 2>/dev/null | grep yt-dlp")
    if result.returncode == 0 and "yt-dlp" in result.stdout:
        return "uv"
    result = ssh_run("pipx list 2>/dev/null | grep yt-dlp")
    if result.returncode == 0 and "yt-dlp" in result.stdout:
        return "pipx"
    # pip-installed (but not via system package manager)
    result = ssh_run(f"dpkg -S {shlex.quote(path)} 2>/dev/null")
    if result.returncode == 0:
        # Installed via apt — too stale to be useful, but we can't safely
        # update it. Report as unknown so the user gets a manual hint.
        return "unknown"
    result = ssh_run("pip show yt-dlp 2>/dev/null")
    if result.returncode == 0:
        return "pip"
    result = ssh_run("brew list --formula yt-dlp 2>/dev/null")
    if result.returncode == 0:
        return "brew"
    return "standalone"


_UPDATE_COMMANDS: dict[str, str] = {
    "uv": "uv tool upgrade yt-dlp",
    "pip": "pip install -U yt-dlp",
    "pipx": "pipx upgrade yt-dlp",
    "standalone": "yt-dlp -U",
    "brew": "brew upgrade yt-dlp",
}


def update_ytdlp() -> bool:
    """Detect how yt-dlp is installed on the LXC and update it.

    Returns True if the update succeeded.
    """
    method = _detect_ytdlp_install_method()
    if method == "unknown":
        print(f"{E_ERROR}{yellow('Cannot find yt-dlp on the LXC')}")
        return False

    update_cmd = _UPDATE_COMMANDS[method]
    print(f"{E_WARN}{bold_yellow('yt-dlp may be outdated — updating')} {dim(f'({method}: {update_cmd})')}")
    result = ssh_run(update_cmd, stream=True)
    if result.returncode != 0:
        print(f"{E_ERROR}{yellow('Update failed — try manually:')} {bold(f'ssh {SSH_HOST} {shlex.quote(update_cmd)}')}")
        return False
    print(f"{E_CHECK}{bold_green('yt-dlp updated')}")
    return True


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
        return AudioMetadata()

    artist = data.get("artist", "") or data.get("uploader", "") or ""
    # Strip " - Topic" suffix from auto-generated YouTube Music channels
    if artist.endswith(" - Topic"):
        artist = artist[: -len(" - Topic")]

    title = data.get("title", "") or ""
    description = data.get("description", "") or ""
    track_number = str(data.get("track_number", "")) if data.get("track_number") else ""

    date = normalize_date(
        data.get("release_date", "") or data.get("upload_date", "") or ""
    )

    return AudioMetadata(
        artist=humanize_name(artist),
        title=humanize_name(title),
        date=date,
        description=description,
        track_number=track_number,
    )


def _cleanup_stale_temp_dirs() -> None:
    """Remove any orphaned gm-download temp dirs from prior interrupted runs."""
    ssh_run("find /tmp -maxdepth 1 -name 'gm-download-*' -type d -mmin +30 -exec rm -rf {} + 2>/dev/null")


def handle_youtube(url: str) -> None:
    """Download audio from YouTube URL via SSH + yt-dlp on LXC."""
    _cleanup_stale_temp_dirs()
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
            existing = check_video_id_exists(YOUTUBE_ROOT, video_id)
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
    result = ssh_run(ytdlp_cmd, stream=True)
    if result.returncode != 0:
        ssh_run(f"rm -rf {temp_dir}")
        # Try updating yt-dlp and retrying once
        if update_ytdlp():
            print(f"{E_LINK}{bold_cyan('Retrying download...')}")
            ssh_run(f"mkdir -p {temp_dir}", check=True)
            result = ssh_run(ytdlp_cmd, stream=True)
            if result.returncode != 0:
                ssh_run(f"rm -rf {temp_dir}")
                print(f"{E_ERROR}{yellow('Download still failed after updating yt-dlp')}")
                raise SystemExit(1)
        else:
            print(f"{E_ERROR}{yellow('Download failed')}")
            raise SystemExit(1)

    # Read metadata from info.json (use ls -1t to pick the newest if multiple exist)
    result = ssh_run(
        f"cat \"$(ls -1t {temp_dir}/*.info.json | head -1)\"", check=True
    )
    defaults = parse_ytdlp_metadata(result.stdout)

    # Find the downloaded audio file (ls -1t picks newest if multiple exist)
    audio_result = ssh_run(
        f"ls -1t {temp_dir}/*.mp3 {temp_dir}/*.opus {temp_dir}/*.m4a "
        f"{temp_dir}/*.flac {temp_dir}/*.ogg 2>/dev/null | head -1",
        check=True,
    )
    audio_file = audio_result.stdout.strip()
    if not audio_file:
        raise RuntimeError("No audio file found after download")

    # Verify thumbnail is embedded in the audio file
    if not verify_thumbnail_embedded(audio_file):
        # Diagnose why: check if thumbnail file exists loose in temp dir
        thumb_check = ssh_run(
            f"ls -1 {temp_dir}/*.jpg {temp_dir}/*.png "
            f"{temp_dir}/*.webp 2>/dev/null | head -1"
        )
        loose_thumb = thumb_check.stdout.strip()

        # Check info.json for thumbnail URLs
        try:
            info = json.loads(result.stdout)
            has_thumb_url = bool(info.get("thumbnail") or info.get("thumbnails"))
        except (json.JSONDecodeError, AttributeError):
            has_thumb_url = False

        ext = PurePosixPath(audio_file).suffix
        print(f"{E_ERROR}{yellow('Thumbnail not embedded in audio file')}")
        if not has_thumb_url:
            print(f"  {dim('YouTube provided no thumbnail URL for this video')}")
        elif loose_thumb:
            print(f"  {dim(f'Thumbnail was downloaded ({PurePosixPath(loose_thumb).name}) but yt-dlp failed to embed it into {ext} file')}")
        else:
            print(f"  {dim(f'Thumbnail URL was available but yt-dlp failed to download it')}")
        print(f"  {dim(f'Audio format: {ext}  File: {PurePosixPath(audio_file).name}')}")
        ssh_run(f"rm -rf {temp_dir}")
        raise SystemExit(1)

    # Find thumbnail file if present (for cover art in album directory)
    thumb_result = ssh_run(
        f"ls -1 {temp_dir}/*.jpg {temp_dir}/*.png "
        f"{temp_dir}/*.webp 2>/dev/null | head -1"
    )
    thumb_file = thumb_result.stdout.strip()

    # Prompt user for metadata (YouTube tracks are singles: album = title)
    meta = prompt_metadata(defaults, single=True, music_root=YOUTUBE_ROOT)
    extension = PurePosixPath(audio_file).suffix
    dest = build_destination_path(meta, extension, video_id=video_id, music_root=YOUTUBE_ROOT)
    dest_dir = str(PurePosixPath(dest).parent)

    # Late duplicate check: destination path only (if not already handled)
    if not early_dup_handled and check_destination_exists(dest):
        action = prompt_duplicate_action(dest)
        if action == "skip":
            ssh_run(f"rm -rf {temp_dir}")
            print(f"{E_SKIP}{yellow('Skipped.')}")
            return
        if action == "rename":
            meta = prompt_metadata(meta, single=True, music_root=YOUTUBE_ROOT)
            dest = build_destination_path(meta, extension, video_id=video_id, music_root=YOUTUBE_ROOT)
            dest_dir = str(PurePosixPath(dest).parent)

    # Move file to final destination
    ssh_run(f"mkdir -p {quote_path(dest_dir)}", check=True)
    ssh_run(f"mv {quote_path(audio_file)} {quote_path(dest)}", check=True)

    # Write user-confirmed metadata into the audio file
    write_metadata_ssh(dest, meta, thumb_file=thumb_file)

    # Save thumbnail as cover art in album directory
    thumb_dest = ""
    if thumb_file:
        thumb_ext = PurePosixPath(thumb_file).suffix
        thumb_dest = str(PurePosixPath(dest_dir) / f"cover{thumb_ext}")
        ssh_run(f"mv {quote_path(thumb_file)} {quote_path(thumb_dest)}")

    # Post-verification: ensure final file still has embedded artwork
    if not verify_thumbnail_embedded(dest):
        recovered = False
        # Try re-embedding from cover file or loose thumbnail
        cover_source = thumb_dest or thumb_file
        if cover_source:
            recovered = reembed_thumbnail_ssh(dest, cover_source)
        if recovered:
            print(f"{E_WARN}{bold_yellow('Artwork was re-embedded after metadata rewrite')}")
        else:
            ext = PurePosixPath(dest).suffix
            print(f"{E_ERROR}{yellow('Final file has no embedded artwork')}")
            print(f"  {dim(f'Audio format: {ext}  File: {PurePosixPath(dest).name}')}")
            ssh_run(f"rm -rf {temp_dir}")
            raise SystemExit(1)

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
