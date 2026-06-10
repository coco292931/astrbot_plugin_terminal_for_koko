from __future__ import annotations

import sys
import time
from dataclasses import dataclass

from .backends import PtyProcessSession, WinPtySession
from .screen_buffer import ScreenSnapshot, TextRingBuffer


KEY_MAP = {
    "enter": "\r",
    "tab": "\t",
    "escape": "\x1b",
    "esc": "\x1b",
    "backspace": "\x7f",
    "ctrl_c": "\x03",
    "ctrl_d": "\x04",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
}


@dataclass
class TerminalSession:
    session_id: str
    command: str
    cwd: str
    rows: int
    cols: int
    max_history_chars: int

    def __post_init__(self):
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.buffer = TextRingBuffer(self.max_history_chars)
        self.backend = self._create_backend()

    @property
    def alive(self) -> bool:
        return bool(getattr(self.backend, "alive", False))

    def touch(self) -> None:
        self.updated_at = time.time()

    def is_idle_expired(self, ttl_seconds: int) -> bool:
        return time.time() - self.updated_at > ttl_seconds

    def write(self, text: str) -> None:
        self.touch()
        self.backend.write(text)

    def send_key(self, key: str) -> None:
        normalized = (key or "").strip().lower().replace("-", "_")
        if normalized not in KEY_MAP:
            raise ValueError(f"不支持的 key: {key}")
        self.write(KEY_MAP[normalized])

    def resize(self, rows: int, cols: int) -> None:
        self.rows = max(1, rows)
        self.cols = max(20, cols)
        self.touch()
        self.backend.resize(self.rows, self.cols)

    def snapshot(self, screen_limit: int, recent_limit: int) -> ScreenSnapshot:
        self.touch()
        return self.buffer.snapshot(screen_limit, recent_limit)

    def close(self) -> None:
        self.touch()
        self.backend.close()

    def _create_backend(self):
        if sys.platform.startswith("win"):
            return WinPtySession(
                command=self.command,
                cwd=self.cwd,
                rows=self.rows,
                cols=self.cols,
                buffer=self.buffer,
            )
        return PtyProcessSession(
            command=self.command,
            cwd=self.cwd,
            rows=self.rows,
            cols=self.cols,
            buffer=self.buffer,
        )
