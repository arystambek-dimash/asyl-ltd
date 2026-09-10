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
    def run_sync(self, root, encoded, *, model="", detail=""):
        env = {
            **os.environ,
            "OPENAI_API_KEY_B64": encoded,
            "SHIPPING_WAGON_AI_MODEL_B64": model,
            "SHIPPING_WAGON_AI_DETAIL_B64": detail,
        }
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

    def test_shipping_model_override_preserves_key_and_weighbridge_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            original = "OPENAI_API_KEY=existing\nWEIGHING_AI_MODEL=gpt-5-mini\n"
            path.write_text(original + "export SHIPPING_WAGON_AI_MODEL=old\nSHIPPING_WAGON_AI_MODEL=duplicate\n")
            result = self.run_sync(
                directory, "", model=base64.b64encode(b"gpt-5.4-mini").decode(),
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout + result.stderr, "")
            self.assertEqual(path.read_text(), original + "SHIPPING_WAGON_AI_MODEL=gpt-5.4-mini\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.run_sync(directory, "").returncode, 0)
            self.assertEqual(path.read_text(), original + "SHIPPING_WAGON_AI_MODEL=gpt-5.4-mini\n")

    def test_invalid_model_prevents_partial_key_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            original = "OPENAI_API_KEY=existing\nWEIGHING_AI_MODEL=gpt-5-mini\n"
            path.write_text(original)
            result = self.run_sync(
                directory, base64.b64encode(KEY.encode()).decode(),
                model=base64.b64encode(b"bad\nWEIGHING_AI_MODEL=other").decode(),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_text(), original)
            self.assertNotIn(KEY, result.stdout + result.stderr)
            self.assertNotIn("WEIGHING_AI_MODEL", result.stdout + result.stderr)

    def test_shipping_detail_can_change_without_updating_model_or_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            original = "OPENAI_API_KEY=existing\nWEIGHING_AI_MODEL=gpt-5-mini\nSHIPPING_WAGON_AI_MODEL=gpt-5.4-mini\n"
            path.write_text(original)
            for detail in ("original", "high"):
                result = self.run_sync(directory, "", detail=base64.b64encode(detail.encode()).decode())
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout + result.stderr, "")
                self.assertEqual(path.read_text(), original + "SHIPPING_WAGON_AI_DETAIL=" + detail + "\n")

    def test_invalid_detail_prevents_partial_configuration_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            original = "OPENAI_API_KEY=existing\nWEIGHING_AI_MODEL=gpt-5-mini\n"
            path.write_text(original)
            for detail in ("auto", "low", "bad\nOPENAI_API_KEY=injected"):
                result = self.run_sync(
                    directory, base64.b64encode(KEY.encode()).decode(),
                    model=base64.b64encode(b"gpt-5.4-mini").decode(),
                    detail=base64.b64encode(detail.encode()).decode(),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(path.read_text(), original)
                self.assertNotIn(KEY, result.stdout + result.stderr)
                self.assertNotIn(detail, result.stdout + result.stderr)

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
