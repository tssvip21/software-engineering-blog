"""Space registration, module settings and lifecycle requests."""

import json

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
    require_safe,
)

from care import services
from care.json_inputs import load_json
from care.modules import MODULES
from care.services.organization import browse_context
from care.views.common import add_form_error, error
from care.views.interactions import config_state


@require_safe
def home(request):
    memberships = services.request_memberships(request)
    return render(request, "care/home.html", {"memberships": memberships, "has_identity": bool(memberships or request.user.is_authenticated)})


@require_http_methods(["GET", "POST"])
def room_new(request):
    if settings.PUBLIC_DEMO:
        raise PermissionDenied("공개 시연에서는 기존 세 공간만 이용할 수 있습니다.")
    if not request.user.is_authenticated:
        messages.info(request, "반려동물을 등록하려면 구글 계정으로 로그인해 주세요.")
        return redirect("home")
    from care.services.profiles import PetProfileForm, register_pet
    form = PetProfileForm(request.POST if request.method == "POST" else None)
    form.fields["request_token"].required=False
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            room = register_pet(request, form)
            if room is None:
                return error(request, "잠시 후 다시 시도해 주세요", "등록 요청이 여러 번 이어졌어요. 잠시 후 다시 시도해 주세요.", 429)
            messages.success(request, "반려동물을 등록했어요. 오늘의 돌봄을 남겨 보세요.")
            return redirect("today", room_id=room.pk)
        except ValidationError as exc:
            add_form_error(form, exc)
            status = 409 if isinstance(exc, services.Conflict) else 422
    elif request.method == "POST":
        status = 422
    return render(request, "care/space/new_pet.html", {"form": form, "title": "반려동물 등록", "pet_form": True, "form_action":request.get_full_path(), "submit_label": "돌봄 공간 만들기", "back_url": reverse("home")}, status=status)


@require_http_methods(["GET", "POST"])
def module_settings(request, room_id):
    room, member = services.get_room(request, room_id)
    services.require_manager(member)
    problem, status = "", 200
    if request.method == "POST":
        try:
            enabled = request.POST.getlist("enabled")
            order = load_json(request.POST.get("order", "[]"))
            version = int(request.POST.get("version", "0"))
            services.update_modules(member, enabled, order, version, request.POST.getlist("pinned") if "pin_config" in request.POST else None)
            messages.success(request, "사용할 기능을 저장했어요. 사용하지 않는 기능의 기록도 그대로 남아 있어요.")
            return redirect("modules", room_id=room.pk)
        except (ValueError, TypeError, ValidationError) as exc:
            problem = " ".join(exc.messages) if isinstance(exc, ValidationError) else "선택한 기능을 다시 확인해 주세요."
            status = 409 if isinstance(exc, services.Conflict) else 400
    settings_rows = list(room.module_settings.all())
    from care.modules import SELECTABLE_MODULES
    rows = [{"spec": MODULES[s.key], "enabled": s.enabled, "pinned": s.pinned} for s in settings_rows if s.key in SELECTABLE_MODULES]
    return render(request, "care/module_settings.html", {"title": "기능 설정", "rows": rows, "config_state": config_state(room), "chosen": [r for r in rows if r["enabled"]], "order_json": json.dumps([s.key for s in settings_rows]),
                  "problem": problem, "active_tab": "modules", **browse_context(member)}, status=status)


@require_GET
def room_state(request, room_id):
    room, member = services.get_room(request, room_id)
    return JsonResponse({"revision": room.revision})


@require_POST
def room_archive(request, room_id):
    room, member = services.get_room(request, room_id)
    try:
        services.archive_room(member, request.POST.get("confirmation", ""))
    except ValidationError as exc:
        return error(request, "이름을 확인해 주세요", " ".join(exc.messages), 400)
    messages.success(request, "공간을 보관했어요. 목록에서 숨기고 새 참여를 막았으며, 기존 기록은 보관했어요.")
    return redirect("home")


@require_POST
def room_delete(request, room_id):
    room, member = services.get_room(request, room_id)
    try:
        services.delete_room(member, request.POST.get("confirmation", ""))
    except ValidationError as exc:
        return error(request, "삭제 확인 문구를 확인해 주세요", " ".join(exc.messages), 400)
    messages.success(request, "이 공간과 기록을 삭제했어요. 다른 반려동물의 기록은 그대로예요.")
    return redirect("home")
