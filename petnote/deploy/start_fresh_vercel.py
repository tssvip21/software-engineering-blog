"""Deploy PetNote to the existing Hobby project using the signed-in Windows CLI."""
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from tempfile import mkdtemp


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ["--project", "petnote", "--scope", "petnote"]
DOMAIN = "petnote.duckdns.org"


def main():
    cli = shutil.which("vercel.cmd" if os.name == "nt" else "vercel")
    if not cli:
        sys.exit("Vercel CLI is required.")
    # Keep an existing signing key when retrying or deploying again.
    key_result = subprocess.run(
        [cli, "env", "add", "DJANGO_SECRET_KEY", *PROJECT, "--environment",
         "production", "--sensitive", "--yes"],
        input=secrets.token_urlsafe(64), capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=ROOT,
    )
    if key_result.returncode and "already exists" not in (key_result.stdout + key_result.stderr).lower():
        sys.exit("Could not configure DJANGO_SECRET_KEY. Check vercel whoami and project access.")

    # Only upload application files, even when the checkout contains local data.
    staging = Path(mkdtemp(prefix="petnote-deploy-"))
    try:
        for name in ("care", "config", "static", "templates"):
            shutil.copytree(ROOT / name, staging / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("manage.py", "requirements.txt", "vercel.json"):
            shutil.copy2(ROOT / name, staging / name)
        (staging / "deploy").mkdir()
        shutil.copy2(ROOT / "deploy/vercel_build.py", staging / "deploy")
        command = [cli, "deploy", "--prod", "--yes", *PROJECT,
                   "--build-env", "PETNOTE_INITIALIZE_DB=1"]
        for setting in ("APP_ORIGIN=https://" + DOMAIN, "DJANGO_ALLOWED_HOSTS=" + DOMAIN):
            command.extend(["--build-env", setting, "--env", setting])
        subprocess.run(command, cwd=staging, check=True)
    finally:
        try:
            shutil.rmtree(staging)
        except OSError as exc:
            print(f"Temporary deployment files could not be removed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
