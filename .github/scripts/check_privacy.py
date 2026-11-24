"""Check new Git history for common personal identifiers and exposed credentials."""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path


TEXT_RULES = {
    "personal email": re.compile(
        r"[\w.+%-]+@(?:gmail\.com|naver\.com|daum\.net|hanmail\.net|outlook\.com|hotmail\.com|icloud\.com)\b",
        re.IGNORECASE,
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
        r"(?:gh[pousr]_|github_pat_|GOCSPX-|sk-(?:proj-)?)[A-Za-z0-9_-]{20,}|AIza[\w-]{35}"
    ),
}


def git(*args):
    return subprocess.check_output(["git", *args])


def commit_findings(commit):
    """Report categories only so a failing CI log never echoes a matching value."""
    info = git("show", "-s", "--format=%an <%ae>%n%cn <%ce>%n%B", commit)
    parent_ids = git("show", "-s", "--format=%P", commit).split()
    base = parent_ids[0].decode() if parent_ids else subprocess.check_output(
        ["git", "hash-object", "-t", "tree", "--stdin"], input=b""
    ).decode().strip()
    diff_args = ["diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--diff-filter=AMT"]
    filenames = git(*diff_args, "--name-only", "-z", base, commit, "--").decode("utf-8", "replace").split("\0")
    patch = git(*diff_args, "--unified=0", base, commit, "--")
    lines = [line[1:] for line in patch.splitlines() if line.startswith(b"+")]
    text = "\n".join(filenames) + "\n" + (info + b"\n".join(lines)).decode("utf-8", "replace")
    findings = {name for name, rule in TEXT_RULES.items() if rule.search(text)}
    for filename in filenames:
        name = Path(filename).name.lower()
        if name in {".env", "id_rsa", "id_ed25519", "credentials.json"} or name.endswith((".sqlite3", ".p12", ".pfx")):
            findings.add("private configuration or database file")
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", nargs="?")
    parser.add_argument("head", nargs="?")
    args = parser.parse_args()
    base, head = args.base, args.head
    if not head:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        if "pull_request" in event:
            base, head = event["pull_request"]["base"]["sha"], event["pull_request"]["head"]["sha"]
        else:
            base, head = event.get("before"), os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", head) or (base and not re.fullmatch(r"[0-9a-f]{40}", base)):
        parser.error("expected full commit IDs")
    if base and base != "0" * 40:
        commits = git("rev-list", "--reverse", f"{base}..{head}").decode().split()
    else:
        commits = git("rev-list", "--reverse", head).decode().split()
    failures = 0
    for commit in commits:
        for finding in sorted(commit_findings(commit)):
            print(f"{commit[:12]}: {finding}")
            failures += 1
    print(f"Checked {len(commits)} commits; {failures} findings.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
