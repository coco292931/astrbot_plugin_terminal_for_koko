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


def _stub_tmux_session(tui_cleanup: bool):
    """Build a TmuxSession without running tmux, with a fixed capture source."""
    session = object.__new__(tmux.TmuxSession)
    session._last_capture = ""
    session._read_capture = ""
    session._seq = 5
    session.tui_cleanup = tui_cleanup
    session._refresh_capture = lambda: RAW_TUI_SCREEN
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


if __name__ == "__main__":
    unittest.main()
