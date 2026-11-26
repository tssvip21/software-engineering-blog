"""Keep Compose browser tests confined to an explicitly isolated local demo."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file())
SPEC = importlib.util.spec_from_file_location("compose_browser_qa", ROOT / "scripts" / "qa" / "verify_petnote_browser.py")
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class BrowserTargetTests(unittest.TestCase):
    def test_explicit_loopback_origins_are_allowed(self):
        for origin in ("http://127.0.0.1:18096", "http://localhost:18096/", "http://[::1]:18096"):
            with self.subTest(origin=origin):
                self.assertEqual(QA.local_target(origin, "petnote-maintenance-qa"), origin.rstrip("/"))

    def test_remote_and_ambiguous_origins_are_rejected_before_docker(self):
        for origin in (
            "https://example.com:443", "http://example.com:18096",
            "http://0.0.0.0:18096", "http://127.0.0.2:18096",
            "https://127.0.0.1:18096", "http://127.0.0.1",
            "http://127.0.0.1:0", "http://127.0.0.1:99999",
            "http://127.0.0.1:18096/path", "http://127.0.0.1:18096?target=x",
            "http://127.0.0.1:18096#fragment", "http://user@127.0.0.1:18096",
        ):
            with self.subTest(origin=origin), patch.object(QA.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    QA.qa_backend(origin, "petnote-maintenance-qa")
                run.assert_not_called()

    def test_ordinary_and_production_projects_are_rejected(self):
        for project in ("petnote-local", "petnote-production", "other-qa", "petnote-local-qa;echo test"):
            with self.subTest(project=project), patch.object(QA.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    QA.qa_backend("http://127.0.0.1:18096", project)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
