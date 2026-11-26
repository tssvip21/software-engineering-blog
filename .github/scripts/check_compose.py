"""Validate Compose contracts without launching or changing services."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2] / 'petnote'
VALUES = {
    'SITE_ADDRESS': 'petnote.example.invalid',
    'APP_ORIGIN': 'https://petnote.example.invalid',
    'DJANGO_ALLOWED_HOSTS': 'petnote.example.invalid',
    'DJANGO_SECRET_KEY': 'compose-validation-only-' * 4,
    'POSTGRES_PASSWORD': 'compose-validation-only-' * 3,
    'POSTGRES_DB': 'petnote', 'POSTGRES_USER': 'petnote',
    'DEMO_DJANGO_SECRET_KEY': 'separate-compose-validation-' * 3,
    'PUBLIC_DEMO_SEED_PATH': './tmp/synthetic-seed.sqlite3',
    'PETNOTE_PORT': '18096',
}


def render(*files, missing=None):
    env = os.environ.copy()
    for key in ('DJANGO_DEBUG', 'HTTP_BIND', 'HTTPS_BIND', 'HTTP_PORT', 'HTTPS_PORT', 'CARE_PUBLIC_DEMO_ENTRY', *VALUES):
        env.pop(key, None)
    env.update(VALUES)
    if missing:
        env.pop(missing)
    with tempfile.TemporaryDirectory(prefix='petnote-compose-check-') as folder:
        envfile = Path(folder) / 'empty.env'
        envfile.touch()
        command = ['docker', 'compose', '--env-file', str(envfile)]
        for file in files:
            command.extend(['-f', file])
        command.extend(['config', '--format', 'json'])
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8')
    if missing:
        assert result.returncode != 0, f'Production accepted missing {missing}'
        return None
    if result.returncode:
        raise RuntimeError('Compose configuration failed. Check the template and Docker Compose installation.')
    return json.loads(result.stdout)


def common(config):
    services = config['services']
    assert not services['backend'].get('ports'), 'Backend port must remain private'
    assert not services['db'].get('ports'), 'Database port must remain private'
    assert config['networks']['database']['internal'], 'Database network must be internal'
    backend = services['backend']
    assert backend['read_only'] and 'ALL' in backend['cap_drop']
    assert 'no-new-privileges:true' in backend['security_opt']
    for name in ('frontend', 'db'):
        assert re.search(r'@sha256:[a-f0-9]{64}$', services[name]['image']), 'Pin runtime images by digest'
    for name in ('backend', 'db'):
        assert services[name].get('healthcheck')
    assert services['frontend']['depends_on']['backend']['condition'] == 'service_healthy'
    assert services['backend']['depends_on']['db']['condition'] == 'service_healthy'
    assert '-h 127.0.0.1' in ' '.join(services['db']['healthcheck']['test']), 'Wait for the final TCP database server'
    for service in services.values():
        assert service['logging']['options']['max-size'] == '10m'


def main():
    local = render('compose.yaml')
    common(local)
    assert all(port['host_ip'] == '127.0.0.1' for port in local['services']['frontend']['ports'])
    assert local['services']['backend']['environment']['CARE_DEMO_MODE'] == '1'
    assert not local['services']['backend']['environment'].get('POSTGRES_PASSWORD')
    assert local['services']['frontend'].get('healthcheck')
    production = render('compose.production.yaml')
    common(production)
    assert production['services']['backend']['environment']['DJANGO_DEBUG'] == '0'
    assert production['services']['backend']['environment']['CARE_DEMO_MODE'] == '0'
    assert production['services']['frontend']['environment']['CARE_PUBLIC_DEMO_ENTRY'] == '0'
    assert production['services']['backend']['environment']['CARE_PUBLIC_DEMO_ENTRY'] == '0'
    public_demo = render('compose.production.yaml', 'compose.demo.yaml')
    for name in ('frontend', 'backend'):
        assert public_demo['services'][name]['environment']['CARE_PUBLIC_DEMO_ENTRY'] == '1'
    assert public_demo['services']['frontend']['depends_on']['demo']['condition'] == 'service_healthy'
    demo = public_demo['services']['demo']
    assert demo['read_only'] and not demo.get('ports')
    assert demo['environment']['GOOGLE_CLIENT_SECRET'] == ''
    assert 'POSTGRES_HOST' not in demo['environment']
    for key in ('DJANGO_SECRET_KEY', 'POSTGRES_PASSWORD', 'APP_ORIGIN', 'DJANGO_ALLOWED_HOSTS', 'SITE_ADDRESS'):
        render('compose.production.yaml', missing=key)
    for key in ('DEMO_DJANGO_SECRET_KEY', 'PUBLIC_DEMO_SEED_PATH'):
        render('compose.production.yaml', 'compose.demo.yaml', missing=key)
    print('Compose contracts passed: local, production, optional demo, TCP readiness, and seven missing-setting checks.')


if __name__ == '__main__':
    main()
