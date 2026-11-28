"""Record form, detail and cancellation request handlers."""

import uuid
from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from care import services
from care.forms import CancelForm, RecordForm
from care.models import Activity, RoutineRun
from care.modules import MODULES, payload_display
from care.presentation import forms as ui_forms
from care.presentation.records import record_card
from care.services.common import datetime_for_day
from care.views.browsing import record_return, with_return
from care.views.common import add_form_error, error


@require_http_methods(["GET", "POST"])
def record_new(request, room_id, module):
    room, member = services.get_room(request, room_id)
    return record_form_view(request, room, member, module)


@require_http_methods(["GET", "POST"])
def record_edit(request, room_id, activity_id):
    room, member = services.get_room(request, room_id)
    activity = get_object_or_404(Activity, pk=activity_id, room=room)
    return record_form_view(request, room, member, activity.module, activity)


def record_form_view(request, room, member, module, activity=None):
    try:
        services.require_module(member, module, adding=activity is None)
    except ValidationError as exc:
        return error(request, "사용하지 않는 기능이에요", " ".join(exc.messages), 409)
    if activity and not services.can_edit_record(member, activity):
        raise PermissionDenied("자신의 기록만 수정할 수 있어요.")
    if activity and activity.voided:
        return error(request, "취소한 기록이에요", "기존 이력은 상세 화면에서 확인하고, 필요한 경우 새로 기록해 주세요.", 409)
    initial = {}
    if request.method == "GET":
        if request.GET.get("routine_item"):
            try:
                item_id = uuid.UUID(request.GET["routine_item"])
                day = date.fromisoformat(request.GET.get("date", ""))
                item = services.require_routine_slot(member, item_id, day, module)
                initial.update({"routine_item": item.pk, "routine_date": day})
                if day != services.local_date(room):
                    initial["occurred_at"] = datetime_for_day(room, day, item.routine.at)
            except (ValueError, ValidationError) as exc:
                return error(request, "루틴 정보를 확인해 주세요", " ".join(exc.messages) if isinstance(exc, ValidationError) else "올바른 날짜를 선택해 주세요.")
        if request.GET.get("start") == "1" and module in ("walk", "shedding"):
            initial["status"] = "active"
    form = RecordForm(request.POST or None, room=room, module=module, member=member, instance=activity, initial=initial)
    if request.method == "GET" and activity and request.GET.get("finish") == "1" and activity.status == "active":
        form.initial.update({"status": "done", "ended_at": timezone.localtime()})
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            record, changed = services.save_activity(member, module, form, activity.pk if activity else None)
            if ui_forms.in_dialog(request):
                return ui_forms.saved(request,"기록을 저장했어요." if changed else "이미 저장된 기록이에요.",reverse("record_detail",args=[room.pk,record.pk]),id=str(record.pk),changed=changed)
            messages.success(request, "기록을 저장했어요." if changed else "이미 저장한 기록이에요. 같은 기록을 다시 만들지 않았어요.")
            return redirect(with_return(reverse("record_detail", args=[room.pk, record.pk]), record_return(request, room)))
        except ValidationError as exc:
            add_form_error(form, exc)
            status = 409 if isinstance(exc, services.Conflict) else 400
        except OperationalError:
            form.add_error(None, "지금은 기록을 저장할 수 없어요. 입력은 유지했으니 잠시 후 다시 저장해 주세요.")
            status = 503
    return ui_forms.render_input(request, "care/record_form.html", {"title": MODULES[module].name + (" 기록 수정" if activity else " 기록"), "form": form,
                  "spec": MODULES[module], "activity": activity, "active_tab": "modules", "return_to": record_return(request, room), "back_url": with_return(reverse("record_detail", args=[room.pk, activity.pk]), record_return(request, room)) if activity else reverse("module", args=[room.pk, module])}, status=status)


@require_GET
def record_detail(request, room_id, activity_id):
    room, member = services.get_room(request, room_id)
    activity = get_object_or_404(Activity.objects.select_related("author", "performer"), pk=activity_id, room=room)
    services.require_module(member, activity.module)
    record_card(activity, member)
    from care.reporting.display_values import rounded_originals
    activity.exact_numeric_values = rounded_originals(activity.module,activity.payload)
    revisions = list(activity.revisions.select_related("actor").all())
    for revision in revisions:
        revision.after_values = payload_display(activity.module, revision.after.get("payload", {}))
        revision.before_values = payload_display(activity.module, revision.before.get("payload", {})) if revision.before else []
    run = RoutineRun.objects.filter(activity=activity).select_related("item__routine").first()
    return render(request, "care/record_detail.html", {"title": activity.spec.name + " 기록", "activity": activity, "details": payload_display(activity.module, activity.payload, status=activity.status),
                  "revisions": revisions, "run": run, "return_to": record_return(request, room), "edit_url": with_return(reverse("record_edit", args=[room.pk, activity.pk]) + ("?finish=1" if activity.status == "active" else ""), record_return(request, room)), "cancel_form": CancelForm(initial={"version": activity.version}), "active_tab": "records"})


@require_POST
def record_cancel(request, room_id, activity_id):
    room, member = services.get_room(request, room_id)
    activity = get_object_or_404(Activity, pk=activity_id, room=room)
    services.require_module(member, activity.module)
    form = CancelForm(request.POST)
    status = 400
    if form.is_valid():
        try:
            services.cancel_activity(member, activity.pk, form.cleaned_data["version"], form.cleaned_data["reason"])
            messages.success(request, "기록을 취소했어요. 통계에는 포함하지 않고 수정 이력은 남겨요.")
            return redirect(with_return(reverse("record_detail", args=[room.pk, activity.pk]), record_return(request, room)))
        except ValidationError as exc:
            add_form_error(form, exc)
            status = 409 if isinstance(exc, services.Conflict) else 400
    return render(request, "care/form.html", {"title": "기록 취소", "intro": "입력값을 확인한 뒤 다시 시도해 주세요.", "form": form,
                  "submit_label": "기록 취소", "danger": True, "back_url": reverse("record_detail", args=[room.pk, activity.pk]), "active_tab": "records"}, status=status)
