"""Local file/directory processing and scp transfer."""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

from gm.metadata import (
    AudioMetadata,
    build_destination_path,
    check_destination_exists,
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


def is_audio_file(path: Path) -> bool:
    """Check if a file is an audio file by extension."""
    return path.suffix.lower() in AUDIO_EXTENSIONS


def is_video_file(path: Path) -> bool:
    """Check if a file is a video file by extension."""
    return path.suffix.lower() in VIDEO_EXTENSIONS


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


def extract_audio_from_video(video_path: Path) -> Path:
    """Extract audio from a video file using ffmpeg."""
    output_path = video_path.with_suffix(".mp3")
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vn", "-acodec", "libmp3lame", "-q:a", "0",
        "-y", str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()}")
    return output_path


def handle_file(
    path: Path,
    *,
    batch_meta: AudioMetadata | None = None,
    track_number: int = 0,
) -> None:
    """Process and transfer a local audio/video file to the music library."""
    source = path

    if is_video_file(path):
        print(f"Extracting audio from video: {path.name}")
        source = extract_audio_from_video(path)
    elif not is_audio_file(path):
        print(f"Skipping unsupported file: {path.name}")
        return

    defaults = read_metadata(source)
    if not defaults.genre and defaults.artist:
        defaults.genre = find_genre_by_artist(defaults.artist)
    if batch_meta is not None:
        meta = prompt_title_only(defaults, batch_meta, track_number)
    else:
        meta = prompt_metadata(defaults)
    extension = source.suffix
    dest = build_destination_path(meta, extension)
    dest_dir = str(PurePosixPath(dest).parent)

    # Check for duplicates: local log by hash, then filesystem
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
            dest = build_destination_path(meta, extension)
            dest_dir = str(PurePosixPath(dest).parent)

    write_metadata(source, meta)
    ssh_mkdir(dest_dir)
    scp_transfer(source, dest)

    # Log the import
    record_import(ImportRecord(
        source=str(path),
        artist=meta.artist,
        album=meta.album,
        title=meta.title,
        destination=dest,
        file_hash=file_hash,
        genre=meta.genre,
    ))

    print(f"Transferred: {path.name} -> {dest}")


def handle_directory(path: Path) -> None:
    """Process all audio/video files in a directory."""
    recursive_input = input("Search recursively? [y/N]: ").strip().lower()
    recursive = recursive_input == "y"

    audio_files = find_audio_files(path, recursive=recursive)
    video_files = find_video_files(path, recursive=recursive)
    all_files = sorted(audio_files + video_files)

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
