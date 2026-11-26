"""Guard local startup against inherited production settings and unsafe paths."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("petnote_local_runner", Path(__file__).resolve().parents[1] / "run_local.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class LocalRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.addCleanup(patch.stopall)
        patch.object(runner, "ROOT", self.root).start()
        patch.dict(os.environ, {}, clear=True).start()

    def test_demo_overrides_production_environment_and_dotenv(self):
        contaminated = {
            "VERCEL": "1", "DATABASE_URL": "postgresql://example.invalid/example",
            "POSTGRES_HOST": "db", "POSTGRES_DB": "petnote_demo", "POSTGRES_USER": "example",
            "POSTGRES_PASSWORD": "test-only-placeholder", "POSTGRES_PORT": "5432",
            "PETNOTE_LOCAL_POSTGRES": "1", "CARE_DATA_DIR": "protected-data",
            "CARE_PUBLIC_DEMO": "1", "CARE_PUBLIC_DEMO_ENTRY": "1", "DJANGO_DEBUG": "0",
            "DJANGO_SECRET_KEY": "test-only-placeholder", "GOOGLE_CLIENT_ID": "example",
            "GOOGLE_CLIENT_SECRET": "test-only-placeholder", "GOOGLE_REDIRECT_URI": "https://example.invalid/callback/",
        }
        self.root.joinpath(".env").write_text("\n".join(f"{key}={value}" for key, value in contaminated.items()), encoding="utf-8")
        os.environ.update(contaminated)
        data = runner.configure_environment(demo=True, port=8051, profile="qa")
        # settings.py reads .env again; cleared keys must remain authoritative.
        from dotenv import load_dotenv
        load_dotenv(self.root / ".env", override=False)
        self.assertEqual(data, self.root / "tmp" / "previews" / "qa")
        self.assertEqual(os.environ["CARE_DATA_DIR"], str(data))
        for key in ("DATABASE_URL", "POSTGRES_HOST", "POSTGRES_PASSWORD", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "DJANGO_SECRET_KEY"):
            self.assertEqual(os.environ[key], "", key)
        for key in ("VERCEL", "PETNOTE_LOCAL_POSTGRES", "CARE_PUBLIC_DEMO", "CARE_PUBLIC_DEMO_ENTRY"):
            self.assertEqual(os.environ[key], "0", key)
        self.assertEqual(os.environ["APP_ORIGIN"], "http://127.0.0.1:8051")
        self.assertEqual(os.environ["DJANGO_ALLOWED_HOSTS"], "127.0.0.1,localhost")
        self.assertEqual(os.environ["DJANGO_DEBUG"], "1")
        self.assertFalse(data.exists())

    def test_demo_keeps_default_demo_location(self):
        os.environ["CARE_DATA_DIR"] = "protected-data"
        self.assertEqual(runner.configure_environment(demo=True, port=8001), self.root / "tmp" / "local-demo")

    def test_different_profiles_use_different_directories(self):
        first = runner.configure_environment(demo=True, port=8051, profile="review-a")
        second = runner.configure_environment(demo=True, port=8052, profile="review_b")
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_relative_to(self.root / "tmp"))
        self.assertTrue(second.is_relative_to(self.root / "tmp"))

    def test_invalid_profile_does_not_modify_environment_or_files(self):
        for value in ("", "../data", "..", "a/b", "a\\b", "a b", "a" * 49, "/data"):
            with self.subTest(profile=value), self.assertRaises(ValueError):
                runner.configure_environment(demo=True, port=8001, profile=value)
            self.assertEqual(dict(os.environ), {})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_profile_requires_demo(self):
        with self.assertRaises(ValueError):
            runner.configure_environment(demo=False, port=8000, profile="qa")

    def test_local_run_refuses_postgres_from_dotenv(self):
        self.root.joinpath(".env").write_text("POSTGRES_HOST=example.invalid\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            runner.configure_environment(demo=False, port=8000)
        self.assertFalse((self.root / "data").exists())

    def test_local_run_refuses_vercel_environment(self):
        os.environ["VERCEL"] = "1"
        with self.assertRaises(ValueError):
            runner.configure_environment(demo=False, port=8000)

    def test_local_sqlite_keeps_explicit_data_directory(self):
        expected = self.root / "existing-data"
        os.environ["CARE_DATA_DIR"] = str(expected)
        self.assertEqual(runner.configure_environment(demo=False, port=8000), expected)
        self.assertEqual(os.environ["CARE_DEMO_MODE"], "0")

    def test_invalid_port_is_rejected_before_setup(self):
        for value in ("0", "1023", "65536"):
            with self.subTest(port=value), patch.object(runner, "configure_environment") as setup:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    runner.main(["--demo", "--port", value])
                self.assertEqual(error.exception.code, 2)
                setup.assert_not_called()

    def test_existing_server_port_is_not_reused(self):
        with socket.socket() as existing:
            existing.bind(("127.0.0.1", 0))
            existing.listen()
            port = existing.getsockname()[1]
            with self.assertRaises(OSError):
                runner.ensure_port_available(port)
            self.assertGreater(existing.fileno(), -1)

    def test_busy_port_stops_before_any_database_setup(self):
        with patch.object(runner, "ensure_port_available", side_effect=OSError("occupied")):
            with patch.object(runner, "configure_environment") as setup:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    runner.main(["--demo"])
                self.assertEqual(error.exception.code, 2)
                setup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
