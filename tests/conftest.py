"""Shared test fixtures."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def tmp_audio_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with fake audio files."""
    for name in ["song1.mp3", "song2.flac", "song3.ogg"]:
        (tmp_path / name).write_bytes(b"\x00" * 100)
    return tmp_path


@pytest.fixture
def tmp_nested_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with nested audio files."""
    sub = tmp_path / "subdir"
    sub.mkdir()
    (tmp_path / "top.mp3").write_bytes(b"\x00" * 100)
    (sub / "nested.flac").write_bytes(b"\x00" * 100)
    return tmp_path


@pytest.fixture
def mock_ssh():
    """Mock subprocess.run for SSH commands."""
    with patch("subprocess.run") as mock:
        mock.return_value.returncode = 0
        mock.return_value.stdout = ""
        mock.return_value.stderr = ""
        yield mock


@pytest.fixture
def mock_input():
    """Mock builtins.input for user prompts."""
    with patch("builtins.input") as mock:
        yield mock
