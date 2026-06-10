from __future__ import annotations

import shlex
import threading
from pathlib import Path

from ..screen_buffer import TextRingBuffer


class UnsupportedPtySession:
    def __init__(self, reason: str):
        self.reason = reason

    @property
    def alive(self) -> bool:
        return False

    def write(self, text: str) -> None:
        raise RuntimeError(self.reason)

    def resize(self, rows: int, cols: int) -> None:
        raise RuntimeError(self.reason)

    def close(self) -> None:
        return None


class PtyProcessSession:
    def __init__(
        self,
        command: str,
        cwd: str,
        rows: int,
        cols: int,
        buffer: TextRingBuffer,
    ):
        try:
            from ptyprocess import PtyProcessUnicode
        except Exception as exc:
            raise RuntimeError(
                "缺少 ptyprocess 依赖，请安装 requirements.txt 后重启 AstrBot"
            ) from exc

        argv = shlex.split(command, posix=True)
        if not argv:
            raise RuntimeError("终端 command 为空")

        kwargs = {"dimensions": (max(1, rows), max(20, cols))}
        if cwd:
            kwargs["cwd"] = str(Path(cwd))

        self.buffer = buffer
        self._closed = False
        self._proc = PtyProcessUnicode.spawn(argv, **kwargs)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    @property
    def alive(self) -> bool:
        if self._closed:
            return False
        try:
            return bool(self._proc.isalive())
        except Exception:
            return False

    def write(self, text: str) -> None:
        if not self.alive:
            raise RuntimeError("终端会话已结束")
        self._proc.write(text)

    def resize(self, rows: int, cols: int) -> None:
        if not self.alive:
            raise RuntimeError("终端会话已结束")
        self._proc.setwinsize(max(1, rows), max(20, cols))

    def close(self) -> None:
        self._closed = True
        try:
            if self._proc.isalive():
                self._proc.terminate(force=True)
        except Exception:
            pass

    def _read_loop(self) -> None:
        while not self._closed:
            try:
                chunk = self._proc.read(4096)
            except EOFError:
                break
            except Exception as exc:
                self.buffer.append(f"\n[terminal read error: {exc}]\n")
                break
            if chunk:
                self.buffer.append(str(chunk))
        self._closed = True
