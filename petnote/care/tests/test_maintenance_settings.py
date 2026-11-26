"""Production startup rejects keys which fail Django's deployment check."""
import os
from pathlib import Path
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory

from django.conf import settings
from django.test import SimpleTestCase


class ProductionSecretTests(SimpleTestCase):
    def load_settings(self, key, *, debug=False):
        with TemporaryDirectory(prefix="petnote-settings-") as directory:
            env = os.environ.copy()
            env.update(
                DJANGO_SETTINGS_MODULE="config.settings",
                DJANGO_DEBUG="1" if debug else "0",
                DJANGO_SECRET_KEY=key,
                CARE_DEMO_MODE="0",
                CARE_PUBLIC_DEMO="0",
                CARE_PUBLIC_DEMO_ENTRY="0",
                CARE_DATA_DIR=directory,
                VERCEL="0",
                POSTGRES_HOST="",
                DATABASE_URL="",
            )
            return subprocess.run(
                [sys.executable, "-X", "utf8", "-c", "import config.settings"],
                cwd=Path(settings.BASE_DIR), env=env,
                capture_output=True, text=True, encoding="utf-8",
                timeout=20,
            )

    def test_production_rejects_missing_and_weak_keys(self):
        for key in ("", "example-key", "a" * 80, "django-insecure-" + secrets.token_urlsafe(48)):
            with self.subTest(kind="missing" if not key else "weak"):
                result = self.load_settings(key)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ImproperlyConfigured", result.stderr)
                if key:
                    self.assertNotIn(key, result.stderr)

    def test_production_accepts_generated_key(self):
        result = self.load_settings(secrets.token_urlsafe(64))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_demo_can_generate_its_own_key(self):
        result = self.load_settings("", debug=True)
        self.assertEqual(result.returncode, 0, result.stderr)
