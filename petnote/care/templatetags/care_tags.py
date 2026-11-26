from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from care.modules import compact
register = template.Library()

# 펫노트 아이콘: 24 격자, 선 1.75, 둥근 끝. 점은 채움만 쓴다.
D = 'stroke="none" fill="currentColor"'
PATHS = {
    "more": f'<circle cx="6" cy="12" r="1.4" {D}/><circle cx="12" cy="12" r="1.4" {D}/><circle cx="18" cy="12" r="1.4" {D}/>',
    "link": '<path d="M10 13.6a4 4 0 0 0 5.7 0l2.8-2.8a4 4 0 0 0-5.7-5.7l-1 1M14 10.4a4 4 0 0 0-5.7 0l-2.8 2.8a4 4 0 0 0 5.7 5.7l1-1"/>',
    "leaf": '<path d="M12 20.5V12M12 12c0-4 3-6.5 7-6.5 0 4-3 6.5-7 6.5ZM12 14.5c0-3-2.3-5-5.5-5 0 3 2.3 5 5.5 5Z"/>',
    "sidebar": '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15"/>',
    "close": '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "grip": f'<circle cx="9" cy="6" r="1.2" {D}/><circle cx="15" cy="6" r="1.2" {D}/><circle cx="9" cy="12" r="1.2" {D}/><circle cx="15" cy="12" r="1.2" {D}/><circle cx="9" cy="18" r="1.2" {D}/><circle cx="15" cy="18" r="1.2" {D}/>',
    "crown": '<path d="M4.5 8.2l3.7 3.5L12 6.2l3.8 5.5 3.7-3.5-1.4 10.3H5.9Z"/>',
    "home": '<path d="M4 10.5 12 4l8 6.5V19a1.5 1.5 0 0 1-1.5 1.5H15v-6H9v6H5.5A1.5 1.5 0 0 1 4 19Z"/>',
    "today": f'<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 9.5h17M8 3v4M16 3v4"/><circle cx="12" cy="15" r="1.8" {D}/>',
    "grid": '<rect x="4" y="4" width="6.5" height="6.5" rx="1.8"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.8"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.8"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.8"/>',
    "list": f'<path d="M9.5 7h10M9.5 12h10M9.5 17h10"/><circle cx="5" cy="7" r="1.2" {D}/><circle cx="5" cy="12" r="1.2" {D}/><circle cx="5" cy="17" r="1.2" {D}/>',
    "chart": '<path d="M4 4.5v13a2 2 0 0 0 2 2h14M8 15l3.5-4 3 2.5L19.5 8"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "users": '<circle cx="9" cy="8.5" r="3.2"/><path d="M3.5 19.5c0-3 2.5-5 5.5-5s5.5 2 5.5 5M16 5.6a3 3 0 0 1 0 5.8M17.5 14.6c1.8.6 3 2.4 3 4.9"/>',
    "settings": '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "back": '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    "down": '<path d="M6.5 9.5 12 15l5.5-5.5"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "paw": '<circle cx="6.8" cy="10.4" r="1.8"/><circle cx="10.2" cy="6.4" r="1.9"/><circle cx="13.8" cy="6.4" r="1.9"/><circle cx="17.2" cy="10.4" r="1.8"/><path d="M12 12.3c2.5 0 5 3 5 5.1 0 1.5-1.1 2.1-2.4 2.1-1 0-1.6-.45-2.6-.45s-1.6.45-2.6.45C8.1 19.5 7 18.9 7 17.4c0-2.1 2.5-5.1 5-5.1Z"/>',
    "bowl": '<path d="M3.5 13.5h17l-1.8 5.2a2 2 0 0 1-1.9 1.3H7.2a2 2 0 0 1-1.9-1.3Z"/><circle cx="8.8" cy="10" r="1.3"/><circle cx="12.4" cy="8.4" r="1.3"/><circle cx="15.6" cy="10.2" r="1.3"/>',
    "treat": '<g transform="rotate(-40 12 12)"><path d="M8 10.6h8a2.3 2.3 0 1 1 3.3 1.4A2.3 2.3 0 1 1 16 13.4H8a2.3 2.3 0 1 1-3.3-1.4A2.3 2.3 0 1 1 8 10.6Z"/></g>',
    "drop": '<path d="M12 3.8c-2.9 3.8-6 7.2-6 10.7a6 6 0 0 0 12 0c0-3.5-3.1-6.9-6-10.7Z"/><path d="M9.3 14.8a2.8 2.8 0 0 0 2.2 2.6"/>',
    "toilet": '<rect x="4" y="5" width="16" height="14" rx="2.5"/><rect x="7.5" y="8.5" width="9" height="7" rx="1" stroke-dasharray="2 2.2"/>',
    "walk": f'<g {D}><ellipse cx="7.8" cy="17.6" rx="2.5" ry="2.1"/><circle cx="5.3" cy="14.4" r="1.05"/><circle cx="7.8" cy="13.3" r="1.05"/><circle cx="10.3" cy="14.4" r="1.05"/><ellipse cx="16.2" cy="9.4" rx="2.5" ry="2.1"/><circle cx="13.7" cy="6.2" r="1.05"/><circle cx="16.2" cy="5.1" r="1.05"/><circle cx="18.7" cy="6.2" r="1.05"/></g>',
    "ball": '<circle cx="12" cy="12" r="8"/><path d="M4.4 9.6c3 .6 5 3.6 4.4 8.6M19.6 14.4c-3-.6-5-3.6-4.4-8.6"/>',
    "target": '<path d="M5.5 20.5V8.5M18.5 20.5V8.5M5.5 11.5h13M3.5 20.5h4M16.5 20.5h4"/><path d="M9.5 11.5l-2 0M14.5 11.5h-2" stroke-width="3"/>',
    "scale": '<rect x="3.5" y="3.5" width="17" height="17" rx="4.5"/><path d="M8.3 10a4.6 4.6 0 0 1 7.4 0M12 10.5l1.3-2"/>',
    "spark": '<circle cx="9" cy="14.5" r="4.5"/><circle cx="16.5" cy="8.5" r="2.8"/><circle cx="17.5" cy="16.5" r="1.6"/><path d="M7 13a2.4 2.4 0 0 1 2-2"/>',
    "scissors": '<circle cx="6.5" cy="17.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/><path d="M8.2 15.7 16 4.5M15.8 15.7 8 4.5"/>',
    "clean": '<path d="M18 3.5l-4.6 7M10 10.4l5.6 3.8-2.4 5.7a1.3 1.3 0 0 1-1.9.6l-5.4-3.4a1.3 1.3 0 0 1-.4-1.8ZM9.2 18.3l1.6-2.8M12.2 19.9l1.4-2.7"/>',
    "pill": '<g transform="rotate(-45 12 12)"><rect x="4" y="8.5" width="16" height="7" rx="3.5"/><path d="M12 8.5v7"/></g>',
    "eye": '<path d="M14.6 5.4l4 4L9 19H5v-4ZM12.6 7.4l4 4"/>',
    "cross": '<path d="M6.5 4v5a4.5 4.5 0 0 0 9 0V4M5 4h3M14 4h3M11 13.5v1.5a4 4 0 0 0 8 0v-1.7"/><circle cx="19" cy="11.3" r="2"/>',
    "moon": '<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10Z"/>',
    "thermometer": '<path d="M10 13.6V6a2 2 0 0 1 4 0v7.6a4 4 0 1 1-4 0ZM12 9.5v7"/>',
    "egg": '<path d="M18.5 14c0 4.2-2.8 6.5-6.5 6.5S5.5 18.2 5.5 14 8.6 3.5 12 3.5s6.5 6.3 6.5 10.5Z"/>',
    "feather": '<path d="M20 4c-8 0-13 5-13 12v4h4c7 0 9-6 9-16ZM4 20 14 10M10.5 14H16"/>',
    "box": '<path d="M4 8l8-4 8 4v8.5l-8 4-8-4ZM4 8l8 4 8-4M12 12v8.5"/>',
    "wallet": '<path d="M6 3.5h12v17l-2-1.4-2 1.4-2-1.4-2 1.4-2-1.4-2 1.4ZM9 8h6M9 11.5h6M9 15h3.5"/>',
}

@register.simple_tag(takes_context=True)
def navigation(context):
    from care.navigation import build_navigation
    return build_navigation(context.get("room"), context.get("active_tab", ""))


@register.simple_tag
def icon(name):
    path = PATHS.get(name, PATHS["grid"])
    return format_html('<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>', mark_safe(path))

@register.filter
def number(value):
    try:
        return compact(value)
    except (ValueError, TypeError, ArithmeticError):
        return value


@register.filter
def record_status(value):
    return {"done": "완료", "active": "진행 중", "planned": "예정"}.get(value, value)


@register.filter
def module_category(value):
    from care.organization import CATEGORY_OF
    return CATEGORY_OF.get(value, "default")


@register.filter
def result_category(value):
    return {"weight": "nutrition", "food_actual": "nutrition", "food_offered": "nutrition",
            "food_mass": "nutrition", "meal_gap": "nutrition", "water": "nutrition",
            "activity": "activity", "rest": "activity"}.get(value, "wellbeing")



@register.simple_tag
def displayed(value,unit='',kind=''):
    from care.reporting.display_values import measure
    return measure(value,unit,kind=kind)


@register.filter
def readable_number(value):
    from care.reporting.display_values import number
    return number(value)


@register.filter
def history_time(value,zone='Asia/Seoul'):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        parsed=datetime.fromisoformat(str(value))
        return parsed.astimezone(ZoneInfo(zone)).strftime('%Y.%m.%d %H:%M') if parsed.tzinfo else str(value)
    except (TypeError,ValueError,KeyError):return value


# ── 오늘 화면 머리용: 화면 표시만 만든다(저장·권한·계산 규칙은 건드리지 않음) ──
@register.filter
def pet_profile(room, day=None):
    """'비숑 프리제 · 4살'처럼 품종과 나이를 한 줄로."""
    from datetime import date
    from care.breeds import catalogue
    parts = []
    breed = catalogue().get(room.breed_key or "") if getattr(room, "breed_key", "") else None
    if breed:
        parts.append(breed["ko"])
    elif getattr(room, "subspecies", ""):
        parts.append(room.subspecies)
    else:
        parts.append(room.get_species_display())
    born = getattr(room, "birth_date", None)
    if born:
        today = day or date.today()
        years = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        if years >= 1:
            parts.append(f"{years}살")
        else:
            months = (today.year - born.year) * 12 + today.month - born.month - (today.day < born.day)
            parts.append(f"{max(months, 0)}개월")
    return " · ".join(parts)


@register.simple_tag
def day_strip(records, pending, completed, day):
    """06시부터 24시까지 한 시간 한 칸. 상태: now, record, done, todo, future, empty."""
    from django.utils import timezone
    now = timezone.localtime()
    is_today = now.date() == day
    cells = {h: {"hour": h, "record": 0, "done": 0, "todo": 0} for h in range(6, 24)}
    for a in records or []:
        at = getattr(a, "occurred_at", None) or (a.get("occurred_at") if isinstance(a, dict) else None)
        if not at:
            continue
        local = timezone.localtime(at)
        if local.date() == day and local.hour in cells:
            cells[local.hour]["record"] += 1
    for key, rows in (("todo", pending or []), ("done", completed or [])):
        for row in rows:
            at = row.get("at") if isinstance(row, dict) else getattr(row, "at", None)
            hour = timezone.localtime(at).hour if hasattr(at, "tzinfo") and getattr(at, "tzinfo", None) else getattr(at, "hour", None)
            if hour in cells:
                cells[hour][key] += 1
    out = []
    for h in range(6, 24):
        c = cells[h]
        if is_today and h == now.hour:
            state = "now"
        elif c["record"]:
            state = "record"
        elif c["done"]:
            state = "done"
        elif c["todo"]:
            state = "todo"
        elif is_today and h > now.hour:
            state = "future"
        else:
            state = "empty"
        bits = []
        if c["record"]:
            bits.append(f"기록 {c['record']}건")
        if c["done"]:
            bits.append(f"끝낸 일 {c['done']}")
        if c["todo"]:
            bits.append(f"남은 일 {c['todo']}")
        c["state"] = state
        c["label"] = f"{h}시" + (" · " + ", ".join(bits) if bits else "") + (" · 지금" if state == "now" else "")
        out.append(c)
    return out


@register.simple_tag
def day_marks(records, pending, completed, day):
    """0~24시 막대 위 점 위치(15분 단위 0~95칸)와 지금 위치."""
    from django.utils import timezone
    now = timezone.localtime()
    def slot(dt):
        dt = timezone.localtime(dt) if getattr(dt, "tzinfo", None) else dt
        return min(95, dt.hour * 4 + dt.minute // 15)
    marks = []
    for a in records or []:
        at = getattr(a, "occurred_at", None)
        if at and timezone.localtime(at).date() == day:
            s = slot(at)
            if s is not None:
                name = getattr(getattr(a, "spec", None), "name", "기록")
                marks.append({"slot": s, "kind": "record", "label": f"{timezone.localtime(at):%H:%M} {name}"})
    for kind, rows in (("todo", pending or []), ("done", completed or [])):
        for row in rows:
            at = row.get("at") if isinstance(row, dict) else getattr(row, "at", None)
            s = slot(at) if at else None
            if s is not None:
                marks.append({"slot": s, "kind": kind, "label": f"{timezone.localtime(at):%H:%M} {row.get('name', '') if isinstance(row, dict) else ''}"})
    now_slot = slot(now) if now.date() == day else None
    return {"marks": marks, "now": now_slot, "now_text": f"{now:%H:%M}", "now_sec": now.hour * 3600 + now.minute * 60 + now.second}
