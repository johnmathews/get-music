# 2026-03-20: Fix opus artwork and metadata rewrite

## Problem

Three issues with YouTube downloads in opus format (the most common format):

1. **Metadata rewrite silently failed.** `write_metadata_ssh` used ffmpeg to rewrite tags, but ffmpeg cannot mux
   video/picture streams into opus containers. The command either failed entirely (with `-map 0`) or stripped the
   embedded artwork (with `-map 0:a`). Either way, user-confirmed metadata was never written.

2. **No post-verification.** `verify_thumbnail_embedded` only ran *before* `write_metadata_ssh`, so there was no check
   that the final file still had artwork after the metadata rewrite step.

3. **Filenames with spaces crashed the tool.** Shell glob expansion of `ls -1t /tmp/dir/*.opus` produces unquoted
   results, so filenames like `Classical Morning - Relaxing, Uplifting Classical Music.opus` cause `ls` to see each word
   as a separate argument.

## Fix

### Opus metadata via mutagen (`gm/metadata.py`)

`write_metadata_ssh` now uses mutagen directly for opus/ogg files instead of ffmpeg. Mutagen modifies OGG tags in-place
without touching `metadata_block_picture` (embedded artwork), eliminating the strip-then-re-embed problem entirely.
Non-opus formats (mp3, m4a, flac) still use ffmpeg with `-map 0 -c copy`.

### Post-verification (`gm/youtube.py`)

After `write_metadata_ssh` completes, `verify_thumbnail_embedded` runs on the final file. For opus files, this also
checks `metadata_block_picture` via mutagen (ffprobe only reports it as a video stream when embedded a certain way).
If artwork is missing, attempts recovery via `reembed_thumbnail_ssh`, or exits with diagnostics.

### File discovery with spaces (`gm/youtube.py`)

Replaced all `ls` glob patterns with `find ... -print0 | xargs -0 ls -1t` which handles spaces via null-delimited
output. Split info.json discovery into `find` + `cat` so the path is properly quoted.

## Evolution of the fix

The initial approach tried `-map 0:a` (audio-only ffmpeg) then re-embedding the thumbnail via mutagen. This failed
because yt-dlp deletes the loose thumbnail file after embedding (`--embed-thumbnail` without `--write-thumbnail`), so
there was nothing to re-embed from. The final approach — using mutagen for the entire metadata write — is simpler and
avoids the problem entirely.

## Key decision

Using mutagen for opus metadata instead of ffmpeg. This is the same library yt-dlp uses for opus thumbnail embedding,
and it's already installed on the LXC. Mutagen preserves all existing tags and artwork when writing, unlike ffmpeg which
requires explicit stream mapping.
