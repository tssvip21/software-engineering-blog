"""Room-scoped business operations. Every mutation rechecks live permissions."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import hashlib
import hmac
import json
import secrets
import uuid
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.http import Http404
from django.utils import timezone
from .models import (
    Room, Membership, ModuleSetting, Product, Activity, ActivityRevision,
    Invitation, Routine, RoutineItem, RoutineVersion, RoutineRun,
    AuditEvent, RateBucket, ModuleConfigVersion,
)
from .modules import MODULES, DEFAULT_MODULES, SENSITIVE_KEYS, decimal


class Conflict(ValidationError):
    """The user's original input remains available when a concurrent change wins."""


def guest_identity(request):
    raw = request.session.get("guest_identity")
    try:
        return uuid.UUID(raw) if raw else None
    except (ValueError, TypeError, AttributeError):
        return None


def my_memberships(request):
    base = Membership.objects.filter(active=True, room__archived_at__isnull=True).select_related("room")
    if request.user.is_authenticated:
        return base.filter(user=request.user)
    key = guest_identity(request)
    return base.filter(guest_key=key, user__isnull=True) if key else base.none()


def get_room(request, room_id):
    member = my_memberships(request).filter(room_id=room_id).first()
    if member is None:
        raise Http404("참여 중인 돌봄 공간을 찾지 못했어요.")
    timezone.activate(ZoneInfo(member.room.timezone))
    request.care_room, request.care_member = member.room, member
    return member.room, member


def visible_modules(member):
    if member.can_manage or member.room.guest_sensitive:
        return tuple(MODULES)
    return tuple(k for k in MODULES if k not in SENSITIVE_KEYS)


def require_module(member, key, *, adding=False):
    if key not in MODULES:
        raise Http404("이 돌봄 기능을 찾지 못했어요.")
    if key not in visible_modules(member):
        raise PermissionDenied("이 기록은 방장과 관리자만 볼 수 있어요.")
    if adding and not ModuleSetting.objects.filter(room=member.room, key=key, enabled=True).exists():
        raise ValidationError("사용하지 않는 기능이에요. 방장이나 관리자에게 사용 설정을 요청해 주세요.")


def require_manager(member):
    if not member.can_manage:
        raise PermissionDenied("방장이나 관리자만 바꿀 수 있어요.")


def require_owner(member):
    if not member.can_manage or member.role != "owner" or member.room.owner_id != member.user_id:
        raise PermissionDenied("방장만 할 수 있는 작업이에요.")


def fresh_member(member):
    room = Room.objects.select_for_update().filter(pk=member.room_id, archived_at__isnull=True).first()
    if room is None:
        raise PermissionDenied("이 돌봄 공간에 참여하고 있지 않아요.")
    fresh = Membership.objects.select_related("room").filter(pk=member.pk, room=room, active=True).first()
    if fresh is None or (fresh.user_id != member.user_id) or (fresh.guest_key != member.guest_key):
        raise PermissionDenied("참여 권한이 바뀌었어요. 새로고침해서 확인해 주세요.")
    return fresh


def bump(room, actor, action, details=None):
    Room.objects.filter(pk=room.pk).update(revision=F("revision") + 1)
    AuditEvent.objects.create(room=room, actor=actor, action=action, details=details or {})


def local_date(room):
    return timezone.now().astimezone(ZoneInfo(room.timezone)).date()


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(x) for x in value]
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def activity_snapshot(activity):
    return json_safe({"module": activity.module, "occurred_at": activity.occurred_at, "ended_at": activity.ended_at,
                      "status": activity.status, "payload": activity.payload, "nutrition": activity.nutrition,
                      "note": activity.note, "performer_id": activity.performer_id, "voided": activity.voided,
                      "void_reason": activity.void_reason, "version": activity.version})


@transaction.atomic
def create_room(user, form):
    if not user.is_authenticated:
        raise PermissionDenied("반려동물을 등록하려면 구글 로그인이 필요해요.")
    if not form.is_valid():
        raise ValidationError("입력값을 확인해 주세요.")
    room = form.save(commit=False)
    room.owner = user
    room.full_clean()
    room.save()
    member = Membership.objects.create(room=room, user=user, display_name=(user.first_name or "보호자")[:40], role="owner")
    defaults = DEFAULT_MODULES.get(room.species, DEFAULT_MODULES["other"]) if form.cleaned_data.get("use_defaults", True) else ()
    ordering = list(defaults) + [k for k in MODULES if k not in defaults]
    ModuleSetting.objects.bulk_create([ModuleSetting(room=room, key=k, enabled=k in defaults, pinned=k in defaults, position=i) for i, k in enumerate(ordering)])
    ModuleConfigVersion.objects.create(room=room, settings={"enabled": list(defaults), "order": ordering})
    bump(room, member, "room.created", {"species": room.species})
    return room


@transaction.atomic
def update_room(member, form):
    member = fresh_member(member)
    require_manager(member)
    room = Room.objects.get(pk=member.room_id)
    if form.cleaned_data.get("version") != room.settings_version:
        raise Conflict("다른 사람이 공간 설정을 바꿨어요. 최신 내용을 불러온 뒤 다시 확인해 주세요.")
    for key in ("name", "species", "subspecies", "breed_key", "size_origin", "maturity", "size", "birth_date", "sex", "timezone"):
        setattr(room, key, form.cleaned_data[key])
    if member.role == "owner":
        room.guest_sensitive = form.cleaned_data["guest_sensitive"]
    room.settings_version += 1
    room.full_clean()
    room.save()
    bump(room, member, "room.updated")
    return room


@transaction.atomic
def update_modules(member, enabled, order, version, pinned=None):
    member = fresh_member(member)
    require_manager(member)
    if member.room.settings_version != version:
        raise Conflict("기능 설정이 이미 바뀌었어요. 최신 설정을 확인한 뒤 다시 선택해 주세요.")
    if not isinstance(order, list) or len(order) != len(MODULES) or set(order) != set(MODULES) or not set(enabled) <= set(MODULES):
        raise ValidationError("기능 목록과 순서를 확인해 주세요.")
    rows = {m.key: m for m in member.room.module_settings.all()}
    from .modules import RETIRED_MODULES
    # Compatibility with earlier clients: an already active retired function may
    # continue its existing schedule; no new room can activate it through this API.
    if any(key in enabled and not rows[key].enabled for key in RETIRED_MODULES):
        raise ValidationError("통합된 이전 기능은 새로 켤 수 없어요. 위생·손질 또는 특이사항 메모에서 기록해 주세요.")
    if pinned is not None and (not isinstance(pinned, list) or not set(pinned) <= set(enabled)):
        raise ValidationError("오늘 화면에 표시할 기능은 사용 중인 기능에서 골라 주세요.")
    for index, key in enumerate(order):
        if key in RETIRED_MODULES:
            continue  # Preserve old pins/order/availability for existing routine snapshots.
        rows[key].position, rows[key].enabled = index, key in enabled
        if pinned is not None:
            rows[key].pinned = key in pinned
    ModuleSetting.objects.bulk_update(list(rows.values()), ["position", "enabled", "pinned"])
    Room.objects.filter(pk=member.room_id).update(settings_version=F("settings_version") + 1)
    ModuleConfigVersion.objects.create(room=member.room, settings={"enabled": [k for k in order if rows[k].enabled], "order": order, "pinned": [k for k in order if rows[k].pinned]})
    bump(member.room, member, "modules.updated", {"enabled_count": len(enabled)})


@transaction.atomic
def save_product(member, form, product_id=None, *, _receipt=False):
    member = fresh_member(member)
    require_manager(member)
    if not form.is_valid():
        raise ValidationError("제품 입력값을 확인해 주세요.")
    if product_id is None and form.cleaned_data.get("request_token") and not _receipt:
        from .mutations7 import once
        return once(member, form, "product.create", lambda: save_product(member, form, _receipt=True))
    if product_id:
        product = Product.objects.select_for_update().get(pk=product_id, room=member.room)
        if product.version != form.cleaned_data.get("version"):
            raise Conflict("제품 정보가 바뀌었어요. 새로고침한 뒤 다시 확인해 주세요.")
        product.version += 1
    else:
        product = Product(room=member.room)
    for key in form.Meta.fields:
        setattr(product, key, form.cleaned_data[key])
    product.full_clean()
    product.save()
    bump(member.room, member, "product.updated" if product_id else "product.created", {"product_id": str(product.pk)})
    return product


def product_snapshot(product):
    return json_safe({"id": product.pk, "name": product.name, "brand": product.brand, "energy": product.energy, "basis": product.energy_basis,
                      "grams_per_piece": product.grams_per_piece, "protein": product.protein, "protein_kind": product.protein_kind,
                      "fat": product.fat, "fat_kind": product.fat_kind, "source": product.source, "version": product.version})


def nutrition_values(payload, snapshot):
    consumed = decimal(payload.get("consumed"))
    amount = consumed if consumed is not None else decimal(payload.get("quantity"))
    unit = payload.get("unit")
    if amount is None:
        return {"basis": "unknown", "kcal": None, "grams": None, "snapshot": snapshot, "nutrients": {}}
    grams = None
    if unit in ("g", "kg", "oz"):
        grams = amount * {"g": Decimal(1), "kg": Decimal(1000), "oz": Decimal("28.349523125")}[unit]
    elif unit == "piece" and snapshot and snapshot.get("grams_per_piece") is not None:
        grams = amount * decimal(snapshot["grams_per_piece"])
    kcal = None
    if snapshot and snapshot.get("energy") is not None:
        energy, basis = decimal(snapshot["energy"]), snapshot["basis"]
        if basis in ("100g", "kg") and grams is not None:
            kcal = energy * grams / (100 if basis == "100g" else 1000)
        elif basis == "piece" and unit == "piece":
            kcal = energy * amount
        elif basis == "piece" and grams is not None and decimal(snapshot.get("grams_per_piece")):
            kcal = energy * grams / decimal(snapshot["grams_per_piece"])
        elif basis == "100ml" and unit == "ml":
            kcal = energy * amount / 100
    nutrients = {}
    if snapshot and grams is not None:
        for key in ("protein", "fat"):
            if snapshot.get(key) is not None:
                nutrients[key] = {"grams": str((grams * decimal(snapshot[key]) / 100).quantize(Decimal("0.001"))), "kind": snapshot.get(key + "_kind", "exact")}
    return {"basis": "consumed" if consumed is not None else "provided", "kcal": str(kcal.quantize(Decimal("0.001"))) if kcal is not None else None,
            "grams": str(grams) if grams is not None else None, "snapshot": snapshot, "nutrients": nutrients}


def inventory_value(payload):
    unit = payload["unit"]
    factor = {"g": Decimal(1), "kg": Decimal(1000), "ml": Decimal(1), "l": Decimal(1000), "piece": Decimal(1)}[unit]
    base_unit = {"kg": "g", "l": "ml"}.get(unit, unit)
    key = (" ".join(payload["item"].casefold().split()), base_unit)
    return key, decimal(payload["quantity"]) * factor, (decimal(payload.get("minimum")) * factor if payload.get("minimum") is not None else None)


def validate_inventory(room):
    from .inventory_ledger import validate
    validate(room)


def can_edit_record(member, activity):
    return member.active and activity.room_id == member.room_id and activity.module in visible_modules(member) and (member.can_manage or activity.author_id == member.pk)


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
def save_activity(member, module, form, activity_id=None):
    member = fresh_member(member)
    require_module(member, module, adding=activity_id is None)
    if not form.is_valid():
        raise ValidationError("입력값을 확인해 주세요.")
    data, payload = form.cleaned_data, form.cleaned_payload()
    activity = None
    if activity_id:
        activity = Activity.objects.select_for_update().filter(pk=activity_id, room=member.room, module=module).first()
        if activity is None:
            raise Http404()
        if not can_edit_record(member, activity):
            raise PermissionDenied("직접 작성한 기록만 수정할 수 있어요.")
        if activity.voided:
            raise Conflict("취소한 기록은 수정할 수 없어요. 필요한 내용은 새 기록으로 남겨 주세요.")
        if activity.version != data.get("version"):
            raise Conflict("다른 사람이 기록을 수정했어요. 새로고침해서 최신 내용을 먼저 확인해 주세요.")
    performer = data.get("performer")
    if performer:
        valid_performer = Membership.objects.filter(pk=performer.pk, room=member.room).first()
        if valid_performer is None or (not valid_performer.active and (activity is None or activity.performer_id != performer.pk)):
            raise ValidationError("이 공간의 구성원 중 돌본 사람을 골라 주세요.")
    raw = json_safe({"module": module, "payload": payload, "status": data["status"], "occurred_at": data["occurred_at"],
                     "ended_at": data.get("ended_at"), "performer": performer.pk if performer else None, "note": data.get("note", ""),
                     "routine_item": data.get("routine_item"), "routine_date": data.get("routine_date")})
    fingerprint = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if activity is None:
        duplicate = Activity.objects.filter(room=member.room, client_key=data["client_key"]).first()
        if duplicate:
            if duplicate.author_id == member.pk and duplicate.module == module and hmac.compare_digest(duplicate.fingerprint, fingerprint):
                return duplicate, False
            raise Conflict("앞서 저장한 요청과 내용이 달라요. 새 입력창을 열어 기록해 주세요.")
    slot = None
    if not activity and data.get("routine_item"):
        if data["status"] == "planned":
            raise ValidationError("예정만 적은 기록으로는 루틴을 완료할 수 없어요.")
        item = require_routine_slot(member, data["routine_item"], data.get("routine_date"), module)
        if data["occurred_at"].astimezone(ZoneInfo(member.room.timezone)).date() != data["routine_date"]:
            raise ValidationError("활동한 날짜와 루틴 날짜를 맞춰 주세요.")
        slot, _ = RoutineRun.objects.get_or_create(item=item, date=data["routine_date"])
        if slot.activity_id and not slot.activity.voided:
            raise Conflict("이미 완료 기록이 연결돼 있어요. 기존 기록을 먼저 확인해 주세요.")
    elif activity:
        existing_run = RoutineRun.objects.filter(activity=activity).first()
        if existing_run and (data["status"] == "planned" or data["occurred_at"].astimezone(ZoneInfo(member.room.timezone)).date() != existing_run.date):
            raise ValidationError("루틴에 연결한 기록은 같은 날짜의 진행 중 또는 완료 상태여야 해요. 잘못 남긴 기록은 취소하고 다시 적어 주세요.")
    nutrition = {}
    if module in ("meal", "treat"):
        product = data.get("product")
        snapshot = None
        if product:
            product = Product.objects.filter(pk=product.pk, room=member.room).first()
            if product is None:
                raise ValidationError("이 공간에 등록한 제품을 선택해 주세요.")
            same_product = bool(activity and activity.payload.get("product_id") == str(product.pk))
            if not product.active and not same_product:
                raise ValidationError("사용 중인 제품을 선택해 주세요.")
            snapshot = activity.nutrition.get("snapshot") if same_product and not data.get("refresh_product") else product_snapshot(product)
            payload["food_name"] = snapshot["name"] if snapshot else product.name
        nutrition = nutrition_values(payload, snapshot)
        from .inventory_ledger import product_binding, food_effect
        same_original_product = bool(activity and activity.payload.get("product_id") == payload.get("product_id"))
        # No retroactive stock link: old unbound entries remain unbound on correction.
        if same_original_product and not data.get("refresh_stock"):
            binding = activity.payload.get("_stock")
        else:
            binding = product_binding(product)
        effect = food_effect(payload, binding)
        if effect is None:
            payload.pop("_stock", None)
        else:
            payload["_stock"] = effect
    before = activity_snapshot(activity) if activity else {}
    creating = activity is None
    if creating:
        activity = Activity(room=member.room, module=module, author=member, client_key=data["client_key"])
    else:
        activity.version += 1
    activity.payload, activity.nutrition = payload, nutrition
    activity.occurred_at, activity.ended_at = data["occurred_at"], data.get("ended_at")
    activity.status, activity.performer = data["status"], performer
    activity.note, activity.fingerprint = data.get("note", ""), fingerprint
    activity.save()
    if module == "inventory" or payload.get("_stock") or (before.get("payload") or {}).get("_stock"):
        validate_inventory(member.room)
    if slot:
        slot.activity = activity
        slot.skipped_at, slot.skip_note = None, ""
        slot.save(update_fields=["activity", "skipped_at", "skip_note"])
    reason = "기록 생성" if creating else data.get("edit_reason") or "내용 수정"
    ActivityRevision.objects.create(activity=activity, actor=member, before=before, after=activity_snapshot(activity), reason=reason)
    bump(member.room, member, "activity.created" if creating else "activity.updated", {"module": module, "activity_id": str(activity.pk)})
    return activity, True


@transaction.atomic
def cancel_activity(member, activity_id, version, reason):
    member = fresh_member(member)
    activity = Activity.objects.select_for_update().get(pk=activity_id, room=member.room)
    require_module(member, activity.module)
    if not can_edit_record(member, activity):
        raise PermissionDenied("직접 작성한 기록만 취소할 수 있어요.")
    if activity.voided:
        return activity
    if activity.version != version:
        raise Conflict("기록 내용이 바뀌었어요. 최신 내용을 확인한 뒤 취소해 주세요.")
    before = activity_snapshot(activity)
    activity.voided, activity.void_reason = True, reason.strip()
    if not activity.void_reason:
        raise ValidationError("취소 이유를 입력해 주세요.")
    activity.version += 1
    activity.save(update_fields=["voided", "void_reason", "version", "updated_at"])
    if activity.module == "inventory" or activity.payload.get("_stock"):
        validate_inventory(member.room)
    RoutineRun.objects.filter(activity=activity).update(activity=None, skipped_at=None, skip_note="")
    ActivityRevision.objects.create(activity=activity, actor=member, before=before, after=activity_snapshot(activity), reason=activity.void_reason)
    bump(member.room, member, "activity.cancelled", {"activity_id": str(activity.pk)})
    return activity


@transaction.atomic
def create_invitation(member, hours, max_uses):
    member = fresh_member(member)
    require_owner(member)
    if hours not in (1, 24, 168) or not 1 <= max_uses <= 100:
        raise ValidationError("초대 설정을 확인해 주세요.")
    token = secrets.token_urlsafe(32)
    invite = Invitation.objects.create(room=member.room, created_by=member, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=timezone.now() + timedelta(hours=hours), max_uses=max_uses)
    bump(member.room, member, "invite.created", {"invite_id": str(invite.pk), "max_uses": max_uses})
    return invite, token


def get_invitation(token, lock=False):
    if not isinstance(token, str) or not 32 <= len(token) <= 120:
        raise ValidationError("초대 링크를 확인해 주세요.")
    qs = Invitation.objects.select_related("room")
    if lock:
        qs = qs.select_for_update()
    invite = qs.filter(token_hash=hashlib.sha256(token.encode()).hexdigest()).first()
    if invite is None or not invite.usable:
        raise ValidationError("기간이 지났거나 사용을 중단한 초대예요. 방장에게 새 링크를 요청해 주세요.")
    return invite


@transaction.atomic
def join_room(request, token, name):
    invite = get_invitation(token, lock=True)
    Room.objects.select_for_update().get(pk=invite.room_id)
    if request.user.is_authenticated:
        identity = {"user": request.user}
    else:
        key = guest_identity(request) or uuid.uuid4()
        request.session["guest_identity"] = str(key)
        request.session.cycle_key()
        identity = {"guest_key": key, "user": None}
    member = Membership.objects.filter(room=invite.room, **identity).first()
    if member and member.active:
        return member
    if not invite.usable:
        raise Conflict("사용할 수 없는 초대예요. 새 링크를 받아 주세요.")
    if member:
        member.active, member.role, member.display_name = True, "guest", name.strip()[:40]
        member.save(update_fields=["active", "role", "display_name"])
    else:
        member = Membership.objects.create(room=invite.room, display_name=name.strip()[:40], role="guest", **identity)
    invite.uses += 1
    invite.save(update_fields=["uses"])
    bump(invite.room, member, "member.joined")
    return member


@transaction.atomic
def revoke_invitation(member, invite_id):
    member = fresh_member(member)
    require_owner(member)
    invite = Invitation.objects.get(pk=invite_id, room=member.room)
    invite.revoked = True
    invite.save(update_fields=["revoked"])
    bump(member.room, member, "invite.revoked", {"invite_id": str(invite.pk)})


@transaction.atomic
def change_member(member, target_id, action, revoke_links=False):
    member = fresh_member(member)
    require_owner(member)
    target = Membership.objects.get(pk=target_id, room=member.room, active=True)
    if target.role == "owner":
        raise ValidationError("방장의 권한은 이 메뉴에서 바꿀 수 없어요.")
    if action == "promote":
        if target.user_id is None:
            raise ValidationError("구글 계정을 연결한 구성원만 관리자로 지정할 수 있어요.")
        target.role = "admin"
    elif action == "demote":
        target.role = "guest"
    elif action == "remove":
        target.active = False
        target.role = "guest"
        if revoke_links:
            member.room.invitations.filter(revoked=False).update(revoked=True)
    else:
        raise ValidationError("이 작업은 할 수 없어요. 메뉴에서 다시 선택해 주세요.")
    target.save(update_fields=["role", "active"])
    bump(member.room, member, "member." + action, {"member_id": str(target.pk), "links_revoked": bool(revoke_links)})


@transaction.atomic
def leave_room(member):
    member = fresh_member(member)
    if member.role == "owner":
        raise ValidationError("방장은 바로 나갈 수 없어요. 더 이상 사용하지 않으면 공간 설정에서 보관해 주세요.")
    member.active = False
    member.role = "guest"
    member.save(update_fields=["active", "role"])
    bump(member.room, member, "member.left")


@transaction.atomic
def archive_room(member, confirmation):
    member = fresh_member(member)
    require_owner(member)
    if confirmation != member.room.name:
        raise ValidationError("반려동물 이름을 정확히 입력해 주세요.")
    bump(member.room, member, "room.archived")
    member.room.archived_at = timezone.now()
    member.room.save(update_fields=["archived_at"])
    member.room.invitations.update(revoked=True)


@transaction.atomic
def save_routine(member, form, routine_id=None, *, _receipt=False):
    member = fresh_member(member)
    require_manager(member)
    if not form.is_valid():
        raise ValidationError("루틴 입력값을 확인해 주세요.")
    data = form.cleaned_data
    if routine_id is None and data.get("request_token") and not _receipt:
        from .mutations7 import once
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


@transaction.atomic
def merge_guest_memberships(guest_key, user):
    if not guest_key:
        return
    for guest in Membership.objects.filter(guest_key=guest_key, user__isnull=True).select_for_update():
        existing = Membership.objects.filter(room=guest.room, user=user).first()
        if existing is None:
            guest.user, guest.guest_key, guest.role = user, None, "guest"
            guest.save(update_fields=["user", "guest_key", "role"])
            bump(guest.room, guest, "member.account_linked")
            continue
        Activity.objects.filter(author=guest).update(author=existing)
        Activity.objects.filter(performer=guest).update(performer=existing)
        ActivityRevision.objects.filter(actor=guest).update(actor=existing)
        Routine.objects.filter(assignee=guest).update(assignee=existing)
        Invitation.objects.filter(created_by=guest).update(created_by=existing)
        AuditEvent.objects.filter(actor=guest).update(actor=existing)
        guest.active = False
        guest.save(update_fields=["active"])
        bump(existing.room, existing, "member.account_linked")


@transaction.atomic
def rate_limit(request, scope, limit=30, seconds=60):
    ip = request.META.get("REMOTE_ADDR", "unknown")
    identity = request.session.session_key or "anonymous"
    period = int(timezone.now().timestamp()) // seconds
    accepted = True
    for label, suffix, maximum in (("session", identity, limit), ("ip", "", limit * 4)):
        key = hmac.new(settings.SECRET_KEY.encode(), f"{scope}:{label}:{ip}:{suffix}".encode(), hashlib.sha256).hexdigest()
        bucket, _ = RateBucket.objects.get_or_create(key=key, defaults={"period": period, "count": 0})
        if bucket.period != period:
            bucket.period, bucket.count = period, 0
        bucket.count += 1
        bucket.save(update_fields=["period", "count"])
        accepted = accepted and bucket.count <= maximum
    return accepted


@transaction.atomic
def delete_room(member, confirmation):
    member = fresh_member(member)
    require_owner(member)
    if confirmation != member.room.name + " 삭제":
        raise ValidationError("반려동물 이름 뒤에 ' 삭제'를 붙여 정확히 입력해 주세요.")
    room = member.room
    ActivityRevision.objects.filter(activity__room=room).delete()
    AuditEvent.objects.filter(room=room).delete()
    Invitation.objects.filter(room=room).delete()
    RoutineRun.objects.filter(item__routine__room=room).delete()
    Activity.objects.filter(room=room).delete()
    RoutineVersion.objects.filter(routine__room=room).delete()
    RoutineItem.objects.filter(routine__room=room).delete()
    Routine.objects.filter(room=room).delete()
    Membership.objects.filter(room=room).delete()
    room.delete()
