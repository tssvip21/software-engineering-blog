from urllib.parse import urlencode
import base64
import hashlib
import hmac
import secrets
import time
import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404
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
        messages.error(request, "구글 로그인 연결이 아직 준비되지 않았어요. 운영자의 설정이 필요해요.")
        return redirect("home")
    if not rate_limit(request, "google-start", 12, 60):
        return render(request, "care/error.html", {"title": "잠시 후 다시 시도해 주세요", "detail": "로그인 시도가 여러 번 이어졌어요. 잠시 후 다시 시도해 주세요."}, status=429)
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
    try:
        # A second device may finish the same first login at the same time.
        # Keep the failed insert inside a savepoint so this transaction remains usable.
        with transaction.atomic():
            user = User.objects.create_user(username=username, password=None,
                                           first_name=str(claims.get("name") or "보호자")[:150],
                                           email=str(claims.get("email") or "")[:254])
            GoogleAccount.objects.create(user=user, subject=subject)
    except IntegrityError:
        linked = GoogleAccount.objects.select_related("user").filter(subject=subject).first()
        if linked is None:
            raise
        if not linked.user.is_active:
            raise ValueError("Inactive account")
        return linked.user
    return user


@require_GET
def google_callback(request):
    attempt = request.session.pop("google_login", None)
    state = request.GET.get("state", "")
    if not attempt or not state.isascii() or len(state) > 200 or not hmac.compare_digest(state, attempt.get("state", "")):
        return render(request, "care/error.html", {"title": "로그인을 다시 시작해 주세요", "detail": "로그인을 시작한 화면을 확인하지 못했어요. 처음 화면에서 다시 로그인해 주세요."}, status=400)
    if time.time() - attempt.get("created", 0) > 600 or not settings.GOOGLE_CONFIGURED:
        return render(request, "care/error.html", {"title": "로그인할 수 있는 시간이 지났어요", "detail": "처음 화면으로 돌아가 구글 로그인을 다시 눌러 주세요."}, status=400)
    if request.GET.get("error"):
        messages.info(request, "로그인을 취소했어요. 게스트로 남긴 기록은 그대로예요.")
        return redirect("home")
    code = request.GET.get("code", "")
    if not code or len(code) > 4096:
        return render(request, "care/error.html", {"title": "로그인 결과를 확인하지 못했어요", "detail": "구글 로그인을 다시 시작해 주세요."}, status=400)
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
        messages.success(request, "로그인했어요. 연결한 반려동물의 기록을 이어서 볼 수 있어요.")
        return redirect("home")
    except (ValueError, requests.RequestException, GoogleAuthError, KeyError, TypeError):
        return render(request, "care/error.html", {"title": "로그인하지 못했어요", "detail": "연결 상태를 확인하고 다시 로그인해 주세요. 계속 안 되면 운영자에게 알려 주세요. 기존 기록은 바뀌지 않았어요."}, status=400)


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "로그아웃했어요. 게스트 기록을 계정에 연결했다면 그 계정으로 다시 로그인해 주세요.")
    return redirect("home")


@require_POST
def demo_login(request):
    local_request = request.META.get("REMOTE_ADDR") in ("127.0.0.1", "::1", "testserver")
    if not settings.DEMO_MODE or not settings.DEBUG or not (local_request or settings.LOCAL_DEMO_POSTGRES):
        raise Http404()
    role = request.POST.get("identity")
    if role not in ("owner", "admin", "member", "outsider"):
        raise PermissionDenied()
    user = get_user_model().objects.filter(username="demo_" + role, is_active=True).first()
    if not user:
        return render(request, "care/error.html", {"title": "시연 데이터를 준비해 주세요", "detail": "로컬 실행 스크립트를 --demo 옵션으로 다시 실행해 주세요."}, status=503)
    logout(request)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    messages.info(request, "시연 계정으로 접속했어요. 실제 구글 인증을 거친 계정은 아니에요.")
    return redirect("home")
