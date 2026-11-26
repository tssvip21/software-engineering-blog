"""Build a source archive from an allowlist; never include local DBs or secrets."""
import tarfile
import sys
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file())
sys.path.insert(0, str(ROOT))
from deploy.source_files import application_files
FILES = ("Dockerfile", ".dockerignore", "compose.production.yaml", "compose.demo.yaml", "manage.py",
         "requirements.lock", "requirements-deploy.txt")
DEPLOY_FILES = ("Caddyfile", "entrypoint.sh", "entrypoint-demo.sh", "serve.py", "healthcheck.py", "source_files.py",
                "bootstrap-vm.sh", "backup-postgres.sh", "petnote-backup.service",
                "petnote-backup.timer", "verify-backup.sh")


def main():
    output = ROOT / "tmp/deploy/petnote-source.tar.gz"
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = [ROOT / name for name in FILES]
    paths += [ROOT / "deploy" / name for name in DEPLOY_FILES]
    paths.extend(ROOT / relative for relative in application_files(ROOT))
    with tarfile.open(output, "w:gz") as archive:
        for path in sorted(paths):
            if path.is_symlink():
                raise ValueError(f"Unexpected symlink: {path.relative_to(ROOT)}")
            archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
            if path == ROOT / "compose.production.yaml":
                archive.add(path, arcname="compose.yaml", recursive=False)
    print(f"Packaged {len(paths)} application files: {output.name} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
