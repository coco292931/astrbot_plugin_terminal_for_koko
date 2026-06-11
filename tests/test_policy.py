from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


def load_policy_module():
    policy_path = Path(__file__).resolve().parents[1] / "terminal" / "policy.py"
    spec = importlib.util.spec_from_file_location("terminal_policy_under_test", policy_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


policy = load_policy_module()


class TerminalPolicyConfigTest(unittest.TestCase):
    def test_allow_all_ignores_allowed_commands(self):
        config = policy.TerminalPolicyConfig(
            command_permission_mode="allow_all",
            allowed_commands=["bash"],
        )

        ok, command, message = config.normalize_command("sshpass -p x ssh host")

        self.assertTrue(ok, message)
        self.assertEqual(command, "sshpass -p x ssh host")
        self.assertEqual(config.resolve_backend_mode(command), "pipe")

    def test_allow_all_from_nested_config_ignores_allowed_commands(self):
        config = policy.TerminalPolicyConfig.from_config(
            {
                "terminal": {
                    "command_permission_mode": "allow_all",
                    "allowed_commands": ["bash"],
                    "sshpass_pipe_fallback": True,
                }
            }
        )

        ok, _, message = config.normalize_command("python -V")

        self.assertTrue(ok, message)

    def test_sshpass_fallback_bypasses_allowed_commands(self):
        config = policy.TerminalPolicyConfig(
            command_permission_mode="blacklist",
            allowed_commands=["bash"],
            sshpass_pipe_fallback=True,
        )

        ok, command, message = config.normalize_command("sshpass -p x ssh host")

        self.assertTrue(ok, message)
        self.assertEqual(config.resolve_backend_mode(command), "pipe")

    def test_allowed_commands_still_blocks_when_fallback_disabled(self):
        config = policy.TerminalPolicyConfig(
            command_permission_mode="blacklist",
            allowed_commands=["bash"],
            sshpass_pipe_fallback=False,
        )

        ok, _, message = config.normalize_command("sshpass -p x ssh host")

        self.assertFalse(ok)
        self.assertIn("allowed_commands", message)

    def test_allowed_commands_still_blocks_other_commands(self):
        config = policy.TerminalPolicyConfig(
            command_permission_mode="blacklist",
            allowed_commands=["bash"],
        )

        ok, _, message = config.normalize_command("python -V")

        self.assertFalse(ok)
        self.assertIn("allowed_commands", message)


if __name__ == "__main__":
    unittest.main()
