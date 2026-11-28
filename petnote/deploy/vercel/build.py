"""Validate the existing runtime and schema without modifying production data."""
import os
import subprocess
import sys


def main():
    if os.environ.get("VERCEL") != "1":
        raise SystemExit("Run this validation in the Vercel build environment.")
    # A failed or retried build must never apply schema or reference-data changes.
    # Apply planned migrations separately after checking a current backup.
    for arguments in (
        ("check", "--deploy", "--fail-level", "ERROR"),
        ("migrate", "--check", "--noinput"),
    ):
        subprocess.run([sys.executable, "manage.py", *arguments], check=True)


if __name__ == "__main__":
    main()
