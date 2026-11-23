"""Private backend, reachable only through the Compose frontend network."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
from config.wsgi import application
from waitress import serve

serve(application, host="0.0.0.0", port=8000, threads=4,
      trusted_proxy="*", trusted_proxy_count=1,
      trusted_proxy_headers="x-forwarded-proto x-forwarded-for",
      clear_untrusted_proxy_headers=True,
      max_request_body_size=262144, expose_tracebacks=False)
