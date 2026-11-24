"""Run scoped local checks without using an existing care or preview database."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp/handoff/phase5"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    logs = OUTPUT / "checks"
    logs.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", DJANGO_SETTINGS_MODULE="config.settings", CARE_DATA_DIR=str(OUTPUT / "check-data"),
               CARE_DEMO_MODE="0", DJANGO_DEBUG="1", DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost,testserver")
    python = sys.executable
    commands = [
        ("system", [python, "manage.py", "check"]),
        ("unit", [python, "manage.py", "test", "care.tests", "--verbosity", "1", "--noinput"]),
        ("migrations", [python, "manage.py", "makemigrations", "--check", "--dry-run"]),
        ("dependencies", [python, "-m", "pip", "check"]),
        ("lint", [python, "-m", "ruff", "check", "care", "config", "scripts", "--select", "E9,F63,F7,F82"]),
    ]
    commands += [("javascript-" + name, ["node", "--check", "static/care/" + name + ".js"])
                 for name in ["app", "taily", "module-browser", "module-editor", "disclosures"]]
    if args.browser:
        commands.append(("browser", [python, "scripts/verify_dashboard_browser.py"]))
        commands.append(("visual", [python, "scripts/verify_dashboard_visual.py"]))
        commands.append(("flows", [python, "scripts/verify_dashboard_flows.py"]))
    result = {"at": datetime.now(timezone.utc).isoformat(), "checks": []}
    for name, command in commands:
        log = logs / (name + ".log")
        with log.open("w", encoding="utf-8") as output:
            run = subprocess.run(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
        text = log.read_text(encoding="utf-8", errors="replace")
        item = {"name": name, "exit": run.returncode, "log": str(log.relative_to(ROOT))}
        if name == "unit":
            found = re.search(r"Ran (\d+) tests?", text)
            item["tests"] = int(found.group(1)) if found else None
        result["checks"].append(item)
        (OUTPUT / "verification-run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(name + ": " + ("PASS" if run.returncode == 0 else "FAIL"), flush=True)
    result["passed"] = all(c["exit"] == 0 for c in result["checks"])
    (OUTPUT / "verification-run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
