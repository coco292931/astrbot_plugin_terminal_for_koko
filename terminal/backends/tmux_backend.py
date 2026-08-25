from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
from pathlib import Path

from ..keys import key_to_tmux
from ..screen_buffer import ScreenSnapshot

# Box-drawing, block, shade, geometric and status characters used by TUI
# programs for borders, scrollbars, progress bars, spinners and prompts. They
# carry no information for an LLM reading the screen, so they are stripped when
# tui_cleanup is enabled. Note the ASCII pipe "|" is intentionally kept, as are
# letters, digits, and path/command punctuation like / _ - : ~.
_BRAILLE_CHARS = "".join(chr(c) for c in range(0x2800, 0x2900))
_TUI_DECORATION_CHARS = (
    # Box-drawing set.
    "│┃┌┐└┘├┤┬┴┼┏┓┗┛┣┫┳┻╋╭╮╰╯╔╗╚╝╠╣╦╩╬═║─━"
    # Block elements (full U+2580..U+259F range, incl. half blocks ▄▀▐▌).
    "▀▁▂▃▄▅▆▇█▉▊▋▌▍▎▏▐░▒▓▔▕▖▗▘▙▚▛▜▝▞▟"
    # Progress-bar blocks and geometric shapes (triangles, arrows, circles).
    "▰▱■□▪◼◻△▲▽▼◢◣◤◥▶◀◁▷◸◹●○◉◎"
    # Dots/rings used as decoration (·˚°⋅∙◦) and status glyphs (✓✗ etc.).
    "·˚°⋅∙◦✓✔✗✕✖×÷"
    # Direction arrows, prompt marks seen in status lines/input prompts,
    # and braille spinners.
    "↑↓←→↕↔↗↘↙↖›❯»" + _BRAILLE_CHARS
)
_TUI_DECORATION_TRANSLATION = str.maketrans("", "", _TUI_DECORATION_CHARS)

# Lines made only of spinner/fish/animation characters (e.g. the classic "><>"
# fish, the |/-\ spinner, braille spinners) are transient decoration and are
# dropped entirely. Pure pattern lines that the regex cannot enumerate (fish
# art like ``.'-.'`` / ``(___)`` / ``//|``, ``△▲``, ``·˚``) are handled by
# _is_pure_decoration_line below.
_TUI_ANIMATION_LINE_RE = re.compile(r"^[><oO0*·.\-|/\\_⠀-⣿◐◓◑◒]+$")

# Digits are stripped before comparing screen frames so that redrawn screens
# that only differ in numbers (top/htop counters, timers) still match.
_DIGIT_RE = re.compile(r"\d")

# Context lines kept around each changed region when diffing full-screen redraws.
_DIFF_CONTEXT_LINES = 2
# Safety valve: difflib is quadratic in pathological cases, skip it for huge
# captures (tmux capture-pane is bounded by -S -2000, so this rarely triggers).
_DIFF_MAX_TOTAL_LINES = 8000

# Full-screen redraw detection: a redraw is reported as empty recent_output
# when the visible pane region is rewritten (context-inclusive diff region
# covers at least this fraction) while the content stays largely identical
# (quick_ratio at least this) and the region is big enough to be a "screen".
_FULL_SCREEN_REDRAW_MIN_LINES = 10
_FULL_SCREEN_REDRAW_MIN_SIMILARITY = 0.5
_FULL_SCREEN_REDRAW_MIN_REGION = 0.6

# Repeated-screen collapse on the screen output: adjacent blocks of this many
# lines are folded into the last one when their digit-stripped line sets
# overlap by at least this fraction and the repeated run covers a meaningful
# part of the screen (so normal command output is never touched).
_SCREEN_REDRAW_MIN_PERIOD = 4
_SCREEN_REDRAW_MAX_PERIOD = 120
_SCREEN_REDRAW_OVERLAP = 0.6
# Runs must contain at least one near-perfect pair (a well-aligned period);
# misaligned block sizes only share recurring headers, not whole frames.
_SCREEN_REDRAW_STRONG_OVERLAP = 0.9
_SCREEN_REDRAW_SINGLE_PAIR_OVERLAP = 0.95
# Pairs at or above this overlap count as near-perfect frame repeats.
_SCREEN_REDRAW_PERFECT_OVERLAP = 0.99
_SCREEN_REDRAW_MIN_COVERAGE = 0.3
# Blocks whose lines share fewer than this many distinct digit-stripped forms
# (numbered lists, ``seq`` output) are not screens and never collapsed.
_SCREEN_REDRAW_MIN_DIVERSITY = 3


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
        recent = _capture_delta(
            self._read_capture,
            capture,
            rows=self.rows,
            tui_cleanup=self.tui_cleanup,
        )
        self._read_capture = capture
        if self.tui_cleanup:
            # Incremental dedup: consecutive repeated line blocks (e.g. a
            # banner frame appended several times) are reported only once.
            recent = _dedup_repeated_blocks(recent)

        recent_truncated = len(recent) > recent_limit
        if recent_truncated:
            recent = recent[-recent_limit:]
        screen = capture[-screen_limit:]
        if self.tui_cleanup:
            # Keep only the last state of full-screen redraws accumulated in
            # the scrollback, then merge consecutive identical lines.
            screen = _collapse_repeated_screens(screen, self.rows)
            screen = _compress_consecutive_lines(screen)
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


def _dedup_repeated_blocks(text: str) -> str:
    """Collapse consecutive repeated line blocks in recent output.

    Consecutive runs of the same block (single lines or multi-line blocks,
    e.g. a banner frame reprinted several times while scrolling) are kept
    exactly once — the last occurrence wins.

    Args:
        text: Output to deduplicate.

    Returns:
        Text with consecutive repeated blocks collapsed; unchanged otherwise.
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        deleted = False
        for block in range((len(lines) - i) // 2, 0, -1):
            if lines[i : i + block] == lines[i + block : i + 2 * block]:
                del lines[i + block : i + 2 * block]
                deleted = True
                break
        if not deleted:
            i += 1
    return "\n".join(lines)


def _compress_consecutive_lines(text: str) -> str:
    """Merge consecutive identical lines in a screen (line-level compression).

    Args:
        text: Screen text to compress.

    Returns:
        Text where runs of identical adjacent lines keep only one copy.
    """
    lines = []
    for line in text.splitlines():
        if lines and lines[-1] == line:
            continue
        lines.append(line)
    return "\n".join(lines)


def _line_fingerprint(line: str) -> str:
    """Digit-stripped copy of a line used to match redrawn screen frames.

    Numbers (counters, timers, progress percentages, top columns) change on
    every redraw; stripping them lets near-identical frames compare equal.

    Args:
        line: A screen line.

    Returns:
        The line with all digits removed.
    """
    return _DIGIT_RE.sub("", line)


def _block_overlap(first: list[str], second: list[str]) -> float:
    """Fraction of shared lines (by fingerprint) between two screen blocks.

    Set-based, so rows that only reorder between frames (top sorts by %CPU)
    and rows that repeat within a frame (zombie processes) count as shared
    once; overlapping blocks yield 1.0, disjoint blocks 0.0.

    Args:
        first: Fingerprints of the first block.
        second: Fingerprints of the second block.

    Returns:
        Overlap in [0.0, 1.0].
    """
    first_set = set(first)
    second_set = set(second)
    if not first_set or not second_set:
        return 0.0
    return len(first_set & second_set) / max(len(first_set), len(second_set))


def _collapse_repeated_screens(text: str, rows: int) -> str:
    """Fold repeated full-screen redraws in scrollback into the last state.

    TUIs that redraw by scrolling (batch ``top -b``, banners reprinted into
    history) leave many near-identical copies of the same screen in the
    capture. Consecutive blocks whose digit-stripped line sets overlap heavily
    are treated as one redrawn screen: all copies but the last are dropped.

    Args:
        text: Screen text (already cleaned) that may contain repeated frames.
        rows: Visible pane height, used to size the search range.

    Returns:
        Screen with repeated frames collapsed; unchanged when no repetition.
    """
    lines = text.splitlines()
    if len(lines) < 6:
        return text
    for _ in range(10):
        n = len(lines)
        if n < 6:
            break
        fps = [_line_fingerprint(line) for line in lines]
        best: tuple[int, float, int, int, int] | None = None
        # Candidate ordering, lexicographic: more near-perfect pairs (a
        # well-aligned period has frames that repeat almost verbatim, while
        # misaligned block sizes never do), then higher max overlap, then
        # more lines removed, then smaller period.
        max_period = min(n // 2, _SCREEN_REDRAW_MAX_PERIOD)
        for period in range(_SCREEN_REDRAW_MIN_PERIOD, max_period + 1):
            overlaps: list[float] = []
            for block in range(1, (n + period - 1) // period):
                prev = fps[block * period - period : block * period]
                cur = fps[block * period : (block + 1) * period]
                overlaps.append(_block_overlap(prev, cur))
            # Split the pair overlaps into runs of consecutive matches.
            run_start: int | None = None
            run_count = 0
            for idx, overlap in enumerate(overlaps):
                if overlap >= _SCREEN_REDRAW_OVERLAP:
                    if run_start is None:
                        run_start = idx
                    run_count += 1
                else:
                    candidate = _screen_run_candidate(
                        fps, n, period, run_start, run_count, overlaps
                    )
                    if candidate is not None and (best is None or candidate > best):
                        best = candidate
                    run_start = None
                    run_count = 0
            candidate = _screen_run_candidate(
                fps, n, period, run_start, run_count, overlaps
            )
            if candidate is not None and (best is None or candidate > best):
                best = candidate
        if best is None:
            break
        _, _, removed, neg_period, start = best
        period = -neg_period
        drop_start = start * period
        keep_start = drop_start + removed
        del lines[drop_start:keep_start]
    return "\n".join(lines)


def _screen_run_candidate(
    fps: list[str],
    n: int,
    period: int,
    run_start: int | None,
    run_count: int,
    overlaps: list[float],
) -> tuple[int, float, int, int, int] | None:
    """Build a collapse candidate from one run of similar blocks.

    A run is only a candidate when the blocks look like redrawn screens:
    a single pair must be near-perfect, longer runs must contain at least one
    near-perfect pair (a well-aligned period — misaligned block sizes only
    share recurring headers, not whole frames), the first block must hold
    diverse rows (numbered lists/``seq`` output never qualify) and the run
    must cover a meaningful part of the screen.

    Args:
        fps: Precomputed line fingerprints of the current screen.
        n: Number of lines.
        period: Candidate frame period (block size in lines).
        run_start: Index of the first pair of the run in ``overlaps``.
        run_count: Number of consecutive matching pairs in the run.
        overlaps: Pair overlaps for this period.

    Returns:
        Candidate tuple ``(perfect_pairs, max_overlap, removed, -period,
        start)`` (ordered so plain tuple comparison picks the best one) or
        None.
    """
    if run_start is None or run_count < 1:
        return None
    blocks = run_count + 1
    pair_overlaps = overlaps[run_start : run_start + run_count]
    if blocks == 2:
        if pair_overlaps[0] < _SCREEN_REDRAW_SINGLE_PAIR_OVERLAP:
            return None
    elif max(pair_overlaps) < _SCREEN_REDRAW_STRONG_OVERLAP:
        return None
    first_block = fps[run_start * period : (run_start + 1) * period]
    if len(set(first_block)) < _SCREEN_REDRAW_MIN_DIVERSITY:
        return None
    if blocks * period / n < _SCREEN_REDRAW_MIN_COVERAGE:
        return None
    removed = blocks * period - period
    perfect = sum(1 for ov in pair_overlaps if ov >= _SCREEN_REDRAW_PERFECT_OVERLAP)
    return (perfect, max(pair_overlaps), removed, -period, run_start * period)


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


def _is_pure_decoration_line(line: str) -> bool:
    """Whether a line carries no letters or digits.

    Pattern/decoration lines (fish art ``><>``/``.'-.'``/``(___)``/``//|``,
    ``△▲``, ``·˚``, pure punctuation) contain no letters or digits, so they
    are safe to drop; text lines (paths, commands, labels) always contain at
    least one alphanumeric character and are preserved.

    Args:
        line: The cleaned (decoration-stripped) line to inspect.

    Returns:
        True when the line is pure decoration.
    """
    return not any(ch.isalnum() for ch in line)


def _cleanup_tui_output(text: str) -> str:
    """Strip TUI decoration noise from a captured screen.

    Removes box-drawing/block/shade/geometric characters, drops purely
    decorative lines (spinners, fish art, triangle/dot patterns), collapses
    runs of blank lines and trims trailing whitespace, so LLM-facing output
    stays compact.

    Args:
        text: Raw tmux capture output.

    Returns:
        Cleaned text; blank-only input collapses to an empty string.
    """
    lines = []
    for raw in text.splitlines():
        raw = raw.rstrip()
        if not raw:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        # Spinner/animation rows (braille, |/-\\, fish) are dropped entirely
        # without leaving a blank separator behind.
        if _TUI_ANIMATION_LINE_RE.match(raw.strip()):
            continue
        line = raw.translate(_TUI_DECORATION_TRANSLATION).rstrip()
        if not line:
            # Border/filler rows (box-drawing, blocks) become blank separators.
            if lines and lines[-1] != "":
                lines.append("")
            continue
        # Pure pattern rows (fish art ``.'-.'``/``(___)``/``//|``, ``△▲``,
        # ``·˚``, ``><>``) carry no letters or digits and are dropped.
        if _is_pure_decoration_line(line):
            continue
        lines.append(line)
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _capture_delta(
    previous: str, current: str, rows: int | None = None, tui_cleanup: bool = True
) -> str:
    """Extract only the genuinely new/changed part of current vs previous.

    Handles four patterns:
    - appended output: current starts with previous, the appended suffix is new;
    - scrolled output: the previous tail matches the current head, only the
      scrolled-in lines are new;
    - full-screen redraws (top/htop/banner animations, ``rows`` given): the
      visible screen region is rewritten with largely identical content, so
      only the last state matters — it already lives in ``screen``, hence an
      empty delta is returned instead of repeating the whole screen;
    - other in-place changes: a line diff restricted to changed regions.

    Args:
        previous: The (cleaned) capture returned by the last snapshot.
        current: The (cleaned) capture from the current snapshot.
        rows: Visible pane height used for full-screen-redraw detection; when
            None the redraw detection is skipped (raw/legacy behavior).
        tui_cleanup: When False, redraw suppression is disabled and only the
            legacy append/scroll/diff behavior applies.

    Returns:
        The portion of current that is new or changed; empty if unchanged.
    """
    if not previous:
        return current
    if current.startswith(previous):
        # The boundary newline terminates the previous content, not the new
        # content; captures never end with "\n", so drop it.
        suffix = current[len(previous) :].lstrip("\n")
        # A frame that is byte-identical to the already-reported tail is a
        # redraw of the same screen (e.g. a banner reprinted into the
        # scrollback) — keep only the last state.
        if tui_cleanup and _is_repeated_frame(previous, suffix):
            return ""
        return suffix

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

    # Full-screen redraw: the visible screen was rewritten with largely
    # identical content — keep only the last state (delivered via ``screen``).
    if (
        tui_cleanup
        and rows is not None
        and _is_full_screen_redraw(prev_lines, current_lines, rows)
    ):
        return ""

    # Other in-place changes: report only the changed regions (with context).
    return "\n".join(_changed_regions(prev_lines, current_lines))


def _is_repeated_frame(previous: str, suffix: str) -> bool:
    """Whether the appended suffix is an exact repeat of the already-read tail.

    Args:
        previous: The previously returned capture.
        suffix: Newly appended content.

    Returns:
        True when the suffix is a multi-line exact copy of previous's tail.
    """
    suffix_lines = suffix.splitlines()
    if len(suffix_lines) < 3:
        return False
    prev_lines = previous.splitlines()
    return suffix_lines == prev_lines[-len(suffix_lines) :]


def _is_full_screen_redraw(
    prev_lines: list[str], current_lines: list[str], rows: int
) -> bool:
    """Detect a full-screen redraw with largely identical content.

    Periodic TUIs (top, htop, animated banners) rewrite the whole visible
    screen on every tick. The rewritten region (with context) covers most of
    the visible screen while the content stays largely identical, so the
    redraw is not new information — the caller keeps only the last state.

    Args:
        prev_lines: Lines of the previous capture.
        current_lines: Lines of the current capture.
        rows: Visible pane height; the comparison focuses on this tail region.

    Returns:
        True when the visible region looks like a periodic full-screen redraw.
    """
    if len(current_lines) < _FULL_SCREEN_REDRAW_MIN_LINES:
        return False
    k = min(max(1, int(rows)), len(prev_lines), len(current_lines))
    if k < _FULL_SCREEN_REDRAW_MIN_LINES:
        return False
    prev_tail = prev_lines[-k:]
    current_tail = current_lines[-k:]
    # Similarity is measured on characters, not whole lines: top refreshes
    # change numbers on almost every row, so whole-line equality would report
    # near-zero similarity even though the screen is largely the same. The
    # region fraction below still uses the line diff (what gets reported).
    char_matcher = difflib.SequenceMatcher(
        None, "\n".join(prev_tail), "\n".join(current_tail), autojunk=False
    )
    if char_matcher.quick_ratio() < _FULL_SCREEN_REDRAW_MIN_SIMILARITY:
        return False
    matcher = difflib.SequenceMatcher(None, prev_tail, current_tail, autojunk=False)
    region = _changed_regions(prev_tail, current_tail, matcher)
    return len(region) >= _FULL_SCREEN_REDRAW_MIN_REGION * k


def _changed_regions(
    prev_lines: list[str],
    current_lines: list[str],
    matcher: difflib.SequenceMatcher | None = None,
) -> list[str]:
    """Return current lines restricted to regions that differ from previous.

    Args:
        prev_lines: Lines of the previous capture.
        current_lines: Lines of the current capture.
        matcher: Optional pre-built SequenceMatcher over the two line lists;
            created when omitted.

    Returns:
        Lines of current that sit inside changed regions, each region padded
        with a small context of unchanged lines; empty when nothing changed.
    """
    if not current_lines:
        return []
    if len(prev_lines) + len(current_lines) > _DIFF_MAX_TOTAL_LINES:
        return current_lines
    if matcher is None:
        matcher = difflib.SequenceMatcher(
            None, prev_lines, current_lines, autojunk=False
        )
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
