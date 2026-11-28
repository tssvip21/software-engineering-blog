import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

origin = urlsplit(os.environ["APP_ORIGIN"])
request = Request("http://127.0.0.1:8000/health/", headers={
    "Host": origin.netloc,
    "X-Forwarded-Proto": origin.scheme,
})
with urlopen(request, timeout=4) as response:
    if response.status != 200 or json.load(response).get("status") != "ok":
        raise SystemExit(1)
