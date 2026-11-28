"""Small UI actions reuse the authoritative room-scoped record services."""
import json
import uuid
from datetime import date
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from care import services
from care.forms import LocalDateTimeInput, RecordForm
from care.json_inputs import load_json
from care.models import Activity, CareCadence, Room
from care.modules import MODULES, record_summary
from care.services import cadences

CORE_FIELDS = {
    "meal": ("product", "food_name", "quantity", "unit"),
    "treat": ("product", "food_name", "quantity", "unit"),
    "water": ("kind", "amount", "unit"), "toilet": ("kind", "condition"),
    "walk": (), "play": ("kind", "minutes"), "training": ("skill", "minutes"),
    "weight": ("weight", "unit"), "hygiene": ("task",), "grooming": ("task",),
    "cleaning": ("target",), "medication": ("medicine", "dose", "unit", "result", "status"),
    "observation": ("category", "condition", "detail"), "veterinary": ("kind", "detail", "status"),
    "sleep": ("kind", "minutes"), "environment": ("metric", "value", "unit", "location"),
    "reproduction": ("kind", "detail"), "shedding": ("kind", "status"),
    "inventory": ("item", "movement", "quantity", "unit"),
    "expense": ("item", "amount", "currency", "category"),
}
REUSABLE = {"meal", "treat", "play", "training", "hygiene", "grooming", "cleaning"}


def wants_fragment(request):
    return request.headers.get("X-Taily-UI") == "1"


def fail_json(exc, status=400):
    text = " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
    return JsonResponse({"ok": False, "message": text}, status=status)


def recent_choices(member, module):
    if module not in REUSABLE:
        return []
    found, seen = [], set()
    for activity in Activity.objects.filter(room=member.room, module=module, voided=False, status="done").order_by("-occurred_at", "-created_at")[:15]:
        signature = json.dumps(activity.payload, sort_keys=True)
        if signature in seen:
            continue
        seen.add(signature)
        found.append({"id": activity.pk, "label": record_summary(module, activity.payload), "date": activity.occurred_at})
        if len(found) == 3:
            break
    return found


@transaction.atomic
def save_quick(member, module, form, activity=None):
    member = services.fresh_member(member)
    data = form.cleaned_data
    cadence_id = data.get("cadence_id")
    cadence = None
    duplicate = Activity.objects.filter(room=member.room, client_key=data["client_key"]).first() if activity is None else None
    if cadence_id:
        cadence = get_object_or_404(CareCadence.objects.select_for_update(), pk=cadence_id, room=member.room, module=module)
        services.require_module(member, module, adding=activity is None)
        if cadence.variant_field and str(data.get(cadence.variant_field, "")) != cadence.variant_value:
            raise ValidationError("반복 알림에 정한 활동과 지금 기록이 달라요. 활동 종류를 확인해 주세요.")
        if not duplicate:
            if (not cadence.active and activity is None) or cadence.version != data.get("cadence_version"):
                raise services.Conflict("반복 알림이 바뀌어 저장하지 않았어요. 최신 내용을 먼저 확인해 주세요.")
            if activity is None and not cadences.state(cadence, member.room)["due"]:
                raise services.Conflict("이미 완료했거나 미룬 알림이에요. 별개의 활동은 돌봄 기능에서 새로 기록해 주세요.")
            if activity is None and Activity.objects.filter(room=member.room, module=module, status="active", voided=False, payload___cadence_id=str(cadence.pk)).exists():
                raise services.Conflict("이 활동은 이미 진행 중이에요. 오늘 화면에서 진행 기록을 종료해 주세요.")
        form.cadence_marker = str(cadence.pk)
    record, changed = services.save_activity(member, module, form, activity.pk if activity else None)
    if cadence and changed and record.status == "done":
        cadence.version += 1
        cadence.save(update_fields=["version"])
    return record, changed


@require_http_methods(["GET", "POST"])
def quick_record(request, room_id, module=None, activity_id=None):
    room, member = services.get_room(request, room_id)
    activity = get_object_or_404(Activity, pk=activity_id, room=room) if activity_id else None
    if activity:
        module = activity.module
        if not services.can_edit_record(member, activity):
            raise PermissionDenied("자신이 작성한 기록만 바꿀 수 있어요.")
        if activity.voided:
            return fail_json("이미 취소한 기록이에요.", 409) if wants_fragment(request) else redirect("record_detail", room_id=room.pk, activity_id=activity.pk)
    try:
        services.require_module(member, module, adding=activity is None)
    except ValidationError as exc:
        return fail_json(exc, 409) if wants_fragment(request) else render(request, "care/error.html", {"title": "사용하지 않는 기능이에요", "detail": " ".join(exc.messages)}, status=409)
    initial = {}
    selected_cadence = None
    reuse = None
    finish = bool(activity and activity.status == "active" and request.GET.get("finish") == "1")
    if request.method == "GET":
        if module == "walk" and not activity:
            initial["status"] = "active" if request.GET.get("done") != "1" else "done"
        if request.GET.get("cadence"):
            try:
                selected_cadence = get_object_or_404(CareCadence, pk=uuid.UUID(request.GET["cadence"]), room=room, module=module)
            except (ValueError, ValidationError):
                raise Http404()
            initial.update(cadence_id=selected_cadence.pk, cadence_version=selected_cadence.version)
            if selected_cadence.variant_field:
                initial[selected_cadence.variant_field] = selected_cadence.variant_value
        elif activity and activity.payload.get("_cadence_id"):
            selected_cadence = CareCadence.objects.filter(pk=activity.payload["_cadence_id"], room=room, module=module).first()
            if selected_cadence:
                initial.update(cadence_id=selected_cadence.pk, cadence_version=selected_cadence.version)
        if request.GET.get("routine_item"):
            try:
                item_id = uuid.UUID(request.GET["routine_item"])
                day = date.fromisoformat(request.GET.get("date", ""))
                item = services.require_routine_slot(member, item_id, day, module)
                initial.update(routine_item=item.pk, routine_date=day)
                if day != services.local_date(room):
                    from care.services.common import datetime_for_day
                    initial["occurred_at"] = datetime_for_day(room, day, item.routine.at)
                    if module == "walk":
                        initial["status"] = "done"
            except (ValueError, ValidationError) as exc:
                return fail_json("루틴 날짜와 활동을 확인해 주세요.", 400) if wants_fragment(request) else render(request, "care/error.html", {"title": "루틴 확인", "detail": str(exc)}, status=400)
        # Explicit selection only: never prefill a quantity from history without the user's action.
        if request.GET.get("reuse") and module in REUSABLE:
            try:
                reuse = get_object_or_404(Activity, pk=uuid.UUID(request.GET["reuse"]), room=room, module=module, voided=False, status="done")
            except (ValueError, ValidationError):
                raise Http404()
            for field in MODULES[module].fields:
                if field.key not in ("next_date", "consumed"):
                    initial[field.key] = reuse.payload.get(field.key)
            if module in ("meal", "treat"):
                product_id = reuse.payload.get("product_id")
                if product_id and room.products.filter(pk=product_id, active=True, category__in=(module, "both")).exists():
                    initial["product"] = product_id
        field = cadences.VARIANT_FIELDS.get(module)
        value = request.GET.get("variant")
        if field and value:
            spec = next(f for f in MODULES[module].fields if f.key == field)
            if value in dict(spec.choices) and not (module == "reproduction" and value in ("heat", "egg")):
                initial[field] = value
    form = RecordForm(request.POST if request.method == "POST" else None, room=room, module=module, member=member, instance=activity, initial=initial, quick=True)
    form.fields["cadence_id"] = forms.UUIDField(required=False, widget=forms.HiddenInput())
    form.fields["cadence_version"] = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput())
    # Use second precision for start/finish while keeping the rendered value stable across a retry.
    form.fields["occurred_at"].widget = LocalDateTimeInput(format="%Y-%m-%dT%H:%M:%S", attrs={"step": "1"})
    if module == "walk":
        form.fields["status"].widget = forms.HiddenInput()
        if "ended_at" in form.fields:
            form.fields["ended_at"].widget = LocalDateTimeInput(format="%Y-%m-%dT%H:%M:%S", attrs={"step": "1"})
    if finish:
        form.initial.update(status="done", ended_at=timezone.localtime())
    if module == "reproduction" and (not activity or activity.payload.get("kind") not in ("heat", "egg")):
        form.fields["eggs"].widget = forms.HiddenInput()
    if module == "sleep" and not activity:
        form.fields["occurred_at"].label = "확인을 마친 시각"
        form.fields["occurred_at"].help_text = "이 시각에서 입력한 시간만큼 앞선 때부터 수면·휴식을 기록해요."
    status = 200
    if request.method == "POST":
        status = 422
        if form.is_valid():
            try:
                record, changed = save_quick(member, module, form, activity)
                text = ("산책을 시작했어요." if module == "walk" and record.status == "active" else MODULES[module].name + " 기록을 저장했어요.") if changed else "이미 저장한 기록이에요. 중복으로 추가하지 않았어요."
                if wants_fragment(request):
                    return JsonResponse({"ok": True, "id": str(record.pk), "version": record.version, "message": text,
                                         "url": reverse("record_detail", args=[room.pk, record.pk]),
                                         "undo_url": reverse("quick_undo", args=[room.pk, record.pk]),
                                         "undo_allowed": activity is None, "created": changed and activity is None})
                messages.success(request, text)
                return redirect("record_detail", room_id=room.pk, activity_id=record.pk)
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
                status = 409 if isinstance(exc, services.Conflict) else 422
            except OperationalError:
                form.add_error(None, "지금은 저장할 수 없어요. 입력한 내용은 남아 있으니 같은 화면에서 다시 저장해 주세요.")
                status = 503
    core = [form[key] for key in CORE_FIELDS[module] if key in form.fields and not form[key].is_hidden]
    core_names = {field.name for field in core}
    extra = [field for field in form.visible_fields() if field.name not in core_names]
    advanced_errors = any(field.errors for field in extra)
    selected_status = form["status"].value()
    submit = "산책 시작" if module == "walk" and selected_status == "active" else "산책 종료" if finish else "기록 저장"
    if module == "walk" and (selected_status == "done" or finish):
        names = ("minutes", "distance", "distance_unit")
        extra = [f for f in extra if f.name not in names]
        core.extend(form[name] for name in names if name not in core_names)
    context = {"title": MODULES[module].name + (" 종료" if finish else " 빠른 기록"), "spec": MODULES[module], "form": form,
               "core_fields": core, "extra_fields": extra, "extra_open": advanced_errors,
               "submit_label": submit, "activity": activity, "finish": finish, "cadence": selected_cadence,
               "recent_choices": recent_choices(member, module) if not activity else [], "reuse": reuse,
               "active_tab": "modules", "quick_action": request.get_full_path(),
               "quick_base": reverse("quick_record", args=[room.pk, module]),
               "reuse_query": urlencode({k: v for k, v in request.GET.items() if k != "reuse"})}
    if wants_fragment(request) and request.method == "POST":
        return JsonResponse({"ok": False, "html": render_to_string("care/_quick_form.html", context, request=request)}, status=status)
    return render(request, "care/_quick_form.html" if wants_fragment(request) else "care/quick.html", context, status=status)


@require_POST
def quick_undo(request, room_id, activity_id):
    room, member = services.get_room(request, room_id)
    get_object_or_404(Activity, pk=activity_id, room=room)
    try:
        version = int(request.POST.get("version", ""))
        services.cancel_activity(member, activity_id, version, "빠른 기록 실행 취소")
    except (ValueError, ValidationError) as exc:
        return fail_json(exc if isinstance(exc, ValidationError) else "기록 버전을 확인해 주세요.", 409)
    return JsonResponse({"ok": True, "message": "기록을 취소했어요. 통계와 반복 알림에도 반영했어요."})


def config_state(room):
    from care.modules import SELECTABLE_MODULES
    rows = [row for row in room.module_settings.all() if row.key in SELECTABLE_MODULES]
    latest_room = Room.objects.only("settings_version", "revision").get(pk=room.pk)
    return {"version": latest_room.settings_version, "revision": latest_room.revision, "order": [s.key for s in rows if s.enabled],
            "enabled": [s.key for s in rows if s.enabled], "pinned": [s.key for s in rows if s.enabled and s.pinned]}


@require_http_methods(["GET", "POST"])
def module_config(request, room_id):
    room, member = services.get_room(request, room_id)
    services.require_manager(member)
    if request.method == "GET":
        return JsonResponse({"ok": True, **config_state(room)})
    try:
        data = load_json(request.body)
        if not isinstance(data, dict):
            raise ValueError()
        enabled, pinned, order, version = (data.get(k) for k in ("enabled", "pinned", "order", "version"))
        if type(version) is not int or version < 1:
            raise ValueError()
        for values in (enabled, pinned, order):
            if not isinstance(values, list) or len(values) > len(MODULES) or any(not isinstance(k, str) or k not in MODULES for k in values) or len(values) != len(set(values)):
                raise ValueError()
        if set(order) != set(enabled) or not set(pinned) <= set(enabled):
            raise ValueError()
        full_order = order + [key for key in MODULES if key not in enabled]
        services.update_modules(member, enabled, full_order, version, pinned)
        return JsonResponse({"ok": True, **config_state(room)})
    except services.Conflict as exc:
        return fail_json(exc, 409)
    except (ValueError, TypeError, ValidationError):
        return fail_json("기능 설정값을 확인해 주세요. 저장하지 않았어요.", 400)
    except OperationalError:
        return fail_json("지금은 설정을 저장할 수 없어요. 선택한 기능은 화면에 남아 있어요.", 503)


@require_http_methods(["GET", "POST"])
def cadence_edit(request, room_id, cadence_id=None):
    room, member = services.get_room(request, room_id)
    services.require_manager(member)
    instance = get_object_or_404(CareCadence, pk=cadence_id, room=room) if cadence_id else None
    initial = {"activity": request.GET.get("activity", "")}
    form = cadences.CadenceForm(request.POST if request.method == "POST" else None, member=member, instance=instance, initial=initial)
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            cadences.save(member, form, cadence_id)
            messages.success(request, "반복 알림를 저장했어요. 기록은 그대로 유지돼요.")
            return redirect("cadence_list", room_id=room.pk)
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
            status = 409 if isinstance(exc, services.Conflict) else 422
        except OperationalError:
            form.add_error(None, "지금은 저장할 수 없어요. 입력한 내용은 남아 있으니 다시 저장해 주세요.")
            status = 503
    return render(request, "care/form.html", {"title": "반복 알림 수정" if instance else "반복 알림 추가", "intro": "양치와 목욕처럼 같은 기능 안의 활동도 각각 간격을 정할 수 있어요.",
                  "form": form, "submit_label": "알림 저장", "back_url": reverse("cadence_list", args=[room.pk]), "active_tab": "routines"}, status=status)


@require_http_methods(["GET"])
def cadence_list(request, room_id):
    room, member = services.get_room(request, room_id)
    return render(request, "care/cadences.html", {"title": "반복 알림", "cadences": cadences.schedule(member, include_paused=True), "active_tab": "routines", "live_page": True})


@require_POST
def cadence_snooze(request, room_id, cadence_id):
    room, member = services.get_room(request, room_id)
    services.require_manager(member)
    get_object_or_404(CareCadence, pk=cadence_id, room=room)
    try:
        until = date.fromisoformat(request.POST.get("until", ""))
        version = int(request.POST.get("version", ""))
        cadences.snooze(member, cadence_id, version, until)
    except (ValueError, ValidationError) as exc:
        return fail_json(exc if isinstance(exc, ValidationError) else "미룰 날짜를 확인해 주세요.", 409)
    except OperationalError:
        return fail_json("지금은 날짜를 바꿀 수 없어요. 잠시 후 다시 미뤄 주세요.", 503)
    if wants_fragment(request):
        return JsonResponse({"ok": True, "message": "이번 알림 날짜를 미뤘어요. 반복 간격은 그대로예요."})
    return redirect("cadence_list", room_id=room.pk)
