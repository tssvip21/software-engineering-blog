"""Exercise an isolated local Compose test stack, including backup restoration."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.request import urlopen
from uuid import uuid4

from check_caddy import check_routes


ROOT = Path(__file__).resolve().parents[2] / 'petnote'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--port', type=int, default=8096)
    args = parser.parse_args()
    if not re.fullmatch(r'petnote-(?:ci(?:-[a-z0-9-]+)?|maintenance-qa)', args.project):
        parser.error('Use a dedicated petnote-ci or petnote-maintenance-qa test project.')
    if not 1024 <= args.port <= 65535:
        parser.error('Use an unprivileged local port.')
    env = os.environ.copy()
    env['PETNOTE_PORT'] = str(args.port)
    compose = ['docker', 'compose', '-f', 'compose.yaml', '-p', args.project]
    origin = f'http://127.0.0.1:{args.port}'

    def run(*command, data=None):
        result = subprocess.run(compose + list(command), cwd=ROOT, env=env, input=data,
                                capture_output=True, check=True)
        return result.stdout

    def shell(code):
        prelude = (
            "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')\n"
            "import django; django.setup()\n"
            "from django.conf import settings\n"
            f"assert settings.DEBUG and settings.DEMO_MODE and settings.APP_ORIGIN == {origin!r}\n"
            "assert settings.DATABASES['default']['NAME'] == 'petnote_demo'\n"
            "from django.contrib.auth import get_user_model\n"
        )
        return run('exec', '-T', 'backend', 'sh', '-ec',
                   'export POSTGRES_PASSWORD="$(cat /run/dbkey/password)"; exec python -',
                   data=(prelude + code).encode('utf-8'))

    marker = 'compose-check-' + uuid4().hex
    restore_db = 'restore_check_' + uuid4().hex
    created_restore = False
    shell(f"get_user_model().objects.create_user({marker!r})\n")
    try:
        run('restart', 'db', 'backend', 'frontend')
        deadline = time.monotonic() + 120
        while True:
            try:
                with urlopen(origin + '/health/', timeout=5) as response:
                    if json.load(response).get('status') == 'ok':
                        break
            except (OSError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError('Compose stack did not recover after restart.')
            time.sleep(1)
        shell(f"assert get_user_model().objects.filter(username={marker!r}).exists()\n")
        with urlopen(origin + '/', timeout=10) as response:
            html = response.read().decode('utf-8')
            assert '펫노트' in html
            assert response.headers.get('X-Frame-Options') == 'DENY'
        css = re.search(r'(?:src|href)="(/static/[^\"]+\.css[^\"]*)"', html)
        assert css, 'Homepage must reference a bundled stylesheet'
        with urlopen(origin + css.group(1), timeout=10) as response:
            assert response.status == 200 and len(response.read()) > 100
            assert response.headers.get('X-Content-Type-Options') == 'nosniff'
        dump = run('exec', '-T', 'db', 'pg_dump', '-U', 'petnote', '-d', 'petnote_demo', '-Fc')
        assert dump.startswith(b'PGDMP')
        run('exec', '-T', 'db', 'createdb', '-U', 'petnote', restore_db)
        created_restore = True
        run('exec', '-T', 'db', 'pg_restore', '-U', 'petnote', '-d', restore_db,
            '--no-owner', '--no-privileges', '--exit-on-error', data=dump)
        result = run('exec', '-T', 'db', 'psql', '-U', 'petnote', '-d', restore_db,
                     '-At', '-v', 'ON_ERROR_STOP=1', '-c',
                     f"SELECT COUNT(*) FROM auth_user WHERE username='{marker}'")
        assert result.strip() == b'1', 'Restored backup lost the synthetic marker'
        print('Compose runtime passed: health, homepage, static file, response headers, restart persistence, backup and restore.')
    finally:
        if created_restore:
            run('exec', '-T', 'db', 'dropdb', '-U', 'petnote', '--if-exists', restore_db)
        shell(f"get_user_model().objects.filter(username={marker!r}).delete()\n")
    check_routes(run('images', '--quiet', 'backend').decode('utf-8').strip())


if __name__ == '__main__':
    main()
