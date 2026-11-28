"""Anonymous entry to the isolated synthetic showcase."""
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_GET

from care.models import Membership, Room
from care.services import rate_limit

SHOWCASE_ROOMS = (('강아지', 'dog'), ('고양이', 'cat'))


@require_GET
def enter(request):
    if not settings.PUBLIC_DEMO:
        raise Http404()
    if not rate_limit(request, 'public-demo-entry', 30, 60):
        raise PermissionDenied('시연 접속이 많습니다. 잠시 후 다시 시도해 주세요.')
    actual=set(Room.objects.filter(owner__username='demo_owner', archived_at__isnull=True).values_list('name','species'))
    expected = set(SHOWCASE_ROOMS)
    if actual!=expected or Room.objects.count()!=len(expected):
        raise PermissionDenied('시연 공간을 확인할 수 없습니다.')
    user=get_user_model().objects.filter(username='demo_admin',is_active=True).first()
    if not user or Membership.objects.filter(room__archived_at__isnull=True,active=True,role='admin',user=user).count()!=len(expected):
        raise PermissionDenied('시연 구성원을 확인할 수 없습니다.')
    login(request,user,backend='django.contrib.auth.backends.ModelBackend')
    response=redirect('home')
    response['Cache-Control']='no-store'
    return response
