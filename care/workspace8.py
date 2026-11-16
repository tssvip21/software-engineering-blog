"""Connected workspace panels, separate settings and a unified supplies entry point."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from . import schedule7, services, supplies, ui_forms
from .settings8 import PetProfileForm, SpacePreferencesForm, save_pet, save_preferences


def schedule_context(member,entries=None):
    day=services.local_date(member.room)
    entries=schedule7.opportunities(member,day,day) if entries is None else entries
    rows=sorted(entries,key=lambda x:(x['done'] or x['skipped'],x['at'],x['name']))
    active=[x for x in rows if not x['paused']]
    return {'schedule_entries':rows,'schedule_pending':[x for x in rows if not x['done']],
            'schedule_completed':[x for x in rows if x['done']],
            'notices':schedule7.notices(member),'schedule_day':day,
            'schedule_done':sum(x['done'] for x in active),'schedule_total':len(active),
            'schedule_remaining':sum(not x['done'] and not x['skipped'] for x in active)}


def settings_response(request,form,context,status=200):
    # Dialog and standalone URLs use the same validation and save services.
    return ui_forms.render_input(request,'care8/settings_page.html',{'form':form,'form_action':request.get_full_path(),**context},status=status)


@require_http_methods(['GET','POST'])
def pet_profile(request,room_id):
    room,member=services.get_room(request,room_id)
    if request.method=='POST' and not member.can_manage:raise PermissionDenied('반려동물 정보는 방장과 관리자만 바꿀 수 있어요.')
    form=PetProfileForm(request.POST or None,instance=room,member=member);status=200
    if request.method=='POST' and form.is_valid():
        try:
            result=save_pet(member,form)
            message='반려동물 정보를 저장했어요.'
            if ui_forms.in_dialog(request):return ui_forms.saved(request,message,reverse('room_settings',args=[room.pk]),refresh_shell=True,**result)
            messages.success(request,message);return redirect('room_settings',room_id=room.pk)
        except ValidationError as exc:form.add_error(None,' '.join(exc.messages));status=409 if isinstance(exc,services.Conflict) else 422
        except OperationalError:form.add_error(None,'지금은 저장할 수 없어요. 입력을 유지했으니 다시 시도해 주세요.');status=503
    return settings_response(request,form,{'title':'반려동물 상태','active_tab':'pet','pet_form':True,'read_only':not member.can_manage,
        'submit_label':'상태 저장','back_url':reverse('today',args=[room.pk])},status)


@require_http_methods(['GET','POST'])
def preferences(request,room_id):
    room,member=services.get_room(request,room_id)
    form=SpacePreferencesForm(request.POST or None,member=member);status=200
    if request.method=='POST' and form.is_valid():
        try:
            result=save_preferences(member,form)
            message='설정을 저장했어요.'
            if ui_forms.in_dialog(request):return ui_forms.saved(request,message,reverse('preferences8',args=[room.pk]),refresh_shell=True,**result)
            messages.success(request,message);return redirect('preferences8',room_id=room.pk)
        except ValidationError as exc:form.add_error(None,' '.join(exc.messages));status=409 if isinstance(exc,services.Conflict) else 422
        except OperationalError:form.add_error(None,'지금은 저장할 수 없어요. 입력을 유지했으니 다시 시도해 주세요.');status=503
    return settings_response(request,form,{'title':'일반 설정','active_tab':'preferences','preferences_form':True,
        'submit_label':'설정 저장','back_url':reverse('today',args=[room.pk])},status)


@require_GET
def supply_list(request,room_id):
    room,member=services.get_room(request,room_id)
    tab='stock' if request.GET.get('tab')=='stock' else 'registered'
    query=request.GET.get('q','').strip()[:100]
    items=supplies.visible_products(member).order_by('-active','category','name')
    if query:items=items.filter(name__icontains=query)
    allowed=set(services.visible_modules(member));enabled=set(room.module_settings.filter(enabled=True).values_list('key',flat=True))&allowed
    inventory=supplies.stock_view(member) if 'inventory' in allowed else {'items':[],'issues':[]}
    stock_map={x['key']:x for x in inventory['items']}
    rows=[]
    for item in items:
        module={'meal':'meal','treat':'treat','both':'meal','supp':'medication','medicine':'medication','hygiene':'hygiene','supply':'cleaning'}[item.category]
        stock=stock_map.get((supplies.inventory_ledger.normalized_item(item.stock_item),item.stock_unit)) if item.stock_item else None
        from .packaging import stock_context
        rows.append({'item':item,'stock':stock,'stock_context':stock_context(item,stock),'record_module':module,
            'record_url':reverse('entry7',args=[room.pk,module])+'?product='+str(item.pk) if module in enabled and item.active else None,
            'stock_url':reverse('entry7',args=[room.pk,'inventory'])+'?product='+str(item.pk) if item.stock_item and 'inventory' in enabled else None,
            'edit_url':reverse('product_edit',args=[room.pk,item.pk]) if member.can_manage else None})
    return render(request,'care8/supplies.html',{'title':'물품 관리','active_tab':'supplies','tab':tab,'rows':rows,'query':query,
        'inventory':inventory,'can_record_stock':'inventory' in enabled,'supplies_count':supplies.visible_products(member).count(),
        'categories':supplies.CATEGORIES})


@require_http_methods(['GET','POST'])
def supply_edit(request,room_id,product_id=None):
    room,member=services.get_room(request,room_id);services.require_manager(member)
    instance=get_object_or_404(supplies.visible_products(member),pk=product_id) if product_id else None
    initial={}
    if not instance:
        kind=request.GET.get('category','meal')
        if kind in dict(supplies.CATEGORIES):initial['category']=kind
    form=supplies.SupplyForm(request.POST or None,member=member,instance=instance,initial=initial);status=200
    if request.method=='POST' and form.is_valid():
        try:
            result=supplies.save_supply(member,form,instance)
            message='물품 정보를 저장했어요. 이전 사용 기록은 그대로예요.'
            if ui_forms.in_dialog(request):return ui_forms.saved(request,message,reverse('supplies8',args=[room.pk]),**result)
            messages.success(request,message);return redirect('supplies8',room_id=room.pk)
        except ValidationError as exc:form.add_error(None,' '.join(exc.messages));status=409 if isinstance(exc,services.Conflict) else 422
        except OperationalError:form.add_error(None,'지금은 저장할 수 없어요. 같은 입력창에서 다시 시도해 주세요.');status=503
    return settings_response(request,form,{'title':'물품 수정' if instance else '물품 등록','active_tab':'supplies','supply_form':True,
        'submit_label':'물품 저장','back_url':reverse('supplies8',args=[room.pk])},status)


@require_GET
def space_management(request,room_id):
    room,member=services.get_room(request,room_id)
    services.require_owner(member)
    return render(request,'care8/space_management.html',{'title':'공간 관리','active_tab':'preferences'})
