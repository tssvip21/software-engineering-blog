"""Run maintained local checks using disposable SQLite data and concise logs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true", help="브라우저 회귀 검사도 실행")
    parser.add_argument("--browsers", nargs="+", choices=("chromium", "firefox", "webkit"), default=["chromium", "firefox"])
    parser.add_argument("--output", type=Path, default=ROOT / "tmp" / "checks")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    commands = [
        ("dependencies", [python, "-m", "pip", "check"], 60),
        ("python", [python, "-m", "ruff", "check", "care", "config", "scripts", "deploy", "--select", "E9,F63,F7,F82"], 60),
        ("django", [python, "manage.py", "check"], 60),
        ("migrations", [python, "manage.py", "makemigrations", "--check", "--dry-run"], 60),
        ("server-tests", [python, "manage.py", "test", "care.tests", "--noinput"], 600),
        ("tool-tests", [python, "-m", "unittest", "discover", "-s", "scripts/tests", "-v"], 120),
    ]
    commands.extend(("javascript-" + path.stem, ["node", "--check", str(path)], 30)
                    for path in sorted((ROOT / "static" / "care").glob("*.js")))
    if args.browser:
        commands.append(("browser", [python, "scripts/qa/verify_maintenance.py", "--browsers", *args.browsers,
                                     "--output", str(output / "browser-results.json")], 600))
    result = {"started_utc": datetime.now(timezone.utc).isoformat(), "checks": [], "passed": False}
    with tempfile.TemporaryDirectory(prefix="petnote-check-") as data_dir:
        env = os.environ.copy()
        env.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        env.update({
            "PYTHONUTF8": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1", "DJANGO_SETTINGS_MODULE": "config.settings",
            "CARE_DATA_DIR": data_dir, "CARE_DEMO_MODE": "0", "CARE_PUBLIC_DEMO": "0", "CARE_PUBLIC_DEMO_ENTRY": "0",
            "DJANGO_DEBUG": "1", "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
            "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost,testserver", "APP_ORIGIN": "http://127.0.0.1:8000",
            "VERCEL": "0", "DATABASE_URL": "", "POSTGRES_HOST": "", "PETNOTE_LOCAL_POSTGRES": "0",
            "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "",
        })
        for name, command, timeout in commands:
            start = time.monotonic()
            log_path = output / f"{name}.log"
            with log_path.open("w", encoding="utf-8") as log:
                try:
                    completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
                    exit_code = completed.returncode
                except subprocess.TimeoutExpired:
                    log.write(f"Check exceeded its {timeout}-second timeout.\n")
                    exit_code = 124
                except OSError as error:
                    log.write(f"Could not start the check: {error}\n")
                    exit_code = 127
            text = log_path.read_text(encoding="utf-8", errors="replace")
            count = re.search(r"Ran (\d+) tests?", text)
            entry = {"name": name, "exit": exit_code, "seconds": round(time.monotonic() - start, 2), "log": log_path.name}
            if count:
                entry["tests"] = int(count.group(1))
            result["checks"].append(entry)
            (output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{name}: {'PASS' if exit_code == 0 else 'FAIL'} ({entry['seconds']}s)", flush=True)
    result["passed"] = all(item["exit"] == 0 for item in result["checks"])
    result["finished_utc"] = datetime.now(timezone.utc).isoformat()
    (output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Results: {output / 'results.json'}", flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
