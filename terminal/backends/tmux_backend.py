from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from ..keys import key_to_tmux
from ..screen_buffer import ScreenSnapshot


class TmuxSession:
    """Terminal backend backed by a real tmux pane.

    This avoids several edge cases of self-managed PTYs because ssh/sudo/TUI
    programs see a normal tmux-backed terminal.
    """

    def __init__(
        self,
        session_id: str,
        command: str,
        cwd: str,
        rows: int,
        cols: int,
    ):
        if not shutil.which("tmux"):
            raise RuntimeError("未找到 tmux，请先在系统中安装 tmux 或改用 backend_mode=pty")

        self.name = _safe_tmux_name(session_id)
        self.target = f"{self.name}:0.0"
        self.rows = max(1, rows)
        self.cols = max(20, cols)
        self._last_capture = ""
        self._read_capture = ""
        self._seq = 0
        self._closed = False

        tmux_command = _wrap_command_for_utf8(command)
        args = [
            "tmux",
            "new-session",
            "-d",
            "-s",
            self.name,
            "-x",
            str(self.cols),
            "-y",
            str(self.rows),
        ]
        if cwd:
            args.extend(["-c", str(Path(cwd))])
        args.append(tmux_command)
        self._run(args)
        self._refresh_capture()

    @property
    def alive(self) -> bool:
        if self._closed:
            return False
        return (
            subprocess.run(
                ["tmux", "has-session", "-t", self.name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            == 0
        )

    @property
    def output_seq(self) -> int:
        self._refresh_capture()
        return self._seq

    def write(self, text: str) -> None:
        if not self.alive:
            raise RuntimeError("tmux 会话已结束")
        buffer_name = f"{self.name}_input"
        self._run(
            ["tmux", "load-buffer", "-b", buffer_name, "-"],
            input_text=text,
        )
        self._run(["tmux", "paste-buffer", "-b", buffer_name, "-t", self.target, "-d"])
        self._refresh_capture()

    def send_key(self, key: str) -> None:
        tmux_key = key_to_tmux(key)
        self._run(["tmux", "send-keys", "-t", self.target, tmux_key])
        self._refresh_capture()

    def resize(self, rows: int, cols: int) -> None:
        if not self.alive:
            raise RuntimeError("tmux 会话已结束")
        self.rows = max(1, rows)
        self.cols = max(20, cols)
        self._run(
            [
                "tmux",
                "resize-window",
                "-t",
                self.name,
                "-x",
                str(self.cols),
                "-y",
                str(self.rows),
            ]
        )
        self._refresh_capture()

    def snapshot(self, screen_limit: int, recent_limit: int) -> ScreenSnapshot:
        screen_limit = max(200, int(screen_limit or 8000))
        recent_limit = max(200, int(recent_limit or 4000))
        capture = self._refresh_capture()
        recent = _capture_delta(self._read_capture, capture)
        self._read_capture = capture

        recent_truncated = len(recent) > recent_limit
        if recent_truncated:
            recent = recent[-recent_limit:]
        screen = capture[-screen_limit:]
        return ScreenSnapshot(
            seq=self._seq,
            screen=screen,
            recent_output=recent,
            truncated=len(capture) > screen_limit or recent_truncated,
        )

    def close(self) -> None:
        self._closed = True
        subprocess.run(
            ["tmux", "kill-session", "-t", self.name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    def _refresh_capture(self) -> str:
        if not self.alive:
            return self._last_capture
        capture = self._run(
            ["tmux", "capture-pane", "-t", self.target, "-p", "-S", "-2000"],
            capture_output=True,
        ).rstrip("\n")
        if capture != self._last_capture:
            self._last_capture = capture
            self._seq += 1
        return self._last_capture

    def _run(
        self,
        args: list[str],
        input_text: str | None = None,
        capture_output: bool = False,
    ) -> str:
        env = os.environ.copy()
        env.setdefault("LANG", "C.UTF-8")
        env.setdefault("LC_ALL", env.get("LANG", "C.UTF-8"))
        result = subprocess.run(
            args,
            input=input_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip()
            raise RuntimeError(f"tmux command failed: {' '.join(args)}; {err}")
        return result.stdout or ""


def _safe_tmux_name(session_id: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "terminal")
    return f"astrbot_{name}"


def _wrap_command_for_utf8(command: str) -> str:
    command = (command or "sh").strip()
    return (
        'export LANG="${LANG:-C.UTF-8}"; '
        'export LC_ALL="${LC_ALL:-$LANG}"; '
        f"exec {command}"
    )


def _capture_delta(previous: str, current: str) -> str:
    if not previous:
        return current
    if current.startswith(previous):
        return current[len(previous) :]
    prev_lines = previous.splitlines()
    current_lines = current.splitlines()
    max_overlap = min(len(prev_lines), len(current_lines))
    for count in range(max_overlap, 0, -1):
        if prev_lines[-count:] == current_lines[:count]:
            return "\n".join(current_lines[count:])
    return current
