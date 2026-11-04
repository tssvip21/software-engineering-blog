from django.conf import settings
from .services import my_memberships


def site_context(request):
    memberships = list(my_memberships(request).order_by("room__name")) if hasattr(request, "session") else []
    room = getattr(request, "care_room", None)
    member = getattr(request, "care_member", None)
    return {"app_title": "돌봄 기록", "nav_memberships": memberships,
            "room": room, "member": member, "demo_mode": settings.DEMO_MODE,
            "google_configured": settings.GOOGLE_CONFIGURED, "local_mode": settings.DEBUG,
            "active_path": request.path}
