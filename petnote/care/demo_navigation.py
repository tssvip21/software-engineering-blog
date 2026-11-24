"""Demo list presentation only; never changes authorization, rooms or records."""
import json
from pathlib import Path
from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

DEMO_IDENTITIES={"demo_owner","demo_admin","demo_member","demo_outsider"}

def manifest(request):
    if not settings.DEMO_MODE or not request.user.is_authenticated or request.user.username not in DEMO_IDENTITIES:
        return {}
    explicit=getattr(settings,"DEMO_NAVIGATION",None)
    if explicit is not None:return explicit
    if settings.LOCAL_DEMO_POSTGRES or (settings.DATA_DIR / "showcase" / "fixture.json").is_file():
        fixture_path = settings.DATA_DIR / "showcase" / "fixture.json"
        rooms = json.loads(fixture_path.read_text(encoding="utf8"))["rooms"]
        from .models import Room
        synthetic = Room.objects.filter(owner__username="demo_owner").values_list("pk", flat=True)
        return {"showcase": [rooms[key] for key in ("rich", "cat")],
                "synthetic_rooms": [str(pk) for pk in synthetic],
                "hide_auxiliary": True}
    path=Path(settings.BASE_DIR)/"tmp/handoff/phase7/demo-navigation.json"
    try:
        value=json.loads(path.read_text(encoding="utf8"))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}

def split(request,memberships):
    rows=list(memberships);config=manifest(request)
    main=list(config.get("showcase",[]));known=set(config.get("synthetic_rooms",[]))
    if not main or not known:return rows,[]
    primary=[m for m in rows if str(m.room_id) in main or str(m.room_id) not in known]
    auxiliary=[m for m in rows if str(m.room_id) in known and str(m.room_id) not in main]
    primary.sort(key=lambda m:(main.index(str(m.room_id)) if str(m.room_id) in main else len(main),m.room.name))
    if config.get("hide_auxiliary"):
        return primary, []
    return primary,auxiliary

@require_GET
def auxiliary(request):
    from .services import my_memberships
    if not manifest(request):raise Http404()
    _,rows=split(request,my_memberships(request).order_by("room__name"))
    return render(request,"care7/demo_auxiliary.html",{"title":"추가 검수 자료","memberships":rows,"has_identity":True})
