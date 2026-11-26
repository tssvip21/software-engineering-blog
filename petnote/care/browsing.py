"""Category-first record browsing and narrowly scoped module configuration."""
from datetime import date
from urllib.parse import urlencode, urlsplit
import uuid
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction, OperationalError
from django.db.models import Q, Count
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods
from . import services
from .json_inputs import load_json
from .models import Activity, Room
from .modules import MODULES
from .organization import CATEGORIES, CATEGORY_OF
from .presentation import GROUPS
from .reporting.stats import bounds


def record_return(request, room):
    value = request.POST.get("return_to", request.GET.get("return_to", ""))
    fallback = reverse("records", args=[room.pk])
    if not isinstance(value, str) or len(value) > 3000 or any(ord(c) < 32 for c in value) or "\\" in value:
        return fallback
    try:
        parsed = urlsplit(value)
    except ValueError:
        return fallback
    if parsed.scheme or parsed.netloc or parsed.path != fallback:
        return fallback
    return parsed.path + ("?" + parsed.query if parsed.query else "")


def with_return(path, return_to):
    return path + ("&" if "?" in path else "?") + urlencode({"return_to": return_to})


def category_state(room, category):
    title, keys = CATEGORIES[category]
    from .modules import SELECTABLE_MODULES
    keys = tuple(k for k in keys if k in SELECTABLE_MODULES)
    settings = {row.key: row for row in room.module_settings.all()}
    room.refresh_from_db(fields=["settings_version"])
    return {"ok": True, "category": category, "title": title, "version": room.settings_version,
            "items": [{"key": k, "name": MODULES[k].name, "description": MODULES[k].description,
                       "selected": settings[k].enabled} for k in keys]}


@transaction.atomic
def apply_category(member, category, values):
    member = services.fresh_member(member)
    services.require_manager(member)
    room = Room.objects.select_for_update().get(pk=member.room_id)
    if category not in CATEGORIES:
        raise Http404()
    if not isinstance(values, dict) or type(values.get("version")) is not int or values["version"] < 1:
        raise ValidationError("선택 내용을 확인해 주세요.")
    selected = values.get("selected")
    from .modules import SELECTABLE_MODULES
    keys = set(CATEGORIES[category][1]) & set(SELECTABLE_MODULES)
    if not isinstance(selected, list) or any(not isinstance(k, str) or k not in keys for k in selected) or len(set(selected)) != len(selected):
        raise ValidationError("이 분류에 있는 기능만 선택할 수 있어요.")
    if room.settings_version != values["version"]:
        raise services.Conflict("다른 사람이 기능 설정을 바꿨어요. 내 선택은 그대로 두었으니 최신 설정을 확인해 주세요.")
    rows = list(room.module_settings.all())
    before = {row.key for row in rows if row.enabled and row.key in keys}
    if before != set(selected):
        enabled = [row.key for row in rows if (row.key in selected if row.key in keys else row.enabled)]
        # Only activation changes. Order and every existing pin remain untouched.
        services.update_modules(member, enabled, [row.key for row in rows], room.settings_version, pinned=None)
    return category_state(room, category)


@transaction.atomic
@require_http_methods(["GET", "POST"])
def category_config(request, room_id, category):
    room, member = services.get_room(request, room_id)
    services.require_manager(member)
    if category not in CATEGORIES:
        raise Http404()
    if request.method == "GET":
        return JsonResponse(category_state(room, category))
    try:
        result = apply_category(member, category, load_json(request.body))
        result["message"] = "선택한 기능을 적용했어요. 이전 기록은 그대로 남아 있어요."
        return JsonResponse(result)
    except services.Conflict as exc:
        return JsonResponse({"ok": False, "message": " ".join(exc.messages)}, status=409)
    except (ValueError, TypeError, ValidationError) as exc:
        return JsonResponse({"ok": False, "message": " ".join(exc.messages) if isinstance(exc, ValidationError) else "선택 내용을 읽지 못했어요. 다시 확인해 주세요."}, status=400)
    except OperationalError:
        return JsonResponse({"ok": False, "message": "지금은 저장할 수 없어요. 선택은 유지했으니 잠시 후 다시 적용해 주세요."}, status=503)


@require_GET
def records(request, room_id):
    from .views import record_card
    room, member = services.get_room(request, room_id)
    allowed = set(services.visible_modules(member))
    from .supplies import hidden_stock_names, visible_records
    hidden = hidden_stock_names(member)
    qs = visible_records(member, Activity.objects.filter(room=room, module__in=allowed).select_related("author", "performer"), hidden_names=hidden)
    category = request.GET.get("category", "")
    if category and category not in CATEGORIES:
        raise Http404()
    module = request.GET.get("module", "")
    selected_modules = request.GET.get("modules", "")
    selected_group = request.GET.get("group", "")
    if module and module not in allowed:
        raise PermissionDenied()
    selected_keys = []
    legacy_keys = []
    if selected_modules:
        keys = selected_modules.split(",")
        if len(keys) > len(MODULES) or any(k not in allowed for k in keys):
            raise PermissionDenied()
        selected_keys = keys
    if selected_group:
        if selected_group not in GROUPS:
            raise Http404()
        legacy_keys = set(GROUPS[selected_group][1]) & allowed
    if module:
        category = category or CATEGORY_OF[module]
    state = request.GET.get("state", "valid")
    if state == "cancelled":
        qs = qs.filter(voided=True)
    elif state in ("done", "active", "planned"):
        qs = qs.filter(voided=False, status=state)
    elif state != "all":
        state = "valid"
        qs = qs.filter(voided=False)
    problem = ""
    author = request.GET.get("author", "")
    if author:
        try:
            qs = qs.filter(author_id=uuid.UUID(author))
        except ValueError:
            problem = "기록한 사람을 다시 선택해 주세요."
            qs = qs.none()
    begin, end = request.GET.get("start", ""), request.GET.get("end", "")
    try:
        if begin:
            start_day = date.fromisoformat(begin)
            qs = qs.filter(occurred_at__gte=bounds(room, start_day, start_day)[0])
        if end:
            end_day = date.fromisoformat(end)
            qs = qs.filter(occurred_at__lt=bounds(room, end_day, end_day)[1])
        if begin and end and start_day > end_day:
            raise ValueError()
    except (ValueError, OverflowError):
        problem = "조회할 시작일과 종료일을 확인해 주세요."
        qs = qs.none()
    query = request.GET.get("q", "").strip()[:100]
    if query:
        qs = qs.filter(Q(note__icontains=query) | Q(payload__icontains=query))
    # Counts are permission-scoped and computed over all matches, not a first page.
    counts = {row["module"]: row["n"] for row in qs.values("module").annotate(n=Count("id")).order_by()}
    # Navigation counts reflect the same date/search/person filters before a
    # single activity is chosen. Category links clear that activity selection.
    if selected_keys:
        qs = qs.filter(module__in=selected_keys)
    if selected_group:
        qs = qs.filter(module__in=legacy_keys)
    if module:
        qs = qs.filter(module=module)
    root = reverse("records", args=[room.pk])
    return_to = request.get_full_path()
    def link(**changes):
        params = request.GET.copy()
        params.pop("page", None)
        for k, v in changes.items():
            params.pop(k, None)
            if v:
                params[k] = v
        return root + ("?" + params.urlencode() if params else "")
    def prepare(activity):
        record_card(activity, member, hidden_names=hidden)
        activity.detail_url = with_return(reverse("record_detail", args=[room.pk, activity.pk]), return_to)
        return activity
    categories = []
    for key, (label, keys) in CATEGORIES.items():
        visible = [k for k in keys if k in allowed]
        categories.append({"key": key, "label": label, "count": sum(counts.get(k, 0) for k in visible),
                           "selected": category == key, "url": link(category=key, module="", modules="", group=""),
                           "modules": [{"key": k, "label": MODULES[k].name, "count": counts.get(k, 0),
                                        "url": link(category=key, module=k, modules="", group="")} for k in visible]})
    matching = qs.filter(module__in=CATEGORIES[category][1]) if category else qs
    page = Paginator(matching, 20).get_page(request.GET.get("page"))
    page.object_list = [prepare(a) for a in page.object_list]
    sections = []
    if not category:
        for entry in categories:
            keys = [r["key"] for r in entry["modules"]]
            recent = [prepare(a) for a in qs.filter(module__in=keys)[:3]] if entry["count"] else []
            sections.append({**entry, "recent": recent})
    params = request.GET.copy()
    params.pop("page", None)
    if category:
        params["category"] = category
    scope = [k for k in CATEGORIES[category][1] if k in allowed] if category else [k for k in MODULES if k in allowed]
    return render(request, "care/records.html", {"title": "전체 기록", "active_tab": "records", "live_page": True,
        "page_obj": page, "record_categories": categories, "record_sections": sections, "selected_category": category,
        "category_label": CATEGORIES[category][0] if category else "분류별 기록", "selected_module": module,
        "selected_modules": selected_modules, "selected_group": selected_group, "selected_state": state,
        "selected_author": author, "module_choices": [(k, MODULES[k].name) for k in scope],
        "authors": room.members.order_by("display_name"), "start": begin, "end": end, "query": query,
        "problem": problem, "query_params": params.urlencode(), "all_categories_url": link(category="", module="", modules="", group=""),
        "filters_active": bool(begin or end or query or author or state != "valid"),
        "filtered_scope": bool(selected_modules or selected_group), "return_to": return_to})
