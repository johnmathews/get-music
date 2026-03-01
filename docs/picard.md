# MusicBrainz Picard

[MusicBrainz Picard](https://picard.musicbrainz.org/) is a free, open-source music tagger that identifies and tags audio
files using the [MusicBrainz](https://musicbrainz.org/) database.

Use it to clean up files with bad or missing metadata before importing them with `gm`.

## Install

```bash
brew install musicbrainz-picard
```

## How It Works

1. **Load files** — drag audio files into Picard's left pane ("Unclustered Files")
2. **Identify** — two methods:
   - **Lookup** — searches MusicBrainz using existing metadata (title, artist)
   - **Scan** — generates an acoustic fingerprint via [AcoustID](https://acoustid.org/) and matches the actual audio,
     even if metadata is wrong or missing
3. **Match** — matched files move to the right pane, grouped by album release. Color indicates confidence: green = high,
   yellow = partial, red = poor
4. **Review** — compare original vs. new metadata before saving. Adjust anything that looks wrong.
5. **Save** — writes tags directly into the audio files

## Features

- **Acoustic fingerprinting** — identifies music by how it sounds, not by filename or existing tags
- **Cover art** — pulls album art from the [Cover Art Archive](https://coverartarchive.org/)
- **Batch processing** — tag entire albums or libraries at once
- **Plugins** — extensible (e.g., genre tags from Last.fm, Wikidata links)
- **Format support** — MP3, FLAC, OGG, Opus, M4A, WAV, WMA, and more

## Typical Workflow with gm

1. Open Picard and drag in files that need tagging
2. Click **Scan** (or **Lookup** if metadata is partially correct)
3. Review matches — fix any misidentified tracks
4. Click **Save** to write corrected metadata into the files
5. Import the tagged files with `gm`:
   ```bash
   gm ~/Downloads/album/
   ```
   `gm` will pick up the embedded metadata as defaults during its prompts.

## Tips

- **Scan over Lookup** — Scan (acoustic fingerprint) is more reliable for files with bad or missing metadata
- **Cluster first** — use **Tools > Cluster** to group files before lookup, which helps Picard match full albums
- **Check the colors** — green icons mean confident matches; yellow/red need manual review
- **Plugins** — install via **Options > Plugins**. The "Last.fm" plugin adds genre tags, which `gm` will use as defaults

## Links

- [Picard documentation](https://picard-docs.musicbrainz.org/)
- [MusicBrainz database](https://musicbrainz.org/)
- [AcoustID fingerprinting](https://acoustid.org/)
