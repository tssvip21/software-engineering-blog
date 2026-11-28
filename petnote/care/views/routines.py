"""Routine editing and record-linking request handlers."""

import uuid
from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from care import services
from care.forms import RoutineForm
from care.models import Activity, Routine, RoutineItem
from care.presentation import forms as ui_forms
from care.presentation.records import record_cards
from care.reporting.stats import bounds
from care.views.common import add_form_error, error


@require_http_methods(["GET", "POST"])
def routine_edit(request, room_id, routine_id=None):
    room, member = services.get_room(request, room_id)
    services.require_manager(member)
    instance = get_object_or_404(Routine, pk=routine_id, room=room) if routine_id else None
    form = RoutineForm(request.POST or None, room=room, visible_keys=services.visible_modules(member), instance=instance)
    if ui_forms.in_dialog(request) and instance is None:
        form.fields["request_token"].required=True
    # Present the actual task first, rather than starting a long form with dates.
    form.order_fields(["name","at","weekdays","modules","preset","start_date","end_date","assignee","note","active","version","module_order","request_token"])
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            routine=services.save_routine(member, form, routine_id)
            if ui_forms.in_dialog(request):
                return ui_forms.saved(request,"루틴을 저장했어요.",reverse("routines",args=[room.pk]),id=str(routine.pk))
            messages.success(request, "루틴을 저장했어요. 바꾼 내용은 오늘부터 적용돼요.")
            return redirect("routines", room_id=room.pk)
        except ValidationError as exc:
            add_form_error(form, exc)
            status = 409 if isinstance(exc, services.Conflict) else 400
    elif request.method == "POST":
        status = 422
    return ui_forms.render_input(request, "care/form.html", {"title": "루틴 수정" if instance else "새 루틴", "intro": "같은 시간에 확인할 활동을 묶어 주세요. 돌봄 기록을 남기면 완료로 표시해요.",
                  "form": form, "submit_label": "루틴 저장", "back_url": reverse("routines", args=[room.pk]), "active_tab": "routines", "routine_form": True}, status=status)


@require_http_methods(["GET", "POST"])
def routine_link(request, room_id, item_id):
    room, member = services.get_room(request, room_id)
    item = get_object_or_404(RoutineItem.objects.select_related("routine"), pk=item_id, routine__room=room)
    services.require_module(member, item.module)
    try:
        day = date.fromisoformat(request.POST.get("date", "") if request.method == "POST" else request.GET.get("date", ""))
        services.require_routine_slot(member, item.pk, day, item.module)
    except (ValueError, ValidationError) as exc:
        return error(request, "루틴 날짜를 확인해 주세요", " ".join(exc.messages) if isinstance(exc, ValidationError) else "올바른 날짜를 선택해 주세요.")
    lower, upper = bounds(room, day, day)
    candidates = Activity.objects.filter(room=room, module=item.module, voided=False, status__in=("active", "done"), occurred_at__gte=lower, occurred_at__lt=upper, routine_run__isnull=True).select_related("author")
    if not member.can_manage:
        candidates = candidates.filter(author=member)
    problem = ""
    if request.method == "POST":
        try:
            activity_id = uuid.UUID(request.POST.get("activity", ""))
            services.link_activity(member, item.pk, day, activity_id)
            messages.success(request, "이미 남긴 기록을 연결했어요. 같은 기록을 새로 만들지는 않았어요.")
            return redirect(reverse("routines", args=[room.pk]) + "?" + urlencode({"date": day.isoformat()}))
        except (ValueError, ValidationError) as exc:
            problem = " ".join(exc.messages) if isinstance(exc, ValidationError) else "연결할 기록을 선택해 주세요."
    return render(request, "care/routine_link.html", {"title": "기존 기록 연결", "item": item, "day": day, "candidates": record_cards(candidates, member), "problem": problem, "active_tab": "routines"})
