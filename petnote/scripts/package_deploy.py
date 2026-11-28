"""Build a source archive from an allowlist; never include local DBs or secrets."""
import sys
import tarfile
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file())
sys.path.insert(0, str(ROOT))
from deploy.source_files import application_files, safe_source_file

FILES = ("Dockerfile", ".dockerignore", "compose.production.yaml", "compose.demo.yaml", "manage.py",
         "requirements.lock", "requirements-deploy.txt")
DEPLOY_FILES = (
    "docker/Caddyfile", "docker/entrypoint.sh", "docker/entrypoint-demo.sh",
    "docker/start-local.sh", "docker/serve.py", "docker/healthcheck.py",
    "docker/bootstrap-vm.sh", "docker/production.env.example", "__init__.py", "source_files.py",
    "backup/backup-postgres.sh", "backup/petnote-backup.service",
    "backup/petnote-backup.timer", "backup/verify-backup.sh",
)


def main():
    output = ROOT / "tmp/deploy/petnote-source.tar.gz"
    output.parent.mkdir(parents=True, exist_ok=True)
    relative_paths = {Path(name) for name in FILES}
    relative_paths.update(Path("deploy") / name for name in DEPLOY_FILES)
    relative_paths.update(application_files(ROOT))
    paths = [safe_source_file(ROOT, relative) for relative in sorted(relative_paths)]
    with tarfile.open(output, "w:gz") as archive:
        for path in paths:
            archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
            if path == ROOT / "compose.production.yaml":
                archive.add(path, arcname="compose.yaml", recursive=False)
    print(f"Packaged {len(paths)} application files: {output.name} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
