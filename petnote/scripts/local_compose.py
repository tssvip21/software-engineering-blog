"""Portable, loopback-only Docker Compose launcher for Windows and macOS.

No Google credentials are copied. A persistent local env file and named volumes
belong to this project only. 'down' keeps volumes; there is no data-reset option.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file())
SETTING_PREFIXES = ("CARE_", "COMPOSE_", "DEMO_", "DJANGO_", "GOOGLE_", "PETNOTE_", "POSTGRES_")
SETTING_NAMES = {
    "APP_ORIGIN", "DATABASE_URL", "HTTP_BIND", "HTTP_PORT", "HTTPS_BIND",
    "HTTPS_PORT", "SITE_ADDRESS", "VERCEL",
}


class LocalComposeError(ValueError):
    """A safe, user-facing setup error without configuration values."""


def fixed_settings(port):
    return {
        "DJANGO_DEBUG": "1",
        "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1,testserver",
        "APP_ORIGIN": f"http://127.0.0.1:{port}",
        "POSTGRES_DB": "petnote", "POSTGRES_USER": "petnote",
        "SITE_ADDRESS": "http://:80",
        "HTTP_BIND": "127.0.0.1", "HTTP_PORT": str(port),
        "HTTPS_BIND": "127.0.0.1", "HTTPS_PORT": str(port + 1000),
        "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "",
    }


def isolated_environment(settings=None):
    """Keep Docker/OS configuration, but never inherit application settings."""
    env = {
        key: value for key, value in os.environ.items()
        if key.upper() not in SETTING_NAMES and not key.upper().startswith(SETTING_PREFIXES)
    }
    if settings is not None:
        # The shell wins over --env-file interpolation. Supply only validated
        # local values, including an explicit opt-out of the public demo.
        env.update(settings)
        env["CARE_PUBLIC_DEMO_ENTRY"] = "0"
    return env


def local_docker_environment(docker):
    """Resolve Docker's context precedence and pin a local daemon endpoint."""
    env = isolated_environment()
    context = env.get("DOCKER_CONTEXT")
    endpoint = env.get("DOCKER_HOST") if not context else None
    if not endpoint:
        command = [docker, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"]
        if context:
            command.append(context)
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=15)
        if result.returncode:
            raise LocalComposeError("Docker 실행 대상을 확인할 수 없어요. Docker Desktop 연결을 확인해 주세요.")
        endpoint = result.stdout.strip()
    if not endpoint.startswith(("unix:///", "npipe:////./pipe/")):
        raise LocalComposeError("원격 Docker 대상에서는 로컬 실행기를 사용할 수 없어요. Docker Desktop의 로컬 context를 선택해 주세요.")
    # Keep later commands on the inspected endpoint even if the default context
    # changes while the launcher is running.
    env.pop("DOCKER_CONTEXT", None)
    env["DOCKER_HOST"] = endpoint
    return env


def ensure_new_project(docker, project, env):
    label = f"label=com.docker.compose.project={project}"
    for command in (
        [docker, "container", "ls", "--all", "--quiet", "--filter", label],
        [docker, "volume", "ls", "--quiet", "--filter", label],
    ):
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=15)
        if result.returncode:
            raise LocalComposeError("기존 Docker 자료를 확인할 수 없어 새 환경을 만들지 않았어요.")
        if result.stdout.strip():
            raise LocalComposeError("같은 Docker 프로젝트의 기존 자료가 있어요. 원래 runtime.env를 복구하거나 다른 프로젝트 이름을 사용해 주세요.")


def read_settings(envfile, port):
    settings = {}
    for line in envfile.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            raise LocalComposeError("로컬 환경 파일의 형식을 확인해 주세요. 파일을 덮어쓰지 않았어요.")
        key, value = line.split("=", 1)
        if key in settings:
            raise LocalComposeError("로컬 환경 파일에 중복 설정이 있어요. 파일을 덮어쓰지 않았어요.")
        settings[key] = value
    expected = fixed_settings(port)
    allowed = set(expected) | {"DJANGO_SECRET_KEY", "POSTGRES_PASSWORD"}
    if set(settings) != allowed or any(settings[key] != value for key, value in expected.items()):
        raise LocalComposeError("기존 로컬 설정과 안전한 실행 조건이 달라요. 파일을 덮어쓰지 않았어요.")
    for key in ("DJANGO_SECRET_KEY", "POSTGRES_PASSWORD"):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", settings[key]):
            raise LocalComposeError("자동 생성된 로컬 비밀 설정의 형식이 달라요. 파일을 덮어쓰지 않았어요.")
    return settings


def ensure_ports_available(port):
    for candidate in (port, port + 1000):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", candidate)) == 0:
                raise LocalComposeError(f"{candidate} 포트가 사용 중이에요. 다른 포트를 지정해 주세요.")


def create_settings(envfile, port):
    settings = fixed_settings(port)
    settings.update({
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(48),
        "POSTGRES_PASSWORD": secrets.token_urlsafe(40),
    })
    envfile.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Create exclusively with private permissions before writing any secret.
    descriptor = os.open(envfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        output.write("\n".join(f"{key}={value}" for key, value in settings.items()) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="펫노트 로컬 Docker 실행 · 운영 DB와 별도")
    parser.add_argument("action", choices=("up", "stop", "down", "status", "check", "test"))
    parser.add_argument("--project")
    parser.add_argument("--port", type=int, default=8096)
    args = parser.parse_args(argv)
    if args.project is None:
        environments = ROOT / "tmp/local-docker"
        legacy = environments / "taily-local-review/runtime.env"
        current = environments / "petnote-local-review/runtime.env"
        args.project = "taily-local-review" if legacy.is_file() and not current.exists() else "petnote-local-review"
    if not re.fullmatch(r"(?:petnote|taily)-local-[a-z0-9-]{1,32}", args.project):
        parser.error("다른 컨테이너를 건드리지 않도록 프로젝트 이름은 petnote-local-로 시작해야 해요.")
    if not 1024 <= args.port <= 64000:
        parser.error("1024~64000 범위의 로컬 포트를 사용해 주세요.")
    docker = shutil.which("docker")
    if not docker:
        parser.error("Docker Desktop을 설치·실행한 뒤 다시 실행해 주세요.")
    envfile = ROOT / "tmp/local-docker" / args.project / "runtime.env"
    try:
        env = local_docker_environment(docker)
        check = subprocess.run(
            [docker, "info", "--format", "{{.OSType}}"],
            capture_output=True, text=True, env=env, timeout=15,
        )
        if check.returncode or check.stdout.strip() != "linux":
            parser.error("Docker Desktop의 Linux 컨테이너 엔진에 연결할 수 없어요.")
        if not envfile.exists():
            if args.action != "up":
                parser.error("먼저 up으로 이 프로젝트의 로컬 환경을 준비해 주세요.")
            ensure_new_project(docker, args.project, env)
            ensure_ports_available(args.port)
            create_settings(envfile, args.port)
        settings = read_settings(envfile, args.port)
    except LocalComposeError as error:
        parser.error(str(error))
    except (OSError, UnicodeError, subprocess.SubprocessError):
        parser.error("로컬 환경 파일 또는 Docker 연결을 확인해 주세요. 기존 설정을 덮어쓰지 않았어요.")
    command = [
        docker, "compose", "--project-directory", str(ROOT),
        "-f", str(ROOT / "compose.production.yaml"),
        "--env-file", str(envfile), "-p", args.project,
    ]
    steps = {
        "up": ["up", "-d", "--build", "--wait"],
        "stop": ["stop"], "down": ["down"], "status": ["ps"],
        "check": ["config", "--quiet"],
        "test": ["exec", "-T", "backend", "python", "manage.py", "test", "care.tests", "--noinput", "--verbosity", "1"],
    }
    env.update(settings)
    env["CARE_PUBLIC_DEMO_ENTRY"] = "0"
    result = subprocess.run(command + steps[args.action], cwd=ROOT, env=env)
    if result.returncode:
        return result.returncode
    if args.action == "up":
        print(f"LOCAL_DOCKER http://127.0.0.1:{args.port} · 운영/시연 DB와 별도 · Google 로그인 미연결")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
