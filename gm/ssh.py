"""SSH utilities for communicating with the LXC."""

from __future__ import annotations

import shlex
import subprocess

SSH_HOST = "music"

# SSH options for reliability: connection timeout, multiplexing, keep-alive
_SSH_OPTIONS = [
    "-o", "ConnectTimeout=10",
    "-o", "ControlMaster=auto",
    "-o", "ControlPath=/tmp/gm-ssh-%r@%h:%p",
    "-o", "ControlPersist=60",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
]

# Default timeout for SSH commands (seconds)
_DEFAULT_TIMEOUT = 300
_STREAM_TIMEOUT = 600


def quote_path(path: str) -> str:
    """Shell-quote a path for safe use in SSH commands."""
    return shlex.quote(path)


def ssh_run(
    command: str, *, check: bool = False, stream: bool = False,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command on the LXC via SSH.

    Uses connection multiplexing to reuse a single TCP/SSH connection
    across calls, and enforces timeouts to prevent indefinite hangs.
    """
    ssh_cmd = ["ssh"] + _SSH_OPTIONS + [SSH_HOST, command]
    effective_timeout = timeout or (_STREAM_TIMEOUT if stream else _DEFAULT_TIMEOUT)

    try:
        if stream:
            result = subprocess.run(
                ssh_cmd, text=True, check=False,
                timeout=effective_timeout,
            )
            completed = subprocess.CompletedProcess(
                result.args, result.returncode,
                result.stdout or "", result.stderr or "",
            )
        else:
            completed = subprocess.run(
                ssh_cmd,
                capture_output=True, text=True, check=False,
                timeout=effective_timeout,
            )
    except subprocess.TimeoutExpired:
        completed = subprocess.CompletedProcess(ssh_cmd, 1, "", "SSH command timed out")

    if check and completed.returncode != 0:
        raise RuntimeError(
            f"SSH command failed (exit {completed.returncode}): {completed.stderr.strip()}"
        )
    return completed
