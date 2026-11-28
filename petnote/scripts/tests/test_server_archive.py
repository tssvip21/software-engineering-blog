"""Verify that the exported server archive remains a complete build context."""
import shlex
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import package_deploy


class ServerArchiveTests(unittest.TestCase):
    def fixture(self, folder):
        application = Path(__file__).resolve().parents[2]
        root = Path(folder) / "application"
        relative_paths = {Path(name) for name in package_deploy.FILES}
        relative_paths.update(Path("deploy") / name for name in package_deploy.DEPLOY_FILES)
        for relative in relative_paths:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((application / relative).read_bytes())
        for name in ("care/models.py", "config/settings.py", "templates/care/home.html", "static/care/app.css"):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("synthetic fixture", encoding="utf-8")
        return root

    def test_export_contains_docker_copy_inputs_and_excludes_private_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            excluded = (".env", "data/care.sqlite3", "backups/user.dump", "blog/drafts/private.md",
                        "care/tests/test_private.py", "config/local.env")
            for name in excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("local fixture", encoding="utf-8")
            with mock.patch.object(package_deploy, "ROOT", root):
                package_deploy.main()
            with tarfile.open(root / "tmp/deploy/petnote-source.tar.gz") as archive:
                names = set(archive.getnames())
                for line in (root / "Dockerfile").read_text(encoding="utf-8").splitlines():
                    if not line.startswith("COPY "):
                        continue
                    for source in shlex.split(line)[1:-1]:
                        with self.subTest(source=source):
                            self.assertTrue(source in names or any(name.startswith(source.rstrip("/") + "/") for name in names))
                self.assertEqual(archive.extractfile("compose.yaml").read(),
                                 (root / "compose.production.yaml").read_bytes())
                self.assertIn("deploy/docker/Caddyfile", names)
                self.assertIn("deploy/docker/start-local.sh", names)
                self.assertIn("deploy/backup/backup-postgres.sh", names)
                self.assertTrue(names.isdisjoint(excluded))
                self.assertTrue(all(member.isfile() for member in archive.getmembers()))

    def test_missing_runtime_input_stops_before_writing_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            (root / "deploy/docker/start-local.sh").unlink()
            with mock.patch.object(package_deploy, "ROOT", root), self.assertRaises(ValueError):
                package_deploy.main()
            self.assertFalse((root / "tmp/deploy/petnote-source.tar.gz").exists())


if __name__ == "__main__":
    unittest.main()
