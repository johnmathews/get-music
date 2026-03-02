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
gm https://youtube.com/shorts/abc123
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
using `ffmpeg` (stream copy, no re-encoding), then transferred. If the video has an embedded thumbnail (attached picture
stream), it's extracted and used as cover art — both embedded in the audio file and saved as `cover.jpg`. For files with
a YouTube video ID in the filename (e.g., `Song-[dQw4w9WgXcQ].mp4`), the thumbnail is downloaded directly from YouTube
if not embedded. Intermediate files created during processing (extracted audio, thumbnails) are automatically cleaned up
after transfer.

All metadata from the source file is preserved through extraction. Rich metadata fields like description and comment
(common in YouTube-sourced videos) are carried through unchanged into the final audio file — they are read from the
source, passed through the processing pipeline, and written back. The interactive prompt only asks for core cataloging
fields (artist, album, title, genre, date), but other embedded metadata is not stripped.

### Process a directory

```bash
gm ~/Downloads/album/
```

You'll be prompted whether to search recursively and whether files share the same album:

```
Search recursively? [y/N]: n
Found 12 file(s)
Same album? [Y/n]: y

Shared metadata for all files (press Enter to leave empty):
  Artist: Led Zeppelin
  Album: IV
  Genre: Rock
  Date: 1971

[1/12] 01-Black-Dog.flac
  Title [Black Dog]:
```

When files share the same album (default), each file gets automatic track numbering and only prompts for the title.
Shared fields (artist, album, genre, date) are set once for the whole batch.

If files are from different artists or albums, answer "n" to get full metadata prompting for each file individually:

```
Same album? [Y/n]: n

[1/5] song-a.mp3
  Artist []: Pink Floyd
  Album []: The Wall
  Title [song-a]: Another Brick in the Wall
  Genre []: Rock
  Date []: 1979
```

If a file fails during batch import (e.g., transfer error), `gm` logs the error and continues with the remaining files. A
summary of successes and failures is printed at the end.

### View import history

```bash
gm log        # Show last 20 imports
gm log 50     # Show last 50 imports
```

### Prune stale log entries

```bash
gm prune
```

If you've deleted files from the NFS mount (e.g., `rm -rf` on the server), the import log still has records for those
files. This can cause false duplicate hits on re-import. `gm prune` checks every record's destination against the server
and removes entries for files that no longer exist:

```
  Stale: /mnt/nfs/music/Artist/Album/Song.opus
  Stale: /mnt/nfs/music/Artist/Album/Song2.opus
Pruned 2 stale record(s) out of 150 total.
```

Note: stale log entries are also cleaned up automatically during normal imports — when a duplicate check finds a log hit
whose file no longer exists on disk, the stale record is silently deleted and the import proceeds.

### Help

```bash
gm help
```

## Metadata Prompting

For every file processed, you'll be shown the detected metadata and can accept defaults or override:

```
Metadata (press Enter to accept default):
  Artist [Channel Name]: Actual Artist
  Album [YouTube]: Album Name
  Title [Video Title]:
  Genre []:
  Date []:
```

- Press Enter to accept the value in brackets
- Type a new value to override
- For YouTube downloads, the channel name is used as the default artist (with " - Topic" suffix stripped)
- Album defaults to "YouTube" when not detected from a YouTube download
- The generic "Music" genre tag is filtered out everywhere — from embedded audio tags, YouTube metadata, and import history lookups (it's a YouTube platform category, not a real genre)
- When a metadata field is left empty (or cleared with `-` or a space), the tag is explicitly removed from the file — stale values from the source (like a "Music" genre) won't persist
- Title suggestions automatically strip the artist name prefix — if the artist is "Joe Bloggs" and the detected title is "Joe Bloggs - My Song", the suggestion shows just "My Song"

### Metadata Defaults

`gm` extracts defaults from multiple sources, using the first available value:

1. **Embedded tags** — mutagen reads artist, album, title, genre, date from the audio file
2. **YouTube-style filenames** — files named `Artist_Name-Song_Title-[videoID]` (e.g., downloaded with yt-dlp) have
   artist, title, and album ("YouTube") extracted from the filename pattern
3. **Genre from history** — if you've imported songs by the same artist before, the most recently used genre is
   suggested as the default
4. **Date from file** — when no date tag is found, the file's creation date (macOS birth time, or modification time on
   Linux) is used as the default

### Date Normalization

Dates are automatically normalized to `YYYY-MM-DD` format:

- `20240115` (yt-dlp format) → `2024-01-15`
- `2024-1-5` (unpadded) → `2024-01-05`
- `1971` (bare year) → `1971` (kept as-is)

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
- **Special characters:** "Ex:Re" or "AC/DC" → silently matches "Ex-Re" or "AC-DC" without prompting, preserving
  the original characters in metadata

Press Enter or `y` to accept the suggestion, or `n` to keep your original input.

## Duplicate Detection

Before transferring any file, `gm` checks for duplicates in three ways:

1. **Import log** (fast, local) — checks the SQLite database by YouTube video ID and file hash (local files) or
   YouTube video ID (YouTube downloads). Log hits are **live-verified** on the server — if the file has been deleted,
   the stale record is automatically removed and the import proceeds as if no duplicate existed.
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

## Cover Art

Navidrome primarily reads cover art embedded in audio file metadata, so `gm` ensures artwork is both embedded in the
audio file and saved as `cover.jpg` in the album directory.

### YouTube downloads

`yt-dlp` handles artwork automatically via `--embed-thumbnail`. The thumbnail is also copied to `cover.jpg` in the album
directory.

### Local file imports

For local video files (`.mp4`, `.mkv`, etc.), `gm` tries to find artwork in this order:

1. **Embedded thumbnail** — extracts attached picture streams from the video (e.g., album art embedded by yt-dlp)
2. **YouTube thumbnail download** — if the filename contains a video ID (e.g., `Song-[dQw4w9WgXcQ].mp4`), downloads the
   thumbnail from `img.youtube.com` (tries 1080p first, falls back to 480p)

When a thumbnail is found, it is:

- **Embedded** in the extracted audio file using mutagen (format-specific: ID3 APIC for mp3, MP4Cover for m4a,
  METADATA_BLOCK_PICTURE for ogg/opus, FLAC Picture for flac)
- **Copied** as `cover.jpg` to the album directory on the server

### Metadata vs Filenames

Metadata text fields (artist, album, title) preserve the original characters you type — "Ex:Re", "AC/DC", etc. Only
the filesystem paths are sanitized (colons, slashes, and other unsafe characters replaced with hyphens). This means
Navidrome displays the correct artist/album names while the files are stored safely on disk.

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
│   └── YouTube/
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
- Artist, album, title, genre
- Destination path on the server
- File hash (SHA-256, for local files)
- YouTube video ID (for YouTube downloads)

This enables fast duplicate detection without needing SSH calls for every file. The genre field is also used to suggest
a default genre when importing new songs by a previously seen artist. Stale records (for files deleted from the server)
are automatically pruned during imports, or can be bulk-cleaned with `gm prune`.
