"""Mobile navigation derived from the original desktop menu.

Navigation groups describe a task area; authorization remains in each view.
"""
from django.urls import reverse


GROUPS = (
    ("care", "돌봄", "매일의 돌봄과 준비", "grid", (
        ("modules", "modules", "돌봄 기능", "기능", "grid"),
        ("routines", "routines", "루틴", "루틴", "clock"),
        ("supplies", "supplies", "물품 관리", "물품", "box"),
    )),
    ("history", "기록", "지난 기록과 변화", "list", (
        ("records", "records", "기록", "기록", "list"),
        ("overview", "overview", "최근 통계", "통계", "chart"),
    )),
    ("manage", "관리", "함께 돌보는 사람과 공간", "settings", (
        ("members", "members", "구성원", "구성원", "users"),
        ("pet", "room_settings", "반려동물 상태", "반려동물", "paw"),
        ("preferences", "preferences", "일반 설정", "설정", "settings"),
    )),
)


def build_navigation(room, active_tab=""):
    """Build fresh, room-scoped links without changing permissions or state."""
    if room is None:
        return None

    def item(key, route, label, short_label, icon):
        return {
            "key": key,
            "url": reverse(route, args=[room.pk]),
            "label": label,
            "short_label": short_label,
            "icon": icon,
            "active": key == active_tab,
        }

    today = item("today", "today", "오늘", "오늘", "today")
    menu = item("navigation", "navigation", "전체 메뉴", "전체", "more")
    groups = []
    selected = None
    for key, label, description, icon, entries in GROUPS:
        links = [item(*entry) for entry in entries]
        group = {
            "key": key,
            "label": label,
            "description": description,
            "icon": icon,
            "items": links,
            "url": links[0]["url"],
            "active": any(link["active"] for link in links),
        }
        groups.append(group)
        if group["active"]:
            selected = group

    global_items = [today] + [
        {
            "key": group["key"],
            "url": group["url"],
            "label": group["label"],
            "short_label": group["label"],
            "icon": group["icon"],
            "active": group["active"],
        }
        for group in groups
    ] + [menu]
    return {
        "today": today,
        "menu": menu,
        "groups": groups,
        "global_items": global_items,
        "scope": selected["key"] if selected else "global",
        "scope_label": selected["label"] if selected else "돌봄 공간",
        "items": selected["items"] if selected else global_items,
        "back_url": today["url"],
    }
