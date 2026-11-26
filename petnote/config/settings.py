from pathlib import Path
from urllib.parse import urlsplit
import os
import secrets
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)
ON_VERCEL = os.environ.get("VERCEL") == "1"
DEBUG = not ON_VERCEL and os.environ.get("DJANGO_DEBUG", "1") == "1"
DEMO_MODE = os.environ.get("CARE_DEMO_MODE", "0") == "1"
PUBLIC_DEMO = os.environ.get("CARE_PUBLIC_DEMO", "0") == "1"
PUBLIC_DEMO_ENTRY = os.environ.get("CARE_PUBLIC_DEMO_ENTRY", "0") == "1" and not PUBLIC_DEMO
if PUBLIC_DEMO and (DEBUG or not DEMO_MODE or os.environ.get("POSTGRES_HOST") or not os.environ.get("CARE_DATA_DIR")):
    raise ImproperlyConfigured("공개 시연은 DEBUG=0 분리형 SQLite에서만 가능합니다.")
if DEMO_MODE and not DEBUG and not PUBLIC_DEMO:
    raise ImproperlyConfigured("시연 환경은 로컬 개발 설정에서만 실행할 수 있습니다.")
DATA_DIR = Path(os.environ.get("CARE_DATA_DIR", str(BASE_DIR / "data")))
if not ON_VERCEL:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("운영 환경에서는 DJANGO_SECRET_KEY가 필요합니다.")
    key_file = DATA_DIR / ".local_secret"
    try:
        with key_file.open("x", encoding="utf-8") as key_out:
            key_out.write(secrets.token_urlsafe(64))
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
    except FileExistsError:
        pass
    SECRET_KEY = key_file.read_text(encoding="utf-8").strip()
# Apply the same minimums as Django's deployment check before serving requests.
if not DEBUG and (len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or SECRET_KEY.startswith("django-insecure-")):
    raise ImproperlyConfigured("운영 DJANGO_SECRET_KEY는 서로 다른 문자가 포함된 50자 이상의 임의 값으로 설정하세요.")
ALLOWED_HOSTS = [v.strip() for v in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if v.strip()]
APP_ORIGIN = os.environ.get("APP_ORIGIN", "http://127.0.0.1:8000").rstrip("/")
if ON_VERCEL:
    try:
        origin = urlsplit(APP_ORIGIN)
        valid_origin = (origin.scheme == "https" and origin.hostname in ALLOWED_HOSTS
                        and origin.port in (None, 443) and origin.username is None
                        and origin.password is None and not (origin.path or origin.query or origin.fragment))
    except ValueError:
        valid_origin = False
    if not valid_origin:
        raise ImproperlyConfigured("Vercel에서는 APP_ORIGIN과 DJANGO_ALLOWED_HOSTS에 서비스의 HTTPS 주소와 호스트를 설정하세요.")
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    for variable in ("VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
        deployment_host = os.environ.get(variable, "")
        if deployment_host.endswith(".vercel.app") and deployment_host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(deployment_host)
CSRF_TRUSTED_ORIGINS = [APP_ORIGIN]
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", APP_ORIGIN + "/auth/google/callback/")
GOOGLE_CONFIGURED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "care.apps.CareConfig"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware", "care.middleware.ServerTimingMiddleware", "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware", "care.middleware.PrivateResponseMiddleware"]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages", "care.context.site_context"]}}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(DATA_DIR / ("demo.sqlite3" if DEMO_MODE else "care.sqlite3")), "OPTIONS": {"timeout": 25, "transaction_mode": "IMMEDIATE"}}}
LOCAL_DEMO_POSTGRES = (
    os.environ.get("PETNOTE_LOCAL_POSTGRES") == "1" and DEBUG and DEMO_MODE and not PUBLIC_DEMO
    and os.environ.get("POSTGRES_HOST") == "db" and os.environ.get("POSTGRES_DB") == "petnote_demo"
)
if os.environ.get("POSTGRES_HOST") and not ON_VERCEL:
    if DEMO_MODE and not LOCAL_DEMO_POSTGRES:
        raise ImproperlyConfigured("시연 DB는 로컬 SQLite만 사용합니다.")
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "petnote"),
        "USER": os.environ.get("POSTGRES_USER", "petnote"),
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ["POSTGRES_HOST"],
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"connect_timeout": 10},
    }}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
if ON_VERCEL:
    from .database import vercel_database

    if DEMO_MODE:
        raise ImproperlyConfigured("Vercel 운영 앱에서는 시연 DB를 사용할 수 없습니다.")
    DATABASES = {"default": vercel_database(os.environ.get("DATABASE_URL", ""))}
LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}, "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"}}
if ON_VERCEL:
    STORAGES["staticfiles"]["BACKEND"] = "django.contrib.staticfiles.storage.StaticFilesStorage"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7
SESSION_SAVE_EVERY_REQUEST = False
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 256 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200
FILE_UPLOAD_MAX_MEMORY_SIZE = 0
CSRF_FAILURE_VIEW = "care.views.csrf_failure"
LOGIN_URL = "/"
LOGGING = {"version": 1, "disable_existing_loggers": False, "filters": {"redact": {"()": "config.logfilter.RedactQuery"}}, "handlers": {"console": {"class": "logging.StreamHandler", "filters": ["redact"]}}, "loggers": {"django": {"handlers": ["console"], "level": "WARNING", "propagate": False}, "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False}}}

SESSION_COOKIE_NAME = "care_demo_session" if DEMO_MODE else "care_session"
CSRF_COOKIE_NAME = "care_demo_csrf" if DEMO_MODE else "care_csrf"
if PUBLIC_DEMO:
    if not APP_ORIGIN.startswith("https://"):
        raise ImproperlyConfigured("공개 시연은 HTTPS에서만 사용합니다.")
    FORCE_SCRIPT_NAME = "/try"
    SESSION_COOKIE_NAME = "taily_public_demo_session"
    CSRF_COOKIE_NAME = "taily_public_demo_csrf"
    SESSION_COOKIE_PATH = "/try/"
    CSRF_COOKIE_PATH = "/try/"
