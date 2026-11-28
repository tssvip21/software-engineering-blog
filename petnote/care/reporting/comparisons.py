"""Comparable, permission-scoped statistics with explicit denominators.

This layer describes recorded values, not health, causal effects, or population norms.
Equal closed-calendar windows are separate from the older robust pattern detector.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.urls import reverse
from django.utils import timezone

from ..breeds import reference_for
from ..models import Activity
from ..modules import MODULES, SELECTABLE_MODULES, compact
from ..services import local_date, visible_modules
from .stats import get_statistics

METRICS = {
    "weight": ("weight", "체중", "kg", "scale"),
    "walk_minutes": ("walk", "산책 시간", "분", "walk"),
    "walk_distance": ("walk", "산책 거리", "km", "walk"),
}


def valid_number(value, *, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        return None
    return result


def measurement(activity, key):
    payload = activity.payload
    if key == "weight":
        value = valid_number(payload.get("weight"), positive=True)
        factor = {"kg": Decimal(1), "g": Decimal(".001"), "lb": Decimal(".45359237")}.get(payload.get("unit"))
    elif key == "walk_distance":
        value = valid_number(payload.get("distance"))
        factor = {"km": Decimal(1), "m": Decimal(".001")}.get(payload.get("distance_unit"))
    else:
        value, factor = valid_number(payload.get("minutes")), Decimal(1)
    return value * factor if value is not None and factor is not None else None


def chronology(activity):
    return activity.occurred_at, getattr(activity, "created_at", activity.occurred_at), str(activity.pk)


def daily_values(records, key, timezone_name):
    """Weight: last valid measurement/day. Walk: complete measured totals/day.

An explicit zero distance is a value; one unmeasured walk makes that day's distance
incomplete, independently of whether its duration was measured.
"""
    module = METRICS[key][0]
    tz = ZoneInfo(timezone_name)
    groups = defaultdict(list)
    unique = {str(row.pk): row for row in records if row.module == module and not row.voided and row.status == "done"}
    for row in sorted(unique.values(), key=chronology):
        groups[row.occurred_at.astimezone(tz).date()].append(row)
    values, incomplete = [], []
    for day, rows in sorted(groups.items()):
        measured = [(row, measurement(row, key)) for row in rows]
        if key == "weight":
            known = [(row, number) for row, number in measured if number is not None]
            if known:
                row, number = known[-1]
                values.append({"date": day, "value": number, "ids": [str(row.pk)], "count": 1})
            else:
                incomplete.append(day)
        elif all(value is not None for _, value in measured):
            values.append({"date": day, "value": sum(value for _, value in measured),
                           "ids": [str(row.pk) for row, _ in measured], "count": len(measured)})
        else:
            incomplete.append(day)
    return values, incomplete


def signed(value):
    return ("+" if value > 0 else "−" if value < 0 else "") + compact(abs(value))


def change(old, current):
    if old is None or current is None:
        return {"available": False, "delta": None, "percent": None, "delta_text": "비교할 값이 더 필요해요", "percent_text": "", "direction": "missing"}
    delta = current - old
    pct = delta / abs(old) * 100 if old != 0 else None
    return {"available": True, "delta": delta, "percent": pct,
            "delta_text": signed(delta), "percent_text": signed(pct if pct and abs(pct) < Decimal('.1') else pct.quantize(Decimal('.1'))) + "%" if pct is not None else "이전 값이 0이라 비율은 계산하지 않아요",
            "direction": "up" if delta > 0 else "down" if delta < 0 else "same"}


def compare_metric(records, key, end, days, timezone_name):
    if key not in METRICS or days not in (7, 30, 90):
        raise ValueError("Unsupported comparison")
    module, label, unit, icon = METRICS[key]
    start, previous_start = end - timedelta(days=days - 1), end - timedelta(days=2 * days - 1)
    previous_end = start - timedelta(days=1)
    tz = ZoneInfo(timezone_name)
    closed = [row for row in records if previous_start <= row.occurred_at.astimezone(tz).date() <= end
              and (not row.ended_at or row.ended_at.astimezone(tz).date() <= end)]
    points, incomplete = daily_values(closed, key, timezone_name)
    def period(lower, upper):
        selected = [p for p in points if lower <= p['date'] <= upper]
        average = sum((p['value'] for p in selected), Decimal(0)) / len(selected) if selected else None
        return {"start": lower, "end": upper, "average": average,
                "text": compact(average) if average is not None else "—", "days": len(selected),
                "records": sum(p['count'] for p in selected), "partial_days": sum(lower <= d <= upper for d in incomplete),
                "values": selected, "total": compact(sum((p['value'] for p in selected), Decimal(0))) if selected else None}
    before, recent = period(previous_start, previous_end), period(start, end)
    diff = change(before['average'], recent['average'])
    if diff['available'] and diff['delta'] and before['text'] == recent['text']:
        # Do not show identical rounded endpoints beside a non-zero difference.
        places = max(3, min(12, 1 - abs(diff['delta']).adjusted()))
        for period in (before, recent):
            period['text'] = format(period['average'].quantize(Decimal(1).scaleb(-places)), 'f').rstrip('0').rstrip('.')
    if diff['available']:
        noun = "측정일 평균 체중이" if key == 'weight' else label + ("이" if key == 'walk_minutes' else "가")
        title = noun + (" 늘었어요" if diff['direction'] == 'up' else " 줄었어요" if diff['direction'] == 'down' else " 같아요")
    else:
        title = "이전·최근 기록을 함께 모으고 있어요"
    note = "하루 마지막 체중을 사용해 측정한 날끼리 평균을 비교해요." if key == 'weight' else "값을 모두 남긴 날의 산책 합계를 평균냈어요. 실제 하루 전체 활동량은 아니에요."
    if min(before['days'], recent['days']) < 3:
        note += " 기록일이 적어 한 번의 값에도 크게 달라질 수 있어요."
    if abs(before['days'] - recent['days']) > days / 2:
        note += " 두 기간에 기록한 날의 수가 많이 달라요."
    if abs(before['days'] - recent['days']) > days / 2:
        note += " 두 기간에 기록한 날의 수가 많이 달라요."
    if before['partial_days'] or recent['partial_days']:
        note += " 수치가 빠진 날은 해당 지표의 평균에서 제외했어요."
    data = [{**p, "text": compact(p['value']), "recent": p['date'] >= start} for p in reversed(points)]
    return {"key": key, "module": module, "name": label, "unit": unit, "icon": icon, "title": title,
            "before": before, "recent": recent, "change": diff, "note": note, "values": data,
            "basis": "측정일 평균" if key == 'weight' else "기록일 평균", "days": days}


def reference_geometry(reference):
    if not reference.get('available') or reference.get('value') is None:
        return reference
    low, high, value = (Decimal(str(reference[k])) for k in ('low', 'high', 'value'))
    lower = max(Decimal(0), low - (high - low) * Decimal('.5'))
    upper = high + (high - low) * Decimal('.5')
    def x(v):
        return round(max(20, min(300, 20 + float((v - lower) / (upper - lower)) * 280)))
    return {**reference, "low_text": compact(low), "high_text": compact(high), "value_text": compact(value),
            "low_x": x(low), "band_width": x(high) - x(low), "high_x": x(high), "value_x": x(value), "clipped": value < lower or value > upper}


def recent_statistics(member, days=7, *, end=None, records=None):
    """All figures are computed locally from authorized records and bounded periods."""
    from ..services import fresh_member
    member = fresh_member(member)
    room, today = member.room, local_date(member.room)
    try:
        days = int(days)
    except (ValueError, TypeError):
        days = 7
    if days not in (7, 30, 90):
        days = 7
    end = min(end or today - timedelta(days=1), today - timedelta(days=1))
    now = timezone.now()
    allowed = set(visible_modules(member))
    enabled = set(room.module_settings.filter(enabled=True).values_list('key', flat=True)) & allowed & set(SELECTABLE_MODULES)
    if records is None:
        # Stateful inventory and latest weight need history; one shared query avoids
        # a separate record scan for every module on this small, manual-log service.
        records = list(Activity.objects.filter(room_id=room.pk, module__in=allowed, voided=False,
                       occurred_at__lte=now).select_related('author').order_by('occurred_at', 'created_at', 'id'))
    rows = [row for row in records if row.room_id == room.pk and row.module in allowed and not row.voided
            and row.occurred_at <= now and (not row.ended_at or row.ended_at <= now)]
    core = []
    root = reverse('records', args=[room.pk])
    for key, (module, _, _, _) in METRICS.items():
        if module not in enabled:
            continue
        item = compare_metric(rows, key, end, days, room.timezone)
        item['url'] = root + '?' + urlencode({'module': module, 'state': 'done', 'start': item['before']['start'].isoformat(), 'end': end.isoformat()})
        item['detail_url'] = reverse('module', args=[room.pk, module])
        for period in ('before','recent'):
            item[period]['url'] = root + '?' + urlencode({'module': module, 'state': 'done', 'start': item[period]['start'].isoformat(), 'end': item[period]['end'].isoformat()})
        core.append(item)
    weight_points, _ = daily_values(rows, 'weight', room.timezone) if 'weight' in enabled else ([], [])
    latest, previous = (weight_points[-1] if weight_points else None), (weight_points[-2] if len(weight_points) > 1 else None)
    latest_display = {**latest, 'text': compact(latest['value']), 'url': reverse('record_detail', args=[room.pk, latest['ids'][0]])} if latest else None
    previous_display = {**previous, 'text': compact(previous['value'])} if previous else None
    ref = reference_geometry(reference_for(room, latest)) if 'weight' in enabled else None
    bcs = None
    if 'weight' in enabled and room.species in ('dog', 'cat'):
        candidates = sorted((row for row in rows if row.module == 'weight' and row.status == 'done' and str(row.payload.get('bcs','')) in {str(n) for n in range(1,10)}), key=chronology)
        if candidates:
            row = candidates[-1]; score = int(row.payload['bcs']); low, high = (4,5) if room.species == 'dog' else (5,5)
            bcs = {'score':score,'date':row.occurred_at,'assessor':{'vet':'수의사 평가','guardian':'보호자 관찰'}.get(row.payload.get('bcs_by'),'평가자 미기록'),
                   'label':'자료상 참고 구간 안' if low <= score <= high else '자료상 참고 구간보다 낮음' if score < low else '자료상 참고 구간보다 높음',
                   'low':low,'high':high,'segments':[{'number':n,'selected':n==score,'reference':low<=n<=high} for n in range(1,10)],
                   'source':'WSAVA Global Nutrition Committee · BCS (2025-06)',
                   'source_url':'https://wsava.org/wp-content/uploads/2025/06/WSAVA_BCSCat_BCSDog_Nutrition_250612.pdf' if room.species=='dog' else 'https://wsava.org/wp-content/uploads/2025/06/WSAVA_BCSCat_BCSCat_Nutrition_250612.pdf',
                   'url':reverse('record_detail',args=[room.pk,row.pk])}
    start = end - timedelta(days=days-1)
    summaries = []
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.module].append(row)
    for key in SELECTABLE_MODULES:
        if key not in enabled or key in ('weight', 'walk'):
            continue
        stats = get_statistics(room,key,start,end,all_records=grouped[key])
        summaries.append({'key':key,'name':MODULES[key].name,'icon':MODULES[key].icon,'value':stats['primary'],
                          'unit':stats['primary_unit'],'caption':stats['primary_label'],
                          'url':reverse('statistics',args=[room.pk,key])+'?'+urlencode({'start':start.isoformat(),'end':end.isoformat()})})
    return {'comparison_cards':core,'comparison_days':days,'comparison_start':start,'comparison_end':end,
            'comparison_previous_start':start-timedelta(days=days),'comparison_previous_end':start-timedelta(days=1),
            'comparison_periods':[{'days':n,'label':f'{n}일','url':'?days='+str(n),'selected':n==days} for n in (7,30,90)],
            'latest_weight':latest_display,'previous_weight':previous_display,
            'latest_weight_change':change(previous['value'] if previous else None,latest['value'] if latest else None),
            'weight_reference':ref,'bcs_card':bcs,'weight_enabled':'weight' in enabled,'other_statistics':summaries,
            'profile_url':reverse('room_settings',args=[room.pk]),'can_edit_profile':member.can_manage}
