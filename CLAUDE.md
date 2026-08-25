# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`gm` (get music) is a CLI tool for adding music to a Navidrome instance. It accepts a YouTube URL, a file, or a
directory, processes the audio/metadata/artwork, and stores it on a remote NFS-mounted music library.

## Infrastructure

- **Navidrome** runs on an LXC container on a Proxmox server
- SSH access: `ssh music` (home network) or `ssh musict` (remote)
- Music files stored at `/mnt/nfs/music/` on the LXC (NFS mount to TrueNAS `music` dataset on `tank` HDD pool)
- Directory structure: `Artist/Album/Song` (local files), `youtube/Artist/Album/Song` (YouTube)

## Tool Design

- Written in Python with a shell wrapper for terminal invocation
- Uses `yt-dlp` on the LXC (via SSH) for YouTube downloads — audio, artwork, metadata
- Navidrome is audio-only — always extract audio from video files
- YouTube audio kept in native format (usually opus); Navidrome transcodes on the fly
- YouTube tracks are singles — album defaults to the title but can be overridden at the prompt
- Local files transferred to LXC via `scp`; YouTube files download directly to NFS mount
- Intermediate files (extracted audio, thumbnails) cleaned up locally after transfer
- Spaces allowed in directory names to match Lidarr (e.g., `Led Zeppelin/Led Zeppelin IV/Stairway-To-Heaven.opus`)
- YouTube video ID in square brackets at end of filename: `Song-[dQw4w9WgXcQ].opus`
- Directory input prompts whether to search recursively

## Key Features

- **Duplicate detection** — three layers: local SQLite log (by file hash or video ID), SSH filesystem scan for video ID,
  destination path existence check. Log-based hits are live-verified on disk; stale records (deleted files) are
  automatically pruned. Prompts skip/overwrite/rename.
- **Artist/album lookup** — fuzzy-matches user input against existing server directories using
  `difflib.get_close_matches`. Catches typos and normalizes spaces to hyphens.
- **Batch directory import** — shared metadata (artist, album, date) prompted once, per-file title-only prompt
  with automatic track numbering.
- **Import log** — SQLite at `~/.local/share/gm/imports.db`. Records timestamp, source, artist, album, title,
  destination, file_hash, video_id.
- **Metadata embedding** — `yt-dlp --embed-metadata --embed-thumbnail --write-info-json`

## Prerequisites

| Where | What                                                |
| ----- | --------------------------------------------------- |
| Mac   | Python 3.13+, `ffmpeg`, SSH config for `music` host |
| LXC   | `yt-dlp`, `ffmpeg`                                  |

## Usage

```
gm <youtube-url>
gm <directory>
gm <filename>
gm log [N]
gm prune
gm help
```

## Project Structure

- `gm/cli.py` — Argument parsing and input routing
- `gm/youtube.py` — YouTube download via SSH + yt-dlp on LXC
- `gm/files.py` — Local file/directory processing and scp transfer
- `gm/metadata.py` — Audio metadata extraction (mutagen), user prompts, duplicate checks, artist/album lookup
- `gm/history.py` — SQLite import log for tracking imports and duplicate detection
- `gm/ssh.py` — Shared SSH utilities (ssh_run with connection multiplexing, timeouts, SSH_HOST)
- `tests/` — pytest test suite (~96% coverage; the uncovered lines are the mutagen `_embed_*` bodies, which tests mock)
- `docs/usage.md` — Detailed usage documentation

## Development

```bash
uv sync                              # Install dependencies (incl. ruff, pyright, pytest-cov)
uv run python -m pytest              # Run tests
uv run python -m pytest -v           # Run tests with verbose output
uv run python -m pytest --cov=gm     # Run tests with a coverage report
uv run ruff check .                  # Lint (add --fix to auto-fix)
uv run ruff format .                 # Format (CI runs `ruff format --check .`)
uv run pyright                       # Type-check gm/ (standard mode)
```

Always use `uv run python -m pytest` rather than bare `uv run pytest`: a pyenv-global `pytest` can shadow the venv one.
Likewise run `ruff`/`pyright` via `uv run` from the project venv so `mutagen` resolves (`uvx pyright` outside the venv
reports bogus "mutagen could not be resolved" errors).

CI (`.github/workflows/test.yml`) runs `ruff check`, `ruff format --check`, `pyright`, and `pytest --cov=gm` on every
push and pull request. All four must pass before committing.

### Installation

```bash
uv tool install -e .                 # Install gm command on PATH
```

## Conventions

- Type annotations required on all Python code; `pyright` (standard mode) must report 0 errors
- Ruff config lives in `pyproject.toml` (line length 120, `py313` target, `E F I UP B SIM DTZ BLE PL RUF S110`)
- No blind `except Exception` and no `try/except: pass` — catch the specific exceptions a call can raise
  (`mutagen.MutagenError`, `OSError`, `subprocess.SubprocessError`, `sqlite3.Error`, …) and print a short warning when
  a failure is tolerated; use `contextlib.suppress(...)` only where silence is the intended UX
- TDD workflow: write tests before implementation
- All SSH commands use `shlex.quote()` via `quote_path()` for defense-in-depth against shell injection from filenames
