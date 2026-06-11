from __future__ import annotations

import sys
import time
from dataclasses import dataclass

from .backends import PipeProcessSession, PtyProcessSession, TmuxSession, WinPtySession
from .keys import key_to_ansi
from .screen_buffer import ScreenSnapshot, TextRingBuffer


@dataclass
class TerminalSession:
    session_id: str
    command: str
    cwd: str
    rows: int
    cols: int
    max_history_chars: int
    backend_mode: str = "auto"

    def __post_init__(self):
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.buffer = TextRingBuffer(self.max_history_chars)
        self.backend = self._create_backend()

    @property
    def alive(self) -> bool:
        return bool(getattr(self.backend, "alive", False))

    @property
    def output_seq(self) -> int:
        backend_seq = getattr(self.backend, "output_seq", None)
        if backend_seq is not None:
            return int(backend_seq)
        return self.buffer.seq

    @property
    def backend_name(self) -> str:
        return type(self.backend).__name__

    def touch(self) -> None:
        self.updated_at = time.time()

    def is_idle_expired(self, ttl_seconds: int) -> bool:
        return time.time() - self.updated_at > ttl_seconds

    def write(self, text: str) -> None:
        self.touch()
        self.backend.write(text)

    def send_key(self, key: str) -> None:
        if hasattr(self.backend, "send_key"):
            self.touch()
            self.backend.send_key(key)
            return
        self.write(key_to_ansi(key))

    def resize(self, rows: int, cols: int) -> None:
        self.rows = max(1, rows)
        self.cols = max(20, cols)
        self.touch()
        self.backend.resize(self.rows, self.cols)

    def snapshot(self, screen_limit: int, recent_limit: int) -> ScreenSnapshot:
        self.touch()
        if hasattr(self.backend, "snapshot"):
            return self.backend.snapshot(screen_limit, recent_limit)
        return self.buffer.snapshot(screen_limit, recent_limit)

    def close(self) -> None:
        self.touch()
        self.backend.close()

    def _create_backend(self):
        backend_mode = (self.backend_mode or "auto").strip().lower()
        if backend_mode == "pipe":
            return PipeProcessSession(
                command=self.command,
                cwd=self.cwd,
                rows=self.rows,
                cols=self.cols,
                buffer=self.buffer,
            )
        if backend_mode == "tmux":
            return TmuxSession(
                session_id=self.session_id,
                command=self.command,
                cwd=self.cwd,
                rows=self.rows,
                cols=self.cols,
            )
        if backend_mode == "pty":
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
        if sys.platform.startswith("win"):
            return WinPtySession(
                command=self.command,
                cwd=self.cwd,
                rows=self.rows,
                cols=self.cols,
                buffer=self.buffer,
            )
        try:
            return TmuxSession(
                session_id=self.session_id,
                command=self.command,
                cwd=self.cwd,
                rows=self.rows,
                cols=self.cols,
            )
        except Exception:
            if backend_mode == "auto":
                return PtyProcessSession(
                    command=self.command,
                    cwd=self.cwd,
                    rows=self.rows,
                    cols=self.cols,
                    buffer=self.buffer,
                )
            raise
