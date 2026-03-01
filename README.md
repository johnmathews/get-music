# gm (get music)

CLI tool for adding music to a Navidrome instance. Accepts YouTube URLs, local audio/video files, or directories.

## Quick Start

```bash
pip install -e ".[dev]"

gm https://www.youtube.com/watch?v=dQw4w9WgXcQ
gm ~/Downloads/song.mp3
gm ~/Downloads/album/
gm log
gm help
```

## How It Works

- **YouTube URLs** are downloaded via SSH on the LXC using `yt-dlp` (audio-only, native format), so files land directly on the NFS mount
- **Local files** are processed on the Mac and transferred via `scp`
- **Video files** have audio extracted via `ffmpeg` before transfer
- **Directories** prompt for shared metadata (artist, album, genre, date) once, then per-file title only
- All files are organized as `Artist/Album/Title` under `/mnt/nfs/music/` with hyphens instead of spaces

## Features

- **Duplicate detection** — checks the local import log (by file hash or YouTube video ID), then the remote filesystem before transferring. Prompts to skip, overwrite, or rename.
- **Artist/album lookup** — fuzzy-matches your input against existing directories on the server. Catches typos like "Led Zeplin" → "Led-Zeppelin" and normalizes spaces to hyphens automatically.
- **Batch directory import** — shared metadata prompted once for an entire directory, with automatic track numbering and per-file title prompts.
- **Import log** — all imports are recorded in a local SQLite database (`~/.local/share/gm/imports.db`). View with `gm log`.
- **Metadata embedding** — YouTube downloads include embedded metadata and thumbnails via `yt-dlp --embed-metadata --embed-thumbnail`.
- **Native audio format** — YouTube audio is kept in its native format (usually opus); Navidrome transcodes on the fly.

## Infrastructure

- Navidrome runs on an LXC on a Proxmox server
- SSH access: `ssh music` (home network) or `ssh musict` (remote)
- Music stored at `/mnt/nfs/music/` (NFS mount to TrueNAS `music` dataset on `tank` HDD pool)

## Prerequisites

| Where | What |
|-------|------|
| Mac | Python 3.12+, `ffmpeg`, SSH config for `music` host |
| LXC | `yt-dlp`, `ffmpeg` |

## Development

```bash
pip install -e ".[dev]"
pytest
coverage run -m pytest && coverage html
```

See [docs/usage.md](docs/usage.md) for detailed usage documentation.
