"""ANSI color helpers and emoji constants for CLI output.

All helpers auto-detect TTY: when stdout is not a terminal (piped, tests, CI),
they return plain text with no escape codes or emoji prefixes.
"""

from __future__ import annotations

import sys

_COLOR: bool = sys.stdout.isatty()

# --- ANSI escape helpers ---

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"


def bold(text: str) -> str:
    if not _COLOR:
        return text
    return f"{_BOLD}{text}{_RESET}"


def dim(text: str) -> str:
    if not _COLOR:
        return text
    return f"{_DIM}{text}{_RESET}"


def cyan(text: str) -> str:
    if not _COLOR:
        return text
    return f"{_CYAN}{text}{_RESET}"


def green(text: str) -> str:
    if not _COLOR:
        return text
    return f"{_GREEN}{text}{_RESET}"


def yellow(text: str) -> str:
    if not _COLOR:
        return text
    return f"{_YELLOW}{text}{_RESET}"


def red(text: str) -> str:
    if not _COLOR:
        return text
    return f"{_RED}{text}{_RESET}"


def bold_cyan(text: str) -> str:
    if not _COLOR:
        return text
    return f"{_BOLD}{_CYAN}{text}{_RESET}"


def bold_green(text: str) -> str:
    if not _COLOR:
        return text
    return f"{_BOLD}{_GREEN}{text}{_RESET}"


def bold_yellow(text: str) -> str:
    if not _COLOR:
        return text
    return f"{_BOLD}{_YELLOW}{text}{_RESET}"


def bold_red(text: str) -> str:
    if not _COLOR:
        return text
    return f"{_BOLD}{_RED}{text}{_RESET}"


# --- Emoji constants ---

def _emoji(char: str) -> str:
    return f"{char} " if _COLOR else ""


E_MUSIC: str = _emoji("\U0001f3b5")      # 🎵
E_CHECK: str = _emoji("\u2705")           # ✅
E_DONE: str = _emoji("\U0001f389")        # 🎉
E_SKIP: str = _emoji("\u23ed\ufe0f")      # ⏭️
E_WARN: str = _emoji("\u26a0\ufe0f")      # ⚠️
E_ERROR: str = _emoji("\u274c")           # ❌
E_SEARCH: str = _emoji("\U0001f50d")      # 🔍
E_WRITE: str = _emoji("\u270f\ufe0f")     # ✏️
E_SEND: str = _emoji("\U0001f4e4")        # 📤
E_FOLDER: str = _emoji("\U0001f4c2")      # 📂
E_LINK: str = _emoji("\U0001f517")        # 🔗
E_SCISSORS: str = _emoji("\u2702\ufe0f")  # ✂️
E_BROOM: str = _emoji("\U0001f9f9")       # 🧹
