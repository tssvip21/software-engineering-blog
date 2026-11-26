"""Package, validate and stage PetNote; promote only with --promote.

Existing Vercel settings and secrets are never created, downloaded or changed.
Database migrations and reference-data writes are separate operator actions.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.parser import BytesParser
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy.source_files import application_files, safe_source_file

CLI_VERSION = "67.0.0"
ROOT_FILES = (".python-version", "manage.py", "requirements.txt", "requirements.lock", "vercel.json")
BUILD_FILES = ("deploy/vercel_build.py",)


class DeploymentError(RuntimeError):
    pass


def cli_command():
    command = shutil.which("vercel.cmd" if os.name == "nt" else "vercel")
    if command:
        return [command]
    command = shutil.which("npx.cmd" if os.name == "nt" else "npx")
    if not command:
        raise DeploymentError("Install Node.js or the Vercel CLI before deploying.")
    # Avoid Windows cmd AutoRun output when Node's installed npm entry is available.
    npm_entry = Path(command).parent / "node_modules/npm/bin/npx-cli.js"
    node = shutil.which("node")
    if node and npm_entry.is_file():
        return [node, str(npm_entry), "--yes", f"vercel@{CLI_VERSION}"]
    return [command, "--yes", f"vercel@{CLI_VERSION}"]


def redact_diagnostic(output):
    text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output or "")
    # Build tools occasionally echo connection strings, headers or environment values.
    text = re.sub(r"(?i)\b(?:postgres(?:ql)?|mysql|redis|amqps?)://[^\s\"'<>]+", "[redacted connection URL]", text)
    text = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", text)
    text = re.sub(
        r"(?im)(\b(?:[A-Z0-9_]*(?:SECRET|PASSWORD|TOKEN|PRIVATE_KEY|API_KEY|DATABASE_URL)[A-Z0-9_]*"
        r"|Authorization|Cookie|Set-Cookie|x-vercel-protection-bypass)\b[\"']?\s*[:=]\s*).*$",
        r"\1[redacted]", text,
    )
    text = re.sub(
        r"(?i)([?&](?:code|state|token|id_token|access_token|refresh_token|client_secret"
        r"|code_verifier|x-vercel-protection-bypass)=)[^&\s\"']+", r"\1[redacted]", text,
    )
    text = re.sub(r"\b(?:vcp_|gh[pousr]_)[A-Za-z0-9_]{16,}\b", "[redacted token]", text)
    text = re.sub(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b", "[redacted token]", text)
    return text[-131072:]


def save_cli_diagnostic(directory, command, stdout, stderr, exit_status):
    # In particular, never persist API/environment responses or authenticated curl output.
    if directory is None or command not in {"deploy", "build"}:
        return None
    directory = Path(directory).resolve()
    if not directory.is_relative_to((ROOT / "tmp").resolve()):
        raise DeploymentError("Deployment diagnostics must stay inside the application's tmp directory.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{command}-{stamp}-{uuid4().hex[:8]}.log"
    content = (f"Vercel {command}; exit={exit_status}\n"
               f"STDOUT (redacted):\n{redact_diagnostic(stdout)}\n"
               f"STDERR (redacted):\n{redact_diagnostic(stderr)}\n")
    with path.open("x", encoding="utf-8") as log:
        log.write(content)
    path.chmod(0o600)
    return path


def run_cli(cli, arguments, *, cwd=ROOT, timeout=900, diagnostic_dir=None):
    try:
        result = subprocess.run([*cli, *arguments], cwd=cwd, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        log = save_cli_diagnostic(diagnostic_dir, arguments[0], error.stdout, error.stderr, "timeout")
        detail = f" Local diagnostic: {log}" if log else ""
        raise DeploymentError(f"Vercel {arguments[0]} timed out.{detail}") from None
    except OSError as error:
        raise DeploymentError(f"Vercel {arguments[0]} could not start ({type(error).__name__}).") from None
    if result.returncode:
        log = save_cli_diagnostic(diagnostic_dir, arguments[0], result.stdout, result.stderr, result.returncode)
        detail = f" Local diagnostic: {log}" if log else " Inspect its dashboard logs."
        raise DeploymentError(f"Vercel {arguments[0]} failed (exit {result.returncode}).{detail}")
    return result.stdout


def parse_json_output(output):
    text = output.decode("utf-8", errors="replace")
    # Some Windows cmd AutoRun configurations print an unrelated line before JSON.
    start = text.find("{")
    try:
        result, tail = json.JSONDecoder().raw_decode(text[start:]) if start >= 0 else (None, 0)
    except ValueError:
        result = None
    if not isinstance(result, dict):
        raise DeploymentError("Vercel returned an unexpected JSON response.")
    return result


def deployment_url(value):
    value = str(value or "")
    if not value.startswith("https://"):
        value = "https://" + value
    parsed = urlsplit(value)
    if (not parsed.hostname or not parsed.hostname.endswith(".vercel.app")
            or parsed.username or parsed.password or parsed.port
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise DeploymentError("Vercel returned an unexpected deployment URL.")
    return value.rstrip("/")


def public_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise DeploymentError("The public origin must be a single HTTPS origin.")
    return value.rstrip("/")


def package_sources(destination, *, root=None):
    root = ROOT if root is None else Path(root)
    wrapper = safe_source_file(root, "requirements.txt").read_text(encoding="utf-8")
    directives = [line.strip() for line in wrapper.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if directives != ["-r requirements.lock"]:
        raise DeploymentError("Vercel packaging requires requirements.txt to reference only requirements.lock.")
    requirements = safe_source_file(root, "requirements.lock").read_bytes()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    paths = sorted(set(application_files(root)) | {Path(name) for name in (*ROOT_FILES, *BUILD_FILES)},
                   key=lambda path: path.as_posix())
    manifest = {}
    for relative in paths:
        source = safe_source_file(root, relative)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # Vercel native discovery does not reliably resolve nested -r files.
        if relative == Path("requirements.txt"):
            target.write_bytes(requirements)
        else:
            shutil.copyfile(source, target)
        manifest[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    return manifest


def api(cli, path, scope):
    return parse_json_output(run_cli(cli, ["api", path, "--method", "GET", "--scope", scope, "--raw"]))


def project_state(cli, project, scope, origin):
    info = api(cli, "/v9/projects/" + quote(project, safe=""), scope)
    if info.get("paused"):
        raise DeploymentError("The existing project is paused; resolve the pause before deploying.")
    project_id = info.get("id", "")
    if not re.fullmatch(r"prj_[A-Za-z0-9]+", project_id):
        raise DeploymentError("Could not verify the existing project.")
    if info.get("rootDirectory") not in (None, "", "."):
        raise DeploymentError("This source package requires an application-root project with no Root Directory override.")
    current = info.get("targets", {}).get("production", {})
    previous = deployment_url(current.get("url"))
    domains = api(cli, f"/v9/projects/{project_id}/domains?limit=100", scope)
    if urlsplit(origin).hostname not in {item.get("name") for item in domains.get("domains", [])}:
        raise DeploymentError("The requested public origin does not belong to this Vercel project.")
    return project_id, previous


def parse_http_output(output):
    match = re.search(rb"(?:^|\r?\n)(HTTP/\S+ [0-9]{3}[^\r\n]*)\r?\n", output)
    if not match:
        raise DeploymentError("The deployment probe returned no HTTP response.")
    output = output[match.start(1):]
    while True:
        separator = re.search(rb"\r?\n\r?\n", output)
        if not separator:
            raise DeploymentError("The deployment probe returned incomplete HTTP headers.")
        header, body = output[:separator.start()], output[separator.end():]
        lines = header.splitlines()
        status = int(lines[0].split()[1])
        headers = {key.lower(): value for key, value in BytesParser().parsebytes(b"\r\n".join(lines[1:])).items()}
        if body.startswith(b"HTTP/"):
            output = body
            continue
        return status, headers, body


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_public(origin, path):
    request = Request(origin + path, headers={"User-Agent": "PetNote-Deployment-Check/1.0", "Accept-Encoding": "identity"})
    try:
        with build_opener(NoRedirects).open(request, timeout=30) as response:
            body = response.read(2_097_153)
            if len(body) > 2_097_152:
                raise DeploymentError("A public response exceeded the smoke-test size limit.")
            return response.status, {key.lower(): value for key, value in response.headers.items()}, body
    except HTTPError as error:
        return error.code, {}, b""
    except OSError:
        raise DeploymentError("Could not reach the public HTTPS origin.") from None


class Stylesheets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and "stylesheet" in attrs.get("rel", "").split():
            href = attrs.get("href", "")
            parsed = urlsplit(href)
            if not parsed.scheme and not parsed.netloc and parsed.path.startswith("/static/"):
                self.paths.append(href)


def smoke(fetch, source):
    checks = []
    health = fetch("/health/")
    if health[0] != 200:
        raise DeploymentError(f"Health check returned HTTP {health[0]}.")
    try:
        healthy = json.loads(health[2]).get("status") == "ok"
    except (ValueError, AttributeError):
        healthy = False
    if not healthy or "no-store" not in health[1].get("cache-control", ""):
        raise DeploymentError("Health check did not confirm database readiness and private caching.")
    checks.append({"path": "/health/", "status": 200})
    status, headers, body = fetch("/")
    if status != 200 or "text/html" not in headers.get("content-type", "") or "펫노트".encode() not in body:
        raise DeploymentError(f"Homepage validation failed (HTTP {status}).")
    required = {"x-content-type-options": "nosniff", "x-frame-options": "DENY"}
    if any(headers.get(key) != value for key, value in required.items()):
        raise DeploymentError("Homepage security headers are missing.")
    if ("no-store" not in headers.get("cache-control", "")
            or "private" not in headers.get("cache-control", "")
            or "max-age=" not in headers.get("strict-transport-security", "")
            or "frame-ancestors 'none'" not in headers.get("content-security-policy", "")):
        raise DeploymentError("Homepage caching, HTTPS or content-security policy is incomplete.")
    checks.append({"path": "/", "status": status, "security_headers": True})
    parser = Stylesheets()
    parser.feed(body.decode("utf-8"))
    if not parser.paths:
        raise DeploymentError("Homepage has no bundled stylesheet.")
    path = parser.paths[0]
    relative = Path(urlsplit(path).path.lstrip("/"))
    local = safe_source_file(source, relative)
    status, headers, body = fetch(path)
    if status != 200 or "text/css" not in headers.get("content-type", ""):
        raise DeploymentError(f"Stylesheet validation failed (HTTP {status}).")
    digest = hashlib.sha256(body).hexdigest()
    if digest != hashlib.sha256(local.read_bytes()).hexdigest():
        raise DeploymentError("The deployment is not serving the packaged stylesheet.")
    checks.append({"path": path, "status": status, "sha256": digest})
    return checks


def source_commit(root):
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.decode("ascii", errors="ignore").strip()
    return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", value) else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="petnote")
    parser.add_argument("--scope", default="petnote")
    parser.add_argument("--origin", default="https://petnote.duckdns.org")
    parser.add_argument("--promote", action="store_true", help="Promote the verified staged production deployment.")
    parser.add_argument("--package-only", action="store_true", help="Create the local source package without contacting Vercel.")
    parser.add_argument("--output", type=Path, help="New result directory below this application's tmp directory.")
    args = parser.parse_args(argv)
    if args.package_only and args.promote:
        parser.error("--package-only cannot be combined with --promote")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or ROOT / "tmp/deploy/vercel" / f"{stamp}-{uuid4().hex[:8]}").resolve()
    if not output.is_relative_to((ROOT / "tmp").resolve()):
        parser.error("--output must stay inside the application's tmp directory")
    output.mkdir(parents=True, exist_ok=False)
    manifest_path = output / "release.json"
    manifest = {"created_utc": stamp, "project": args.project, "scope": args.scope,
                "status": "preparing", "database_changes": False, "git_commit": source_commit(ROOT)}
    try:
        origin = public_origin(args.origin)
        source = output / "source"
        manifest["sources"] = package_sources(source)
        manifest["origin"] = origin
        manifest["status"] = "packaged"
        if not args.package_only:
            cli = cli_command()
            project_id, previous = project_state(cli, args.project, args.scope, origin)
            manifest.update(project_id=project_id, previous_deployment=previous,
                            rollback_command=[*cli, "rollback", previous, "--scope", args.scope, "--yes"],
                            rollback_cwd=str(source))
            manifest["status"] = "linking"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            # curl resolves automatic deployment protection through this local project link.
            run_cli(cli, ["link", "--project", args.project, "--scope", args.scope, "--yes"],
                    cwd=source, timeout=60)
            arguments = ["deploy", "--prod", "--skip-domain", "--yes", "--project", project_id, "--scope", args.scope]
            manifest["status"] = "deploying"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            result = run_cli(cli, arguments, cwd=source, diagnostic_dir=output / "logs")
            urls = re.findall(rb"(?m)^https://[A-Za-z0-9.-]+\.vercel\.app/?\s*$", result)
            if len(urls) != 1:
                raise DeploymentError("Could not identify the new deployment URL.")
            staged = deployment_url(urls[0].decode().strip())
            manifest.update(staged_deployment=staged, status="staged")
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

            def fetch_staged(path):
                data = run_cli(cli, ["curl", path, "--deployment", staged,
                                    "--scope", args.scope, "--", "--silent", "--show-error", "--compressed",
                                    "--max-time", "30", "--max-filesize", "2097152", "--include"], cwd=source, timeout=60)
                return parse_http_output(data)

            manifest["staged_checks"] = smoke(fetch_staged, source)
            manifest["status"] = "verified_staged"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            if args.promote:
                manifest["status"] = "promoting"
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                run_cli(cli, ["promote", staged, "--scope", args.scope, "--yes"], cwd=source, timeout=240)
                manifest["status"] = "promoted_pending_verification"
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                manifest["public_checks"] = smoke(lambda path: fetch_public(origin, path), source)
                active = api(cli, f"/v9/projects/{project_id}", args.scope)
                if deployment_url(active.get("targets", {}).get("production", {}).get("url")) != staged:
                    raise DeploymentError("The project has not confirmed the promoted deployment as current.")
                manifest["status"] = "promoted"
        print(f"Release state: {manifest['status']}")
        return 0
    except (DeploymentError, ValueError, OSError) as error:
        manifest["failed_at"] = manifest["status"]
        manifest["status"] = "failed"
        manifest["error"] = str(error) if isinstance(error, DeploymentError) else type(error).__name__
        print(f"Deployment stopped: {manifest['error']}", file=sys.stderr)
        return 1
    finally:
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Release manifest: {manifest_path}")


if __name__ == "__main__":
    raise SystemExit(main())
