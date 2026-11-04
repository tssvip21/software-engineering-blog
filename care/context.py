from django.conf import settings
from .services import my_memberships


def site_context(request):
    memberships = list(my_memberships(request).order_by("room__name")) if hasattr(request, "session") else []
    room = getattr(request, "care_room", None)
    member = getattr(request, "care_member", None)
    people = list(room.members.filter(active=True).order_by("joined_at", "id")) if room else []
    people.sort(key=lambda p: (0 if p.role == "owner" else 1 if p.role == "admin" else 2, p.display_name))
    return {"app_title": "테일리 · Taily", "nav_memberships": memberships,
            "sidebar_people": people[:6], "sidebar_people_count": len(people),
            "room": room, "member": member, "demo_mode": settings.DEMO_MODE,
            "google_configured": settings.GOOGLE_CONFIGURED, "local_mode": settings.DEBUG,
            "active_path": request.path}
