# gm (get music)

CLI tool for adding music to a self-hosted [Navidrome](https://www.navidrome.org/) instance.

## Install

```bash
pip install -e .
```

## Usage

```bash
gm https://www.youtube.com/watch?v=dQw4w9WgXcQ   # YouTube URL
gm ~/Downloads/song.flac                           # single file
gm ~/Downloads/album/                              # directory
gm log                                             # view import history
gm help                                            # show help
```

**YouTube** — downloads audio in its native format via `yt-dlp`, embeds metadata and thumbnails, and places the file on the server with the video ID in the filename.

**Files** — reads metadata with [mutagen](https://mutagen.readthedocs.io/), prompts you to confirm or edit, then transfers via `scp`. Video files have audio extracted with `ffmpeg` first.

**Directories** — prompts for shared metadata (artist, album, genre, date) once, then only asks for the title per file. Automatic track numbering.

## Features

- **Duplicate detection** — checks a local import log and the remote filesystem before transferring. Prompts to skip, overwrite, or rename.
- **Artist/album suggestions** — fuzzy-matches your input against existing directories on the server to prevent fragmentation.
- **Import log** — all imports recorded locally. View with `gm log`.
- **Native audio format** — keeps the original format from YouTube (usually opus). No unnecessary transcoding.

## Prerequisites

- Python 3.12+
- `ffmpeg` (local, for video-to-audio extraction)
- SSH access to the Navidrome server configured as the `music` host
- `yt-dlp` and `ffmpeg` on the server

## Development

```bash
pip install -e ".[dev]"
pytest
coverage run -m pytest && coverage html
```

See [docs/usage.md](docs/usage.md) for detailed usage documentation.
