"""Check commit history or repository files without echoing matching values."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


TEXT_RULES = {
    "personal email": re.compile(
        r"[\w.+%-]+@(?:gmail\.com|naver\.com|daum\.net|hanmail\.net|outlook\.com|hotmail\.com|icloud\.com)\b", re.I
    ),
    "Korean mobile number": re.compile(
        r"(?<![a-zA-Z0-9])(?:0|\+82[- .]?)1[016789][- .]?\d{3,4}[- .]?\d{4}(?![a-zA-Z0-9])"
    ),
    "resident registration number": re.compile(
        r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-[1-8]\d{6}(?!\d)"
    ),
    "student identifier": re.compile(
        r"(?:학번|student[_ -]?id)\s*[:=：]?\s*\d{7,12}\b|(?<!\d)20\d{7}[_ -]+[가-힣]{2,5}(?![가-힣])", re.I
    ),
    "personal home directory": re.compile(r"(?:[A-Z]:[\\/]+Users[\\/]+|/Users/)[\w.][^/\\\s<>\"']+", re.I),
    "private key or access token": re.compile(
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|"
        r"(?i:\bvc[piark]_?[a-z0-9_-]{24,}\b|\bsb_secret_[a-z0-9_-]{20,})|"
        r"(?:gh[pousr]_|github_pat_|GOCSPX-|sk-(?:proj-)?)[A-Za-z0-9_-]{20,}|AIza[\w-]{35}"
    ),
    "excluded public wording": re.compile(
        r"\b\u0052\u0045\u0041\b|\uc2dc\ubc1c|\uc528\ubc1c|\ubcd1\uc2e0|\uc886", re.I
    ),
}


def git(*args):
    return subprocess.check_output(["git", *args])


def private_filename(filename):
    name = Path(filename).name.lower()
    return (
        name in {".env", "id_rsa", "id_ed25519", "credentials.json", ".local_secret"}
        or (name.startswith(".env.") and not name.endswith((".example", ".sample")))
        or name.endswith((".env", ".sqlite3", ".sqlite", ".dump", ".p12", ".pfx", ".key", ".pem"))
    )


def text_findings(text, filenames=()):
    findings = {name for name, rule in TEXT_RULES.items() if rule.search(text)}
    if any(private_filename(filename) for filename in filenames):
        findings.add("private configuration or database file")
    return findings


def commit_findings(commit):
    info = git("show", "-s", "--format=%an <%ae>%n%cn <%ce>%n%B", commit)
    parent_ids = git("show", "-s", "--format=%P", commit).split()
    base = parent_ids[0].decode() if parent_ids else subprocess.check_output(
        ["git", "hash-object", "-t", "tree", "--stdin"], input=b""
    ).decode().strip()
    diff_args = ["diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--diff-filter=AMT"]
    filenames = git(*diff_args, "--name-only", "-z", base, commit, "--").decode("utf-8", "replace").split("\0")
    patch = git(*diff_args, "--unified=0", base, commit, "--")
    additions = [line[1:] for line in patch.splitlines() if line.startswith(b"+") and not line.startswith(b"+++")]
    text = "\n".join(filenames) + "\n" + (info + b"\n".join(additions)).decode("utf-8", "replace")
    return text_findings(text, filenames)


def check_working_tree():
    """Inspect tracked and non-ignored files throughout the repository."""
    names = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode("utf-8", "replace").split("\0")
    failures, checked = 0, 0
    for filename in sorted(set(names)):
        if not filename:
            continue
        path = Path(filename)
        if not path.is_file() or path.is_symlink():
            continue
        checked += 1
        text = filename
        # Private data can appear in CSV, config, or extensionless files too.
        # Read UTF-16 exports explicitly; avoid treating binary assets as text.
        data = path.read_bytes()
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text += "\n" + data.decode("utf-16", errors="replace")
        elif b"\0" not in data:
            text += "\n" + data.decode("utf-8-sig", errors="replace")
        for finding in sorted(text_findings(text, [filename])):
            safe_name = "[redacted filename]" if text_findings(filename) else filename
            print(f"{safe_name}: {finding}")
            failures += 1
    print(f"Checked {checked} repository files; {failures} findings.")
    return int(bool(failures))


def revision_range(event, head):
    if "pull_request" in event:
        return event["pull_request"]["base"]["sha"], event["pull_request"]["head"]["sha"]
    base = event.get("before")
    if not base:
        # Manual runs inspect the selected commit without rescanning old history.
        parents = git("show", "-s", "--format=%P", head).decode().split()
        base = parents[0] if parents else None
    return base, head


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", nargs="?")
    parser.add_argument("head", nargs="?")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--working-tree", action="store_true")
    modes.add_argument("--history", metavar="SHA", help="check every ancestor of one full commit ID")
    modes.add_argument("--all", action="store_true", help="check commits reachable from all local refs")
    args = parser.parse_args(argv)
    if (args.working_tree or args.history or args.all) and (args.base or args.head):
        parser.error("explicit scan modes cannot be combined with base/head arguments")
    if args.working_tree:
        return check_working_tree()
    if args.history and not re.fullmatch(r"[0-9a-f]{40}", args.history):
        parser.error("expected a full commit ID for --history")
    if (args.all or args.history) and git("rev-parse", "--is-shallow-repository").strip() == b"true":
        parser.error("full history is required; fetch the missing history before scanning")
    if args.all:
        commits = git("rev-list", "--reverse", "--all").decode().split()
        return check_commits(commits)
    base, head = args.base, args.head
    if args.history:
        base, head = None, args.history
    if not head:
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        if not event_path:
            parser.error("provide base and head commit IDs, or use --working-tree")
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        base, head = revision_range(event, os.environ["GITHUB_SHA"])
    if not re.fullmatch(r"[0-9a-f]{40}", head) or (base and not re.fullmatch(r"[0-9a-f]{40}", base)):
        parser.error("expected full commit IDs")
    commits = git("rev-list", "--reverse", f"{base}..{head}" if base and base != "0" * 40 else head).decode().split()
    return check_commits(commits)


def check_commits(commits):
    """Report only classifications and commit IDs, including removed old data."""
    failures = 0
    for commit in commits:
        for finding in sorted(commit_findings(commit)):
            print(f"{commit[:12]}: {finding}")
            failures += 1
    print(f"Checked {len(commits)} commits; {failures} findings.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
