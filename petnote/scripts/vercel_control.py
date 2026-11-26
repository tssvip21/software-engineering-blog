"""Control only the existing PetNote Vercel project using its saved CLI login."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy.deploy_vercel import CLI_VERSION, cli_command

PROJECT_NAME = SCOPE = "petnote"
PUBLIC_ORIGIN = "https://petnote.duckdns.org"
LINK_FILE = ROOT / ".vercel" / "project.json"


class ControlError(RuntimeError):
    """Only fixed, safe messages cross the CLI/UI boundary."""

    def __init__(self, message, *, kind="request"):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class ServerState:
    paused: bool
    health_status: int | None
    public_paused: bool
    production_id: str

    @property
    def confirmed(self):
        if self.paused:
            return self.health_status == 503 and self.public_paused
        return self.health_status == 200 and not self.public_paused


def find_cli():
    """Reuse the pinned installed CLI; npx is only a repair fallback."""
    node = shutil.which("node")
    if node:
        bases = []
        if os.environ.get("LOCALAPPDATA"):
            bases.append(Path(os.environ["LOCALAPPDATA"]) / "npm-cache")
        if os.environ.get("APPDATA"):
            bases.append(Path(os.environ["APPDATA"]) / "npm-cache")
        bases.append(Path.home() / ".npm")
        for base in bases:
            for package in sorted((base / "_npx").glob("*/node_modules/vercel/package.json")):
                try:
                    data = json.loads(package.read_text(encoding="utf-8"))
                    if not isinstance(data, dict) or data.get("name") != "vercel" or data.get("version") != CLI_VERSION:
                        continue
                    bins = data.get("bin")
                    if not isinstance(bins, dict) or not isinstance(bins.get("vercel"), str):
                        continue
                    entry = (package.parent / bins["vercel"]).resolve()
                    if entry.is_relative_to(package.parent.resolve()) and entry.is_file():
                        return [node, str(entry)]
                except (OSError, ValueError, KeyError, TypeError):
                    continue
    try:
        return cli_command()
    except RuntimeError:
        raise ControlError("Vercel 실행 도구를 찾지 못했습니다. Node.js 설치를 확인해 주세요.", kind="setup") from None


def child_environment():
    env = os.environ.copy()
    # The saved CLI session is the only credential source for this desktop tool.
    for key in ("VERCEL_TOKEN", "VERCEL_ORG_ID", "VERCEL_PROJECT_ID", "FORCE_COLOR"):
        env.pop(key, None)
    env.update({"NO_COLOR": "1", "CI": "1", "VERCEL_TELEMETRY_DISABLED": "1"})
    return env


def classify_cli_error(stdout, stderr):
    # Classify in memory; never display or persist raw API responses or CLI errors.
    output = (stdout + b"\n" + stderr).decode("utf-8", errors="replace").lower()
    if any(word in output for word in (
        "no existing credentials", "not authenticated", "invalid_token",
        "token has expired", "token is invalid", "token is not valid",
        "authentication required", "login required", "vercel login", "401",
    )):
        return ControlError("저장된 Vercel 인증을 사용할 수 없습니다. 'Vercel 다시 연결'을 눌러 주세요.", kind="auth")
    if any(word in output for word in ("403", "forbidden", "not authorized", "permission")):
        return ControlError("현재 Vercel 계정에 펫노트 관리 권한이 없습니다.", kind="permission")
    if "429" in output or "rate limit" in output:
        return ControlError("Vercel 요청이 잠시 제한됐습니다. 잠시 후 다시 확인해 주세요.", kind="rate_limit")
    return ControlError("Vercel에 연결하지 못했습니다. 인터넷 연결을 확인한 뒤 다시 시도해 주세요.")


@contextmanager
def operation_lock():
    """Serialize mutations across desktop and command-line instances."""
    lock_dir = ROOT / "tmp" / "vercel-manager"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / "operation.lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ControlError("다른 창에서 서버를 변경하고 있습니다. 완료 후 다시 확인해 주세요.", kind="busy") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def public_health():
    request = Request(
        PUBLIC_ORIGIN + "/health/",
        method="HEAD",
        headers={"User-Agent": "PetNote-Server-Manager/1.0", "Cache-Control": "no-cache"},
    )
    try:
        with build_opener(NoRedirects).open(request, timeout=8) as response:
            return response.status, response.headers.get("x-vercel-error", "") == "DEPLOYMENT_PAUSED"
    except HTTPError as error:
        with error:
            return error.code, error.headers.get("x-vercel-error", "") == "DEPLOYMENT_PAUSED"
    except (URLError, OSError, TimeoutError):
        return None, False


class VercelController:
    def __init__(self, cli=None, *, runner=None, probe=None, clock=None, sleeper=None):
        try:
            link = json.loads(LINK_FILE.read_text(encoding="utf-8-sig"))
            self.project_id = link["projectId"]
            self.team_id = link["orgId"]
            if not (re.fullmatch(r"prj_[A-Za-z0-9]+", self.project_id)
                    and re.fullmatch(r"team_[A-Za-z0-9]+", self.team_id)):
                raise ValueError
        except (OSError, ValueError, TypeError, KeyError):
            raise ControlError("펫노트 프로젝트 연결을 확인해 주세요. 배포와 운영 문서의 연결 방법을 참고하세요.", kind="setup") from None
        query = f"?teamId={self.team_id}"
        self.project_path = f"/v9/projects/{self.project_id}{query}"
        self.action_paths = {
            True: f"/v1/projects/{self.project_id}/pause{query}",
            False: f"/v1/projects/{self.project_id}/unpause{query}",
        }
        self.allowed_requests = {("GET", self.project_path), *(("POST", path) for path in self.action_paths.values())}
        self.cli = list(cli) if cli is not None else find_cli()
        self.runner = runner or subprocess.run
        self.probe = probe or public_health
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep

    def _api(self, method, path):
        if (method, path) not in self.allowed_requests:
            raise ControlError("허용되지 않은 관리 요청입니다.", kind="target")
        command = [
            *self.cli, "api", path, "--method", method, "--scope", SCOPE,
            "--non-interactive", "--raw", "--no-color",
        ]
        try:
            completed = self.runner(
                command, cwd=ROOT, env=child_environment(), stdin=subprocess.DEVNULL,
                capture_output=True, timeout=45,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            raise ControlError("Vercel 응답이 지연되고 있습니다. '상태 확인'으로 현재 상태를 확인해 주세요.", kind="timeout") from None
        except OSError:
            raise ControlError("Vercel 실행 도구를 시작하지 못했습니다. Node.js 설치를 확인해 주세요.", kind="setup") from None
        if completed.returncode:
            raise classify_cli_error(completed.stdout, completed.stderr)
        try:
            result = json.loads(completed.stdout.decode("utf-8-sig"))
        except (UnicodeError, ValueError):
            raise ControlError("Vercel 상태 응답을 확인할 수 없습니다. 잠시 후 다시 확인해 주세요.", kind="response") from None
        if not isinstance(result, dict) or "error" in result:
            raise ControlError("Vercel 요청을 완료하지 못했습니다. 잠시 후 다시 확인해 주세요.", kind="response")
        return result

    def project(self):
        info = self._api("GET", self.project_path)
        if (info.get("id"), info.get("name"), info.get("accountId")) != (self.project_id, PROJECT_NAME, self.team_id):
            raise ControlError("연결된 프로젝트가 펫노트와 일치하지 않아 작업을 중단했습니다.", kind="target")
        if type(info.get("paused")) is not bool:
            raise ControlError("서버의 켜짐·꺼짐 상태를 확인할 수 없어 작업을 중단했습니다.", kind="response")
        targets = info.get("targets")
        production = targets.get("production") if isinstance(targets, dict) else None
        deployment_id = production.get("id") if isinstance(production, dict) else None
        if not isinstance(deployment_id, str) or not deployment_id.startswith("dpl_") or not deployment_id[4:].isalnum():
            raise ControlError("펫노트의 운영 배포를 찾지 못했습니다.", kind="response")
        return info["paused"], production["id"]

    def status(self):
        paused, production_id = self.project()
        code, is_paused = self.probe()
        return ServerState(paused, code, is_paused, production_id)

    def set_paused(self, desired, *, progress=None, timeout=120):
        if type(desired) is not bool:
            raise ValueError("desired must be a boolean")
        progress = progress or (lambda message: None)
        with operation_lock():
            current, _ = self.project()
            if current != desired:
                progress("서버 끄기를 요청하고 있습니다…" if desired else "서버 켜기를 요청하고 있습니다…")
                try:
                    self._api("POST", self.action_paths[desired])
                except ControlError as error:
                    # A timeout can happen after the mutation; reconcile without sending it twice.
                    if error.kind not in ("timeout", "request", "response"):
                        raise
                    progress("요청 결과를 다시 확인하고 있습니다…")
                    try:
                        current, _ = self.project()
                    except ControlError:
                        raise error from None
                    if current != desired:
                        raise error from None
            deadline = self.clock() + timeout
            while True:
                state = self.status()
                if state.paused == desired and state.confirmed:
                    return state
                if self.clock() >= deadline:
                    raise ControlError(
                        "변경 반영이 지연되고 있습니다. 요청을 반복하지 말고 잠시 후 '상태 확인'을 눌러 주세요.",
                        kind="pending",
                    )
                progress("공개 주소에 꺼짐 상태가 반영되는지 확인 중…" if desired else "공개 주소의 정상 접속을 확인 중…")
                self.sleeper(3)

    def reconnect(self):
        """Only an explicit user button opens the official interactive login."""
        try:
            return subprocess.Popen(
                [*self.cli, "login"], cwd=ROOT,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                env={k: v for k, v in child_environment().items() if k != "CI"},
            )
        except OSError:
            raise ControlError("Vercel 연결 창을 열지 못했습니다.", kind="setup") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="펫노트 공개 서버 켜기·끄기·상태 확인")
    parser.add_argument("action", choices=("status", "start", "stop"))
    args = parser.parse_args(argv)
    try:
        controller = VercelController()
        state = controller.status() if args.action == "status" else controller.set_paused(args.action == "stop")
        print(json.dumps({"ok": True, "confirmed": state.confirmed, **asdict(state)}, ensure_ascii=False))
        return 0 if state.confirmed else 2
    except ControlError as error:
        print(json.dumps({"ok": False, "kind": error.kind, "message": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
