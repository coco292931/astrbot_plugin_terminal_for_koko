from __future__ import annotations

import os
import threading
from pathlib import Path

from ..screen_buffer import TextRingBuffer


class WinPtySession:
    def __init__(
        self,
        command: str,
        cwd: str,
        rows: int,
        cols: int,
        buffer: TextRingBuffer,
    ):
        try:
            from winpty import PtyProcess
        except Exception as exc:
            raise RuntimeError(
                "缺少 pywinpty 依赖，请安装 requirements.txt 后重启 AstrBot"
            ) from exc

        self.buffer = buffer
        self._closed = False
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        kwargs = {"dimensions": (max(1, rows), max(20, cols)), "env": env}
        if cwd:
            kwargs["cwd"] = str(Path(cwd))
        try:
            self._proc = PtyProcess.spawn(command, **kwargs)
        except TypeError:
            kwargs.pop("env", None)
            self._proc = PtyProcess.spawn(command, **kwargs)
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
        if hasattr(self._proc, "setwinsize"):
            self._proc.setwinsize(max(1, rows), max(20, cols))
        else:
            self.buffer.append("\n[terminal warning: backend does not support resize]\n")

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
