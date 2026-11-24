"""Local verification for the module and statistics redesign; never changes source databases."""
from pathlib import Path
from datetime import datetime, timezone
import argparse, json, os, re, subprocess, sys
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'tmp/handoff/phase6'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    logs = OUT / 'checks'; logs.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(PYTHONUTF8='1', CARE_DATA_DIR=str(OUT / 'check-data'), CARE_DEMO_MODE='0',
               DJANGO_DEBUG='1', DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost,testserver')
    commands = [('system', [sys.executable, 'manage.py', 'check']),
                ('unit', [sys.executable, 'manage.py', 'test', 'care.tests', '--verbosity', '1', '--noinput']),
                ('migrations', [sys.executable, 'manage.py', 'makemigrations', '--check', '--dry-run']),
                ('lint', [sys.executable, '-m', 'ruff', 'check', 'care', 'config', 'scripts', '--select', 'E9,F63,F7,F82']),
                ('dependencies', [sys.executable, '-m', 'pip', 'check'])]
    commands += [('js-' + name, ['node', '--check', 'static/care/' + name + '.js'])
                 for name in ['app', 'taily', 'module-browser', 'module-editor', 'disclosures', 'studio']]
    if args.browser:
        commands.append(('browser', [sys.executable, 'scripts/verify_module_statistics_browser.py']))
        commands.append(('visual', [sys.executable, 'scripts/verify_module_statistics_visual.py']))
        commands.append(('flows', [sys.executable, 'scripts/verify_module_statistics_flows.py']))
    result = {'at': datetime.now(timezone.utc).isoformat(), 'checks': []}
    for name, command in commands:
        with (logs / (name + '.log')).open('w', encoding='utf8') as log:
            p = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=600)
        text = (logs / (name + '.log')).read_text(encoding='utf8', errors='replace')
        found = re.search(r'Ran (\d+) tests?', text)
        result['checks'].append({'name': name, 'exit': p.returncode, 'tests': int(found[1]) if found else None})
        (OUT / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
        print(name + ': ' + ('PASS' if p.returncode == 0 else 'FAIL'), flush=True)
    result['passed'] = all(c['exit'] == 0 for c in result['checks'])
    (OUT / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
