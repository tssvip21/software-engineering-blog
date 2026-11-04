from datetime import timedelta
from urllib.parse import urlencode
import base64
import hashlib
import hmac
import secrets
import time
import uuid
import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponse, Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from google.auth.exceptions import GoogleAuthError
from .models import GoogleAccount
from .services import guest_identity, merge_guest_memberships, rate_limit

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class BoundedGoogleRequest(GoogleRequest):
    def __call__(self, *args, **kwargs):
        kwargs["timeout"] = min(float(kwargs.get("timeout", 10)), 10)
        return super().__call__(*args, **kwargs)


@require_POST
def google_start(request):
    if request.user.is_authenticated:
        return redirect("home")
    if settings.DEMO_MODE or not settings.GOOGLE_CONFIGURED:
        messages.error(request, "구글 로그인 설정이 필요합니다. 로컬 실행 안내에서 설정할 항목을 확인해 주세요.")
        return redirect("home")
    if not rate_limit(request, "google-start", 12, 60):
        return render(request, "care/error.html", {"title": "잠시 후 다시 시도해 주세요", "detail": "로그인 요청이 너무 많습니다."}, status=429)
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    request.session.cycle_key()
    request.session["google_login"] = {"state": state, "nonce": nonce, "verifier": verifier, "created": time.time()}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    query = {"client_id": settings.GOOGLE_CLIENT_ID, "redirect_uri": settings.GOOGLE_REDIRECT_URI,
             "response_type": "code", "scope": "openid email profile", "state": state,
             "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account"}
    return redirect(AUTH_URL + "?" + urlencode(query))


@transaction.atomic
def account_for_claims(claims):
    subject = claims.get("sub")
    if not isinstance(subject, str) or not 1 <= len(subject) <= 255 or claims.get("email_verified") is not True:
        raise ValueError("Invalid verified identity")
    linked = GoogleAccount.objects.select_related("user").filter(subject=subject).first()
    if linked:
        if not linked.user.is_active:
            raise ValueError("Inactive account")
        return linked.user
    User = get_user_model()
    username = "g_" + hashlib.sha256(subject.encode()).hexdigest()
    user = User.objects.create_user(username=username, password=None,
                                   first_name=str(claims.get("name") or "보호자")[:150],
                                   email=str(claims.get("email") or "")[:254])
    GoogleAccount.objects.create(user=user, subject=subject)
    return user


@require_GET
def google_callback(request):
    attempt = request.session.pop("google_login", None)
    state = request.GET.get("state", "")
    if not attempt or not isinstance(state, str) or len(state) > 200 or not hmac.compare_digest(state, attempt.get("state", "")):
        return render(request, "care/error.html", {"title": "로그인을 다시 시작해 주세요", "detail": "로그인 요청을 확인할 수 없습니다. 기존 화면으로 돌아가 다시 시도해 주세요."}, status=400)
    if time.time() - attempt.get("created", 0) > 600 or not settings.GOOGLE_CONFIGURED:
        return render(request, "care/error.html", {"title": "로그인 요청이 만료되었습니다", "detail": "시작 화면에서 구글 로그인을 다시 진행해 주세요."}, status=400)
    if request.GET.get("error"):
        messages.info(request, "구글 로그인이 취소되었습니다. 기존 게스트 기록은 그대로 남아 있습니다.")
        return redirect("home")
    code = request.GET.get("code", "")
    if not code or len(code) > 4096:
        return render(request, "care/error.html", {"title": "로그인 응답을 확인하지 못했습니다", "detail": "구글 로그인을 다시 시작해 주세요."}, status=400)
    try:
        response = requests.post(TOKEN_URL, data={"code": code, "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET, "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code", "code_verifier": attempt["verifier"]}, timeout=(5, 15))
        response.raise_for_status()
        encoded = response.json().get("id_token", "")
        if not encoded or len(encoded) > 30000:
            raise ValueError("Missing identity token")
        claims = id_token.verify_oauth2_token(encoded, BoundedGoogleRequest(), settings.GOOGLE_CLIENT_ID)
        if not hmac.compare_digest(str(claims.get("nonce", "")), attempt["nonce"]):
            raise ValueError("Invalid identity nonce")
        if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
            raise ValueError("Invalid issuer")
        if claims.get("iat", 0) > time.time() + 60 or claims.get("exp", 0) <= time.time():
            raise ValueError("Invalid token time")
        if claims.get("azp") and claims["azp"] != settings.GOOGLE_CLIENT_ID:
            raise ValueError("Invalid authorized party")
        previous_guest = guest_identity(request)
        with transaction.atomic():
            user = account_for_claims(claims)
            merge_guest_memberships(previous_guest, user)
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        request.session.pop("guest_identity", None)
        messages.success(request, "구글 계정으로 로그인했습니다. 연결된 돌봄 기록을 이어서 사용할 수 있습니다.")
        return redirect("home")
    except (ValueError, requests.RequestException, GoogleAuthError, KeyError, TypeError):
        return render(request, "care/error.html", {"title": "로그인을 완료하지 못했습니다", "detail": "네트워크와 구글 인증 설정을 확인한 뒤 다시 시도해 주세요. 기존 기록은 변경하지 않았습니다."}, status=400)


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "이 브라우저에서 로그아웃했습니다. 게스트로 작성한 기록을 다시 관리하려면 연결한 계정으로 로그인해 주세요.")
    return redirect("home")


@require_POST
def demo_login(request):
    if not settings.DEMO_MODE or not settings.DEBUG or request.META.get("REMOTE_ADDR") not in ("127.0.0.1", "::1", "testserver"):
        raise Http404()
    role = request.POST.get("identity", "owner")
    if role not in ("owner", "admin", "member", "outsider"):
        raise PermissionDenied()
    user = get_user_model().objects.filter(username="demo_" + role).first()
    if not user:
        return render(request, "care/error.html", {"title": "시연 데이터를 준비해 주세요", "detail": "로컬 실행 스크립트를 --demo 옵션으로 다시 실행해 주세요."}, status=503)
    logout(request)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    messages.info(request, "시연 전용 계정입니다. 실제 구글 로그인으로 인증한 계정이 아닙니다.")
    return redirect("home")
