# 2026-03-20: Fix opus artwork and metadata rewrite

## Problem

Two issues with YouTube downloads in opus format (the most common format):

1. **Metadata rewrite silently failed.** `write_metadata_ssh` used `ffmpeg -map 0 -c copy` which tries to copy all
   streams including the embedded thumbnail (a video stream). The opus/ogg muxer doesn't support video streams, so ffmpeg
   failed entirely — meaning user-confirmed metadata (artist, album, title corrections) was never written. The file kept
   yt-dlp's defaults.

2. **No post-verification.** `verify_thumbnail_embedded` only ran *before* `write_metadata_ssh`, so there was no check
   that the final file still had artwork after the metadata rewrite step. Any future regression in this area would be
   silent.

## Fix

### Opus-aware metadata rewrite (`gm/metadata.py`)

- For opus/ogg files, `write_metadata_ssh` now uses `-map 0:a` (audio only) so the ffmpeg metadata rewrite succeeds.
- After the rewrite, a new `reembed_thumbnail_ssh` function re-embeds the thumbnail using mutagen on the LXC via SSH.
  This mirrors what yt-dlp itself does — `OggOpus` + `FLAC Picture` + `metadata_block_picture`.
- Non-opus formats (mp3, m4a, flac) still use `-map 0` to preserve all streams including thumbnails.

### Post-verification (`gm/youtube.py`)

- After `write_metadata_ssh` completes and cover art is saved, `verify_thumbnail_embedded` runs on the final file.
- If artwork is missing, attempts recovery via `reembed_thumbnail_ssh` from the cover file.
- If recovery fails, prints diagnostics and exits with error instead of silently producing artwork-less files.

### File discovery with spaces in filenames (`gm/youtube.py`)

Discovered during live testing: `ls -1t /tmp/dir/*.opus` fails when the glob expands to a filename with spaces (e.g.,
`Classical Morning - Relaxing, Uplifting Classical Music.opus`). The shell expands the glob but doesn't quote the result,
so `ls` sees each word as a separate argument.

- Replaced all `ls` glob patterns with `find ... -print0 | xargs -0 ls -1t` which handles spaces via null-delimited
  output.
- Split info.json discovery into `find` + `cat` so the path is properly quoted with `quote_path()`.
- Also replaced thumbnail discovery `ls *.jpg *.png *.webp` with `find ... \( -name '*.jpg' -o -name '*.png' ... \)`.

## Key decision

Using mutagen via SSH (`python3 -c '...'`) rather than trying to make ffmpeg work with opus thumbnails. This is the same
approach yt-dlp uses, and mutagen is already installed on the LXC (verified via `python3 -c 'import mutagen'`).
