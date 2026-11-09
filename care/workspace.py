"""Task-oriented screens with the existing server-side membership boundary."""
import json
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.http import require_GET, require_http_methods
from . import services, organization, status_view
from .modules import MODULES
from .dashboard import latest_module_records, module_board_tiles


@require_GET
def module_workspace(request,room_id):
    room,member=services.get_room(request,room_id)
    allowed=set(services.visible_modules(member))
    rows=[{"spec":MODULES[s.key],"enabled":s.enabled,"pinned":s.pinned,"category":organization.CATEGORY_OF[s.key]} for s in room.module_settings.all() if s.key in allowed]
    recent = latest_module_records(member)
    tiles = module_board_tiles(member, rows)
    for row in rows:
        row["latest"] = recent.get(row["spec"].key)
        row["tile"] = tiles.get(row["spec"].key)
    enabled=[r for r in rows if r["enabled"]]
    sections=[{"key":key,"name":label,"rows":[r for r in enabled if r["category"]==key]} for key,(label,_) in organization.CATEGORIES.items()]
    return render(request,"care/modules.html",{"title":"돌봄 기능","sections":sections,"enabled_modules":enabled,"inactive_modules":[r for r in rows if not r["enabled"]],"active_tab":"modules",**organization.browse_context(member)})


@require_GET
def overview_page(request,room_id):
    room,member=services.get_room(request,room_id)
    return render(request,"care/overview.html",{"title":"최근 변화","active_tab":"overview","live_page":True,**status_view.context_for(member)})


@require_GET
def analysis_method(request,room_id):
    services.get_room(request,room_id)
    return redirect("overview", room_id=room_id)


@require_GET
def group_page(request,room_id):
    room,member=services.get_room(request,room_id)
    services.require_manager(member)
    return render(request,"care/groups.html",{"title":"내 그룹","active_tab":"modules","group_state":organization.state(room),"group_modules":[{"key":m.key,"name":m.name,"category":organization.CATEGORY_OF[m.key]} for m in MODULES.values()],**organization.browse_context(member)})


@require_http_methods(["GET","POST"])
def group_config(request,room_id):
    room,member=services.get_room(request,room_id)
    services.require_manager(member)
    if request.method=="GET":return JsonResponse({"ok":True,**organization.state(room)})
    try:
        data=json.loads(request.body)
        result=organization.save_groups(member,data)
        return JsonResponse({"ok":True,**result})
    except services.Conflict as exc:
        return JsonResponse({"ok":False,"message":" ".join(exc.messages)},status=409)
    except (ValueError,TypeError,ValidationError) as exc:
        return JsonResponse({"ok":False,"message":" ".join(exc.messages) if isinstance(exc,ValidationError) else "그룹 입력을 확인해 주세요."},status=400)
    except OperationalError:
        return JsonResponse({"ok":False,"message":"지금은 저장할 수 없어요. 입력한 내용은 남아 있으니 다시 저장해 주세요."},status=503)
