"""Membership, invitation and join request handlers."""

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from care import services
from care.forms import InvitationForm, JoinForm
from care.models import Membership
from care.views.common import add_form_error, error


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
    if settings.PUBLIC_DEMO:
        raise PermissionDenied("공개 시연 구성원은 나갈 수 없습니다.")
    room, member = services.get_room(request, room_id)
    try:
        services.leave_room(member)
    except ValidationError as exc:
        return error(request, "지금은 이 공간에서 나갈 수 없어요", " ".join(exc.messages), 400)
    messages.success(request, "이 공간에서 나왔어요. 함께 돌보며 남긴 기록은 그대로 남아요.")
    return redirect("home")
