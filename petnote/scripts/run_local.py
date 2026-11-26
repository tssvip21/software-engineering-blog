"""Run the application on localhost, with an isolated SQLite demo when requested."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file())
sys.path.insert(0, str(ROOT))
PROFILE_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}\Z")


def configure_environment(*, demo, port, profile=None):
    """Load local settings, then make demo isolation authoritative over .env."""
    from dotenv import load_dotenv

    if profile is not None and (not demo or not PROFILE_PATTERN.fullmatch(profile)):
        raise ValueError("시연 이름은 영문·숫자로 시작하는 1~48자의 영문·숫자·밑줄·하이픈만 사용하세요.")
    load_dotenv(ROOT / ".env", override=False)
    if not demo and (os.environ.get("POSTGRES_HOST") or os.environ.get("VERCEL") == "1"):
        raise ValueError("이 실행 도구는 로컬 SQLite 전용입니다. PostgreSQL은 Docker 실행 도구를 사용하세요.")
    origin = f"http://127.0.0.1:{port}"
    os.environ.update(
        DJANGO_SETTINGS_MODULE="config.settings",
        CARE_DEMO_MODE="1" if demo else "0",
        DJANGO_DEBUG="1",
        APP_ORIGIN=origin,
        DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost",
        CARE_PUBLIC_DEMO="0",
        CARE_PUBLIC_DEMO_ENTRY="0",
    )
    if demo:
        data = ROOT / "tmp" / ("local-demo" if profile is None else f"previews/{profile}")
        # Empty values also prevent settings.py from restoring credentials from .env.
        os.environ.update(
            CARE_DATA_DIR=str(data),
            VERCEL="0",
            DATABASE_URL="",
            POSTGRES_HOST="",
            POSTGRES_DB="",
            POSTGRES_USER="",
            POSTGRES_PASSWORD="",
            POSTGRES_PORT="",
            PETNOTE_LOCAL_POSTGRES="0",
            DJANGO_SECRET_KEY="",
            GOOGLE_CLIENT_ID="",
            GOOGLE_CLIENT_SECRET="",
            GOOGLE_REDIRECT_URI=origin + "/auth/google/callback/",
        )
        return data
    return Path(os.environ.get("CARE_DATA_DIR", str(ROOT / "data")))


def ensure_port_available(port):
    with socket.socket() as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", port))


def main(argv=None):
    parser = argparse.ArgumentParser(description="펫노트 로컬 실행")
    parser.add_argument("--demo", action="store_true", help="실제 기록과 분리한 시연 환경")
    parser.add_argument("--port", type=int, help="로컬 접속 포트")
    parser.add_argument("--profile", help="별도 시연 이름: 예) review, mobile")
    args = parser.parse_args(argv)
    port = args.port if args.port is not None else (8001 if args.demo else 8000)
    if not 1024 <= port <= 65535:
        parser.error("1024~65535 포트만 사용할 수 있습니다.")
    if args.profile is not None and not args.demo:
        parser.error("--profile은 --demo와 함께 사용하세요.")
    try:
        ensure_port_available(port)
        data = configure_environment(demo=args.demo, port=port, profile=args.profile)
    except (OSError, ValueError) as error:
        parser.error(f"로컬 실행을 시작할 수 없습니다: {error}")

    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command

    if args.demo:
        settings.STATIC_ROOT = data / "staticfiles"
        if args.profile:
            cookie = "petnote_" + args.profile
            settings.SESSION_COOKIE_NAME = cookie + "_session"
            settings.CSRF_COOKIE_NAME = cookie + "_csrf"
    call_command("check")
    call_command("migrate", interactive=False, verbosity=0)
    call_command("seed_references")
    if args.demo:
        call_command("seed_demo")
    call_command("collectstatic", interactive=False, verbosity=0)

    from config.wsgi import application
    from waitress import serve

    runtime = ROOT / "tmp" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    name = args.profile or ("demo" if args.demo else "local")
    marker = runtime / f"{name}-{port}.json"
    marker.write_text(json.dumps({"pid": os.getpid(), "port": port,
                                 "mode": "demo" if args.demo else "local",
                                 "profile": args.profile}), encoding="utf-8")
    print(f"LOCAL {'DEMO' if args.demo else 'APP'}: http://127.0.0.1:{port}", flush=True)
    try:
        serve(application, host="127.0.0.1", port=port, threads=4,
              max_request_body_size=262144, expose_tracebacks=False)
    finally:
        marker.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
