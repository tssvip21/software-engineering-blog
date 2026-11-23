"""Anonymous, isolated demo entry: only synthetic rooms and dedicated admin identity."""
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_GET
from .models import Membership, Room
from .services import rate_limit

@require_GET
def enter(request):
    if not settings.PUBLIC_DEMO:
        raise Http404()
    if not rate_limit(request, 'public-demo-entry', 30, 60):
        raise PermissionDenied('시연 접속이 많습니다. 잠시 후 다시 시도해 주세요.')
    actual=set(Room.objects.filter(archived_at__isnull=True).values_list('name','species'))
    expected={('강아지','dog'),('고양이','cat'),('앵무새','bird')}
    if actual!=expected or Room.objects.count()!=3:
        raise PermissionDenied('시연 공간을 확인할 수 없습니다.')
    user=get_user_model().objects.filter(username='demo_admin',is_active=True).first()
    if not user or Membership.objects.filter(room__archived_at__isnull=True,active=True,role='admin',user=user).count()!=3:
        raise PermissionDenied('시연 구성원을 확인할 수 없습니다.')
    login(request,user,backend='django.contrib.auth.backends.ModelBackend')
    response=redirect('home')
    response['Cache-Control']='no-store'
    return response
