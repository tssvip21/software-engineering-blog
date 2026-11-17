"""Build a source archive from an allowlist; never include local DBs or secrets."""
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("Dockerfile", ".dockerignore", "compose.yaml", "compose.demo.yaml", "manage.py",
         "requirements.lock", "requirements-deploy.txt")
DIRECTORIES = ("config", "care", "templates", "static")
DEPLOY_FILES = ("Caddyfile", "entrypoint.sh", "entrypoint-demo.sh", "serve.py", "healthcheck.py",
                "bootstrap-vm.sh", "backup-postgres.sh", "taily-backup.service",
                "taily-backup.timer", "verify-backup.sh")


def main():
    output = ROOT / "tmp/deploy/taily-source.tar.gz"
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = [ROOT / name for name in FILES]
    paths += [ROOT / "deploy" / name for name in DEPLOY_FILES]
    for name in DIRECTORIES:
        paths.extend(p for p in (ROOT / name).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts
                     and p.suffix not in (".pyc", ".pyo")
                     and not p.name.startswith(".env"))
    with tarfile.open(output, "w:gz") as archive:
        for path in sorted(paths):
            if path.is_symlink():
                raise ValueError(f"Unexpected symlink: {path.relative_to(ROOT)}")
            archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
    print(f"Packaged {len(paths)} application files: {output.name} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
