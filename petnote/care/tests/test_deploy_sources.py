from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from deploy.source_files import application_files


class DeploymentSourceTests(TestCase):
    def test_packages_runtime_data_and_font_licenses_but_excludes_local_files(self):
        included = {
            Path("care/data/breeds.json"), Path("config/settings.py"),
            Path("templates/care/home.html"), Path("static/care/fonts/Manrope-OFL.txt"),
            Path("static/care/fonts/Pretendard-OFL.txt"), Path("static/care/brand/logo.svg"),
        }
        local_only = {
            Path("config/.env"), Path("care/data/local.sqlite3"),
            Path("static/care/client_secret.json"), Path("config/signing.key"),
            Path("care/__pycache__/models.pyc"),
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in included | local_only:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            self.assertEqual(set(application_files(root)), included)
