"""Routine versions, available slots and existing record linking."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from care.models import (
    Activity,
    Membership,
    Routine,
    RoutineItem,
    RoutineRun,
    RoutineVersion,
)
from care.services.access import (
    can_edit_record,
    fresh_member,
    require_manager,
    require_module,
)
from care.services.common import Conflict, bump, local_date


def routine_snapshot(routine):
    return {"name": routine.name, "at": routine.at.isoformat(), "weekdays": routine.weekdays,
            "active": routine.active, "assignee_id": str(routine.assignee_id) if routine.assignee_id else None,
            "start_date": routine.start_date.isoformat(), "end_date": routine.end_date.isoformat() if routine.end_date else None,
            "note": routine.note,
            "items": [{"id": str(i.pk), "module": i.module, "position": i.position,
                       "preset_id": str(i.preset_id) if i.preset_id else None} for i in routine.items.filter(active=True)]}


def effective_routine(routine, day):
    version = routine.versions.filter(effective_on__lte=day).first()
    return version.snapshot if version else None


def module_enabled_on(room, key, day):
    today = local_date(room)
    if day >= today:
        return room.module_settings.filter(key=key, enabled=True).exists()
    end = datetime.combine(day + timedelta(days=1), time.min, ZoneInfo(room.timezone))
    version = room.module_versions.filter(effective_at__lt=end).order_by("-effective_at", "-id").first()
    return bool(version and key in version.settings.get("enabled", []))


def require_routine_slot(member, item_id, day, module):
    if not day or day > local_date(member.room):
        raise ValidationError("아직 오지 않은 날의 할 일은 완료로 기록할 수 없어요.")
    item = RoutineItem.objects.select_related("routine").filter(pk=item_id, routine__room=member.room).first()
    if not item or item.module != module:
        raise ValidationError("이 공간에서 해당 활동이 포함된 루틴을 선택해 주세요.")
    snap = effective_routine(item.routine, day)
    if (not snap or not snap["active"] or day < item.routine.start_date or
            (snap.get("end_date") and day.isoformat() > snap["end_date"]) or
            day.weekday() not in snap["weekdays"] or not any(i["id"] == str(item.pk) for i in snap["items"])):
        raise ValidationError("선택한 날짜에는 이 루틴이 없어요. 날짜를 다시 골라 주세요.")
    if not module_enabled_on(member.room, module, day):
        raise ValidationError("이 활동의 기능을 사용하지 않아 루틴도 잠시 멈췄어요.")
    return item


@transaction.atomic
def save_routine(member, form, routine_id=None, *, _receipt=False):
    member = fresh_member(member)
    require_manager(member)
    if not form.is_valid():
        raise ValidationError("루틴 입력값을 확인해 주세요.")
    data = form.cleaned_data
    if routine_id is None and data.get("request_token") and not _receipt:
        from care.services.idempotency import once
        return once(member, form, "routine.create", lambda: save_routine(member, form, _receipt=True))
    if routine_id:
        routine = Routine.objects.select_for_update().get(pk=routine_id, room=member.room)
        if routine.version != data.get("version"):
            raise Conflict("루틴이 바뀌었어요. 최신 내용을 확인한 뒤 다시 저장해 주세요.")
        routine.version += 1
    else:
        routine = Routine(room=member.room, start_date=data.get("start_date") or local_date(member.room))
    for key in data["modules"]:
        require_module(member, key)
    assignee = data.get("assignee")
    if assignee and not Membership.objects.filter(pk=assignee.pk, room=member.room, active=True).exists():
        raise ValidationError("이 공간의 구성원 중 담당할 사람을 골라 주세요.")
    routine.name, routine.at = data["name"], data["at"]
    routine.end_date, routine.note = data.get("end_date"), data.get("note", "")
    routine.weekdays, routine.active, routine.assignee = [int(x) for x in data["weekdays"]], data["active"], assignee
    routine.save()
    routine.items.update(active=False)
    for position, key in enumerate(data["modules"]):
        item = routine.items.filter(module=key).first()
        preset = data.get("preset")
        preset = preset if preset and preset.module == key else None
        if item:
            item.active, item.position, item.preset = True, position, preset
            item.save(update_fields=["active", "position", "preset"])
        else:
            RoutineItem.objects.create(routine=routine, module=key, position=position, preset=preset)
    RoutineVersion.objects.create(routine=routine, effective_on=local_date(member.room), snapshot=routine_snapshot(routine))
    bump(member.room, member, "routine.updated" if routine_id else "routine.created", {"routine_id": str(routine.pk)})
    return routine


@transaction.atomic
def link_activity(member, item_id, day, activity_id):
    member = fresh_member(member)
    activity = Activity.objects.filter(pk=activity_id, room=member.room, voided=False, status__in=("done", "active")).first()
    if not activity:
        raise ValidationError("이 루틴에 연결할 기록이 없어요. 날짜와 활동을 확인해 주세요.")
    require_module(member, activity.module)
    if not can_edit_record(member, activity):
        raise PermissionDenied("직접 작성한 기록만 루틴에 연결할 수 있어요.")
    if activity.occurred_at.astimezone(ZoneInfo(member.room.timezone)).date() != day:
        raise ValidationError("루틴과 같은 날짜에 남긴 기록을 선택해 주세요.")
    item = require_routine_slot(member, item_id, day, activity.module)
    slot, _ = RoutineRun.objects.get_or_create(item=item, date=day)
    if slot.activity_id == activity.pk:
        return slot
    if slot.activity_id or RoutineRun.objects.filter(activity=activity).exists():
        raise Conflict("이미 다른 기록이나 루틴과 연결돼 있어요. 기존 연결을 먼저 확인해 주세요.")
    slot.activity = activity
    slot.skipped_at, slot.skip_note = None, ""
    slot.save(update_fields=["activity", "skipped_at", "skip_note"])
    bump(member.room, member, "routine.linked", {"activity_id": str(activity.pk)})
    return slot
