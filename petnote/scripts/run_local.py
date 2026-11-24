"""Run the local-only application. This script never publishes or deploys."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    parser = argparse.ArgumentParser(description="반려동물 돌봄 로컬 실행")
    parser.add_argument("--demo", action="store_true", help="운영 데이터와 분리한 시연 환경")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    port = args.port or (8001 if args.demo else 8000)
    if not 1024 <= port <= 65535:
        parser.error("1024–65535 포트만 사용할 수 있습니다.")
    os.environ["CARE_DEMO_MODE"] = "1" if args.demo else "0"
    os.environ.setdefault("DJANGO_DEBUG", "1")
    os.environ.setdefault("APP_ORIGIN", f"http://127.0.0.1:{port}")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    if args.demo:
        os.environ.setdefault("CARE_DATA_DIR", str(ROOT / "tmp" / "local-demo"))
    import django
    django.setup()
    from django.core.management import call_command
    call_command("check")
    call_command("migrate", interactive=False, verbosity=0)
    call_command("seed_references")
    if args.demo:
        call_command("seed_demo")
    call_command("collectstatic", interactive=False, verbosity=0)
    from config.wsgi import application
    from waitress import serve
    import json
    runtime = ROOT / "tmp" / "handoff"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / ("server_demo.json" if args.demo else "server_local.json")).write_text(json.dumps({"pid": os.getpid(), "port": port, "mode": "demo" if args.demo else "local"}), encoding="utf-8")
    print(f"LOCAL {'DEMO' if args.demo else 'APP'}: http://127.0.0.1:{port}", flush=True)
    print("Google sign-in needs real OAuth credentials; demo identities are not Google authentication.", flush=True)
    serve(application, host="127.0.0.1", port=port, threads=4, max_request_body_size=262144, expose_tracebacks=False)

if __name__ == "__main__":
    main()
