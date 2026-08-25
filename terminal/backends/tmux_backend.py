from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
from pathlib import Path

from ..keys import key_to_tmux
from ..screen_buffer import ScreenSnapshot

# Box-drawing, block and shade characters used by TUI programs for borders,
# scrollbars and progress bars. They carry no information for an LLM reading
# the screen, so they are stripped when tui_cleanup is enabled. Note this is
# the Unicode box-drawing set only — the ASCII pipe "|" is intentionally kept.
_TUI_DECORATION_CHARS = "│┃┌┐└┘├┤┬┴┼┏┓┗┛┣┫┳┻╋╭╮╰╯╔╗╚╝╠╣╦╩╬═║─━▏▎▍▌▋▊▉▖▗▘▝▚▞░▒▓█■□▪"
_TUI_DECORATION_TRANSLATION = str.maketrans("", "", _TUI_DECORATION_CHARS)

# Lines made only of spinner/fish/animation characters (e.g. the classic "><>"
# fish, the |/-\ spinner, braille spinners) are transient decoration and are
# dropped entirely.
_TUI_ANIMATION_LINE_RE = re.compile(r"^[><oO0*·.\-|/\\_⠀-⣿◐◓◑◒]+$")

# Context lines kept around each changed region when diffing full-screen redraws.
_DIFF_CONTEXT_LINES = 2
# Safety valve: difflib is quadratic in pathological cases, skip it for huge
# captures (tmux capture-pane is bounded by -S -2000, so this rarely triggers).
_DIFF_MAX_TOTAL_LINES = 8000


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
        tui_cleanup: bool = True,
    ):
        if not shutil.which("tmux"):
            raise RuntimeError(
                "未找到 tmux，请先在系统中安装 tmux 或改用 backend_mode=pty"
            )

        self.name = _safe_tmux_name(session_id)
        self.target = f"{self.name}:0.0"
        self.rows = max(1, rows)
        self.cols = max(20, cols)
        self.tui_cleanup = tui_cleanup
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
        # Use send-keys -l (literal) instead of paste-buffer.
        # paste-buffer wraps content in bracketed-paste escape sequences
        # (\x1b[200~...\x1b[201~) which raw TTY readers (ssh password prompt,
        # sudo, etc.) do not strip — the control chars become part of the input
        # and corrupt passwords or commands.  send-keys -l injects bytes
        # verbatim without any bracketed-paste wrapping.
        self._run(["tmux", "send-keys", "-t", self.target, "-l", text])
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
        if self.tui_cleanup:
            capture = _cleanup_tui_output(capture)
        # Delta is computed on the cleaned text so that decoration that merely
        # shifted (borders, animation residue) never re-enters recent_output.
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


def _cleanup_tui_output(text: str) -> str:
    """Strip TUI decoration noise from a captured screen.

    Removes box-drawing/shade/border characters, drops purely decorative
    animation lines (spinners, fish), collapses runs of blank lines and trims
    trailing whitespace, so LLM-facing output stays compact.

    Args:
        text: Raw tmux capture output.

    Returns:
        Cleaned text; blank-only input collapses to an empty string.
    """
    lines = []
    for line in text.splitlines():
        line = line.rstrip().translate(_TUI_DECORATION_TRANSLATION).rstrip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if _TUI_ANIMATION_LINE_RE.match(line.strip()):
            continue
        lines.append(line)
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _capture_delta(previous: str, current: str) -> str:
    """Extract only the genuinely new/changed part of current vs previous.

    Handles three patterns:
    - appended output: current starts with previous, the appended suffix is new;
    - scrolled output: the previous tail matches the current head, only the
      scrolled-in lines are new;
    - full-screen TUI redraws: a line diff restricted to changed regions, so the
      whole screen is never repeated in recent_output.

    Args:
        previous: The (cleaned) capture returned by the last snapshot.
        current: The (cleaned) capture from the current snapshot.

    Returns:
        The portion of current that is new or changed; empty if unchanged.
    """
    if not previous:
        return current
    if current.startswith(previous):
        # The boundary newline terminates the previous content, not the new
        # content; captures never end with "\n", so drop it.
        return current[len(previous) :].lstrip("\n")

    prev_lines = previous.splitlines()
    current_lines = current.splitlines()

    # Scrolling: previous lines shift up, only the tail of current is new.
    max_overlap = min(len(prev_lines), len(current_lines))
    for count in range(max_overlap, 0, -1):
        if prev_lines[-count:] == current_lines[:count]:
            # Only trust the scroll fast path when the overlap is substantial;
            # a small coincidental overlap in a full redraw would otherwise
            # return almost the whole screen again.
            if count >= max(4, len(current_lines) // 2):
                return "\n".join(current_lines[count:])
            break

    # Full redraw: report only the changed regions (with a little context).
    return "\n".join(_changed_regions(prev_lines, current_lines))


def _changed_regions(prev_lines: list[str], current_lines: list[str]) -> list[str]:
    """Return current lines restricted to regions that differ from previous.

    Args:
        prev_lines: Lines of the previous capture.
        current_lines: Lines of the current capture.

    Returns:
        Lines of current that sit inside changed regions, each region padded
        with a small context of unchanged lines; empty when nothing changed.
    """
    if not current_lines:
        return []
    if len(prev_lines) + len(current_lines) > _DIFF_MAX_TOTAL_LINES:
        return current_lines
    matcher = difflib.SequenceMatcher(None, prev_lines, current_lines, autojunk=False)
    changed: list[str] = []
    last_end = -1
    for tag, _, _, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        start = max(0, j1 - _DIFF_CONTEXT_LINES)
        end = min(len(current_lines), j2 + _DIFF_CONTEXT_LINES)
        if start < last_end:
            # Adjacent regions with overlapping context merge into one.
            start = last_end
        if start < end:
            changed.extend(current_lines[start:end])
            last_end = end
    return changed
