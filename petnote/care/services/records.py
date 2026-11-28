"""Transactional record creation, revision and cancellation."""

import hashlib
import hmac
import json
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404

from care.models import Activity, ActivityRevision, Membership, Product, RoutineRun
from care.services.access import (
    _lock_member,
    can_edit_record,
    fresh_member,
    require_module,
)
from care.services.common import Conflict, bump, json_safe
from care.services.products import (
    nutrition_values,
    product_snapshot,
    validate_inventory,
)
from care.services.routines import require_routine_slot


def activity_snapshot(activity):
    return json_safe({"module": activity.module, "occurred_at": activity.occurred_at, "ended_at": activity.ended_at,
                      "status": activity.status, "payload": activity.payload, "nutrition": activity.nutrition,
                      "note": activity.note, "performer_id": activity.performer_id, "voided": activity.voided,
                      "void_reason": activity.void_reason, "version": activity.version})




@transaction.atomic
def save_activity(member, module, form, activity_id=None):
    member = _lock_member(member)
    return _save_activity_locked(member, module, form, activity_id)


def _save_activity_locked(member, module, form, activity_id=None):
    """Save under the caller's room lock and atomic error boundary.

    Only save_activity and record_actions.perform call this core. Both acquire
    _lock_member in their own atomic block and let all save errors escape that
    block; a receipt failure must roll back the activity and its audit together.
    Module, performer, product, record-version and stock checks remain live.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Saving an activity requires an active transaction.")
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
        valid_performer = member if performer.pk == member.pk else Membership.objects.filter(pk=performer.pk, room=member.room).first()
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
    if module=='inventory' and payload.get('_package'):
        pack=payload['_package']
        historical=activity and activity.payload.get('_package') and activity.payload['_package']['product_id']==pack['product_id']
        if not historical:
            product=Product.objects.select_for_update().get(pk=pack['product_id'],room=member.room)
            if product.version!=pack['version']:
                raise Conflict('포장 정보가 바뀌었어요. 입력창을 다시 열어 용량을 확인해 주세요.')
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
        from care.inventory.ledger import food_effect, product_binding
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
    from care.inventory.supplies import apply_usage
    payload = apply_usage(member, module, data, payload, activity)
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
