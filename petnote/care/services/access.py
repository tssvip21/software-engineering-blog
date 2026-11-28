"""Request identity, live permissions and room locking."""

import hashlib
import hmac
import uuid
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from care.models import Membership, ModuleSetting, RateBucket, Room
from care.modules import MODULES, SENSITIVE_KEYS


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


def request_memberships(request):
    """Reuse the navigation read within one safe request, never across requests."""
    if request.method not in ("GET", "HEAD"):
        return list(my_memberships(request).order_by("room__name"))
    identity = ("user", request.user.pk) if request.user.is_authenticated else ("guest", guest_identity(request))
    cached = getattr(request, "_care_memberships", None)
    if cached is None or cached[0] != identity:
        cached = (identity, list(my_memberships(request).order_by("room__name")))
        request._care_memberships = cached
    return list(cached[1])


def read_module_settings(room):
    """GET-scoped configuration reuse; mutation services always use live queries."""
    cache = getattr(room, "_care_read_cache", None)
    if cache is None:
        return list(room.module_settings.all())
    key = ("module_settings", room.settings_version)
    if key not in cache:
        cache[key] = list(room.module_settings.all())
    return cache[key]


def get_room(request, room_id):
    if request.method in ("GET", "HEAD"):
        member = next((row for row in request_memberships(request) if str(row.room_id) == str(room_id)), None)
        if member is not None and not hasattr(member.room, "_care_read_cache"):
            member.room._care_read_cache = {}
    else:
        member = my_memberships(request).filter(room_id=room_id).first()
    if member is None:
        raise Http404("참여 중인 돌봄 공간을 찾지 못했어요.")
    timezone.activate(ZoneInfo(member.room.timezone))
    request.care_room, request.care_member = member.room, member
    return member.room, member


def visible_modules(member):
    if not member.active:
        return ()
    if member.can_manage or member.room.guest_sensitive:
        return tuple(MODULES)
    return tuple(k for k in MODULES if k not in SENSITIVE_KEYS)


def require_module(member, key, *, adding=False):
    if not isinstance(key, str) or key not in MODULES:
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


def read_member(member):
    """Revalidate read access without taking the room's mutation lock."""
    fresh = Membership.objects.select_related("room").filter(
        pk=member.pk, room_id=member.room_id, active=True, room__archived_at__isnull=True,
    ).first()
    if fresh is None or fresh.user_id != member.user_id or fresh.guest_key != member.guest_key:
        raise PermissionDenied("참여 권한이 바뀌었어요. 새로고침해서 확인해 주세요.")
    cache = getattr(member.room, "_care_read_cache", None)
    if cache is not None:
        fresh.room._care_read_cache = (
            cache if fresh.room.revision == member.room.revision
            and fresh.room.settings_version == member.room.settings_version else {}
        )
    return fresh


@transaction.atomic
def fresh_member(member):
    return _lock_member(member)


def _lock_member(member):
    """Revalidate and lock within a transaction owned by the calling operation.

    Do not retain the result for another transaction. Room mutations and member
    changes lock or update this same room before committing.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("A member lock requires an active transaction.")
    room = Room.objects.select_for_update().filter(pk=member.room_id, archived_at__isnull=True).first()
    if room is None:
        raise PermissionDenied("이 돌봄 공간에 참여하고 있지 않아요.")
    fresh = Membership.objects.select_related("room").filter(pk=member.pk, room=room, active=True).first()
    if fresh is None or (fresh.user_id != member.user_id) or (fresh.guest_key != member.guest_key):
        raise PermissionDenied("참여 권한이 바뀌었어요. 새로고침해서 확인해 주세요.")
    return fresh


@transaction.atomic
def rate_limit(request, scope, limit=30, seconds=60):
    ip = request.META.get("REMOTE_ADDR", "unknown")
    identity = request.session.session_key or "anonymous"
    period = int(timezone.now().timestamp()) // seconds
    accepted = True
    for label, suffix, maximum in (("session", identity, limit), ("ip", "", limit * 4)):
        key = hmac.new(settings.SECRET_KEY.encode(), f"{scope}:{label}:{ip}:{suffix}".encode(), hashlib.sha256).hexdigest()
        bucket, _ = RateBucket.objects.select_for_update().get_or_create(
            key=key, defaults={"period": period, "count": 0}
        )
        if bucket.period != period:
            bucket.period, bucket.count = period, 0
        bucket.count += 1
        bucket.save(update_fields=["period", "count"])
        accepted = accepted and bucket.count <= maximum
    return accepted


def can_edit_record(member, activity, *, hidden_names=None):
    from care.inventory.supplies import record_visible
    return (
        member.active
        and activity.room_id == member.room_id
        and (member.can_manage or activity.author_id == member.pk)
        and record_visible(member, activity, hidden_names=hidden_names)
    )
