"""Read-only presentation summaries; the existing record and statistics rules remain authoritative."""
from collections import defaultdict
from datetime import timedelta
from urllib.parse import urlencode

from django.db.models import F, Window
from django.db.models.functions import RowNumber
from django.urls import reverse
from django.utils import timezone

from .models import Activity
from .modules import record_summary
from .services import local_date, visible_modules
from .stats import bounds, get_statistics


def today_tiles(member, cards, records, day=None):
    """Enrich visible pinned actions without changing or inferring any saved measurements."""
    day = day or local_date(member.room)
    allowed = set(visible_modules(member))
    grouped = defaultdict(list)
    now = timezone.now()
    for record in records:
        if (record.room_id == member.room_id and record.module in allowed
                and not record.voided and record.status == "done" and record.occurred_at <= now):
            grouped[record.module].append(record)
    lower, upper = bounds(member.room, day, day)
    enriched = []
    for card in cards:
        key = card["spec"].key
        if key not in allowed:
            continue
        records_for_key = grouped[key]
        rows = [a for a in records_for_key if lower <= a.occurred_at < upper]
        tile = {"value": "—", "unit": "", "caption": "오늘 완료한 기록이 없어요", "empty": True}
        if rows:
            tile = {"value": str(len(rows)), "unit": "건", "caption": "오늘 완료한 기록", "empty": False}
        if key in {"meal", "treat", "weight", "walk", "play", "training", "sleep", "water"}:
            stats = get_statistics(member.room, key, day, day, all_records=records_for_key)
            value, unit = stats["primary"], stats["primary_unit"]
            caption = None
            if key == "weight" and stats.get("measurement_date"):
                measured = stats["measurement_date"]
                caption = "오늘 마지막 측정" if measured == day else measured.strftime("%m.%d 마지막 측정")
            elif key in {"meal", "treat"} and unit == "kcal":
                known = [a for a in rows if a.nutrition.get("kcal") is not None]
                bases = {a.nutrition.get("basis") for a in known}
                if bases == {"consumed"}:
                    caption = "오늘 · 먹은 양으로 계산한 열량"
                elif bases == {"provided"}:
                    caption = "오늘 · 준 양 기준 예상 열량"
                else:
                    caption = "오늘 · 섭취량·제공량 기준 혼합"
                missing = len(rows) - len(known)
                if missing:
                    caption += f" · 열량 미확인 {missing}건"
            elif key in {"meal", "treat"} and rows:
                tile["caption"] = "오늘 급여 기록 · 열량 미확인"
            elif key in {"walk", "play", "training"} and unit == "분":
                missing = sum(a.payload.get("minutes") is None for a in rows)
                caption = "오늘 · 시간을 남긴 활동"
                if missing:
                    caption += f" · 시간 미입력 {missing}건"
            elif key in {"walk", "play", "training"} and rows:
                tile["caption"] = "오늘 활동 기록 · 시간 미입력"
            elif key == "sleep" and unit == "분":
                caption = "오늘 · 확인한 수면·휴식 구간"
            elif key == "water" and unit == "ml":
                caption = "오늘 · 실제로 측정한 음수량"
            elif key == "water" and rows:
                tile["caption"] = "오늘 물 교체·보충 기록"
            if caption is not None:
                tile = {"value": value, "unit": unit, "caption": caption, "empty": False}
        enriched.append({**card, "tile": tile})
    return enriched


def latest_module_records(member):
    """At most one visible completed row per module; no sensitive metadata is emitted."""
    allowed = visible_modules(member)
    rows = (Activity.objects.filter(room_id=member.room_id, module__in=allowed,
                                   voided=False, status="done", occurred_at__lte=timezone.now())
            .order_by().annotate(position_in_module=Window(
                expression=RowNumber(), partition_by=[F("module")],
                order_by=[F("occurred_at").desc(), F("created_at").desc(), F("id").desc()]))
            .filter(position_in_module=1))
    return {a.module: {"text": record_summary(a.module, a.payload), "at": a.occurred_at} for a in rows}


def statistics_periods(room, module, selected_start, selected_end):
    """Explicit local-calendar links rather than silently changing a user's date filter."""
    today = local_date(room)
    root = reverse("statistics", args=[room.pk, module])
    periods = []
    for days, label in [(7, "최근 7일"), (30, "최근 30일"), (90, "최근 90일")]:
        start = today - timedelta(days=days - 1)
        periods.append({"label": label, "url": root + "?" + urlencode({"start": start.isoformat(), "end": today.isoformat()}),
                        "selected": selected_start == start and selected_end == today})
    return periods
