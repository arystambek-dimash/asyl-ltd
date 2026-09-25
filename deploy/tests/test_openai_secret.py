import base64
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "sync-openai-secret.py"
KEY = "sk-test-" + "x" * 40


def _b64(text):
    return base64.b64encode(text.encode()).decode()


class OpenAISecretTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.env_path = self.root / ".env"

    def run_sync(self, key="", *, model="", detail="", key_b64=None):
        env = {
            **os.environ,
            "OPENAI_API_KEY_B64": _b64(key) if key_b64 is None else key_b64,
            "SHIPPING_WAGON_AI_MODEL_B64": _b64(model),
            "SHIPPING_WAGON_AI_DETAIL_B64": _b64(detail),
        }
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_replaces_only_key_and_protects_file_permissions_without_output(self):
        self.env_path.write_text(
            "DB_NAME=asyl\nexport OPENAI_API_KEY=old\n# comment\nOPENAI_API_KEY=duplicate\n"
        )
        result = self.run_sync(KEY)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout + result.stderr, "")
        self.assertEqual(
            self.env_path.read_text(),
            "DB_NAME=asyl\n# comment\nOPENAI_API_KEY=" + KEY + "\n",
        )
        self.assertEqual(self.env_path.stat().st_mode & 0o777, 0o600)

    def test_empty_secret_preserves_existing_configuration(self):
        self.env_path.write_text("OPENAI_API_KEY=existing\n")
        self.assertEqual(self.run_sync().returncode, 0)
        self.assertEqual(self.env_path.read_text(), "OPENAI_API_KEY=existing\n")

    def test_shipping_model_override_preserves_key_and_weighbridge_model(self):
        original = "OPENAI_API_KEY=existing\nWEIGHING_AI_MODEL=gpt-5-mini\n"
        self.env_path.write_text(original + "export SHIPPING_WAGON_AI_MODEL=old\nSHIPPING_WAGON_AI_MODEL=duplicate\n")
        result = self.run_sync(model="gpt-5.4-mini")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout + result.stderr, "")
        self.assertEqual(self.env_path.read_text(), original + "SHIPPING_WAGON_AI_MODEL=gpt-5.4-mini\n")
        self.assertEqual(self.env_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.run_sync().returncode, 0)
        self.assertEqual(self.env_path.read_text(), original + "SHIPPING_WAGON_AI_MODEL=gpt-5.4-mini\n")

    def test_invalid_model_prevents_partial_key_update(self):
        original = "OPENAI_API_KEY=existing\nWEIGHING_AI_MODEL=gpt-5-mini\n"
        self.env_path.write_text(original)
        result = self.run_sync(KEY, model="bad\nWEIGHING_AI_MODEL=other")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.env_path.read_text(), original)
        self.assertNotIn(KEY, result.stdout + result.stderr)
        self.assertNotIn("WEIGHING_AI_MODEL", result.stdout + result.stderr)

    def test_shipping_detail_can_change_without_updating_model_or_key(self):
        original = "OPENAI_API_KEY=existing\nWEIGHING_AI_MODEL=gpt-5-mini\nSHIPPING_WAGON_AI_MODEL=gpt-5.4-mini\n"
        self.env_path.write_text(original)
        for detail in ("original", "high"):
            result = self.run_sync(detail=detail)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout + result.stderr, "")
            self.assertEqual(self.env_path.read_text(), original + "SHIPPING_WAGON_AI_DETAIL=" + detail + "\n")

    def test_invalid_detail_prevents_partial_configuration_update(self):
        original = "OPENAI_API_KEY=existing\nWEIGHING_AI_MODEL=gpt-5-mini\n"
        self.env_path.write_text(original)
        for detail in ("auto", "low", "bad\nOPENAI_API_KEY=injected"):
            result = self.run_sync(KEY, model="gpt-5.4-mini", detail=detail)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.env_path.read_text(), original)
            self.assertNotIn(KEY, result.stdout + result.stderr)
            self.assertNotIn(detail, result.stdout + result.stderr)

    def test_invalid_secret_preserves_file_and_never_echoes_input(self):
        self.env_path.write_text("DB_NAME=asyl\n")
        for bad in ["invalid-base64", _b64("private\nINJECT=true")]:
            result = self.run_sync(key_b64=bad)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(bad, result.stdout + result.stderr)
            self.assertNotIn("private", result.stdout + result.stderr)
            self.assertEqual(self.env_path.read_text(), "DB_NAME=asyl\n")

    def test_symlink_cannot_redirect_secret_write(self):
        target = self.root / "other"
        target.write_text("unchanged")
        self.env_path.symlink_to(target)
        result = self.run_sync(KEY)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_text(), "unchanged")
