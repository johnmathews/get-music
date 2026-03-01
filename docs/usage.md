# gm Usage Guide

## Installation

```bash
uv tool install -e /path/to/get-music
```

**Note:** The `gm` command may conflict with a shell alias (e.g., `gm = git merge`). If so, either unalias it or invoke
via `python -m gm.cli`.

### Prerequisites

- **On your Mac:** Python 3.12+, `ffmpeg` (for video-to-audio conversion), SSH configured for `music` host
- **On the LXC:** `yt-dlp`, `ffmpeg`

## Usage

### Download from YouTube

```bash
gm https://www.youtube.com/watch?v=dQw4w9WgXcQ
gm https://youtu.be/dQw4w9WgXcQ
gm https://music.youtube.com/watch?v=abc123
```

This SSHs into the LXC, runs `yt-dlp` to download audio in its native format (usually opus), extracts metadata, and
prompts you to confirm/override. The file is placed at `/mnt/nfs/music/Artist/Album/Title-[video_id].opus`.

YouTube downloads include:

- Embedded metadata (`--embed-metadata`)
- Embedded thumbnail (`--embed-thumbnail`)
- Video ID in the filename in square brackets for deduplication
- Cover art copied to `cover.jpg` in the album directory

### Process a local file

```bash
gm ~/Downloads/song.mp3
gm ~/Downloads/song.flac
gm ~/Downloads/video.mp4
```

Audio files are read for metadata (via mutagen) and transferred via `scp`. Video files have their audio extracted first
using `ffmpeg`, then transferred.

### Process a directory

```bash
gm ~/Downloads/album/
```

You'll be prompted whether to search recursively, then prompted once for shared metadata:

```
Search recursively? [y/N]: n
Found 12 file(s)

Shared metadata for all files (press Enter to leave empty):
  Artist: Led Zeppelin
  Album: IV
  Genre: Rock
  Date: 1971

[1/12] 01-Black-Dog.flac
  Title [Black Dog]:
```

Each file gets automatic track numbering and only prompts for the title. Shared fields (artist, album, genre, date) are
set once for the whole batch.

If a file fails during batch import (e.g., transfer error), `gm` logs the error and continues with the remaining files. A
summary of successes and failures is printed at the end.

### View import history

```bash
gm log        # Show last 20 imports
gm log 50     # Show last 50 imports
```

### Help

```bash
gm help
```

## Metadata Prompting

For every file processed, you'll be shown the detected metadata and can accept defaults or override:

```
Metadata (press Enter to accept default):
  Artist [Channel Name]: Actual Artist
  Album [Singles]: Album Name
  Title [Video Title]:
  Genre []:
  Date []:
```

- Press Enter to accept the value in brackets
- Type a new value to override
- For YouTube downloads, the channel name is used as the default artist (with " - Topic" suffix stripped)
- Album defaults to "Singles" when not detected

### Artist/Album Suggestions

When you type an artist or album name, `gm` checks existing directories on the server and suggests matches:

```
  Artist: Led Zeplin
  Did you mean 'Led-Zeppelin'? [Y/n]:
```

This prevents library fragmentation by catching:

- **Spaces vs hyphens:** "Led Zeppelin" → matches "Led-Zeppelin"
- **Typos:** "Led Zeplin" → fuzzy matches "Led-Zeppelin"
- **Case differences:** "led zeppelin" → matches "Led-Zeppelin"

Press Enter or `y` to accept the suggestion, or `n` to keep your original input.

## Duplicate Detection

Before transferring any file, `gm` checks for duplicates in three ways:

1. **Import log** (fast, local) — checks the SQLite database by file hash (local files) or YouTube video ID
2. **Filesystem scan** — for YouTube, checks if a file with `[video_id]` already exists under the artist directory
3. **Destination path** — checks if a file already exists at the exact destination path on the server

When a duplicate is found:

```
  Duplicate found: /mnt/nfs/music/Artist/Album/Song-[abc123].opus
  Action — [s]kip / [o]verwrite / [r]ename:
```

- **Skip** (default): don't transfer, move on to the next file
- **Overwrite**: replace the existing file
- **Rename**: re-prompts for all metadata fields so you can choose a different artist, album, or title — the file is then
  saved to the new destination

## Supported Formats

### Audio (processed directly)

mp3, flac, ogg, m4a, wav, opus, aac, wma

### Video (audio extracted first)

mp4, mkv, avi, webm, mov

## File Organization

All files are stored on the LXC at `/mnt/nfs/music/` in this structure:

```
/mnt/nfs/music/
├── Artist-Name/
│   ├── Album-Name/
│   │   ├── Song-Title.flac
│   │   └── cover.jpg
│   └── Singles/
│       └── Another-Song-[dQw4w9WgXcQ].opus
```

- Spaces are replaced with hyphens in all path components
- Unsafe characters are replaced with hyphens: `/`, `\`, `:`, null bytes, `'`, `"`, `` ` ``, `$`, `?`, `*`, `<`, `>`,
  `|`, `;`, `&`, `(`, `)`, newlines, tabs
- Multiple consecutive hyphens are collapsed to one
- YouTube downloads include the video ID in square brackets before the extension

## Import Log

All imports are recorded in a local SQLite database at `~/.local/share/gm/imports.db`. Each record tracks:

- Timestamp
- Source (URL or local path)
- Artist, album, title
- Destination path on the server
- File hash (SHA-256, for local files)
- YouTube video ID (for YouTube downloads)

This enables fast duplicate detection without needing SSH calls for every file.
