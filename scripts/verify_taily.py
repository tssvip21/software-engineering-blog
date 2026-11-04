"""Run repeatable checks without using the application's real or preview databases."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'tmp' / 'handoff' / 'phase2'
CHECK_DATA = OUT / 'verification-data'
BROWSER_DATA = OUT / 'browser-data'


def main():
    parser = argparse.ArgumentParser(description='테일리 회귀 검사: 실제 기록과 분리된 DB만 사용합니다.')
    parser.add_argument('--browser', action='store_true', help='Chromium에서 드래그·빠른 기록·권한·모바일도 검사')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    CHECK_DATA.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.pop('DJANGO_ALLOW_ASYNC_UNSAFE', None)
    env.update(CARE_DATA_DIR=str(CHECK_DATA), CARE_DEMO_MODE='0', DJANGO_DEBUG='1', PYTHONUTF8='1',
               DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost,testserver', APP_ORIGIN='http://127.0.0.1:8000')
    checks = []

    def run(name, command, environment=None, timeout=120):
        started = time.monotonic()
        completed = subprocess.run(command, cwd=ROOT, env=environment or env, capture_output=True,
                                   text=True, encoding='utf8', errors='replace', timeout=timeout)
        output = completed.stdout + completed.stderr
        (OUT / ('verify-' + name + '.log')).write_text(output, encoding='utf8')
        checks.append({'name':name, 'exit':completed.returncode, 'seconds':round(time.monotonic()-started, 2),
                       'tests':re.findall(r'Ran (\d+) tests?', output)})
        (OUT / 'verification-run.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf8')
        print(name + ': ' + ('PASS' if completed.returncode == 0 else 'FAIL'), flush=True)
        if completed.returncode:
            print(output[-2200:])
            raise SystemExit(completed.returncode)

    python = sys.executable
    run('django-check', [python, 'manage.py', 'check'])
    run('unit', [python, 'manage.py', 'test', 'care.tests', '--noinput'])
    run('migration-drift', [python, 'manage.py', 'makemigrations', '--check', '--dry-run'])
    run('javascript', ['node', '--check', 'static/care/taily.js'])
    run('javascript-common', ['node', '--check', 'static/care/app.js'])
    run('critical-lint', [python, '-m', 'ruff', 'check', 'care', 'config', 'scripts', '--select', 'E9,F63,F7,F82'])
    run('dependencies', [python, '-m', 'pip', 'check'])
    if args.browser:
        BROWSER_DATA.mkdir(parents=True, exist_ok=True)
        demo = env.copy()
        demo.update(CARE_DATA_DIR=str(BROWSER_DATA), CARE_DEMO_MODE='1', APP_ORIGIN='http://127.0.0.1:8012')
        run('isolated-migration', [python, 'manage.py', 'migrate', '--noinput'], demo)
        run('isolated-demo', [python, 'manage.py', 'seed_demo'], demo)
        run('isolated-references', [python, 'manage.py', 'seed_references'], demo)
        run('browser', [python, 'scripts/verify_taily_browser.py'], demo, 240)
        run('access-browser', [python, 'scripts/verify_taily_access.py'], demo, 120)
    print('All requested checks passed. Detailed logs: tmp/handoff/phase2', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, subprocess.TimeoutExpired) as error:
        print('Verification could not finish: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
