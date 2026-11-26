"""Exercise SQLite backup/restore with disposable records, including failure safety."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("petnote_backup", Path(__file__).resolve().parents[1] / "backup.py")
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.addCleanup(patch.stopall)
        patch.object(backup, "ROOT", self.root).start()
        patch.dict(os.environ, {}, clear=True).start()

    def database(self, relative="data/care.sqlite3", value="original"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE notes(value TEXT NOT NULL)")
            connection.execute("INSERT INTO notes VALUES (?)", [value])
        return path

    def value(self, path):
        with contextlib.closing(sqlite3.connect(path)) as connection, connection:
            return connection.execute("SELECT value FROM notes").fetchone()[0]

    def invoke(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            backup.main(list(args))

    def test_backup_includes_committed_wal_data_and_metadata(self):
        database = self.database()
        with contextlib.closing(sqlite3.connect(database)) as live:
            live.execute("PRAGMA journal_mode=WAL")
            live.execute("UPDATE notes SET value='latest'")
            live.commit()
            self.invoke()
        targets = list((self.root / "backups").glob("*.sqlite3"))
        self.assertEqual(len(targets), 1)
        self.assertEqual(self.value(targets[0]), "latest")
        metadata = json.loads(targets[0].with_suffix(".json").read_text())
        self.assertEqual(metadata["sha256"], backup.digest(targets[0]))
        self.assertEqual(metadata["database"], "care.sqlite3")
        self.assertEqual(self.value(database), "latest")

    def test_default_demo_uses_launcher_location_despite_external_env(self):
        self.database("data/demo.sqlite3", "other")
        self.database("tmp/local-demo/demo.sqlite3", "demo")
        os.environ.update(CARE_DATA_DIR=str(self.root / "data"), POSTGRES_HOST="example.invalid", VERCEL="1")
        self.invoke("--demo")
        saved = next((self.root / "backups").glob("*.sqlite3"))
        self.assertEqual(self.value(saved), "demo")

    def test_named_profile_backs_up_only_that_profile(self):
        self.database("tmp/previews/mobile/demo.sqlite3", "mobile")
        self.database("tmp/local-demo/demo.sqlite3", "default")
        self.invoke("--demo", "--profile", "mobile")
        saved = next((self.root / "backups").glob("*.sqlite3"))
        self.assertEqual(self.value(saved), "mobile")

    def test_restore_creates_a_restorable_safety_backup(self):
        database = self.database()
        self.invoke()
        saved = next((self.root / "backups").glob("*.sqlite3"))
        with contextlib.closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("UPDATE notes SET value='changed'")
        self.invoke("--restore", str(saved), "--confirm-stopped")
        self.assertEqual(self.value(database), "original")
        safety = next((self.root / "backups").glob("*before-restore-*.sqlite3"))
        self.assertTrue(safety.with_suffix(".json").is_file())
        self.assertEqual(self.value(safety), "changed")
        self.invoke("--restore", str(safety), "--confirm-stopped")
        self.assertEqual(self.value(database), "changed")

    def test_restore_requires_stopped_confirmation(self):
        database = self.database()
        self.invoke()
        saved = next((self.root / "backups").glob("*.sqlite3"))
        before = backup.digest(database)
        with self.assertRaises(SystemExit) as error:
            self.invoke("--restore", str(saved))
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(backup.digest(database), before)

    def test_corrupt_backup_and_wrong_type_never_replace_original(self):
        database = self.database()
        self.invoke()
        saved = next((self.root / "backups").glob("*.sqlite3"))
        before = backup.digest(database)
        metadata_path = saved.with_suffix(".json")
        metadata = json.loads(metadata_path.read_text())
        for content in ("[]", "{invalid", json.dumps({**metadata, "database": "demo.sqlite3"}), json.dumps({**metadata, "sha256": "0" * 64})):
            metadata_path.write_text(content, encoding="utf-8")
            with self.subTest(metadata=content), self.assertRaises(SystemExit):
                self.invoke("--restore", str(saved), "--confirm-stopped")
            self.assertEqual(backup.digest(database), before)
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        saved.write_bytes(b"corrupt")
        with self.assertRaises(SystemExit):
            self.invoke("--restore", str(saved), "--confirm-stopped")
        self.assertEqual(backup.digest(database), before)

    def test_invalid_profile_and_postgres_do_not_create_a_backup(self):
        self.database()
        for args in (("--demo", "--profile", "../data"), ("--profile", "mobile")):
            with self.assertRaises(SystemExit):
                self.invoke(*args)
        os.environ["POSTGRES_HOST"] = "example.invalid"
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assertFalse((self.root / "backups").exists())

    def test_missing_database_does_not_create_backup_directory(self):
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assertFalse((self.root / "backups").exists())


if __name__ == "__main__":
    unittest.main()
