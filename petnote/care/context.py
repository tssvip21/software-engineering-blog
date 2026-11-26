from django.conf import settings
from .services import request_memberships


def layout_template(request):
    preference = request.GET.get("layout", request.COOKIES.get("petnote_layout"))
    if preference in ("mobile", "desktop"):
        return f"care/layout_{preference}.html"
    agent = request.headers.get("User-Agent", "").lower()
    mobile = request.headers.get("Sec-CH-UA-Mobile") == "?1" or any(
        token in agent for token in ("mobile", "android", "iphone", "ipad", "ipod")
    )
    return "care/layout_mobile.html" if mobile else "care/layout_desktop.html"


def site_context(request):
    memberships = request_memberships(request) if hasattr(request, "session") else []
    room = getattr(request, "care_room", None)
    member = getattr(request, "care_member", None)
    if room and all(m.room_id != room.pk for m in memberships) and member:
        memberships.append(member)
    people = list(room.members.filter(active=True).order_by("joined_at", "id")) if room else []
    people.sort(key=lambda p: (0 if p.role == "owner" else 1 if p.role == "admin" else 2, p.display_name))
    return {"petnote_layout": layout_template(request), "phase7_enabled": True, "phase8_enabled": True, "app_title": "테일리 · Taily", "nav_memberships": memberships,
            "sidebar_people": people[:6], "sidebar_people_count": len(people),
            "room": room, "member": member, "demo_mode": settings.DEMO_MODE, "public_demo": settings.PUBLIC_DEMO, "public_demo_entry": settings.PUBLIC_DEMO_ENTRY,
            "google_configured": settings.GOOGLE_CONFIGURED, "local_mode": settings.DEBUG,
            "active_path": request.path}
