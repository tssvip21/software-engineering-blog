"""Database options shared by the serverless runtime and its startup checks."""
from django.core.exceptions import ImproperlyConfigured
from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict


def vercel_database(db_url):
    """Use transaction pooling without weakening explicitly configured TLS."""
    if not db_url.startswith(("postgres://", "postgresql://")):
        raise ImproperlyConfigured("Vercel 운영 앱에는 PostgreSQL DATABASE_URL이 필요합니다.")
    try:
        params = conninfo_to_dict(db_url)
    except (ProgrammingError, ValueError):
        # libpq may include the malformed password or URI parameter in its error.
        raise ImproperlyConfigured("DATABASE_URL 형식을 확인하세요. 비밀번호의 특수문자는 URL 인코딩이 필요합니다.") from None
    if not all(params.get(key) for key in ("host", "dbname", "user", "password")):
        raise ImproperlyConfigured("DATABASE_URL에 PostgreSQL 호스트, DB명, 사용자와 비밀번호가 필요합니다.")
    sslmode = params.get("sslmode", "require")
    if sslmode not in ("require", "verify-ca", "verify-full"):
        raise ImproperlyConfigured("운영 DATABASE_URL은 SSL 연결을 요구해야 합니다.")
    options = {
        "connect_timeout": 10,
        "sslmode": sslmode,
        "prepare_threshold": None,
        # Django returns clients at request end; the shared pool bounds per-process
        # concurrency and reuses TLS connections without caching application data.
        "pool": {
            "min_size": 0,
            "max_size": 4,
            "timeout": 5,
            "max_waiting": 16,
            "max_idle": 30,
            "max_lifetime": 300,
        },
    }
    for name in ("sslrootcert", "sslcert", "sslkey", "sslcrl", "sslcrldir",
                 "ssl_min_protocol_version", "ssl_max_protocol_version", "channel_binding"):
        if name in params:
            options[name] = params[name]
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": params["dbname"],
        "USER": params["user"],
        "PASSWORD": params["password"],
        "HOST": params["host"],
        "PORT": params.get("port", "5432"),
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": True,
        "DISABLE_SERVER_SIDE_CURSORS": True,
        "OPTIONS": options,
    }
