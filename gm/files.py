"""Local file/directory processing and scp transfer."""

from __future__ import annotations

import base64
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

from gm.metadata import (
    AudioMetadata,
    build_destination_path,
    check_destination_exists,
    extract_video_id_from_filename,
    prompt_batch_metadata,
    prompt_duplicate_action,
    prompt_metadata,
    prompt_title_only,
    read_metadata,
    write_metadata,
)
from gm.history import ImportRecord, record_import, compute_file_hash, find_by_hash, find_genre_by_artist
from gm.ssh import ssh_run, quote_path

SCP_HOST = "music"

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".m4a", ".wav", ".opus", ".aac", ".wma"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".webm", ".mov"}

CODEC_EXTENSION_MAP: dict[str, str] = {
    "opus": ".opus",
    "aac": ".m4a",
    "vorbis": ".ogg",
    "flac": ".flac",
    "mp3": ".mp3",
    "wmav2": ".wma",
    "pcm_s16le": ".wav",
}


def _is_macos_resource_fork(path: Path) -> bool:
    """Check if a file is a macOS AppleDouble resource fork (._prefix)."""
    return path.name.startswith("._")


def is_audio_file(path: Path) -> bool:
    """Check if a file is an audio file by extension."""
    return not _is_macos_resource_fork(path) and path.suffix.lower() in AUDIO_EXTENSIONS


def is_video_file(path: Path) -> bool:
    """Check if a file is a video file by extension."""
    return not _is_macos_resource_fork(path) and path.suffix.lower() in VIDEO_EXTENSIONS


def find_audio_files(directory: Path, *, recursive: bool = False) -> list[Path]:
    """Find audio files in a directory."""
    if recursive:
        return sorted(f for f in directory.rglob("*") if f.is_file() and is_audio_file(f))
    return sorted(f for f in directory.iterdir() if f.is_file() and is_audio_file(f))


def find_video_files(directory: Path, *, recursive: bool = False) -> list[Path]:
    """Find video files in a directory."""
    if recursive:
        return sorted(f for f in directory.rglob("*") if f.is_file() and is_video_file(f))
    return sorted(f for f in directory.iterdir() if f.is_file() and is_video_file(f))


def build_scp_command(local_path: Path, remote_path: str) -> list[str]:
    """Build an scp command to transfer a file to the LXC."""
    return ["scp", str(local_path), f"{SCP_HOST}:{remote_path}"]


def scp_transfer(local_path: Path, remote_path: str) -> None:
    """Transfer a file to the LXC via scp."""
    cmd = build_scp_command(local_path, remote_path)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"scp failed: {result.stderr.strip()}")


def ssh_mkdir(remote_dir: str) -> None:
    """Create a directory on the LXC via SSH."""
    ssh_run(f"mkdir -p {quote_path(remote_dir)}", check=True)


def detect_audio_codec(video_path: Path) -> str:
    """Detect the audio codec of a file using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return result.stdout.strip()


def extract_thumbnail(video_path: Path) -> Path | None:
    """Extract attached picture from a video file.

    Only extracts attached picture streams (0:v:t), NOT video frames.
    Returns the thumbnail Path, or None if no attached picture exists.
    """
    thumb_path = video_path.with_suffix(".jpg")
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-map", "0:v:t",
        "-q:v", "1",
        "-y", str(thumb_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0 and thumb_path.exists():
        return thumb_path
    return None


# Minimum size in bytes for a valid YouTube thumbnail.
# YouTube returns a ~1KB placeholder image for missing maxresdefault URLs.
_MIN_THUMBNAIL_SIZE = 5000


def fetch_youtube_thumbnail(video_id: str, output_path: Path) -> Path | None:
    """Download the YouTube thumbnail for a video ID.

    Tries maxresdefault (1920x1080) first, falls back to hqdefault (480x360).
    Returns the output path on success, or None on failure.
    """
    if not video_id:
        return None
    urls = [
        f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
        f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
    ]
    for url in urls:
        try:
            urllib.request.urlretrieve(url, str(output_path))
            if output_path.exists() and output_path.stat().st_size > _MIN_THUMBNAIL_SIZE:
                return output_path
        except (urllib.error.URLError, OSError):
            continue
    # Clean up any leftover placeholder file
    if output_path.exists():
        output_path.unlink()
    return None


def embed_cover_art(audio_path: Path, image_path: Path) -> None:
    """Embed cover art into an audio file. Best-effort, silent on failure."""
    if not image_path.exists():
        return
    try:
        image_data = image_path.read_bytes()
    except OSError:
        return
    mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    suffix = audio_path.suffix.lower()
    try:
        if suffix == ".mp3":
            _embed_mp3(audio_path, image_data, mime)
        elif suffix in (".m4a", ".mp4"):
            _embed_mp4(audio_path, image_data, mime)
        elif suffix in (".ogg", ".opus"):
            _embed_vorbis(audio_path, image_data, mime)
        elif suffix == ".flac":
            _embed_flac(audio_path, image_data, mime)
    except Exception:
        pass


def _embed_mp3(audio_path: Path, image_data: bytes, mime: str) -> None:
    from mutagen.id3 import ID3, APIC, ID3NoHeaderError
    try:
        tags = ID3(str(audio_path))
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall("APIC")
    tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_data))
    tags.save(str(audio_path))


def _embed_mp4(audio_path: Path, image_data: bytes, mime: str) -> None:
    from mutagen.mp4 import MP4, MP4Cover
    audio = MP4(str(audio_path))
    fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
    audio["covr"] = [MP4Cover(image_data, imageformat=fmt)]
    audio.save()


def _embed_vorbis(audio_path: Path, image_data: bytes, mime: str) -> None:
    import mutagen
    from mutagen.flac import Picture
    audio = mutagen.File(str(audio_path))
    if audio is None:
        return
    pic = Picture()
    pic.type = 3  # Cover (front)
    pic.mime = mime
    pic.desc = "Cover"
    pic.data = image_data
    encoded = base64.b64encode(pic.write()).decode("ascii")
    audio["metadata_block_picture"] = [encoded]
    audio.save()


def _embed_flac(audio_path: Path, image_data: bytes, mime: str) -> None:
    from mutagen.flac import FLAC, Picture
    audio = FLAC(str(audio_path))
    pic = Picture()
    pic.type = 3  # Cover (front)
    pic.mime = mime
    pic.desc = "Cover"
    pic.data = image_data
    audio.clear_pictures()
    audio.add_picture(pic)
    audio.save()


def get_media_duration(path: Path) -> float:
    """Get media duration in seconds using ffprobe. Returns 0.0 on failure."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    try:
        return float(result.stdout.strip())
    except (ValueError, TypeError):
        return 0.0


_BAR_WIDTH = 30


def run_ffmpeg(cmd: list[str], duration: float = 0.0) -> None:
    """Run an ffmpeg command with a single-line progress bar.

    Appends progress flags to the command, reads ffmpeg's key=value progress
    output, and renders a compact progress line to stderr.
    """
    cmd = cmd + ["-v", "error", "-progress", "pipe:1", "-nostats"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None  # for type checker

    stats: dict[str, str] = {}
    start = time.monotonic()

    for line in proc.stdout:
        line = line.strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        stats[key] = value

        if key != "progress":
            continue

        # Build progress line from accumulated stats
        parts: list[str] = []
        pct = 0.0

        out_time_us_str = stats.get("out_time_us", "0")
        try:
            out_time_us = int(out_time_us_str)
        except ValueError:
            out_time_us = 0

        if duration > 0 and out_time_us > 0:
            pct = min(out_time_us / (duration * 1_000_000), 1.0)
            filled = int(pct * _BAR_WIDTH)
            bar = "\u2588" * filled + "\u2591" * (_BAR_WIDTH - filled)
            parts.append(f"{bar} {pct:4.0%}")

            elapsed = time.monotonic() - start
            if pct > 0 and elapsed > 0:
                eta = elapsed / pct - elapsed
                parts.append(f"ETA {int(eta // 60)}:{int(eta % 60):02d}")

        total_size = stats.get("total_size", "N/A")
        if total_size != "N/A":
            try:
                parts.append(f"{int(total_size) // 1024}kB")
            except ValueError:
                pass

        bitrate = stats.get("bitrate")
        if bitrate and bitrate != "N/A":
            parts.append(bitrate)

        speed = stats.get("speed")
        if speed and speed != "N/A":
            parts.append(speed)

        line_str = "  " + "  ".join(parts)
        sys.stderr.write(f"\r{line_str}\033[K")
        sys.stderr.flush()

        stats.clear()

    proc.wait()
    # Clear progress line
    sys.stderr.write("\r\033[K")
    sys.stderr.flush()
    if proc.returncode != 0:
        stderr_output = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}): {stderr_output.strip()}")


def extract_audio_from_video(video_path: Path) -> tuple[Path, Path | None]:
    """Extract audio from a video file using ffmpeg.

    Preserves the native audio codec (stream copy, no re-encoding).
    Returns (audio_path, thumbnail_path). Thumbnail may be None if the
    video has no embedded artwork or extraction fails.
    """
    thumbnail = extract_thumbnail(video_path)
    codec = detect_audio_codec(video_path)
    ext = CODEC_EXTENSION_MAP.get(codec, ".opus")
    output_path = video_path.with_suffix(ext)
    duration = get_media_duration(video_path)
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vn", "-c:a", "copy",
        "-y", str(output_path),
    ]
    run_ffmpeg(cmd, duration)
    return output_path, thumbnail


def handle_file(
    path: Path,
    *,
    batch_meta: AudioMetadata | None = None,
    track_number: int = 0,
) -> None:
    """Process and transfer a local audio/video file to the music library."""
    # Print filename for standalone imports (batch mode prints its own header)
    if batch_meta is None and track_number == 0:
        print(f"\n{path.name}")

    source = path
    thumbnail: Path | None = None
    video_id = extract_video_id_from_filename(path.stem)

    if is_video_file(path):
        print(f"Extracting audio from video: {path.name}")
        source, thumbnail = extract_audio_from_video(path)
    elif not is_audio_file(path):
        print(f"Skipping unsupported file: {path.name}")
        return

    # If no embedded thumbnail, try downloading from YouTube
    if not thumbnail and video_id:
        thumbnail = fetch_youtube_thumbnail(video_id, path.with_suffix(".jpg"))

    defaults = read_metadata(source)
    if not defaults.genre and defaults.artist:
        defaults.genre = find_genre_by_artist(defaults.artist)
    if batch_meta is not None:
        meta = prompt_title_only(defaults, batch_meta, track_number)
    else:
        meta = prompt_metadata(defaults)
    extension = source.suffix
    dest = build_destination_path(meta, extension, video_id=video_id)
    dest_dir = str(PurePosixPath(dest).parent)

    # Check for duplicates: local log by hash, then filesystem
    print("Checking for duplicates...")
    file_hash = compute_file_hash(source)
    existing = ""
    log_hits = find_by_hash(file_hash)
    if log_hits:
        existing = log_hits[0].destination
    if not existing and check_destination_exists(dest):
        existing = dest

    if existing:
        action = prompt_duplicate_action(existing)
        if action == "skip":
            print("Skipped.")
            return
        if action == "rename":
            meta = prompt_metadata(meta)
            dest = build_destination_path(meta, extension, video_id=video_id)
            dest_dir = str(PurePosixPath(dest).parent)

    print("Writing metadata...")
    write_metadata(source, meta)
    if thumbnail:
        embed_cover_art(source, thumbnail)
    print("Transferring...")
    ssh_mkdir(dest_dir)
    scp_transfer(source, dest)

    if thumbnail:
        cover_dest = str(PurePosixPath(dest_dir) / "cover.jpg")
        scp_transfer(thumbnail, cover_dest)

    # Log the import
    record_import(ImportRecord(
        source=str(path),
        artist=meta.artist,
        album=meta.album,
        title=meta.title,
        destination=dest,
        file_hash=file_hash,
        genre=meta.genre,
        video_id=video_id,
    ))

    print(f"Transferred: {path.name} -> {dest}")


def handle_directory(path: Path) -> None:
    """Process all audio/video files in a directory."""
    recursive_input = input("Search recursively? [y/N]: ").strip().lower()
    recursive = recursive_input == "y"

    audio_files = find_audio_files(path, recursive=recursive)
    video_files = find_video_files(path, recursive=recursive)
    video_stems = {f.stem for f in video_files}
    unique_audio = [f for f in audio_files if f.stem not in video_stems]
    all_files = sorted(unique_audio + video_files)

    if not all_files:
        print("No audio or video files found.")
        return

    total = len(all_files)
    print(f"Found {total} file(s)")

    same_album = input("Same album? [Y/n]: ").strip().lower() != "n"
    batch: AudioMetadata | None = None
    if same_album:
        batch = prompt_batch_metadata()
    failures: list[tuple[Path, str]] = []

    for i, file in enumerate(all_files, 1):
        print(f"\n[{i}/{total}] {file.name}")
        track = i if same_album else 0
        try:
            handle_file(file, batch_meta=batch, track_number=track)
        except Exception as exc:
            print(f"  Error: {exc}")
            failures.append((file, str(exc)))

    succeeded = total - len(failures)
    if failures:
        print(f"\n{len(failures)} file(s) failed:")
        for failed_path, reason in failures:
            print(f"  {failed_path.name}: {reason}")
    print(f"\nDone! {succeeded}/{total} file(s) processed.")
