"""Invitations, membership roles and guest account linking."""

import hashlib
import secrets
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from care.models import (
    Activity,
    ActivityRevision,
    AuditEvent,
    Invitation,
    Membership,
    Room,
    Routine,
)
from care.services.access import fresh_member, guest_identity, require_owner
from care.services.common import Conflict, bump


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
