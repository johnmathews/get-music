"""Tests for SSH utilities."""

from __future__ import annotations

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from gm.ssh import ssh_run, SSH_HOST, quote_path, _SSH_OPTIONS


class TestSshRun:
    """Test SSH command execution."""

    @patch("gm.ssh.subprocess.run")
    def test_runs_command_via_ssh(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="output", stderr=""
        )
        result = ssh_run("ls /tmp")
        expected_cmd = ["ssh"] + _SSH_OPTIONS + [SSH_HOST, "ls /tmp"]
        mock_run.assert_called_once_with(
            expected_cmd,
            capture_output=True, text=True, check=False,
            timeout=300,
        )
        assert result.stdout == "output"

    @patch("gm.ssh.subprocess.run")
    def test_raises_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error msg"
        )
        with pytest.raises(RuntimeError, match="error msg"):
            ssh_run("bad command", check=True)

    @patch("gm.ssh.subprocess.run")
    def test_stream_mode(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["ssh", SSH_HOST, "yt-dlp url"], returncode=0,
            stdout="", stderr="",
        )
        result = ssh_run("yt-dlp url", stream=True)
        expected_cmd = ["ssh"] + _SSH_OPTIONS + [SSH_HOST, "yt-dlp url"]
        mock_run.assert_called_once_with(
            expected_cmd,
            text=True, check=False,
            timeout=600,
        )
        assert result.stdout == ""

    @patch("gm.ssh.subprocess.run")
    def test_includes_connection_multiplexing(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        ssh_run("echo test")
        cmd = mock_run.call_args[0][0]
        assert "-o" in cmd
        assert "ControlMaster=auto" in cmd
        assert "ConnectTimeout=10" in cmd

    @patch("gm.ssh.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=300))
    def test_handles_timeout(self, mock_run: MagicMock) -> None:
        result = ssh_run("slow command")
        assert result.returncode == 1
        assert "timed out" in result.stderr

    @patch("gm.ssh.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=300))
    def test_timeout_with_check_raises(self, mock_run: MagicMock) -> None:
        with pytest.raises(RuntimeError, match="timed out"):
            ssh_run("slow command", check=True)

    @patch("gm.ssh.subprocess.run")
    def test_custom_timeout(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        ssh_run("fast command", timeout=30)
        assert mock_run.call_args[1]["timeout"] == 30


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
