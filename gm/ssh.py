"""SSH utilities for communicating with the LXC."""

from __future__ import annotations

import shlex
import subprocess

SSH_HOST = "music"


def quote_path(path: str) -> str:
    """Shell-quote a path for safe use in SSH commands."""
    return shlex.quote(path)


def ssh_run(command: str, *, check: bool = False, stream: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command on the LXC via SSH."""
    if stream:
        result = subprocess.run(
            ["ssh", SSH_HOST, command],
            stdout=subprocess.DEVNULL, text=True, check=False,
        )
        completed = subprocess.CompletedProcess(result.args, result.returncode, "", "")
    else:
        completed = subprocess.run(
            ["ssh", SSH_HOST, command],
            capture_output=True, text=True, check=False,
        )
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"SSH command failed (exit {completed.returncode}): {completed.stderr.strip()}"
        )
    return completed
