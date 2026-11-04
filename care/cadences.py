"""Completion-based, explicitly user-configured care intervals.

Display rules never grant permissions and never imply an unrecorded activity was missed.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .forms import DateInput
from .models import Activity, CareCadence
from .modules import MODULES
from . import services

VARIANT_FIELDS = {
    "water": "kind", "toilet": "kind", "hygiene": "task", "grooming": "task",
    "cleaning": "target", "veterinary": "kind", "sleep": "kind", "environment": "metric",
    "reproduction": "kind", "shedding": "kind", "expense": "category",
}
LIMITS = {"days": 366, "weeks": 52, "months": 24}


def activity_options(keys):
    options = {}
    for key in keys:
        spec = MODULES[key]
        field = VARIANT_FIELDS.get(key, "")
        variants = next((f.choices for f in spec.fields if f.key == field), ())
        if variants:
            for value, label in variants:
                if key == "reproduction" and value in ("heat", "egg"):
                    continue
                options[f"{key}:{field}:{value}"] = (key, field, value, spec.name + " · " + label)
        else:
            options[key + "::"] = (key, "", "", spec.name)
    return options


class CadenceForm(forms.Form):
    activity = forms.ChoiceField(label="주기를 정할 활동")
    title = forms.CharField(label="안내 이름 (선택)", max_length=80, required=False)
    interval = forms.IntegerField(label="반복 간격", min_value=1, max_value=366, widget=forms.NumberInput(attrs={"inputmode": "numeric", "placeholder": "직접 정해 주세요"}))
    unit = forms.ChoiceField(label="간격 단위", choices=[("days", "일"), ("weeks", "주"), ("months", "개월")])
    first_due = forms.DateField(label="다음 안내 날짜", widget=DateInput())
    active = forms.BooleanField(label="주기 안내 켜기", required=False, initial=True)
    version = forms.IntegerField(widget=forms.HiddenInput, required=False, min_value=1)

    def __init__(self, *args, member, instance=None, **kwargs):
        self.member, self.instance = member, instance
        enabled = set(member.room.module_settings.filter(enabled=True).values_list("key", flat=True))
        if instance:
            enabled.add(instance.module)
        keys = [key for key in services.visible_modules(member) if key in enabled]
        self.options = activity_options(keys)
        initial = kwargs.setdefault("initial", {})
        initial.setdefault("first_due", services.local_date(member.room))
        if instance:
            initial.update({"activity": f"{instance.module}:{instance.variant_field}:{instance.variant_value}",
                            "title": instance.title, "interval": instance.interval, "unit": instance.unit,
                            "first_due": state(instance, member.room)["next_due"], "active": instance.active,
                            "version": instance.version})
        super().__init__(*args, **kwargs)
        self.fields["activity"].choices = [("", "활동을 선택해 주세요")] + [(k, v[3]) for k, v in self.options.items()]
        self.fields["first_due"].help_text = "간격이나 안내 날짜를 바꾸면 이 날짜부터 새 주기를 시작해요."
        self.fields["interval"].help_text = "의학적 권장값이 아닌, 직접 정한 돌봄 간격이에요. 완료 기록을 남기면 다음 날짜를 계산해요."

    def clean(self):
        data = super().clean()
        if data.get("activity"):
            data["selection"] = self.options[data["activity"]]
            data["title"] = data.get("title") or data["selection"][3]
        if data.get("unit") and data.get("interval", 0) > LIMITS[data["unit"]]:
            self.add_error("interval", f"이 단위는 {LIMITS[data['unit']]}까지 입력할 수 있어요.")
        day = data.get("first_due")
        today = services.local_date(self.member.room)
        if day and not today - timedelta(days=366) <= day <= today + timedelta(days=3660):
            self.add_error("first_due", "지난 1년부터 앞으로 10년 안의 날짜를 선택해 주세요.")
        return data


def matches(cadence, activity):
    if activity.module != cadence.module or activity.voided or activity.status != "done":
        return False
    if cadence.module == "medication" and activity.payload.get("result") != "given":
        return False
    return not cadence.variant_field or str(activity.payload.get(cadence.variant_field, "")) == cadence.variant_value


def state(cadence, room, *, today=None, records=None, enabled=None):
    today = today or services.local_date(room)
    tz = ZoneInfo(room.timezone)
    if records is None:
        records = Activity.objects.filter(room=room, module=cadence.module, voided=False, status="done").filter(
            Q(created_at__gte=cadence.configured_at) | Q(ended_at__gte=cadence.configured_at)
        ).select_related("author")
    candidates = []
    for activity in records:
        # A setting change explicitly starts a new schedule; older entries are not reinterpreted.
        completed_at = activity.ended_at or activity.occurred_at
        if not matches(cadence, activity):
            continue
        if activity.created_at < cadence.configured_at and completed_at < cadence.configured_at:
            continue
        day = completed_at.astimezone(tz).date()
        if cadence.configured_at.astimezone(tz).date() <= day <= today:
            candidates.append(activity)
    latest = max(candidates, key=lambda a: (a.ended_at or a.occurred_at, a.created_at, str(a.pk)), default=None)
    due = cadence.first_due
    if latest:
        day = (latest.ended_at or latest.occurred_at).astimezone(tz).date()
        due = day + relativedelta(**{cadence.unit: cadence.interval})
    snoozed = bool(cadence.snoozed_until and cadence.snoozed_at and (
        not latest or max(latest.created_at, latest.ended_at or latest.occurred_at) <= cadence.snoozed_at
    ))
    if snoozed:
        due = max(due, cadence.snoozed_until)
    if enabled is None:
        enabled = room.module_settings.filter(key=cadence.module, enabled=True).exists()
    paused = not cadence.active or not enabled
    return {"cadence": cadence, "spec": MODULES[cadence.module], "next_due": due,
            "last": latest, "due": not paused and due <= today, "paused": paused,
            "snoozed": snoozed, "overdue_days": max(0, (today-due).days),
            "interval_label": f"{cadence.interval}{cadence.get_unit_display()}",
            "tomorrow": today + timedelta(days=1)}


def schedule(member, *, include_paused=False):
    room = member.room
    enabled = set(room.module_settings.filter(enabled=True).values_list("key", flat=True))
    visible = set(services.visible_modules(member))
    cadences = list(room.cadences.filter(module__in=visible))
    records = list(Activity.objects.filter(room=room, module__in={c.module for c in cadences}, voided=False, status="done").select_related("author")) if cadences else []
    result = [state(c, room, records=records, enabled=c.module in enabled) for c in cadences]
    if not include_paused:
        result = [row for row in result if not row["paused"]]
    return sorted(result, key=lambda row: (row["paused"], row["next_due"], row["cadence"].title))


@transaction.atomic
def save(member, form, cadence_id=None):
    member = services.fresh_member(member)
    services.require_manager(member)
    if not form.is_valid():
        raise ValidationError("주기 입력값을 확인해 주세요.")
    data = form.cleaned_data
    key, field, value, _ = data["selection"]
    services.require_module(member, key, adding=cadence_id is None)
    current = CareCadence.objects.select_for_update().get(pk=cadence_id, room=member.room) if cadence_id else CareCadence(room=member.room)
    if cadence_id and current.version != data.get("version"):
        raise services.Conflict("다른 참여자가 이 주기를 바꿨어요. 새로고침해서 확인해 주세요.")
    same = CareCadence.objects.filter(room=member.room, module=key, variant_field=field, variant_value=value)
    if cadence_id:
        same = same.exclude(pk=cadence_id)
    if same.exists():
        raise ValidationError("이 활동에는 이미 주기가 있어요. 기존 주기를 수정해 주세요.")
    old_due = state(current, member.room)["next_due"] if cadence_id else None
    reset = not cadence_id or (key, field, value, data["interval"], data["unit"], data["first_due"]) != (
        current.module, current.variant_field, current.variant_value, current.interval, current.unit, old_due)
    current.module, current.variant_field, current.variant_value = key, field, value
    current.title, current.interval, current.unit, current.active = data["title"], data["interval"], data["unit"], data["active"]
    if reset:
        current.first_due = data["first_due"]
        current.configured_at = timezone.now()
        current.snoozed_until = current.snoozed_at = None
    current.version += 1 if cadence_id else 0
    current.full_clean()
    current.save()
    services.bump(member.room, member, "cadence.updated" if cadence_id else "cadence.created", {"cadence_id": str(current.pk)})
    return current


@transaction.atomic
def snooze(member, cadence_id, version, until):
    member = services.fresh_member(member)
    services.require_manager(member)
    current = CareCadence.objects.select_for_update().get(pk=cadence_id, room=member.room)
    if current.version != version:
        raise services.Conflict("주기가 이미 변경됐어요. 새로고침해서 확인해 주세요.")
    services.require_module(member, current.module)
    today = services.local_date(member.room)
    if not today < until <= today + timedelta(days=366):
        raise ValidationError("내일부터 1년 안의 날짜를 선택해 주세요.")
    if not current.active or not member.room.module_settings.filter(key=current.module, enabled=True).exists():
        raise ValidationError("주기나 모듈이 꺼져 있어요. 먼저 모듈과 주기 안내를 켜 주세요.")
    current.snoozed_until, current.snoozed_at = until, timezone.now()
    current.version += 1
    current.save(update_fields=["snoozed_until", "snoozed_at", "version"])
    services.bump(member.room, member, "cadence.snoozed", {"cadence_id": str(current.pk), "until": until.isoformat()})
    return current
