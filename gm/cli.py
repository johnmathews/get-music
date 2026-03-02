"""CLI argument parsing and input routing."""

from __future__ import annotations

import re
import sys
from enum import Enum, auto
from pathlib import Path

from gm.ui import (
    E_BROOM, E_CHECK, E_ERROR,
    bold, bold_cyan, bold_red, cyan, dim, green, yellow,
)
from gm.youtube import handle_youtube
from gm.files import handle_file, handle_directory


class InputType(Enum):
    YOUTUBE_URL = auto()
    FILE = auto()
    DIRECTORY = auto()


_YOUTUBE_RE = re.compile(
    r"^https?://(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/"
)


def detect_input_type(arg: str) -> InputType:
    """Determine whether the argument is a YouTube URL, file, or directory."""
    if _YOUTUBE_RE.match(arg):
        return InputType.YOUTUBE_URL

    if arg.startswith("http://") or arg.startswith("https://"):
        print(f"{E_ERROR}{bold_red('Error:')} unsupported URL: {cyan(arg)}", file=sys.stderr)
        raise SystemExit(1)

    path = Path(arg)
    if not path.exists():
        print(f"{E_ERROR}{bold_red('Error:')} path does not exist: {cyan(arg)}", file=sys.stderr)
        raise SystemExit(1)

    if path.is_dir():
        return InputType.DIRECTORY
    return InputType.FILE


def get_help_text() -> str:
    """Return the help/usage text."""
    return f"""\
{bold_cyan('gm')} - get music for Navidrome

{bold('Usage:')}
  {green('gm <youtube-url>')}   Download audio from YouTube to the music library
  {green('gm <file>')}          Process and transfer a local audio/video file
  {green('gm <directory>')}     Process all audio/video files in a directory
  {green('gm log [N]')}         Show recent imports (default: 20)
  {green('gm prune')}           Remove stale log entries for deleted files
  {green('gm help')}            Show this help message

{bold('Examples:')}
  gm https://www.youtube.com/watch?v=dQw4w9WgXcQ
  gm ~/Downloads/song.mp3
  gm ~/Downloads/album/
  gm log 10
"""


def main(argv: list[str] | None = None) -> None:
    """Entry point for the gm CLI."""
    args = argv if argv is not None else sys.argv[1:]

    if not args or args[0] == "help":
        print(get_help_text())
        raise SystemExit(0)

    if args[0] == "log":
        from gm.history import recent_imports, format_log

        limit = int(args[1]) if len(args) > 1 else 20
        records = recent_imports(limit=limit)
        print(format_log(records))
        return

    if args[0] == "prune":
        from gm.history import all_imports, delete_import
        from gm.metadata import check_destination_exists

        records = all_imports()
        pruned = 0
        for record in records:
            if record.destination and not check_destination_exists(record.destination):
                print(f"  {E_BROOM}{yellow('Stale:')} {dim(record.destination)}")
                delete_import(record.destination)
                pruned += 1
        print(f"{E_CHECK}Pruned {bold(str(pruned))} stale record(s) out of {bold(str(len(records)))} total.")
        return

    arg = args[0]
    input_type = detect_input_type(arg)

    if input_type == InputType.YOUTUBE_URL:
        handle_youtube(arg)
    elif input_type == InputType.FILE:
        handle_file(Path(arg))
    elif input_type == InputType.DIRECTORY:
        handle_directory(Path(arg))
