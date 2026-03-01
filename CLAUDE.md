# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`gm` (get music) is a CLI tool for adding music to a Navidrome instance. It accepts a YouTube URL, a file, or a
directory, processes the audio/metadata/artwork, and stores it on a remote NFS-mounted music library.

## Infrastructure

- **Navidrome** runs on an LXC container on a Proxmox server
- SSH access: `ssh music` (home network) or `ssh musict` (remote)
- Music files stored at `/mnt/nfs/music/` on the LXC (NFS mount to TrueNAS `music` dataset on `tank` HDD pool)
- Directory structure: `Artist/Album/Song`

## Tool Design

- Written in Python with a shell wrapper for terminal invocation
- Uses `yt-dlp` on the LXC (via SSH) for YouTube downloads — audio, artwork, metadata
- Navidrome is audio-only — always extract audio from video files
- YouTube audio kept in native format (usually opus); Navidrome transcodes on the fly
- Local files transferred to LXC via `scp`; YouTube files download directly to NFS mount
- No spaces in filenames — use hyphens (e.g., `Led-Zeppelin/Led-Zeppelin-IV/Stairway-To-Heaven.opus`)
- YouTube video ID in square brackets at end of filename: `Song-[dQw4w9WgXcQ].opus`
- Directory input prompts whether to search recursively

## Key Features

- **Duplicate detection** — three layers: local SQLite log (by file hash or video ID), SSH filesystem scan for video ID,
  destination path existence check. Prompts skip/overwrite/rename.
- **Artist/album lookup** — fuzzy-matches user input against existing server directories using
  `difflib.get_close_matches`. Catches typos and normalizes spaces to hyphens.
- **Batch directory import** — shared metadata (artist, album, genre, date) prompted once, per-file title-only prompt
  with automatic track numbering.
- **Import log** — SQLite at `~/.local/share/gm/imports.db`. Records timestamp, source, artist, album, title,
  destination, file_hash, video_id.
- **Metadata embedding** — `yt-dlp --embed-metadata --embed-thumbnail --write-info-json`

## Prerequisites

| Where | What                                                |
| ----- | --------------------------------------------------- |
| Mac   | Python 3.12+, `ffmpeg`, SSH config for `music` host |
| LXC   | `yt-dlp`, `ffmpeg`                                  |

## Usage

```
gm <youtube-url>
gm <directory>
gm <filename>
gm log [N]
gm help
```

## Project Structure

- `gm/cli.py` — Argument parsing and input routing
- `gm/youtube.py` — YouTube download via SSH + yt-dlp on LXC
- `gm/files.py` — Local file/directory processing and scp transfer
- `gm/metadata.py` — Audio metadata extraction (mutagen), user prompts, duplicate checks, artist/album lookup
- `gm/history.py` — SQLite import log for tracking imports and duplicate detection
- `gm/ssh.py` — Shared SSH utilities (ssh_run, SSH_HOST)
- `tests/` — pytest test suite (100% coverage)
- `docs/usage.md` — Detailed usage documentation

## Development

```bash
uv sync                              # Install dependencies
uv run pytest                        # Run tests
uv run pytest -v                     # Run tests with verbose output
uv run coverage run -m pytest        # Run tests with coverage
uv run coverage html                 # Generate HTML coverage report
```

### Installation

```bash
uv tool install -e .                 # Install gm command on PATH
```

## Conventions

- Type annotations required on all Python code
- TDD workflow: write tests before implementation
- All SSH commands use `shlex.quote()` via `quote_path()` for defense-in-depth against shell injection from filenames
