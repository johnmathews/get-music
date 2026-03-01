"""Tests for SSH utilities."""

from __future__ import annotations

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from gm.ssh import ssh_run, SSH_HOST, quote_path


class TestSshRun:
    """Test SSH command execution."""

    @patch("gm.ssh.subprocess.run")
    def test_runs_command_via_ssh(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="output", stderr=""
        )
        result = ssh_run("ls /tmp")
        mock_run.assert_called_once_with(
            ["ssh", SSH_HOST, "ls /tmp"],
            capture_output=True, text=True, check=False,
        )
        assert result.stdout == "output"

    @patch("gm.ssh.subprocess.run")
    def test_raises_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error msg"
        )
        with pytest.raises(RuntimeError, match="error msg"):
            ssh_run("bad command", check=True)


class TestQuotePath:
    """Test shell-safe path quoting."""

    def test_returns_safe_path_unchanged(self) -> None:
        # shlex.quote doesn't add quotes for paths with only safe chars
        assert quote_path("/mnt/nfs/music/Artist/Album") == "/mnt/nfs/music/Artist/Album"

    def test_quotes_single_quotes(self) -> None:
        result = quote_path("/mnt/nfs/music/It's/Album")
        # shlex.quote escapes single quotes so they're safe in a shell command
        assert "It" in result
        assert "'" in result or '"' in result

    def test_quotes_spaces(self) -> None:
        result = quote_path("/mnt/nfs/music/Led Zeppelin/Album")
        assert "Led Zeppelin" in result
        # Must be quoted since it contains spaces
        assert result != "/mnt/nfs/music/Led Zeppelin/Album"
