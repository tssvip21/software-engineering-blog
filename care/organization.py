"""Navigation categories and room-owned display groups, independent of analysis rules."""
import uuid
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import Room, ModuleGroup
from .modules import MODULES
from . import services

CATEGORIES = {
    "nutrition": ("먹이·체중", ("meal", "treat", "water", "weight")),
    "activity": ("활동·휴식", ("walk", "play", "training", "sleep")),
    "wellbeing": ("상태·건강", ("toilet", "observation", "medication", "veterinary", "reproduction", "shedding")),
    "living": ("생활·환경", ("hygiene", "grooming", "cleaning", "environment", "inventory", "expense")),
}
CATEGORY_OF = {key: name for name, (_, keys) in CATEGORIES.items() for key in keys}


def groups_for(member):
    allowed = set(services.visible_modules(member))
    # A private-only/mixed group's name may itself reveal a sensitive activity.
    return [group for group in member.room.module_groups.all() if set(group.keys) <= allowed]


def state(room):
    room.refresh_from_db(fields=["groups_version"])
    return {"version": room.groups_version, "groups": [{"id": str(g.pk), "name": g.name, "keys": g.keys} for g in room.module_groups.all()]}


@transaction.atomic
def save_groups(member, data):
    member = services.fresh_member(member)
    services.require_manager(member)
    room = Room.objects.select_for_update().get(pk=member.room_id)
    if not isinstance(data, dict) or type(data.get("version")) is not int:
        raise ValidationError("그룹 입력값을 확인해 주세요.")
    if room.groups_version != data["version"]:
        raise services.Conflict("다른 참여자가 그룹을 바꿨어요. 내 변경을 보관하고 최신 구성을 확인해 주세요.")
    values = data.get("groups")
    if not isinstance(values, list) or len(values) > 20:
        raise ValidationError("그룹은 최대 20개까지 만들 수 있어요.")
    clean, names, ids = [], set(), set()
    owned = set(room.module_groups.values_list("pk", flat=True))
    for row in values:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValidationError("그룹 이름을 확인해 주세요.")
        name = row["name"].strip()
        if not name or len(name) > 40 or name.casefold() in names:
            raise ValidationError("중복되지 않는 40자 이내의 그룹 이름을 입력해 주세요.")
        try:
            key = uuid.UUID(str(row["id"])) if row.get("id") else uuid.uuid4()
        except (ValueError, TypeError):
            raise ValidationError("그룹 식별자를 확인해 주세요.")
        if key in ids or (key not in owned and ModuleGroup.objects.filter(pk=key).exists()):
            raise ValidationError("다른 방의 그룹이거나 중복된 그룹이에요.")
        keys = row.get("keys")
        if not isinstance(keys, list) or any(not isinstance(k,str) or k not in MODULES for k in keys) or len(keys)!=len(set(keys)):
            raise ValidationError("그룹에 넣을 모듈을 확인해 주세요.")
        clean.append((key,name,keys)); names.add(name.casefold()); ids.add(key)
    room.module_groups.exclude(pk__in=ids).delete()
    # Temporary names allow an atomic swap under the per-room uniqueness constraint.
    for existing in room.module_groups.all():
        temporary = "~" + uuid.uuid4().hex
        while temporary.casefold() in names:
            temporary = "~" + uuid.uuid4().hex
        ModuleGroup.objects.filter(pk=existing.pk).update(name=temporary)
    for position,(key,name,keys) in enumerate(clean):
        ModuleGroup.objects.update_or_create(pk=key,room=room,defaults={"name":name,"keys":keys,"position":position})
    room.groups_version += 1
    room.save(update_fields=["groups_version"])
    services.bump(room, member, "groups.updated", {"count":len(clean)})
    return state(room)


def browse_context(member):
    allowed=set(services.visible_modules(member))
    categories=[{"key":k,"label":label,"keys":[x for x in keys if x in allowed]} for k,(label,keys) in CATEGORIES.items()]
    groups=[{"key":"custom-"+str(g.pk),"label":g.name,"keys":[x for x in g.keys if x in allowed]} for g in groups_for(member)]
    return {"module_categories":categories,"custom_groups":groups,"module_browse_data":{"categories":categories,"groups":groups}}
