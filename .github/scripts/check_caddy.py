"""Check production Caddy routes with disposable, synthetic upstreams only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
from uuid import uuid4

from check_compose import render

ROOT = Path(__file__).resolve().parents[2] / "petnote"
UPSTREAM = """
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body=json.dumps({
            "path": self.path,
            "proto": self.headers.get("X-Forwarded-Proto"),
            "client": self.headers.get("X-Forwarded-For"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
"""


def check_routes(backend_image: str) -> None:
    """Use the already built Python image; never connect to an application DB."""
    prefix = "petnote-ci-caddy-" + uuid4().hex[:12]
    network = prefix + "-net"
    created = []
    caddy_image = render("compose.production.yaml")["services"]["frontend"]["image"]

    def docker(*args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["docker", *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if check and result.returncode:
            raise RuntimeError(f"Docker fixture failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def response(frontend: str, path: str):
        fetch = """
import json, sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen
request=Request(sys.argv[1], headers={
    "X-Forwarded-For": "203.0.113.55",
    "X-Forwarded-Proto": "https",
})
try:
    result=urlopen(request, timeout=3)
except HTTPError as error:
    result=error
with result:
    print(json.dumps({
        "status": result.status,
        "headers": {key.lower(): value for key, value in result.headers.items()},
        "body": result.read().decode("utf-8"),
    }))
"""
        result = json.loads(docker("exec", upstream, "python", "-c", fetch, "http://" + frontend + path))
        return result["status"], result["headers"], result["body"].encode("utf-8")

    docker("network", "create", "--internal", network)
    try:
        upstream = prefix + "-upstream"
        docker(
            "run", "--rm", "-d", "--name", upstream, "--network", network,
            "--network-alias", "backend", "--network-alias", "demo",
            "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--memory", "128m", "--pids-limit", "64",
            "--entrypoint", "python", backend_image, "-c", UPSTREAM,
        )
        created.append(upstream)
        with tempfile.TemporaryDirectory(prefix="petnote-caddy-static-") as folder:
            (Path(folder) / "probe.css").write_text("body { color: #111; }\n", encoding="utf-8")
            for enabled in (False, True):
                frontend = prefix + ("-on" if enabled else "-off")
                docker(
                    "run", "--rm", "-d", "--name", frontend,
                    "--network", network,
                    "--read-only", "--tmpfs", "/tmp", "--tmpfs", "/data", "--tmpfs", "/config",
                    "--cap-drop", "ALL", "--cap-add", "NET_BIND_SERVICE",
                    "--security-opt", "no-new-privileges:true",
                    "--memory", "128m", "--pids-limit", "64",
                    "-e", "SITE_ADDRESS=http://:80",
                    "-e", "CARE_PUBLIC_DEMO_ENTRY=" + str(int(enabled)),
                    "-v", f"{ROOT / 'deploy/Caddyfile'}:/etc/caddy/Caddyfile:ro",
                    "-v", f"{folder}:/srv/static:ro", caddy_image,
                )
                created.append(frontend)
                deadline = time.monotonic() + 30
                while True:
                    try:
                        status, headers, body = response(frontend, "/health/")
                        if status == 200:
                            break
                    except RuntimeError:
                        pass
                    if time.monotonic() >= deadline:
                        raise AssertionError("Isolated Caddy fixture failed to become ready.")
                    time.sleep(0.25)
                echo = json.loads(body)
                assert echo["path"] == "/health/"
                assert echo["proto"] == "http", "Caddy trusted a forged protocol header"
                assert echo["client"] and "203.0.113.55" not in echo["client"]
                status, headers, body = response(frontend, "/static/probe.css")
                assert status == 200 and body.startswith(b"body")
                assert headers.get("x-content-type-options") == "nosniff"
                status, _, body = response(frontend, "/try/health/?probe=1")
                if enabled:
                    assert status == 200
                    echo = json.loads(body)
                    assert echo["path"] == "/health/?probe=1"
                    assert echo["proto"] == "http"
                    assert echo["client"] and "203.0.113.55" not in echo["client"]
                else:
                    assert status == 404, "Disabled public demo must return 404, not a failed upstream"
                docker("rm", "-f", frontend)
                created.remove(frontend)
        print("Caddy routes passed: production proxy, static file, forwarded headers, optional demo disabled/enabled.")
    finally:
        for container in reversed(created):
            docker("rm", "-f", container, check=False)
        docker("network", "rm", network, check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-image", required=True)
    check_routes(parser.parse_args().backend_image)
