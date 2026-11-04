"""Online SQLite backup and conservative offline restoration for local data."""
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

ROOT = Path(__file__).resolve().parents[1]

def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--demo",action="store_true")
    parser.add_argument("--restore",type=Path)
    parser.add_argument("--confirm-stopped",action="store_true")
    args=parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    data=Path(os.environ.get("CARE_DATA_DIR",str(ROOT/"data")))
    db=data/("demo.sqlite3" if args.demo else "care.sqlite3")
    backups=ROOT/"backups"; backups.mkdir(exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if args.restore:
        if not args.confirm_stopped:
            parser.error("먼저 서버를 종료한 뒤 --confirm-stopped를 명시하세요.")
        source=args.restore.resolve()
        metadata=source.with_suffix(".json")
        if not source.is_file() or not metadata.is_file():
            parser.error("백업 DB와 같은 이름의 JSON 검증 파일이 필요합니다.")
        info=json.loads(metadata.read_text(encoding="utf-8"))
        if info.get("database") != db.name or digest(source) != info.get("sha256"):
            parser.error("백업 종류 또는 SHA-256이 일치하지 않습니다.")
        with closing(sqlite3.connect(source.as_uri()+"?mode=ro",uri=True)) as check:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                parser.error("백업 DB 무결성 검사 실패")
        data.mkdir(parents=True,exist_ok=True)
        if db.exists():
            safety=backups/(db.stem+"-before-restore-"+stamp+".sqlite3")
            with closing(sqlite3.connect(db)) as current, closing(sqlite3.connect(safety)) as target:
                current.backup(target)
        temporary=db.with_suffix(".restore-tmp")
        shutil.copy2(source,temporary)
        os.replace(temporary,db)
        for suffix in ("-wal","-shm"):
            Path(str(db)+suffix).unlink(missing_ok=True)
        print("RESTORED",str(db));return
    if not db.is_file():
        parser.error("백업할 DB가 없습니다.")
    target=backups/(db.stem+"-"+stamp+".sqlite3")
    with closing(sqlite3.connect(db.as_uri()+"?mode=ro",uri=True)) as current, closing(sqlite3.connect(target)) as out:
        current.backup(out)
        if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup integrity failed")
    target.with_suffix(".json").write_text(json.dumps({"database":db.name,"sha256":digest(target),"created_utc":stamp},indent=2),encoding="utf-8")
    print("BACKUP_OK",str(target))

if __name__=="__main__":
    main()
