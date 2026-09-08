import base64
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "sync-openai-secret.py"
KEY = "sk-test-" + "x" * 40


class OpenAISecretTests(unittest.TestCase):
    def run_sync(self, root, encoded):
        env = {**os.environ, "OPENAI_API_KEY_B64": encoded}
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_replaces_only_key_and_protects_file_permissions_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "DB_NAME=asyl\nexport OPENAI_API_KEY=old\n# comment\nOPENAI_API_KEY=duplicate\n"
            )
            result = self.run_sync(directory, base64.b64encode(KEY.encode()).decode())
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout + result.stderr, "")
            self.assertEqual(
                path.read_text(),
                "DB_NAME=asyl\n# comment\nOPENAI_API_KEY=" + KEY + "\n",
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_empty_secret_preserves_existing_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("OPENAI_API_KEY=existing\n")
            self.assertEqual(self.run_sync(directory, "").returncode, 0)
            self.assertEqual(path.read_text(), "OPENAI_API_KEY=existing\n")

    def test_invalid_secret_preserves_file_and_never_echoes_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("DB_NAME=asyl\n")
            for bad in [
                "invalid-base64",
                base64.b64encode(b"private\nINJECT=true").decode(),
            ]:
                result = self.run_sync(directory, bad)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(bad, result.stdout + result.stderr)
                self.assertNotIn("private", result.stdout + result.stderr)
                self.assertEqual(path.read_text(), "DB_NAME=asyl\n")

    def test_symlink_cannot_redirect_secret_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "other"
            target.write_text("unchanged")
            (Path(directory) / ".env").symlink_to(target)
            result = self.run_sync(directory, base64.b64encode(KEY.encode()).decode())
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(target.read_text(), "unchanged")
