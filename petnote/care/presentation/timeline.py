"""Mobile day timeline: who did what and which routines are still open on one day.

Read-only. Records reuse the same visibility rules as the record list, and
routine rows come from the same schedule replay as the today screen.
"""
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from care import services
from care.models import Activity
from care.services import schedule

WEEKDAYS = "월화수목금토일"


def _day(request, today):
    try:
        chosen = date.fromisoformat(request.GET.get("day", ""))
    except ValueError:
        return today
    # Keep the strip within a sensible range; older days stay reachable from the full list.
    return chosen if today - timedelta(days=366) <= chosen <= today + timedelta(days=31) else today


def _person(request, room):
    raw = request.GET.get("by", "")
    try:
        wanted = uuid.UUID(raw)
    except ValueError:
        return None
    return room.members.filter(pk=wanted, active=True).first()


def build(request, room, member):
    from care.inventory.supplies import visible_records
    from care.presentation.records import record_cards
    from care.views.browsing import with_return

    tz = ZoneInfo(room.timezone)
    today = services.local_date(room)
    day = _day(request, today)
    person = _person(request, room)
    now = timezone.now()
    start = datetime.combine(day, time.min, tz)
    query = Activity.objects.filter(
        room=room, voided=False, status__in=("done", "active"),
        occurred_at__gte=start, occurred_at__lt=start + timedelta(days=1),
    ).select_related("author", "performer")
    query = visible_records(member, query)
    if person is not None:
        query = query.filter(author=person) | query.filter(performer=person)
    root = reverse("records", args=[room.pk])
    base = root + "?view=day"

    def link(**changes):
        params = {"day": day.isoformat(), "by": str(person.pk) if person else ""}
        params.update(changes)
        return base + "".join(f"&{k}={v}" for k, v in params.items() if v)

    return_to = link()
    items = []
    for activity in record_cards(query.order_by("occurred_at", "created_at"), member):
        who = activity.performer or activity.author
        items.append({
            "kind": "record", "at": activity.occurred_at.astimezone(tz), "title": activity.brief or activity.spec.name,
            "module": activity.spec.name, "who": who.display_name if who else "",
            "active": activity.status == "active",
            "url": with_return(reverse("record_detail", args=[room.pk, activity.pk]), return_to),
        })
    open_routines = 0
    if person is None:
        for row in schedule.opportunities(member, day, day, now=now):
            if row["done"] or row["paused"] or row["active"]:
                continue
            open_routines += not row["skipped"]
            items.append({
                "kind": "todo", "at": row["at"].astimezone(tz), "title": row["name"], "module": row["spec"].name,
                "row": row, "skipped": row["skipped"], "late": row["due"] and not row["skipped"],
            })
    items.sort(key=lambda item: (item["at"], item["kind"] == "todo"))

    groups = []
    for item in items:
        label = item["at"].strftime("%H:%M")
        if groups and groups[-1]["label"] == label:
            groups[-1]["items"].append(item)
        else:
            groups.append({"label": label, "at": item["at"], "items": [item]})
    now_local = now.astimezone(tz)
    now_index = None
    if day == today:
        now_index = next((i for i, g in enumerate(groups) if g["at"] > now_local), len(groups))
    for index, group in enumerate(groups):
        group["future"] = now_index is not None and index >= now_index
        group["now_before"] = index == now_index

    monday = day - timedelta(days=day.weekday())
    week = [{
        "date": d, "weekday": WEEKDAYS[d.weekday()], "url": link(day=d.isoformat()),
        "selected": d == day, "today": d == today, "future": d > today,
    } for d in (monday + timedelta(days=n) for n in range(7))]
    people = list(room.members.filter(active=True).order_by("joined_at", "id"))
    return {
        "title": "기록", "active_tab": "records", "live_page": True,
        "day": day, "today": today, "is_today": day == today, "now_text": now_local.strftime("%H:%M"),
        "groups": groups, "show_now_at_end": day == today and now_index == len(groups),
        "record_count": sum(item["kind"] == "record" for item in items), "open_routines": open_routines,
        "week": week, "prev_week": link(day=(monday - timedelta(days=7)).isoformat()),
        "next_week": link(day=(monday + timedelta(days=7)).isoformat()) if monday + timedelta(days=7) <= today else "",
        "people": [{"name": p.display_name, "url": link(by=str(p.pk)), "selected": person == p, "me": p == member} for p in people],
        "all_people_url": link(by=""), "person": person,
        "list_url": root, "back_to_today": link(day=today.isoformat(), by=str(person.pk) if person else ""),
    }


def render_day(request, room, member):
    return render(request, "care/records/timeline.html", build(request, room, member))
