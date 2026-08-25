from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


def load_tmux_module():
    """Load terminal/backends/tmux_backend.py with its relative imports intact.

    tmux_backend imports from ..keys and ..screen_buffer, so a synthetic
    ``terminal`` package (pointing at the real directory, without executing the
    backend __init__.py that pulls in winpty etc.) is registered first.
    """
    root = Path(__file__).resolve().parents[1] / "terminal"

    terminal_pkg = types.ModuleType("terminal")
    terminal_pkg.__path__ = [str(root)]
    sys.modules["terminal"] = terminal_pkg

    backends_pkg = types.ModuleType("terminal.backends")
    backends_pkg.__path__ = [str(root / "backends")]
    sys.modules["terminal.backends"] = backends_pkg

    for name, path in (
        ("terminal.keys", root / "keys.py"),
        ("terminal.screen_buffer", root / "screen_buffer.py"),
    ):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)

    spec = importlib.util.spec_from_file_location(
        "terminal.backends.tmux_backend", root / "backends" / "tmux_backend.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_policy_module():
    policy_path = Path(__file__).resolve().parents[1] / "terminal" / "policy.py"
    spec = importlib.util.spec_from_file_location(
        "terminal_policy_under_test", policy_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tmux = load_tmux_module()
policy = load_policy_module()

RAW_TUI_SCREEN = (
    "┌─────────────────────────────┐\n"
    "│  codewhale  v0.1            │\n"
    "├─────────────────────────────┤\n"
    "│  ████████░░  42%            │\n"
    "│  ><>                        │\n"
    "│                             │\n"
    "│                             │\n"
    "│                             │\n"
    "│  done                       │\n"
    "└─────────────────────────────┘\n"
)
CLEANED_TUI_SCREEN = "  codewhale  v0.1\n\n    42%\n\n  done"


class TuiCleanupTest(unittest.TestCase):
    def test_removes_border_and_block_characters(self):
        raw = "│ ┌ ┐ └ ┘ ├ ┤ ┬ ┴ ┼ ▏▎▍▌▋▊▉ ░▒▓ █■□\nkeep | pipe and text\n"

        result = tmux._cleanup_tui_output(raw)

        self.assertEqual(result, "keep | pipe and text")

    def test_removes_animation_lines(self):
        raw = "loading...\n><>\n| \n- \n\\\n/\n  ><>  \n⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏\n◐\nresult: ok\n"

        result = tmux._cleanup_tui_output(raw)

        self.assertEqual(result, "loading...\nresult: ok")

    def test_collapses_blank_runs_and_trims_trailing_whitespace(self):
        raw = "a   \n\n\n\n   \nb\t  \n\n"

        result = tmux._cleanup_tui_output(raw)

        self.assertEqual(result, "a\n\nb")

    def test_handles_crlf_line_endings(self):
        result = tmux._cleanup_tui_output("a\r\n\r\n\r\nb\r\n")

        self.assertEqual(result, "a\n\nb")

    def test_blank_only_input_returns_empty(self):
        self.assertEqual(tmux._cleanup_tui_output("\n\n\n"), "")
        self.assertEqual(tmux._cleanup_tui_output("   \n  \n"), "")

    def test_preserves_indentation_and_ascii_pipe(self):
        result = tmux._cleanup_tui_output("  a | b\n    indented code\n")

        self.assertEqual(result, "  a | b\n    indented code")


class CaptureDeltaTest(unittest.TestCase):
    def test_identical_screens_return_empty(self):
        self.assertEqual(tmux._capture_delta("a\nb", "a\nb"), "")

    def test_first_read_returns_full_screen(self):
        self.assertEqual(tmux._capture_delta("", "a\nb"), "a\nb")

    def test_appended_output_returns_suffix(self):
        self.assertEqual(tmux._capture_delta("a\nb", "a\nb\nc\nd"), "c\nd")

    def test_scrolled_screen_returns_only_new_lines(self):
        previous = "\n".join(f"row {i}" for i in range(24))
        current = "\n".join(f"row {i}" for i in range(1, 25))

        self.assertEqual(tmux._capture_delta(previous, current), "row 24")

    def test_full_redraw_returns_only_changed_region(self):
        previous = "\n".join(f"row {i}" for i in range(24))
        current_lines = [f"row {i}" for i in range(24)]
        current_lines[10] = "row 10 CHANGED"
        current_lines[11] = "row 11 CHANGED"
        current = "\n".join(current_lines)

        result = tmux._capture_delta(previous, current)

        self.assertEqual(
            result,
            "\n".join(
                [
                    "row 8",
                    "row 9",
                    "row 10 CHANGED",
                    "row 11 CHANGED",
                    "row 12",
                    "row 13",
                ]
            ),
        )

    def test_adjacent_changed_regions_merge_context(self):
        previous = "\n".join(f"row {i}" for i in range(20))
        current_lines = [f"row {i}" for i in range(20)]
        current_lines[5] = "row 5 CHANGED"
        current_lines[6] = "row 6 CHANGED"
        current_lines[10] = "row 10 CHANGED"
        current = "\n".join(current_lines)

        result = tmux._capture_delta(previous, current)

        self.assertEqual(
            result,
            "\n".join(
                [
                    "row 3",
                    "row 4",
                    "row 5 CHANGED",
                    "row 6 CHANGED",
                    "row 7",
                    "row 8",
                    "row 9",
                    "row 10 CHANGED",
                    "row 11",
                    "row 12",
                ]
            ),
        )

    def test_cleared_screen_returns_empty(self):
        self.assertEqual(tmux._capture_delta("a\nb\nc", ""), "")


class TuiCleanupConfigTest(unittest.TestCase):
    def test_defaults_to_enabled(self):
        self.assertTrue(policy.TerminalPolicyConfig().tui_cleanup)
        self.assertTrue(
            policy.TerminalPolicyConfig.from_config({"terminal": {}}).tui_cleanup
        )

    def test_can_be_disabled(self):
        self.assertFalse(
            policy.TerminalPolicyConfig.from_config(
                {"terminal": {"tui_cleanup": False}}
            ).tui_cleanup
        )
        self.assertFalse(
            policy.TerminalPolicyConfig.from_config(
                {"terminal": {"tui_cleanup": "false"}}
            ).tui_cleanup
        )

    def test_can_be_enabled_explicitly(self):
        self.assertTrue(
            policy.TerminalPolicyConfig.from_config(
                {"terminal": {"tui_cleanup": True}}
            ).tui_cleanup
        )
        self.assertTrue(
            policy.TerminalPolicyConfig.from_config(
                {"terminal": {"tui_cleanup": "on"}}
            ).tui_cleanup
        )


def _stub_tmux_session(tui_cleanup: bool, captures=None, rows: int = 24):
    """Build a TmuxSession without running tmux, with a fixed capture source.

    Args:
        tui_cleanup: Whether cleanup is enabled on the stub session.
        captures: Optional iterable of captures served one per snapshot call;
            defaults to the RAW_TUI_SCREEN fixture.
        rows: Visible pane height used by the redraw detection.

    Returns:
        A stub TmuxSession.
    """
    session = object.__new__(tmux.TmuxSession)
    session._last_capture = ""
    session._read_capture = ""
    session._seq = 5
    session.tui_cleanup = tui_cleanup
    session.rows = rows
    if captures is None:
        session._refresh_capture = lambda: RAW_TUI_SCREEN
    else:
        iterator = iter(captures)
        session._refresh_capture = lambda: next(iterator)
    return session


class TmuxSnapshotCleanupTest(unittest.TestCase):
    def test_snapshot_cleans_screen_when_enabled(self):
        session = _stub_tmux_session(tui_cleanup=True)

        snapshot = session.snapshot(8000, 4000)

        self.assertEqual(snapshot.screen, CLEANED_TUI_SCREEN)
        self.assertEqual(snapshot.recent_output, CLEANED_TUI_SCREEN)

    def test_snapshot_keeps_raw_screen_when_disabled(self):
        session = _stub_tmux_session(tui_cleanup=False)

        snapshot = session.snapshot(8000, 4000)

        self.assertEqual(snapshot.screen, RAW_TUI_SCREEN)
        self.assertEqual(snapshot.recent_output, RAW_TUI_SCREEN)

    def test_unchanged_screen_does_not_repeat_in_recent_output(self):
        session = _stub_tmux_session(tui_cleanup=True)
        session.snapshot(8000, 4000)

        second = session.snapshot(8000, 4000)

        self.assertEqual(second.recent_output, "")
        self.assertEqual(second.screen, CLEANED_TUI_SCREEN)


class ExtendedCharsetTest(unittest.TestCase):
    def test_removes_half_block_and_triangle_lines(self):
        raw = "▄▀▐▌▄▄▄\n△▲△▲\n·˚·˚·\nkeep text\n"

        result = tmux._cleanup_tui_output(raw)

        self.assertEqual(result, "keep text")

    def test_removes_fish_and_ascii_art_lines(self):
        raw = "><>\n.'-.'\n(___)  \n//|\\\n.-~-.\n\\___/\nloading...\n"

        result = tmux._cleanup_tui_output(raw)

        self.assertEqual(result, "loading...")

    def test_strips_decoration_chars_from_text_lines(self):
        raw = (
            "▄▄▄  CODING WHALE v0.9  ▄▄▄\n"
            "▲ status: ready · 100%\n"
            "│ ✓ ▶ run done · cd /tmp && ls -la\n"
        )

        result = tmux._cleanup_tui_output(raw)

        self.assertEqual(
            result,
            "  CODING WHALE v0.9\n status: ready  100%\n   run done  cd /tmp && ls -la",
        )

    def test_keeps_paths_commands_and_prompts(self):
        raw = (
            "root@host:/AstrBot/data# cd /tmp && ls -la\n"
            "/home/user/file.txt\n"
            "cd ..\n"
            "./run.sh --flag=1\n"
        )

        result = tmux._cleanup_tui_output(raw)

        self.assertEqual(result, raw.rstrip("\n"))

    def test_strips_spinner_glued_to_text(self):
        result = tmux._cleanup_tui_output("▌⢀ using tool · ×1\n")

        self.assertEqual(result, " using tool  1")


class DedupAndCompressTest(unittest.TestCase):
    def test_dedup_repeated_single_lines(self):
        result = tmux._dedup_repeated_blocks("a\na\na\nb\nc\nd\nc\nd")

        self.assertEqual(result, "a\nb\nc\nd")

    def test_dedup_repeated_multi_line_blocks(self):
        result = tmux._dedup_repeated_blocks("x\ny\nx\ny\nx\ny")

        self.assertEqual(result, "x\ny")

    def test_dedup_keeps_non_consecutive_repeats(self):
        self.assertEqual(tmux._dedup_repeated_blocks("a\nb\nc\na\nb"), "a\nb\nc\na\nb")

    def test_dedup_repeated_frames(self):
        frame = "frame line 1\nframe line 2\nframe line 3"

        result = tmux._dedup_repeated_blocks("\n".join([frame, frame, frame]))

        self.assertEqual(result, frame)

    def test_compress_consecutive_lines(self):
        result = tmux._compress_consecutive_lines("a\na\nb\nb\nb\nc")

        self.assertEqual(result, "a\nb\nc")

    def test_compress_keeps_distinct_lines(self):
        self.assertEqual(tmux._compress_consecutive_lines("a\nb\na"), "a\nb\na")


TOP_FRAME_LINES = [
    "top - 12:00:00 up 1 day, 17:00,  0 users,  load average: 0.30, 0.22, 0.18",
    "Tasks:  23 total,   1 running,   7 sleeping,   0 stopped,  15 zombie",
    "%Cpu(s):  4.5 us,  1.6 sy,  0.0 ni, 93.5 id,  0.3 wa,  0.0 hi,  0.2 si,  0.0 st",
    "MiB Mem :   7863.3 total,    380.3 free,   2840.8 used,   4953.4 buff/cache",
    "MiB Swap:      0.0 total,      0.0 free,      0.0 used.   5022.5 avail Mem",
    "",
    "    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND",
    " 105681 root      20   0 1276468 124076  47364 S   6.7   1.5   0:11.09 codewhale",
    "      1 root      20   0 4396124 993.8m 122980 S   2.3  12.6  18:08.55 python",
    " 105500 root      20   0   14492   4832   3472 S   1.3   0.1   0:01.99 tmux: server",
    "     19 root      20   0       0      0      0 Z   0.0   0.0   0:00.00 tmux: server",
    "     58 root      20   0       0      0      0 Z   0.0   0.0   0:00.01 chrome-headless",
    "     59 root      20   0       0      0      0 Z   0.0   0.0   0:00.01 chrome-headless",
    "  20574 root      20   0       0      0      0 Z   0.0   0.0   0:00.05 MainThread",
    "  20575 root      20   0       0      0      0 Z   0.0   0.0   0:00.00 tail",
    "  20587 root      20   0       0      0      0 Z   0.0   0.0   0:01.03 codewhale",
    "  20782 root      20   0       0      0      0 Z   0.0   0.0   0:08.81 tmux: server",
    "  26358 root      20   0       0      0      0 Z   0.0   0.0   2:57.54 codewhale",
    "  26711 root      20   0       0      0      0 Z   0.0   0.0   0:00.05 MainThread",
    "  26727 root      20   0       0      0      0 Z   0.0   0.0   2:44.63 codewhale",
    " 105501 root      20   0    4752   3800   3172 S   0.0   0.0   0:00.00 bash",
    " 105523 root      20   0    4752   3916   3280 S   0.0   0.0   0:00.00 bash",
    " 105547 root      20   0    7872   5324   3180 R   0.0   0.1   0:00.28 top",
    " 105639 root      20   0    4752   3920   3284 S   0.0   0.0   0:00.00 bash",
]


def _top_frame(load: str = "0.30, 0.22, 0.18", cpu: str = "4.5", tick: int = 0) -> str:
    """Build a realistic top-like screen frame of 24 lines.

    Args:
        load: Load average text shown in the header.
        cpu: CPU percentage text shown in the header.
        tick: Optional frame counter embedded in every row so different frames
            differ only in numbers, like real top refreshes.

    Returns:
        The frame as one newline-joined string.
    """
    lines = list(TOP_FRAME_LINES)
    lines[0] = (
        lines[0].replace("0.30, 0.22, 0.18", load).replace("12:00:00", f"12:00:0{tick}")
    )
    lines[2] = lines[2].replace("4.5", cpu)
    if tick:
        lines = [
            f"{line}  {tick}" if line and not line.startswith("    PID") else line
            for line in lines
        ]
    return "\n".join(lines)


class ScreenRedrawCollapseTest(unittest.TestCase):
    def test_three_near_identical_frames_keep_last(self):
        screen = "\n".join([_top_frame(tick=1), _top_frame(tick=2), _top_frame(tick=3)])

        result = tmux._collapse_repeated_screens(screen, rows=24)

        self.assertEqual(result, _top_frame(tick=3))

    def test_three_identical_frames_keep_last(self):
        frame = _top_frame(tick=1)

        result = tmux._collapse_repeated_screens(
            "\n".join([frame, frame, frame]), rows=24
        )

        self.assertEqual(result, frame)

    def test_distinct_screens_unchanged(self):
        screen = "\n".join(f"unique line {i}" for i in range(40))

        self.assertEqual(tmux._collapse_repeated_screens(screen, rows=24), screen)

    def test_small_repeated_block_unchanged(self):
        block = "line 1\nline 2\nline 3\nline 4\nline 5"
        screen = "\n".join([block, block] + [f"other {i}" for i in range(50)])

        self.assertEqual(tmux._collapse_repeated_screens(screen, rows=24), screen)

    def test_numbered_lists_unchanged(self):
        screen = "\n".join(f"item {i}" for i in range(50))

        self.assertEqual(tmux._collapse_repeated_screens(screen, rows=24), screen)

    def test_two_different_sessions_unchanged(self):
        other = (
            _top_frame(tick=9)
            .replace("codewhale", "nginx", 1)
            .replace("python", "java")
            .replace("bash", "zsh")
            .replace("tail", "grep")
            .replace("chrome-headless", "firefox")
            .replace("tmux: server", "postgres")
        )
        screen = "\n".join(
            ["prompt1", "top", _top_frame(tick=1), "prompt2", "top", other]
        )

        self.assertEqual(tmux._collapse_repeated_screens(screen, rows=24), screen)


class FullScreenRedrawDeltaTest(unittest.TestCase):
    def test_top_like_refresh_returns_empty(self):
        previous = _top_frame(load="0.30, 0.22, 0.18", cpu="4.5", tick=1)
        current = _top_frame(load="0.31, 0.23, 0.19", cpu="4.6", tick=2)

        result = tmux._capture_delta(previous, current, rows=24)

        self.assertEqual(result, "")

    def test_single_line_update_is_reported(self):
        previous = _top_frame(tick=1)
        current_lines = _top_frame(tick=1).splitlines()
        current_lines[7] = (
            " 105681 root      20   0 1276468 124076  47364 S  99.9   1.5   0:11.09 codewhale"
        )
        current = "\n".join(current_lines)

        result = tmux._capture_delta(previous, current, rows=24)

        self.assertIn("99.9", result)
        self.assertNotEqual(result, current)

    def test_totally_new_screen_is_returned(self):
        previous = _top_frame(tick=1)
        current = "\n".join(f"new content line {i}" for i in range(24))

        result = tmux._capture_delta(previous, current, rows=24)

        self.assertEqual(result, current)

    def test_identical_frame_appended_returns_empty(self):
        frame = "frame line 1\nframe line 2\nframe line 3"
        previous = "boot log\nmore log\n" + frame

        result = tmux._capture_delta(previous, previous + "\n" + frame, rows=24)

        self.assertEqual(result, "")

    def test_redraw_suppression_disabled_without_cleanup(self):
        previous = _top_frame(load="0.30, 0.22, 0.18", cpu="4.5", tick=1)
        current = _top_frame(load="0.31, 0.23, 0.19", cpu="4.6", tick=2)

        result = tmux._capture_delta(previous, current, rows=24, tui_cleanup=False)

        self.assertNotEqual(result, "")


class SnapshotRedrawTest(unittest.TestCase):
    def test_full_screen_redraw_keeps_last_state(self):
        session = _stub_tmux_session(
            tui_cleanup=True,
            captures=[
                _top_frame(tick=1),
                _top_frame(load="0.31, 0.23, 0.19", cpu="4.6", tick=2),
            ],
        )
        first = session.snapshot(8000, 4000)
        second = session.snapshot(8000, 4000)

        self.assertEqual(first.recent_output, _top_frame(tick=1))
        self.assertEqual(second.recent_output, "")
        self.assertEqual(
            second.screen, _top_frame(load="0.31, 0.23, 0.19", cpu="4.6", tick=2)
        )

    def test_repeated_frames_in_scrollback_keep_last_state(self):
        capture = "\n".join(
            [_top_frame(tick=1), _top_frame(tick=2), _top_frame(tick=3)]
        )
        session = _stub_tmux_session(tui_cleanup=True, captures=[capture])

        snapshot = session.snapshot(8000, 20000)

        self.assertEqual(snapshot.screen, _top_frame(tick=3))
        self.assertEqual(snapshot.recent_output, capture)

    def test_compressed_screen_merges_repeated_lines(self):
        capture = "a\na\na\nb\nb\nc"
        session = _stub_tmux_session(tui_cleanup=True, captures=[capture])

        snapshot = session.snapshot(8000, 4000)

        self.assertEqual(snapshot.screen, "a\nb\nc")


if __name__ == "__main__":
    unittest.main()
