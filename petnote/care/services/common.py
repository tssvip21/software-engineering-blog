"""Shared operation errors, audit events and serialization."""

import uuid
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db.models import F
from django.utils import timezone

from care.models import AuditEvent, Room


class Conflict(ValidationError):
    """The user's original input remains available when a concurrent change wins."""


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



def datetime_for_day(room, day, at):
    from datetime import datetime
    return datetime.combine(day, at, ZoneInfo(room.timezone))
