"""Service health and application error responses."""

from django.db import DatabaseError, InterfaceError, connection
from django.http import JsonResponse
from django.views.decorators.http import require_safe

from care.views.common import error


@require_safe
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except (DatabaseError, InterfaceError):
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def csrf_failure(request, reason=""):
    return error(request, "화면을 새로고침해 주세요", "요청을 확인할 수 없어요. 오래 열린 탭이나 다른 사이트에서 보낸 요청일 수 있어요.", 403)


def bad_request(request, exception=None):
    return error(request, "요청을 확인해 주세요", "입력한 주소나 값을 확인한 뒤 다시 시도해 주세요.", 400)


def permission_denied(request, exception=None):
    return error(request, "이 작업을 할 수 없어요", "이 공간에 참여 중인지, 필요한 권한이 있는지 확인해 주세요.", 403)


def not_found(request, exception=None):
    return error(request, "페이지를 찾지 못했어요", "주소가 바뀌었거나 참여하지 않은 공간일 수 있어요.", 404)


def server_error(request):
    return error(request, "요청을 완료하지 못했어요", "잠시 후 다시 시도해 주세요. 저장 여부가 불확실하면 전체 기록을 먼저 확인해 주세요.", 500)
