"""Back up local SQLite safely, or restore offline after validating a backup."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def create_backup(database, target, stamp):
    if target.exists():
        raise ValueError("같은 이름의 백업이 이미 있습니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(target)) as destination:
                source.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("백업 DB 무결성 검사에 실패했습니다.")
        target.with_suffix(".json").write_text(json.dumps({
            "database": database.name, "sha256": digest(target), "created_utc": stamp,
        }, indent=2), encoding="utf-8")
    except Exception:
        target.unlink(missing_ok=True)
        target.with_suffix(".json").unlink(missing_ok=True)
        raise


def restore_backup(source, database, backups, stamp):
    metadata = source.with_suffix(".json")
    if not source.is_file() or not metadata.is_file():
        raise ValueError("백업 DB와 같은 이름의 JSON 검증 파일이 필요합니다.")
    info = json.loads(metadata.read_text(encoding="utf-8"))
    if not isinstance(info, dict) or info.get("database") != database.name or digest(source) != info.get("sha256"):
        raise ValueError("백업 종류 또는 SHA-256이 일치하지 않습니다.")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as check:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("백업 DB 무결성 검사에 실패했습니다.")
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists():
        safety = backups / f"{database.stem}-before-restore-{stamp}.sqlite3"
        create_backup(database, safety, stamp)
    with tempfile.NamedTemporaryFile(dir=database.parent, prefix=".restore-", suffix=".sqlite3", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        shutil.copyfile(source, temporary)
        if digest(temporary) != info["sha256"]:
            raise ValueError("복원용 복사본의 검증값이 일치하지 않습니다.")
        os.replace(temporary, database)
        for suffix in ("-wal", "-shm"):
            Path(str(database) + suffix).unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="기본 로컬 시연 DB를 백업")
    parser.add_argument("--profile", help="run_local.py --demo --profile과 같은 시연 이름")
    parser.add_argument("--restore", type=Path)
    parser.add_argument("--confirm-stopped", action="store_true", help="복원 대상 서버를 종료했음을 확인")
    args = parser.parse_args(argv)
    from dotenv import load_dotenv
    from scripts.run_local import PROFILE_PATTERN

    if args.profile is not None and (not args.demo or not PROFILE_PATTERN.fullmatch(args.profile)):
        parser.error("--profile은 --demo와 함께 유효한 시연 이름으로 지정하세요.")
    load_dotenv(ROOT / ".env", override=False)
    if args.demo:
        data = ROOT / "tmp" / ("local-demo" if args.profile is None else f"previews/{args.profile}")
    else:
        if os.environ.get("POSTGRES_HOST") or os.environ.get("VERCEL") == "1":
            parser.error("이 도구는 SQLite 전용입니다. PostgreSQL 백업 도구를 사용하세요.")
        data = Path(os.environ.get("CARE_DATA_DIR", str(ROOT / "data")))
    database = (data / ("demo.sqlite3" if args.demo else "care.sqlite3")).resolve()
    backups = ROOT / "backups"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if args.restore and not args.confirm_stopped:
        parser.error("먼저 복원 대상 서버를 종료한 뒤 --confirm-stopped를 명시하세요.")
    try:
        if args.restore:
            restore_backup(args.restore.resolve(), database, backups, stamp)
            print("RESTORED", database)
        else:
            if not database.is_file():
                parser.error("백업할 DB가 없습니다.")
            target = backups / f"{database.stem}-{stamp}.sqlite3"
            create_backup(database, target, stamp)
            print("BACKUP_OK", target)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
