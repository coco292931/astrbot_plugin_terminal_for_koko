from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


def load_sshpass_module():
    module_path = Path(__file__).resolve().parents[1] / "terminal" / "sshpass.py"
    spec = importlib.util.spec_from_file_location("terminal_sshpass_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sshpass = load_sshpass_module()


class SshpassParserTest(unittest.TestCase):
    def test_parse_password_argument(self):
        parsed = sshpass.parse_sshpass_command(
            "sshpass -p 'p@$$ word' ssh -tt root@example.com"
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.password, "p@$$ word")
        self.assertEqual(parsed.argv, ["ssh", "-tt", "root@example.com"])

    def test_parse_compact_password_argument(self):
        parsed = sshpass.parse_sshpass_command("sshpass -psecret ssh host")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.password, "secret")
        self.assertEqual(parsed.argv, ["ssh", "host"])

    def test_parse_env_password_argument(self):
        parsed = sshpass.parse_sshpass_command(
            "sshpass -eMY_PASS ssh host",
            env={"MY_PASS": "secret"},
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.password, "secret")

    def test_parse_file_password_argument(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            password_file = Path(temp_dir) / "password.txt"
            password_file.write_text("secret\nignored", encoding="utf-8")

            parsed = sshpass.parse_sshpass_command(
                "sshpass -f password.txt ssh host",
                cwd=temp_dir,
            )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.password, "secret")

    def test_parse_custom_prompt(self):
        parsed = sshpass.parse_sshpass_command("sshpass -P token: -p secret ssh host")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.prompt, "token:")

    def test_unsupported_fd_password_falls_back_to_raw_pipe(self):
        parsed = sshpass.parse_sshpass_command("sshpass -d 3 ssh host")

        self.assertIsNone(parsed)


if __name__ == "__main__":
    unittest.main()
