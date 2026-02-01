from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import settings
from .base import ToolContext, ToolSpec, register


def _run_local(cmd: List[str], cwd: Path, timeout: int) -> Dict[str, Any]:
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
    )
    return {
        "ok": p.returncode == 0,
        "returncode": p.returncode,
        "stdout": p.stdout[-20000:],
        "stderr": p.stderr[-20000:],
    }


def _run_docker(cmd: List[str], cwd: Path, timeout: int) -> Dict[str, Any]:
    # Requires docker installed on host running the service
    docker_cmd = [
        "docker", "run", "--rm",
        "-v", f"{str(cwd)}:/workspace",
        "-w", "/workspace",
        settings.shell_docker_image,
    ] + cmd
    return _run_local(docker_cmd, cwd=cwd, timeout=timeout)


def shell_exec(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    if not settings.shell_allow:
        raise PermissionError("shell execution disabled by server config")
    command = args.get("command")
    if not command:
        raise ValueError("command is required")
    timeout = int(args.get("timeout", 120))
    # Accept either a string or a list
    if isinstance(command, str):
        cmd = shlex.split(command)
    elif isinstance(command, list):
        cmd = [str(x) for x in command]
    else:
        raise ValueError("command must be string or list")
    cwd = ctx.workspace_root
    if settings.shell_docker_backend:
        return _run_docker(cmd, cwd=cwd, timeout=timeout)
    return _run_local(cmd, cwd=cwd, timeout=timeout)


def register_shell_tools() -> None:
    register(
        ToolSpec(
            name="shell.exec",
            description="Execute a shell command inside the workspace. Returns stdout/stderr/returncode. Use for coding, builds, and automation.",
            json_schema={
                "type": "object",
                "properties": {
                    "command": {"description": "command string or argv list", "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                    "timeout": {"type": "integer", "default": 120},
                },
                "required": ["command"],
            },
            func=shell_exec,
            risky=True,
        )
    )
