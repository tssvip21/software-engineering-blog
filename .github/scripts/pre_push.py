#!/usr/bin/env python3
"""Local pre-push checks for the exact, clean commit being uploaded."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import signal
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

GITLEAKS_VERSION = "8.30.1"
GITLEAKS_WINDOWS_SHA256 = "17157e2ee8b76fc8b1d8bee607a250e34b8a8023c8bc81822d4b5ee4d78fcb7c"
MAX_INPUT_BYTES = 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}")
ZERO = "0" * 40
HOOK_PATH = ".githooks"


class GateError(Exception):
    """A failure whose message is safe to show without command output."""


@dataclass(frozen=True)
class PushRef:
    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str


def command_env():
    repository_overrides = {
        "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_PREFIX",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith("GITLEAKS_") and key.upper() not in repository_overrides
    }
    env.update({"GIT_NO_REPLACE_OBJECTS": "1", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def run_process(command, root, *, label="Git inspection", timeout=30, accepted=(0,), env=None):
    """Capture potentially sensitive output; show only a fixed check label."""
    try:
        process = subprocess.Popen(
            [str(value) for value in command], cwd=root, env=env or command_env(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        try:
            stdout, _stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.kill()
            process.communicate()
            raise GateError(f"{label} timed out; upload blocked.") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise GateError(f"{label} could not run; verify the required local tool.") from error
    if process.returncode not in accepted:
        raise GateError(f"{label} failed; upload blocked. Raw output is withheld for privacy.")
    return stdout, process.returncode


def git(root, *args, accepted=(0,)):
    return run_process(["git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", *args],
                       root, accepted=accepted)[0]


def config_get(root, key, *, scope=None):
    args = ["config", *([scope] if scope else []), "--get", key]
    output, code = run_process(["git", *args], root, accepted=(0, 1))
    return output.decode("utf-8").strip() if code == 0 else None


def repository_root():
    root = Path(__file__).resolve().parents[2]
    actual = Path(git(root, "rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
    if actual != root:
        raise GateError("Run this gate from its own repository checkout.")
    return root


def parse_updates(data):
    if len(data) > MAX_INPUT_BYTES:
        raise GateError("Push instructions exceed the allowed size.")
    try:
        text = data.decode("utf-8")
    except UnicodeError as error:
        raise GateError("Push instructions are not valid UTF-8.") from error
    if not text:
        return []
    updates, remote_refs = [], set()
    for line in text.removesuffix("\n").split("\n"):
        parts = line.split(" ")
        if len(parts) != 4 or any(not part or any(ord(char) < 33 or ord(char) == 127 for char in part)
                                  for part in parts):
            raise GateError("Malformed push instructions: expected four fields.")
        local_ref, local_sha, remote_ref, remote_sha = parts
        if not SHA.fullmatch(local_sha) or not SHA.fullmatch(remote_sha):
            raise GateError("Malformed push instructions: expected full commit IDs.")
        if not remote_ref.startswith("refs/") or remote_ref in remote_refs:
            raise GateError("Malformed or repeated destination ref.")
        if (local_sha == ZERO) != (local_ref == "(delete)"):
            raise GateError("Malformed deletion instructions.")
        remote_refs.add(remote_ref)
        updates.append(PushRef(local_ref, local_sha, remote_ref, remote_sha))
    return updates


def assert_clean(root):
    if git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"):
        raise GateError("Commit or remove staged, unstaged, and non-ignored untracked changes before uploading.")
    for record in git(root, "ls-files", "-v", "-z").split(b"\0"):
        if record and (record[:1].islower() or record[:1] == b"S"):
            raise GateError("A full checkout without assume-unchanged or skip-worktree entries is required.")


def snapshot(root):
    """Include file content and metadata to notice edits even if later restored."""
    assert_clean(root)
    head = git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()
    if not SHA.fullmatch(head):
        raise GateError("A full SHA-1 HEAD commit is required.")
    digest = hashlib.sha256()
    for entry in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        if metadata.startswith(b"160000 "):
            raise GateError("Submodules require a separate verified check before upload.")
        path = root / os.fsdecode(name)
        try:
            before = path.lstat()
            if stat.S_ISLNK(before.st_mode):
                data = os.fsencode(os.readlink(path))
            elif stat.S_ISREG(before.st_mode):
                data = path.read_bytes()
            else:
                raise GateError("An unsupported tracked file prevents verification.")
            after = path.lstat()
        except OSError as error:
            raise GateError("A tracked source file changed or could not be read.") from error
        state = (before.st_mode, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        if state != (after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise GateError("A tracked source file changed during verification.")
        digest.update(entry + b"\0" + repr(state).encode("ascii") + hashlib.sha256(data).digest())
    assert_clean(root)
    if git(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip() != head:
        raise GateError("HEAD changed during verification.")
    return head, digest.digest()


def executable_path(value, label):
    if not value or "\n" in value or "\r" in value:
        raise GateError(f"{label} is not configured; install the local gate first.")
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise GateError(f"{label} must point to an existing absolute executable path.")
    # Resolving a virtualenv Python symlink would silently select the base interpreter.
    return path.absolute()


def verify_tools(root, python, gitleaks):
    output, _ = run_process([python, "-c", "print('petnote-check-python')"], root, label="Python verification")
    if output.strip() != b"petnote-check-python":
        raise GateError("The configured Python executable could not be verified.")
    if os.name == "nt" and hashlib.sha256(gitleaks.read_bytes()).hexdigest() != GITLEAKS_WINDOWS_SHA256:
        raise GateError("The Gitleaks executable does not match the verified Windows release.")
    output, _ = run_process([gitleaks, "version"], root, label="Gitleaks verification")
    if output.decode("ascii", errors="replace").strip() != GITLEAKS_VERSION:
        raise GateError(f"Gitleaks {GITLEAKS_VERSION} is required; upload blocked.")


def run_check(root, label, command, timeout):
    print(f"[pre-push] Running {label}...", flush=True)
    run_process(command, root, label=label, timeout=timeout)
    print(f"[pre-push] {label} passed.", flush=True)


def check_head(root, updates=None):
    before = snapshot(root)
    head = before[0]
    if updates is not None:
        for update in updates:
            git(root, "check-ref-format", update.remote_ref)
            if update.local_sha != ZERO and update.local_sha != head:
                raise GateError("Only the checked-out HEAD commit can be uploaded; switch to the intended commit first.")
    if git(root, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise GateError("Complete Git history is required; fetch missing history before uploading.")
    grafts = Path(git(root, "rev-parse", "--git-path", "info/grafts").decode("utf-8").strip())
    if not grafts.is_absolute():
        grafts = root / grafts
    if grafts.exists() and grafts.stat().st_size:
        raise GateError("History grafts must be removed before a complete history check.")
    python = executable_path(config_get(root, "petnote.checkPython"), "Python")
    gitleaks = executable_path(config_get(root, "petnote.gitleaks"), "Gitleaks")
    verify_tools(root, python, gitleaks)
    privacy = root / ".github/scripts/check_privacy.py"
    checks = root / "petnote/scripts/check.py"
    if not privacy.is_file() or not checks.is_file():
        raise GateError("Required repository checks are missing.")
    with TemporaryDirectory(prefix="petnote-pre-push-") as directory:
        config = Path(directory) / "gitleaks.toml"
        ignore = Path(directory) / ".gitleaksignore"
        config.write_text("[extend]\nuseDefault = true\n", encoding="utf-8")
        ignore.write_text("", encoding="utf-8")
        commands = [
            ("History privacy check", [python, privacy, "--history", head], 600),
            ("Working-tree privacy check", [python, privacy, "--working-tree"], 300),
            ("Gitleaks history check", [
                gitleaks, "git", str(root), f"--log-opts={head}", "--redact=100",
                "--max-decode-depth=5", "--max-archive-depth=2", "--ignore-gitleaks-allow",
                "--config", config, "--gitleaks-ignore-path", ignore,
                "--no-banner", "--no-color", "--timeout=600",
            ], 630),
            ("Application checks", [
                python, checks, "--output", root / "petnote/tmp/pre-push/checks",
            ], 1800),
        ]
        for label, command, timeout in commands:
            run_check(root, label, command, timeout)
            if snapshot(root) != before:
                raise GateError("HEAD or tracked sources changed during checks; commit the final state and run again.")
    print("[pre-push] All checks passed for the current clean HEAD.", flush=True)


def install(root, python_value, gitleaks_value):
    """Change only this worktree's hook/tool settings; preserve existing hooks."""
    expected = (root / HOOK_PATH).resolve()
    if not (expected / "pre-push").is_file():
        raise GateError("The repository pre-push hook is missing.")
    configured = config_get(root, "core.hooksPath")
    if configured is not None:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.resolve() != expected:
            raise GateError("Another core.hooksPath is configured; installation left it unchanged.")
    else:
        common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").decode("utf-8").strip())
        if (common / "hooks/pre-push").exists() or (common / "hooks/pre-push").is_symlink():
            raise GateError("An existing default pre-push hook was preserved; installation stopped.")
    if config_get(root, "core.worktree", scope="--local") is not None:
        raise GateError("Shared core.worktree needs manual migration before worktree-scoped installation.")
    if config_get(root, "core.bare", scope="--local") not in (None, "false"):
        raise GateError("A non-bare working repository is required for installation.")
    python = executable_path(python_value, "Python")
    gitleaks = executable_path(gitleaks_value, "Gitleaks")
    verify_tools(root, python, gitleaks)
    if config_get(root, "extensions.worktreeConfig") != "true":
        common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").decode("utf-8").strip())
        if (common / "config.worktree").exists() or any((common / "worktrees").glob("*/config.worktree")):
            raise GateError("Dormant worktree config exists; inspect it before enabling worktreeConfig.")
    # Enable Git's documented per-worktree config before writing hook/tool paths.
    git(root, "config", "--local", "extensions.worktreeConfig", "true")
    git(root, "config", "--worktree", "petnote.checkPython", str(python))
    git(root, "config", "--worktree", "petnote.gitleaks", str(gitleaks))
    git(root, "config", "--worktree", "core.hooksPath", HOOK_PATH)
    print("[pre-push] Installed for this worktree only. Run this script with --check to verify HEAD.", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="check HEAD without uploading anything")
    mode.add_argument("--install", action="store_true", help="enable the hook for this worktree only")
    parser.add_argument("--python", help="absolute Python executable for --install")
    parser.add_argument("--gitleaks", help=f"absolute verified Gitleaks {GITLEAKS_VERSION} executable for --install")
    args = parser.parse_args(argv)
    if args.install and (not args.python or not args.gitleaks):
        parser.error("--install requires --python and --gitleaks")
    if not args.install and (args.python or args.gitleaks):
        parser.error("--python and --gitleaks can only be used with --install")
    try:
        root = repository_root()
        if args.install:
            install(root, args.python, args.gitleaks)
        elif args.check:
            check_head(root)
        else:
            updates = parse_updates(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
            for update in updates:
                git(root, "check-ref-format", update.remote_ref)
            if any(update.local_sha != ZERO for update in updates):
                check_head(root, updates)
            else:
                print("[pre-push] No commit upload to check.", flush=True)
        return 0
    except (GateError, OSError, UnicodeError) as error:
        message = str(error) if isinstance(error, GateError) else "Local verification could not complete."
        print(f"[pre-push] BLOCKED: {message}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print("[pre-push] BLOCKED: Verification interrupted.", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
