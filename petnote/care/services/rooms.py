"""Space creation, module configuration and lifecycle operations."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from care.models import (
    Activity,
    ActivityRevision,
    AuditEvent,
    Invitation,
    Membership,
    ModuleConfigVersion,
    ModuleSetting,
    Room,
    Routine,
    RoutineItem,
    RoutineRun,
    RoutineVersion,
)
from care.modules import DEFAULT_MODULES, MODULES
from care.services.access import fresh_member, require_manager, require_owner
from care.services.common import Conflict, bump


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
def update_modules(member, enabled, order, version, pinned=None):
    member = fresh_member(member)
    require_manager(member)
    if member.room.settings_version != version:
        raise Conflict("기능 설정이 이미 바뀌었어요. 최신 설정을 확인한 뒤 다시 선택해 주세요.")
    if not isinstance(order, list) or len(order) != len(MODULES) or set(order) != set(MODULES) or not set(enabled) <= set(MODULES):
        raise ValidationError("기능 목록과 순서를 확인해 주세요.")
    rows = {m.key: m for m in member.room.module_settings.all()}
    from care.modules import RETIRED_MODULES
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
