from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

from ..keys import key_to_ansi, parse_key
from ..screen_buffer import TextRingBuffer
from ..sshpass import SshpassCommand, parse_sshpass_command


class SshpassPromptSession:
    """Run the command behind sshpass and answer password prompts directly.

    The normal sshpass binary allocates and watches its own PTY. When terminal
    backends also allocate a PTY, password injection can fail. This backend
    removes the sshpass wrapper and feeds the password into a single PTY.
    """

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
                "缺少 ptyprocess 依赖，无法使用 sshpass prompt 后端"
            ) from exc

        parsed = parse_sshpass_command(command, cwd=cwd)
        if parsed is None:
            raise ValueError("不是可转换的 sshpass 命令")

        self.command = command
        self.inner_argv = parsed.argv
        self.prompt = parsed.prompt
        self.password = parsed.password
        self.buffer = buffer
        self.rows = rows
        self.cols = cols
        self._closed = False
        self._password_sends = 0
        self._recent = ""
        self._last_send_at = 0.0

        env = os.environ.copy()
        env.setdefault("LANG", "C.UTF-8")
        env.setdefault("LC_ALL", env.get("LANG", "C.UTF-8"))
        env.setdefault("TERM", "xterm-256color")
        kwargs = {
            "dimensions": (max(1, rows), max(20, cols)),
            "env": env,
        }
        if cwd:
            kwargs["cwd"] = str(Path(cwd))

        self._proc = PtyProcessUnicode.spawn(parsed.argv, **kwargs)
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

    @property
    def output_seq(self) -> int:
        return self.buffer.seq

    def write(self, text: str) -> None:
        if not self.alive:
            raise RuntimeError("sshpass prompt 会话已结束")
        self._proc.write(text)

    def send_key(self, key: str) -> None:
        modifiers, base = parse_key(key)
        if modifiers == {"ctrl"} and base == "c":
            self.write("\x03")
            return
        if modifiers == {"ctrl"} and base == "d":
            self.write("\x04")
            return
        self.write(key_to_ansi(key))

    def resize(self, rows: int, cols: int) -> None:
        self.rows = max(1, rows)
        self.cols = max(20, cols)
        if not self.alive:
            raise RuntimeError("sshpass prompt 会话已结束")
        self._proc.setwinsize(self.rows, self.cols)

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
                text = str(chunk)
                self.buffer.append(text)
                self._maybe_send_password(text)
        self._closed = True

    def _maybe_send_password(self, text: str) -> None:
        if self._password_sends >= 3:
            return
        self._recent = (self._recent + text)[-2000:]
        if not _looks_like_password_prompt(self._recent, self.prompt):
            return

        now = time.monotonic()
        if now - self._last_send_at < 0.2:
            return
        self._last_send_at = now
        self._password_sends += 1
        self._recent = ""
        try:
            self._proc.write(self.password + "\r")
            self.buffer.append("\n[terminal info: password prompt answered]\n")
        except Exception as exc:
            self.buffer.append(f"\n[terminal password send error: {exc}]\n")


def can_handle_sshpass(command: str, cwd: str = "") -> bool:
    return parse_sshpass_command(command, cwd=cwd) is not None


def _looks_like_password_prompt(text: str, prompt: str) -> bool:
    lower = text.lower()
    if prompt and prompt.lower() in lower:
        return True
    if "密码" in text and re.search(r"[：:]\s*$", text):
        return True
    return re.search(r"(?i)(password|passcode)[^\r\n]*[:：]\s*$", text) is not None
