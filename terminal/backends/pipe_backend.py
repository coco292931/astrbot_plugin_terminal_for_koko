from __future__ import annotations

import os
import signal
import subprocess
import threading
import codecs
from pathlib import Path

from ..keys import key_to_ansi, parse_key
from ..screen_buffer import TextRingBuffer


class PipeProcessSession:
    """Non-PTY command backend.

    This backend is useful for commands such as sshpass that manage their own
    pseudo-terminal and can misbehave when wrapped in another terminal PTY.
    """

    def __init__(
        self,
        command: str,
        cwd: str,
        rows: int,
        cols: int,
        buffer: TextRingBuffer,
    ):
        self.command = command
        self.buffer = buffer
        self.rows = rows
        self.cols = cols
        self._closed = False

        env = os.environ.copy()
        env.setdefault("LANG", "C.UTF-8")
        env.setdefault("LC_ALL", env.get("LANG", "C.UTF-8"))
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")

        popen_kwargs = {
            "shell": True,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "bufsize": 0,
            "env": env,
        }
        if cwd:
            popen_kwargs["cwd"] = str(Path(cwd))

        self._proc = subprocess.Popen(command, **popen_kwargs)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    @property
    def alive(self) -> bool:
        return not self._closed and self._proc.poll() is None

    @property
    def output_seq(self) -> int:
        return self.buffer.seq

    def write(self, text: str) -> None:
        if not self.alive:
            raise RuntimeError("pipe 命令会话已结束")
        if self._proc.stdin is None:
            raise RuntimeError("pipe 命令 stdin 不可写")
        self._proc.stdin.write(text.encode("utf-8", errors="replace"))
        self._proc.stdin.flush()

    def send_key(self, key: str) -> None:
        modifiers, base = parse_key(key)
        if modifiers == {"ctrl"} and base == "c":
            self._send_interrupt()
            return
        if modifiers == {"ctrl"} and base == "d" and self._proc.stdin is not None:
            self._proc.stdin.close()
            return
        self.write(key_to_ansi(key))

    def resize(self, rows: int, cols: int) -> None:
        self.rows = rows
        self.cols = cols
        self.buffer.append("\n[terminal warning: pipe backend does not support resize]\n")

    def close(self) -> None:
        self._closed = True
        try:
            if self._proc.poll() is None:
                if os.name == "nt":
                    self._proc.terminate()
                else:
                    self._proc.send_signal(signal.SIGTERM)
        except Exception:
            pass

    def _send_interrupt(self) -> None:
        if self._proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                self._proc.terminate()
            else:
                self._proc.send_signal(signal.SIGINT)
        except Exception:
            self._proc.terminate()

    def _read_loop(self) -> None:
        if self._proc.stdout is None:
            self._closed = True
            return

        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        fd = self._proc.stdout.fileno()
        while not self._closed:
            try:
                chunk = os.read(fd, 4096)
            except Exception as exc:
                self.buffer.append(f"\n[terminal read error: {exc}]\n")
                break
            if not chunk:
                break
            self.buffer.append(decoder.decode(chunk))

        tail = decoder.decode(b"", final=True)
        if tail:
            self.buffer.append(tail)

        code = self._proc.poll()
        if code is None:
            try:
                code = self._proc.wait(timeout=0.1)
            except Exception:
                code = None
        if code is not None:
            self.buffer.append(f"\n[pipe process exited with code {code}]\n")
        self._closed = True
