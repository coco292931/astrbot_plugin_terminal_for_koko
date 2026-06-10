from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass
class ScreenSnapshot:
    seq: int
    screen: str
    recent_output: str
    truncated: bool


class TextRingBuffer:
    def __init__(self, max_history_chars: int = 20000):
        self.max_history_chars = max(1000, int(max_history_chars or 20000))
        self._lock = Lock()
        self._history = ""
        self._history_start = 0
        self._read_cursor = 0
        self._total_written = 0
        self._seq = 0

    def append(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._history += text
            self._total_written += len(text)
            self._seq += 1
            if len(self._history) > self.max_history_chars:
                overflow = len(self._history) - self.max_history_chars
                self._history = self._history[overflow:]
                self._history_start += overflow
                if self._read_cursor < self._history_start:
                    self._read_cursor = self._history_start

    def snapshot(self, screen_limit: int, recent_limit: int) -> ScreenSnapshot:
        screen_limit = max(200, int(screen_limit or 8000))
        recent_limit = max(200, int(recent_limit or 4000))
        with self._lock:
            history_end = self._history_start + len(self._history)
            start = max(self._read_cursor, self._history_start)
            offset = max(0, start - self._history_start)
            recent = self._history[offset:]
            self._read_cursor = history_end

            recent_truncated = len(recent) > recent_limit
            if recent_truncated:
                recent = recent[-recent_limit:]

            screen = self._history[-screen_limit:]
            history_truncated = self._history_start > 0
            return ScreenSnapshot(
                seq=self._seq,
                screen=screen,
                recent_output=recent,
                truncated=history_truncated or recent_truncated,
            )
