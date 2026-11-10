from .organization import browse_context
from .navigation import record_return, with_return
from datetime import date, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import json
import uuid
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import connection, OperationalError
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from . import services, cadences
from .dashboard import today_tiles, statistics_periods
from .presentation import integrated_context, GROUPS
from .interactions import config_state
from .forms import RoomForm, ProductForm, RecordForm, InvitationForm, JoinForm, CancelForm, RoutineForm, StatsFilterForm
from .models import Activity, Product, Routine, RoutineItem, RoutineRun, Membership
from .modules import MODULES, MODULE_LIST, MODULE_CHOICES, payload_display, record_summary
from .stats import bounds, get_statistics, overview, routine_schedule


def error(request, title, detail, status=400):
    return render(request, "care/error.html", {"title": title, "detail": detail}, status=status)


def add_form_error(form, exc):
    for message in exc.messages:
        form.add_error(None, message)


def record_card(activity, member):
    activity.spec = MODULES[activity.module]
    activity.can_edit = services.can_edit_record(member, activity) and not activity.voided
    activity.brief = record_summary(activity.module, activity.payload)
    if activity.module == "medication" and activity.status == "planned" and activity.payload.get("result") == "given":
        activity.brief = activity.brief.removesuffix("투약함") + "투약 예정"
    activity.display_status = "취소됨" if activity.voided else activity.get_status_display()
    return activity


@require_GET
def home(request):
    memberships = list(services.my_memberships(request).order_by("room__name"))
    return render(request, "care/home.html", {"memberships": memberships, "has_identity": bool(memberships or request.user.is_authenticated)})


@require_http_methods(["GET", "POST"])
def room_new(request):
    if not request.user.is_authenticated:
        messages.info(request, "반려동물을 등록하려면 구글 계정으로 로그인해 주세요.")
        return redirect("home")
    form = RoomForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            if not services.rate_limit(request, "room-create", 10, 3600):
                return error(request, "잠시 후 다시 시도해 주세요", "등록 요청이 여러 번 이어졌어요. 잠시 후 다시 시도해 주세요.", 429)
            room = services.create_room(request.user, form)
            messages.success(request, "반려동물을 등록했어요. 오늘의 돌봄을 남겨 보세요.")
            return redirect("today", room_id=room.pk)
        except ValidationError as exc:
            add_form_error(form, exc)
    return render(request, "care/form.html", {"form": form, "title": "반려동물 등록", "eyebrow": "새 돌봄 공간", "intro": "이름과 동물 종류를 알려 주세요. 나머지는 나중에 적어도 돼요.", "submit_label": "돌봄 공간 만들기", "back_url": reverse("home")})


@require_GET
def today(request, room_id):
    room, member = services.get_room(request, room_id)
    day = services.local_date(room)
    lower, upper = bounds(room, day, day)
    allowed = services.visible_modules(member)
    activities = list(Activity.objects.filter(room=room, module__in=allowed, voided=False).select_related("author", "performer").order_by("-occurred_at", "-created_at"))
    recent = [record_card(a, member) for a in activities if a.occurred_at <= timezone.now()][:6]
    ongoing = [record_card(a, member) for a in activities if a.status == "active"]
    planned = sorted([record_card(a, member) for a in activities if a.status == "planned"], key=lambda a: a.occurred_at)[:8]
    cards = []
    for setting in room.module_settings.filter(enabled=True, pinned=True):
        from .modules import SELECTABLE_MODULES
        if setting.key not in allowed or setting.key not in SELECTABLE_MODULES:
            continue
        candidates = [a for a in activities if a.module == setting.key and a.status != "planned"]
        completed_today = [a for a in candidates if a.status == "done" and lower <= a.occurred_at < upper]
        cards.append({"spec": MODULES[setting.key], "count": len(completed_today), "latest": candidates[0] if candidates else None})
    cards = today_tiles(member, cards, activities, day)
    schedule = routine_schedule(member, day)
    due = [row for row in cadences.schedule(member) if row["due"]]
    total = sum(r["total"] for r in schedule)
    done = sum(r["done"] for r in schedule)
    return render(request, "care/today.html", {"title": room.name + "의 오늘", "day": day, "cards": cards, "due_cadences": due, "cards_more": [], "recent": recent, "ongoing": ongoing,
                  "planned": planned, "schedule": schedule, "routine_total": total, "routine_done": done, "routine_progress": round(done / total * 100, 2) if total else 0, "active_tab": "today", "live_page": True,
                  "today_count": sum(a.status == "done" and lower <= a.occurred_at < upper and a.occurred_at <= timezone.now() for a in activities)})


@require_GET
def module_page(request, room_id, module):
    room, member = services.get_room(request, room_id)
    services.require_module(member, module)
    enabled = room.module_settings.get(key=module).enabled
    records = Activity.objects.filter(room=room, module=module).select_related("author", "performer")
    if request.GET.get("include_cancelled") != "1":
        records = records.filter(voided=False)
    page = Paginator(records, 20).get_page(request.GET.get("page"))
    page.object_list = [record_card(a, member) for a in page.object_list]
    today_date = services.local_date(room)
    stats = get_statistics(room, module, today_date - timedelta(days=6), today_date)
    from .modules import RETIRED_MODULES
    from .comparisons import recent_statistics
    comparison = recent_statistics(member) if module in ("weight", "walk") else {}
    comparison["module_comparison_cards"] = [c for c in comparison.get("comparison_cards", []) if c["module"] == module]
    return render(request, "care/module.html", {**comparison, "retired_module": module in RETIRED_MODULES, "title": MODULES[module].name, "spec": MODULES[module], "enabled": enabled, "page_obj": page,
                  "stats": stats, "cadences": [row for row in cadences.schedule(member, include_paused=True) if row["cadence"].module == module], "activity_options": [(key, value[3]) for key, value in cadences.activity_options([module]).items()] if module not in RETIRED_MODULES else [], "active_tab": "modules", "include_cancelled": request.GET.get("include_cancelled") == "1", "live_page": True})


@require_GET
def modules(request, room_id):
    room, member = services.get_room(request, room_id)
    allowed = services.visible_modules(member)
    groups = {}
    enabled_modules = []
    for setting in room.module_settings.all():
        spec = MODULES[setting.key]
        if spec.key not in allowed:
            continue
        row = {"spec": spec, "enabled": setting.enabled, "pinned": setting.pinned}
        groups.setdefault(spec.group, []).append(row)
        if setting.enabled:
            enabled_modules.append(row)
    return render(request, "care/modules.html", {"title": "돌봄 기능", "groups": groups.items(), "enabled_modules": enabled_modules, "active_tab": "modules", "hidden_count": len(MODULES) - len(allowed)})


@require_http_methods(["GET", "POST"])
def module_settings(request, room_id):
    room, member = services.get_room(request, room_id)
    services.require_manager(member)
    problem, status = "", 200
    if request.method == "POST":
        try:
            enabled = request.POST.getlist("enabled")
            order = json.loads(request.POST.get("order", "[]"))
            version = int(request.POST.get("version", "0"))
            services.update_modules(member, enabled, order, version, request.POST.getlist("pinned") if "pin_config" in request.POST else None)
            messages.success(request, "사용할 기능을 저장했어요. 사용하지 않는 기능의 기록도 그대로 남아 있어요.")
            return redirect("modules", room_id=room.pk)
        except (ValueError, TypeError, ValidationError) as exc:
            problem = " ".join(exc.messages) if isinstance(exc, ValidationError) else "선택한 기능을 다시 확인해 주세요."
            status = 409 if isinstance(exc, services.Conflict) else 400
    settings_rows = list(room.module_settings.all())
    from .modules import SELECTABLE_MODULES
    rows = [{"spec": MODULES[s.key], "enabled": s.enabled, "pinned": s.pinned} for s in settings_rows if s.key in SELECTABLE_MODULES]
    return render(request, "care/module_settings.html", {"title": "기능 설정", "rows": rows, "config_state": config_state(room), "chosen": [r for r in rows if r["enabled"]], "order_json": json.dumps([s.key for s in settings_rows]),
                  "problem": problem, "active_tab": "modules", **browse_context(member)}, status=status)


@require_http_methods(["GET", "POST"])
def record_new(request, room_id, module):
    return record_form_view(request, room_id, module)


@require_http_methods(["GET", "POST"])
def record_edit(request, room_id, activity_id):
    room, member = services.get_room(request, room_id)
    activity = get_object_or_404(Activity, pk=activity_id, room=room)
    services.require_module(member, activity.module)
    if not services.can_edit_record(member, activity):
        raise PermissionDenied("자신의 기록만 수정할 수 있어요.")
    return record_form_view(request, room_id, activity.module, activity)


def record_form_view(request, room_id, module, activity=None):
    room, member = services.get_room(request, room_id)
    try:
        services.require_module(member, module, adding=activity is None)
    except ValidationError as exc:
        return error(request, "사용하지 않는 기능이에요", " ".join(exc.messages), 409)
    if activity and activity.voided:
        return error(request, "취소한 기록이에요", "기존 이력은 상세 화면에서 확인하고, 필요한 경우 새로 기록해 주세요.", 409)
    initial = {}
    if request.method == "GET":
        if request.GET.get("routine_item"):
            try:
                item_id = uuid.UUID(request.GET["routine_item"])
                day = date.fromisoformat(request.GET.get("date", ""))
                item = services.require_routine_slot(member, item_id, day, module)
                initial.update({"routine_item": item.pk, "routine_date": day})
                if day != services.local_date(room):
                    initial["occurred_at"] = datetime_for_day(room, day, item.routine.at)
            except (ValueError, ValidationError) as exc:
                return error(request, "루틴 정보를 확인해 주세요", " ".join(exc.messages) if isinstance(exc, ValidationError) else "올바른 날짜를 선택해 주세요.")
        if request.GET.get("start") == "1" and module in ("walk", "shedding"):
            initial["status"] = "active"
    form = RecordForm(request.POST or None, room=room, module=module, member=member, instance=activity, initial=initial)
    if request.method == "GET" and activity and request.GET.get("finish") == "1" and activity.status == "active":
        form.initial.update({"status": "done", "ended_at": timezone.localtime()})
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            record, changed = services.save_activity(member, module, form, activity.pk if activity else None)
            messages.success(request, "기록을 저장했어요." if changed else "이미 저장한 기록이에요. 같은 기록을 다시 만들지 않았어요.")
            return redirect(with_return(reverse("record_detail", args=[room.pk, record.pk]), record_return(request, room)))
        except ValidationError as exc:
            add_form_error(form, exc)
            status = 409 if isinstance(exc, services.Conflict) else 400
        except OperationalError:
            form.add_error(None, "지금은 기록을 저장할 수 없어요. 입력은 유지했으니 잠시 후 다시 저장해 주세요.")
            status = 503
    return render(request, "care/record_form.html", {"title": MODULES[module].name + (" 기록 수정" if activity else " 기록"), "form": form,
                  "spec": MODULES[module], "activity": activity, "active_tab": "modules", "return_to": record_return(request, room), "back_url": with_return(reverse("record_detail", args=[room.pk, activity.pk]), record_return(request, room)) if activity else reverse("module", args=[room.pk, module])}, status=status)


def datetime_for_day(room, day, at):
    from datetime import datetime
    return datetime.combine(day, at, ZoneInfo(room.timezone))


@require_GET
def record_detail(request, room_id, activity_id):
    room, member = services.get_room(request, room_id)
    activity = get_object_or_404(Activity.objects.select_related("author", "performer"), pk=activity_id, room=room)
    services.require_module(member, activity.module)
    record_card(activity, member)
    revisions = list(activity.revisions.select_related("actor").all())
    for revision in revisions:
        revision.after_values = payload_display(activity.module, revision.after.get("payload", {}))
        revision.before_values = payload_display(activity.module, revision.before.get("payload", {})) if revision.before else []
    run = RoutineRun.objects.filter(activity=activity).select_related("item__routine").first()
    return render(request, "care/record_detail.html", {"title": activity.spec.name + " 기록", "activity": activity, "details": payload_display(activity.module, activity.payload, status=activity.status),
                  "revisions": revisions, "run": run, "return_to": record_return(request, room), "edit_url": with_return(reverse("record_edit", args=[room.pk, activity.pk]) + ("?finish=1" if activity.status == "active" else ""), record_return(request, room)), "cancel_form": CancelForm(initial={"version": activity.version}), "active_tab": "records"})


@require_POST
def record_cancel(request, room_id, activity_id):
    room, member = services.get_room(request, room_id)
    activity = get_object_or_404(Activity, pk=activity_id, room=room)
    services.require_module(member, activity.module)
    form = CancelForm(request.POST)
    status = 400
    if form.is_valid():
        try:
            services.cancel_activity(member, activity.pk, form.cleaned_data["version"], form.cleaned_data["reason"])
            messages.success(request, "기록을 취소했어요. 통계에는 포함하지 않고 수정 이력은 남겨요.")
            return redirect(with_return(reverse("record_detail", args=[room.pk, activity.pk]), record_return(request, room)))
        except ValidationError as exc:
            add_form_error(form, exc)
            status = 409 if isinstance(exc, services.Conflict) else 400
    return render(request, "care/form.html", {"title": "기록 취소", "intro": "입력값을 확인한 뒤 다시 시도해 주세요.", "form": form,
                  "submit_label": "기록 취소", "danger": True, "back_url": reverse("record_detail", args=[room.pk, activity.pk]), "active_tab": "records"}, status=status)


@require_GET
def records(request, room_id):
    room, member = services.get_room(request, room_id)
    allowed = services.visible_modules(member)
    qs = Activity.objects.filter(room=room, module__in=allowed).select_related("author", "performer")
    selected_modules = request.GET.get("modules", "")
    if selected_modules:
        keys = selected_modules.split(",")
        if len(keys) > len(MODULES) or any(k not in allowed for k in keys):
            raise PermissionDenied()
        qs = qs.filter(module__in=keys)
    selected_module = request.GET.get("module", "")
    selected_group = request.GET.get("group", "")
    if selected_group in GROUPS:
        qs = qs.filter(module__in=set(GROUPS[selected_group][1]) & set(allowed))
    elif selected_group:
        raise Http404()
    selected_state = request.GET.get("state", "valid")
    selected_author = request.GET.get("author", "")
    problem = ""
    if selected_module:
        if selected_module not in allowed:
            raise PermissionDenied()
        qs = qs.filter(module=selected_module)
    if selected_state == "cancelled":
        qs = qs.filter(voided=True)
    elif selected_state in ("done", "active", "planned"):
        qs = qs.filter(voided=False, status=selected_state)
    elif selected_state != "all":
        qs = qs.filter(voided=False)
    if selected_author:
        try:
            author = uuid.UUID(selected_author)
            qs = qs.filter(author_id=author)
        except ValueError:
            problem = "기록자를 다시 선택해 주세요."
            qs = qs.none()
    begin, end = request.GET.get("start", ""), request.GET.get("end", "")
    try:
        if begin:
            begin_day = date.fromisoformat(begin)
            qs = qs.filter(occurred_at__gte=bounds(room, begin_day, begin_day)[0])
        if end:
            end_day = date.fromisoformat(end)
            qs = qs.filter(occurred_at__lt=bounds(room, end_day, end_day)[1])
        if begin and end and begin_day > end_day:
            raise ValueError()
    except ValueError:
        problem = "시작일과 종료일을 확인해 주세요."
        qs = qs.none()
    query = request.GET.get("q", "").strip()[:100]
    if query:
        qs = qs.filter(Q(note__icontains=query) | Q(payload__icontains=query))
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    page.object_list = [record_card(a, member) for a in page.object_list]
    params = request.GET.copy(); params.pop("page", None)
    return render(request, "care/records.html", {"title": "전체 기록", "page_obj": page, "module_choices": [(k, MODULES[k].name) for k in allowed],
                  "selected_modules": selected_modules, "selected_module": selected_module, "selected_group": selected_group, "group_label": GROUPS.get(selected_group, ("", ()))[0], "selected_state": selected_state, "selected_author": selected_author,
                  "authors": room.members.order_by("display_name"), "start": begin, "end": end, "query": query, "query_params": params.urlencode(),
                  "problem": problem, "active_tab": "records", "live_page": True})


@require_GET
def statistics(request, room_id, module):
    room, member = services.get_room(request, room_id)
    services.require_module(member, module)
    today_date = services.local_date(room)
    initial = {"start": today_date - timedelta(days=29), "end": today_date}
    submitted = {"start": request.GET.get("start"), "end": request.GET.get("end")} if "start" in request.GET or "end" in request.GET else None
    form = StatsFilterForm(submitted, initial=initial)
    stats = None
    if submitted is None:
        stats = get_statistics(room, module, initial["start"], initial["end"])
    elif form.is_valid():
        stats = get_statistics(room, module, form.cleaned_data["start"], form.cleaned_data["end"])
    chosen = form.cleaned_data if submitted is not None and form.is_valid() else initial if submitted is None else {}
    periods = statistics_periods(room, module, chosen.get("start"), chosen.get("end"))
    return render(request, "care/statistics.html", {"title": MODULES[module].name + " 통계", "spec": MODULES[module], "form": form, "stats": stats, "periods": periods, "active_tab": "overview"})


@require_GET
def overview_page(request, room_id):
    room, member = services.get_room(request, room_id)
    context = integrated_context(member)
    context.update({"title": "최근 통계", "active_tab": "overview", "live_page": True})
    return render(request, "care/overview.html", context)


@require_GET
def products(request, room_id):
    room, member = services.get_room(request, room_id)
    return render(request, "care/products.html", {"title": "사료·간식 정보", "products": room.products.order_by("-active", "name"), "active_tab": "settings"})


@require_http_methods(["GET", "POST"])
def product_edit(request, room_id, product_id=None):
    room, member = services.get_room(request, room_id)
    services.require_manager(member)
    instance = get_object_or_404(Product, pk=product_id, room=room) if product_id else None
    form = ProductForm(request.POST or None, instance=instance)
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            services.save_product(member, form, instance.pk if instance else None)
            messages.success(request, "제품 정보를 저장했어요. 이전 기록에 쓰인 영양 정보는 그대로예요.")
            return redirect("products", room_id=room.pk)
        except ValidationError as exc:
            add_form_error(form, exc)
            status = 409 if isinstance(exc, services.Conflict) else 400
    return render(request, "care/form.html", {"title": "제품 정보 수정" if instance else "제품 등록", "intro": "포장지에서 확인한 값과 단위를 적어 주세요. 모르는 값은 비워 두세요.",
                  "form": form, "submit_label": "제품 저장", "back_url": reverse("products", args=[room.pk]), "active_tab": "settings"}, status=status)


@require_http_methods(["GET", "POST"])
def room_settings(request, room_id):
    room, member = services.get_room(request, room_id)
    if not member.can_manage:
        return render(request, "care/room_info.html", {"title": "반려동물 정보", "active_tab": "settings"})
    form = RoomForm(request.POST or None, instance=room, member=member)
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            services.update_room(member, form)
            messages.success(request, "정보를 저장했어요. 사용 중인 돌봄 기능은 바뀌지 않았어요.")
            return redirect("room_settings", room_id=room.pk)
        except ValidationError as exc:
            add_form_error(form, exc)
            status = 409 if isinstance(exc, services.Conflict) else 400
    return render(request, "care7/settings.html", {"title": "공간 설정", "form": form, "active_tab": "settings"}, status=status)


def members_context(room, member, form=None, invite_url=None):
    return {"title": "구성원", "people": room.members.filter(active=True).order_by("joined_at"), "invitation_form": form or InvitationForm(),
            "invitations": list(room.invitations.order_by("-created_at")[:20]) if member.role == "owner" else [], "invite_url": invite_url, "active_tab": "members"}


@require_GET
def members(request, room_id):
    room, member = services.get_room(request, room_id)
    flash = request.session.pop("created_invitation", None)
    link = flash["url"] if flash and flash.get("room_id") == str(room.pk) and member.role == "owner" else None
    return render(request, "care/members.html", members_context(room, member, invite_url=link))


@require_POST
def invitation_create(request, room_id):
    room, member = services.get_room(request, room_id)
    services.require_owner(member)
    form = InvitationForm(request.POST)
    if form.is_valid():
        if not services.rate_limit(request, "invite-create", 20, 3600):
            return error(request, "잠시 후 다시 시도해 주세요", "초대 링크를 여러 번 만들었어요. 잠시 후 다시 만들어 주세요.", 429)
        _, token = services.create_invitation(member, form.cleaned_data["hours"], form.cleaned_data["max_uses"])
        request.session["created_invitation"] = {"room_id": str(room.pk), "url": settings.APP_ORIGIN + reverse("join") + "#" + token}
        return redirect("members", room_id=room.pk)
    return render(request, "care/members.html", members_context(room, member, form=form), status=400)


@require_POST
def invitation_revoke(request, room_id, invite_id):
    room, member = services.get_room(request, room_id)
    services.require_owner(member)
    get_object_or_404(room.invitations, pk=invite_id)
    services.revoke_invitation(member, invite_id)
    messages.success(request, "초대 링크 사용을 중단했어요. 이미 참여한 사람을 내보내려면 구성원 메뉴에서 선택해 주세요.")
    return redirect("members", room_id=room.pk)


@require_GET
def join_entry(request):
    return render(request, "care/join_entry.html", {"title": "돌봄 공간에 참여", "autofocus_join": True})


@require_POST
def join_preview(request):
    if not services.rate_limit(request, "invite-preview", 30, 60):
        return error(request, "잠시 후 다시 시도해 주세요", "초대를 여러 번 확인했어요. 잠시 후 다시 열어 주세요.", 429)
    token = request.POST.get("token", "").strip()
    if "#" in token:
        token = token.rsplit("#", 1)[-1]
    try:
        invite = services.get_invitation(token)
    except ValidationError as exc:
        return error(request, "초대를 확인하지 못했어요", " ".join(exc.messages), 410)
    form = JoinForm(initial={"token": token, "display_name": request.user.first_name[:40] if request.user.is_authenticated else ""})
    return render(request, "care/join.html", {"title": "돌봄 공간에 참여", "invited_room": invite.room, "form": form, "expires_at": invite.expires_at})


@require_POST
def join_confirm(request):
    if not services.rate_limit(request, "invite-join", 12, 60):
        return error(request, "잠시 후 다시 시도해 주세요", "참여 요청이 여러 번 이어졌어요. 잠시 후 다시 시도해 주세요.", 429)
    form = JoinForm(request.POST)
    try:
        invite = services.get_invitation(request.POST.get("token", ""))
    except ValidationError as exc:
        return error(request, "초대를 확인하지 못했어요", " ".join(exc.messages), 410)
    if form.is_valid():
        try:
            member = services.join_room(request, form.cleaned_data["token"], form.cleaned_data["display_name"])
            messages.success(request, "함께 돌보는 공간에 참여했어요. 맡은 돌봄을 기록해 주세요.")
            return redirect("today", room_id=member.room_id)
        except ValidationError as exc:
            add_form_error(form, exc)
    return render(request, "care/join.html", {"title": "돌봄 공간에 참여", "invited_room": invite.room, "form": form, "expires_at": invite.expires_at}, status=400)


@require_POST
def member_change(request, room_id, member_id):
    room, member = services.get_room(request, room_id)
    services.require_owner(member)
    get_object_or_404(Membership, pk=member_id, room=room, active=True)
    try:
        services.change_member(member, member_id, request.POST.get("action"), request.POST.get("revoke_links") == "1")
        if request.headers.get("X-Taily-UI") == "1":
            return JsonResponse({"ok": True, "message": "구성원 정보를 변경했어요."})
        messages.success(request, "참여자 권한을 변경했어요.")
    except ValidationError as exc:
        if request.headers.get("X-Taily-UI") == "1":
            return JsonResponse({"ok": False, "message": " ".join(exc.messages)}, status=400)
        messages.error(request, " ".join(exc.messages))
    return redirect("members", room_id=room.pk)


@require_POST
def room_leave(request, room_id):
    room, member = services.get_room(request, room_id)
    try:
        services.leave_room(member)
    except ValidationError as exc:
        return error(request, "지금은 이 공간에서 나갈 수 없어요", " ".join(exc.messages), 400)
    messages.success(request, "이 공간에서 나왔어요. 함께 돌보며 남긴 기록은 그대로 남아요.")
    return redirect("home")


@require_POST
def room_archive(request, room_id):
    room, member = services.get_room(request, room_id)
    try:
        services.archive_room(member, request.POST.get("confirmation", ""))
    except ValidationError as exc:
        return error(request, "이름을 확인해 주세요", " ".join(exc.messages), 400)
    messages.success(request, "공간을 보관했어요. 목록에서 숨기고 새 참여를 막았으며, 기존 기록은 보관했어요.")
    return redirect("home")


@require_GET
def routines(request, room_id):
    room, member = services.get_room(request, room_id)
    today_date = services.local_date(room)
    try:
        day = date.fromisoformat(request.GET.get("date", today_date.isoformat()))
        if not today_date - timedelta(days=366) <= day <= today_date + timedelta(days=31):
            raise ValueError()
    except ValueError:
        return error(request, "날짜를 확인해 주세요", "최근 1년부터 앞으로 31일까지 선택할 수 있어요.")
    schedule = routine_schedule(member, day)
    return render(request, "care/routines.html", {"title": "루틴", "schedule": schedule, "day": day, "today_date": today_date, "future_day": day > today_date,
                  "routine_list": list(room.routines.all()) if member.can_manage else [], "active_tab": "routines", "live_page": True})


@require_http_methods(["GET", "POST"])
def routine_edit(request, room_id, routine_id=None):
    room, member = services.get_room(request, room_id)
    services.require_manager(member)
    instance = get_object_or_404(Routine, pk=routine_id, room=room) if routine_id else None
    form = RoutineForm(request.POST or None, room=room, visible_keys=services.visible_modules(member), instance=instance)
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            services.save_routine(member, form, routine_id)
            messages.success(request, "루틴을 저장했어요. 바꾼 내용은 오늘부터 적용돼요.")
            return redirect("routines", room_id=room.pk)
        except ValidationError as exc:
            add_form_error(form, exc)
            status = 409 if isinstance(exc, services.Conflict) else 400
    return render(request, "care/form.html", {"title": "루틴 수정" if instance else "새 루틴", "intro": "같은 시간에 확인할 활동을 묶어 주세요. 돌봄 기록을 남기면 완료로 표시해요.",
                  "form": form, "submit_label": "루틴 저장", "back_url": reverse("routines", args=[room.pk]), "active_tab": "routines", "routine_form": True}, status=status)


@require_http_methods(["GET", "POST"])
def routine_link(request, room_id, item_id):
    room, member = services.get_room(request, room_id)
    item = get_object_or_404(RoutineItem.objects.select_related("routine"), pk=item_id, routine__room=room)
    services.require_module(member, item.module)
    try:
        day = date.fromisoformat(request.POST.get("date", "") if request.method == "POST" else request.GET.get("date", ""))
        services.require_routine_slot(member, item.pk, day, item.module)
    except (ValueError, ValidationError) as exc:
        return error(request, "루틴 날짜를 확인해 주세요", " ".join(exc.messages) if isinstance(exc, ValidationError) else "올바른 날짜를 선택해 주세요.")
    lower, upper = bounds(room, day, day)
    candidates = Activity.objects.filter(room=room, module=item.module, voided=False, status__in=("active", "done"), occurred_at__gte=lower, occurred_at__lt=upper, routine_run__isnull=True).select_related("author")
    if not member.can_manage:
        candidates = candidates.filter(author=member)
    problem = ""
    if request.method == "POST":
        try:
            activity_id = uuid.UUID(request.POST.get("activity", ""))
            services.link_activity(member, item.pk, day, activity_id)
            messages.success(request, "이미 남긴 기록을 연결했어요. 같은 기록을 새로 만들지는 않았어요.")
            return redirect(reverse("routines", args=[room.pk]) + "?" + urlencode({"date": day.isoformat()}))
        except (ValueError, ValidationError) as exc:
            problem = " ".join(exc.messages) if isinstance(exc, ValidationError) else "연결할 기록을 선택해 주세요."
    return render(request, "care/routine_link.html", {"title": "기존 기록 연결", "item": item, "day": day, "candidates": [record_card(a, member) for a in candidates], "problem": problem, "active_tab": "routines"})


@require_GET
def room_state(request, room_id):
    room, member = services.get_room(request, room_id)
    return JsonResponse({"revision": room.revision})


@require_GET
def health(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"status": "ok"})


def csrf_failure(request, reason=""):
    return error(request, "화면을 새로고침해 주세요", "요청을 확인할 수 없어요. 오래 열린 탭이나 다른 사이트에서 보낸 요청일 수 있어요.", 403)


def bad_request(request, exception=None):
    return error(request, "요청을 확인해 주세요", "입력한 주소나 값을 확인한 뒤 다시 시도해 주세요.", 400)


def permission_denied(request, exception=None):
    return error(request, "이 작업을 할 수 없어요", "이 공간에 참여 중인지, 필요한 권한이 있는지 확인해 주세요.", 403)


def not_found(request, exception=None):
    return error(request, "페이지를 찾지 못했어요", "주소가 바뀌었거나 참여하지 않은 공간일 수 있어요.", 404)


def server_error(request):
    return error(request, "요청을 완료하지 못했어요", "잠시 후 다시 시도해 주세요. 저장 여부가 불확실하면 전체 기록을 먼저 확인해 주세요.", 500)


@require_POST
def room_delete(request, room_id):
    room, member = services.get_room(request, room_id)
    try:
        services.delete_room(member, request.POST.get("confirmation", ""))
    except ValidationError as exc:
        return error(request, "삭제 확인 문구를 확인해 주세요", " ".join(exc.messages), 400)
    messages.success(request, "이 공간과 기록을 삭제했어요. 다른 반려동물의 기록은 그대로예요.")
    return redirect("home")
