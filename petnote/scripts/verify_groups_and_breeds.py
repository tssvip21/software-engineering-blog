"""Reproduce third-iteration checks using disposable local data, without publishing."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp/handoff/phase3"


def main():
    parser = argparse.ArgumentParser(description="테일리 3차 로컬 검증")
    parser.add_argument("--browser", action="store_true", help="별도 DB와 8014번 포트에서 실제 브라우저 동작도 검사")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(CARE_DATA_DIR=str(OUT / "check-db"), CARE_DEMO_MODE="0", DJANGO_DEBUG="1",
               DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost,testserver", PYTHONUTF8="1")
    commands = [
        ("system", [sys.executable, "manage.py", "check"]),
        ("unit", [sys.executable, "manage.py", "test", "care.tests", "--noinput", "--verbosity", "1"]),
        ("migrations", [sys.executable, "manage.py", "makemigrations", "--check", "--dry-run"]),
        ("javascript", ["node", "--check", "static/care/module-browser.js"]),
        ("common-javascript", ["node", "--check", "static/care/taily.js"]),
        ("base-javascript", ["node", "--check", "static/care/app.js"]),
        ("critical-lint", [sys.executable, "-m", "ruff", "check", "care", "scripts", "config", "--select", "E9,F63,F7,F82"]),
        ("dependencies", [sys.executable, "-m", "pip", "check"]),
    ]
    if args.browser:
        commands.append(("browser", [sys.executable, "scripts/verify_groups_and_breeds_browser.py"]))
    report = {"started_utc": datetime.now(timezone.utc).isoformat(), "checks": [], "complete": False}
    result_path = OUT / "verification-run.json"
    for name, command in commands:
        try:
            run = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=420 if name == "browser" else 180)
            output = run.stdout + "\n" + run.stderr
            exit_code = run.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            output = type(error).__name__ + ": " + str(error)
            exit_code = -1
        log = OUT / (name + "-final.log")
        log.write_text(output, encoding="utf-8")
        row = {"name": name, "exit": exit_code, "tests": re.findall(r"Ran (\d+) tests", output),
               "log": str(log.relative_to(ROOT))}
        report["checks"].append(row)
        result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(name + ": " + ("PASS" if exit_code == 0 else "FAIL"), flush=True)
        if exit_code:
            print("Details: " + row["log"], flush=True)
            raise SystemExit(1)
    report["complete"] = True
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("All requested checks passed. Logs: tmp/handoff/phase3", flush=True)


if __name__ == "__main__":
    main()
