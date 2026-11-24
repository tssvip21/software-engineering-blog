"""Initialize the new database only on an explicitly requested production build."""
import os
import subprocess
import sys


if os.environ.get("PETNOTE_INITIALIZE_DB") == "1":
    if os.environ.get("VERCEL_ENV") != "production":
        sys.exit("Database initialization requires the production environment.")
    subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], check=True)
    subprocess.run([sys.executable, "manage.py", "seed_references"], check=True)
