"""Apply only the three requested display names to a local synthetic demo database.

Default is read-only. With --apply, a SQLite backup is created first.
The real production PostgreSQL database and all other demo rooms are excluded.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


SHOWCASE = (
    ("024b42be-590a-4c19-a98c-2228429c11a3", "dog", "보리", "강아지"),
    ("537cec39-91b3-4757-ad35-18b59ec7cd00", "cat", "나비", "고양이"),
    ("b74c27c7-a65a-4427-b6bf-3c4b43f5dbaf", "bird", "초록이", "앵무새"),
)


def inspect(db):
    rows = []
    for uid, species, previous, name in SHOWCASE:
        record = db.execute(
            "SELECT r.name, r.species, u.username "
            "FROM care_room r JOIN auth_user u ON u.id = r.owner_id "
            "WHERE r.id = ?", (uid.replace("-", ""),)
        ).fetchone()
        if record is None or record[1] != species or record[2] != "demo_owner":
            raise ValueError("시연 공간 ID·동물 종류·소유자가 일치하지 않아 변경하지 않았어요.")
        if record[0] not in {previous, name, "7차 검수 · " + previous}:
            raise ValueError("시연 이름이 예상과 달라 사용자 변경을 덮어쓰지 않았어요.")
        rows.append({"id": uid, "before": record[0], "after": name, "species": species})
    return rows


def main():
    parser = argparse.ArgumentParser(description="강아지·고양이·앵무새 세 시연 공간의 이름만 반영")
    parser.add_argument("--database", type=Path, required=True, help="기존 로컬 시연 demo.sqlite3 경로")
    parser.add_argument("--apply", action="store_true", help="백업 후 세 공간의 이름만 수정")
    args = parser.parse_args()
    source = args.database.resolve(strict=True)
    if source.name != "demo.sqlite3" or not source.is_file():
        parser.error("로컬 시연 demo.sqlite3 파일만 사용할 수 있어요.")

    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as db:
        rows = inspect(db)
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("DB 검사에 실패해 이름을 변경하지 않았어요.")
        if not args.apply or all(row["before"] == row["after"] for row in rows):
            print(json.dumps({"applied": False, "rooms": rows}, ensure_ascii=False))
            return
        backup = source.with_name("before-three-room-names-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite3")
        if backup.exists():
            raise FileExistsError("백업 이름이 중복돼 원본을 변경하지 않았어요.")
        with sqlite3.connect(backup) as saved:
            db.backup(saved)

    with sqlite3.connect(source.as_uri() + "?mode=rw", uri=True, timeout=20) as db:
        db.execute("BEGIN IMMEDIATE")
        fresh = inspect(db)
        if fresh != rows:
            db.rollback()
            raise ValueError("백업 중 시연 데이터가 변경되어 중단했어요.")
        for row in rows:
            if row["before"] != row["after"]:
                db.execute(
                    "UPDATE care_room SET name = ? WHERE id = ?",
                    (row["after"], row["id"].replace("-", "")),
                )
        assert all(row["before"] == row["after"] for row in inspect(db))
        db.commit()
    print(json.dumps({"applied": True, "backup": str(backup), "rooms": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
