"""Import user-provided OAuth credentials without printing credential values."""
import argparse
import json
import re
from pathlib import Path

from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("credentials", type=Path)
    parser.add_argument("--env", type=Path, default=Path("tmp/deploy/production.env"))
    args = parser.parse_args()
    values = dotenv_values(args.env)
    origin = values.get("APP_ORIGIN", "").rstrip("/")
    if not origin.startswith("https://"):
        parser.error("운영 APP_ORIGIN을 먼저 설정해야 합니다.")
    callback = origin + "/auth/google/callback/"
    if args.credentials.suffix.lower() == ".json":
        web = json.loads(args.credentials.read_text(encoding="utf-8-sig")).get("web", {})
        if callback not in web.get("redirect_uris", []):
            parser.error("OAuth JSON의 승인된 리디렉션 URI가 운영 주소와 일치하지 않습니다.")
        client_id, secret = web.get("client_id", ""), web.get("client_secret", "")
    else:
        supplied = dotenv_values(args.credentials, encoding="utf-8-sig")
        client_id = supplied.get("GOOGLE_CLIENT_ID", "")
        secret = supplied.get("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_id.endswith(".apps.googleusercontent.com"):
        parser.error("올바른 웹 애플리케이션 클라이언트 ID가 필요합니다.")
    if not secret or len(secret) < 16:
        parser.error("전체 클라이언트 보안비밀을 입력해야 합니다. 가려진 값은 사용할 수 없습니다.")
    for value in (client_id, secret):
        if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            parser.error("자격증명에 예상하지 못한 문자가 있습니다. 복사한 값을 확인하세요.")
    values.update(GOOGLE_CLIENT_ID=client_id, GOOGLE_CLIENT_SECRET=secret)
    args.env.write_text("".join(f"{key}={value or ''}\n" for key, value in values.items()), encoding="utf-8")
    print("OAuth credentials applied locally. Secret values omitted.")
    print("Expected callback:", callback)


if __name__ == "__main__":
    main()
