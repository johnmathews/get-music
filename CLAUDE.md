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
- Uses `yt-dlp` for YouTube downloads (audio, artwork, metadata)
- Must check whether Navidrome supports video file playback — if not, download audio-only from YouTube and convert video
  files accordingly
- Directory input should prompt whether to search recursively

## Usage

```
gm <youtube-url>
gm <directory>
gm <filename>
gm log [N]
gm help
```

## Project Structure

- `src/gm/cli.py` — Argument parsing and input routing
- `src/gm/youtube.py` — YouTube download via SSH + yt-dlp on LXC
- `src/gm/files.py` — Local file/directory processing and scp transfer
- `src/gm/metadata.py` — Audio metadata extraction (mutagen), user prompts, duplicate checks, artist/album lookup
- `src/gm/history.py` — SQLite import log for tracking imports and duplicate detection
- `src/gm/ssh.py` — Shared SSH utilities (ssh_run, SSH_HOST)
- `tests/` — pytest test suite (100% coverage)
- `docs/usage.md` — Detailed usage documentation

## Development

```bash
pip install -e ".[dev]"     # Install with dev dependencies
pytest                      # Run tests
pytest -v                   # Run tests with verbose output
coverage run -m pytest      # Run tests with coverage
coverage html               # Generate HTML coverage report
```

## Conventions

- Type annotations required on all Python code
- TDD workflow: write tests before implementation
- `src/` layout to prevent accidental imports of uninstalled code
- Navidrome is audio-only — always extract audio from video files
