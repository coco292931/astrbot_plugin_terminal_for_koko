from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SshpassCommand:
    password: str
    argv: list[str]
    prompt: str = "assword:"


def parse_sshpass_command(
    command: str,
    cwd: str = "",
    env: dict[str, str] | None = None,
) -> SshpassCommand | None:
    try:
        argv = shlex.split(command, posix=True)
    except Exception:
        return None
    if not argv or Path(argv[0]).name != "sshpass":
        return None

    password: str | None = None
    prompt = "assword:"
    env = env or os.environ
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            index += 1
            break
        if arg == "-p":
            index += 1
            if index >= len(argv):
                return None
            password = argv[index]
            index += 1
            continue
        if arg.startswith("-p") and len(arg) > 2:
            password = arg[2:]
            index += 1
            continue
        if arg == "-f":
            index += 1
            if index >= len(argv):
                return None
            password = _read_password_file(argv[index], cwd)
            if password is None:
                return None
            index += 1
            continue
        if arg.startswith("-f") and len(arg) > 2:
            password = _read_password_file(arg[2:], cwd)
            if password is None:
                return None
            index += 1
            continue
        if arg == "-e":
            password = env.get("SSHPASS")
            index += 1
            continue
        if arg.startswith("-e") and len(arg) > 2:
            password = env.get(arg[2:])
            index += 1
            continue
        if arg == "-P":
            index += 1
            if index >= len(argv):
                return None
            prompt = argv[index]
            index += 1
            continue
        if arg.startswith("-P") and len(arg) > 2:
            prompt = arg[2:]
            index += 1
            continue
        if arg in {"-v"}:
            index += 1
            continue
        if arg.startswith("-"):
            return None
        break

    inner_argv = argv[index:]
    if not password or not inner_argv:
        return None
    return SshpassCommand(password=password, argv=inner_argv, prompt=prompt)


def _read_password_file(path_text: str, cwd: str) -> str | None:
    try:
        path = Path(path_text).expanduser()
        if not path.is_absolute() and cwd:
            path = Path(cwd).expanduser() / path
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    return text.splitlines()[0] if text.splitlines() else ""
