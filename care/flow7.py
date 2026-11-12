"""Purpose-specific views for phase7; all mutations reuse room-scoped services."""
from collections import defaultdict
from datetime import timedelta
import json
import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import actions7, analytics7, cadences, organization, schedule7, services, ui_forms
from .dashboard import today_tiles
from .forms import RecordForm, StatsFilterForm
from .models import Activity, Membership, RecordPreset
from .modules import MODULES, SELECTABLE_MODULES, RETIRED_MODULES, record_summary
from .presets import PRESET_MODULES, PresetForm, save_preset
from .stats import bounds, get_statistics
from .views import record_card


@require_POST
def action(request,room_id):
    room,member=services.get_room(request,room_id)
    try:
        data=json.loads(request.body)
        result=actions7.perform(member,data)
        return JsonResponse(result)
    except (json.JSONDecodeError,UnicodeDecodeError):
        return JsonResponse({'ok':False,'message':'요청 내용을 읽을 수 없어요. 입력을 확인해 주세요.'},status=400)
    except services.Conflict as exc:
        return JsonResponse({'ok':False,'message':' '.join(exc.messages)},status=409)
    except ValidationError as exc:
        return JsonResponse({'ok':False,'message':' '.join(exc.messages)},status=422)
    except PermissionDenied:
        return JsonResponse({'ok':False,'message':'이 기록을 처리할 권한이 없어요.'},status=403)
    except OperationalError:
        return JsonResponse({'ok':False,'message':'지금은 저장할 수 없어요. 같은 요청으로 다시 시도해 주세요. 중복으로 저장하지 않아요.'},status=503)


def board_data(member):
    room=member.room;allowed=set(services.visible_modules(member));today=services.local_date(room);now=timezone.now()
    settings=list(room.module_settings.all());canonical_enabled={s.key for s in settings if s.enabled and s.key in allowed and s.key in SELECTABLE_MODULES}
    presets=list(RecordPreset.objects.filter(room=room,active=True,module__in=allowed).select_related('product'))
    grouped=defaultdict(list)
    for p in presets:grouped[p.module].append(p)
    records=list(Activity.objects.filter(room=room,module__in=allowed,voided=False,occurred_at__lte=now).select_related('author','performer').order_by('-occurred_at','-created_at','-id'))
    basic=[{'spec':MODULES[s.key],'enabled':s.key in canonical_enabled,'pinned':s.pinned,'category':organization.CATEGORY_OF[s.key]}
           for s in settings if s.key in allowed and s.key in SELECTABLE_MODULES]
    cards=today_tiles(member,basic,records,today)
    for card in cards:
        key=card['spec'].key;card['presets']=grouped[key];card['default_preset']=next((p for p in grouped[key] if p.is_default),None)
        same=[a for a in records if schedule7.logical_module(a.module)==key and a.status=='done']
        if key in ('hygiene','observation'):
            current=[a for a in same if a.occurred_at.astimezone(timezone.get_current_timezone()).date()==today]
            if current:card['tile'].update(value=str(len(current)),unit='건',caption='오늘 남긴 기록 · 이전 통합 유형 포함',empty=False)
        card['latest']={'text':record_summary(same[0].module,same[0].payload),'at':same[0].occurred_at} if same else None
        if key in ('toilet','shedding','hygiene','cleaning') and card['tile']['empty']:
            card['tile'].update(value='0',unit='건',caption='오늘 남긴 관찰·완료 기록 · 실제 발생 여부와 구분',empty=False)
        card['is_timer']=key in actions7.TIMERS;card['has_presets']=key in PRESET_MODULES
        card['instant']=bool(card['default_preset']) or key in actions7.QUICK_WITHOUT_PRESET or card['is_timer']
        card['action_label']='시작' if card['is_timer'] else '물 교체' if key=='water' and not card['default_preset'] else '관찰 +1' if key in ('toilet','shedding') else '기록'
        card['quick_url']=reverse('entry7',args=[room.pk,key]);card['preset_url']=reverse('presets7',args=[room.pk,key]) if card['has_presets'] else None
    return cards,records


@require_GET
def modules(request,room_id):
    room,member=services.get_room(request,room_id);cards,records=board_data(member)
    enabled=[x for x in cards if x['enabled']]
    sections=[{'key':key,'name':label,'rows':[x for x in enabled if x['category']==key]} for key,(label,_) in organization.CATEGORIES.items()]
    allowed=set(services.visible_modules(member))
    history=[{'spec':MODULES[s.key]} for s in room.module_settings.all() if s.key in allowed and (not s.enabled or s.key in RETIRED_MODULES)]
    return render(request,'care7/modules.html',{'title':'돌봄 기능','active_tab':'modules','sections':sections,'enabled_modules':enabled,
        'inactive_modules':history,'ongoing':[record_card(a,member) for a in records if a.status=='active'],'live_page':True,**organization.browse_context(member)})


@require_GET
def today(request,room_id):
    room,member=services.get_room(request,room_id);day=services.local_date(room);cards,records=board_data(member)
    chosen=[c for c in cards if c['enabled'] and c['pinned']]
    ongoing=[record_card(a,member) for a in records if a.status=='active']
    schedule=schedule7.opportunities(member,day,day)
    return render(request,'care7/today.html',{'title':room.name+'의 오늘','active_tab':'today','day':day,'cards':chosen,
        'ongoing':ongoing,'schedule_entries':schedule,'notices':schedule7.notices(member),
        'recent':[record_card(a,member) for a in records[:6]],'today_count':sum(a.status=='done' and services.local_date(room)==a.occurred_at.astimezone(timezone.get_current_timezone()).date() for a in records),
        'routine_total':sum(not r['paused'] for r in schedule),'routine_done':sum(r['done'] for r in schedule),'routine_progress':round(sum(r['done'] for r in schedule)/sum(not r['paused'] for r in schedule)*100,2) if any(not r['paused'] for r in schedule) else 0,'live_page':True})


@require_GET
def overview(request,room_id):
    room,member=services.get_room(request,room_id)
    report=analytics7.build_report(member,request.GET.get('days',7))
    return render(request,'care7/overview.html',{'title':'최근 통계','active_tab':'overview','report':report,'live_page':True})


@require_GET
def module_page(request,room_id,module):
    room,member=services.get_room(request,room_id);services.require_module(member,module)
    cards,records=board_data(member);canonical=schedule7.logical_module(module)
    card=next((c for c in cards if c['spec'].key==canonical),None)
    report=analytics7.build_report(member,request.GET.get('days',7))
    current=next((c for c in report['module_cards'] if c['key']==canonical),None)
    from django.core.paginator import Paginator
    query=Activity.objects.filter(room=room,module__in=[k for k in services.visible_modules(member) if schedule7.logical_module(k)==canonical]).select_related('author','performer').order_by('-occurred_at','-created_at','-id')
    if request.GET.get('include_cancelled')!='1':query=query.filter(voided=False)
    page=Paginator(query,20).get_page(request.GET.get('page'))
    page.object_list=[record_card(a,member) for a in page.object_list]
    matching=page.object_list
    end=services.local_date(room);stats=get_statistics(room,module,end-timedelta(days=6),end)
    return render(request,'care7/module.html',{'title':MODULES[module].name,'spec':MODULES[module],'active_tab':'modules',
        'card':card,'comparison':current,'report':report,'ongoing':[record_card(a,member) for a in records if a.status=='active' and schedule7.logical_module(a.module)==canonical],'records':matching,'page_obj':page,'include_cancelled':request.GET.get('include_cancelled')=='1','stats':stats,'retired_module':module in RETIRED_MODULES,
        'canonical_name':MODULES[canonical].name,'canonical_url':reverse('module',args=[room.pk,canonical]),'live_page':True})


@require_GET
def statistics(request,room_id,module):
    room,member=services.get_room(request,room_id);services.require_module(member,module)
    today_date=services.local_date(room);end=today_date-timedelta(days=1)
    initial={'start':end-timedelta(days=6),'end':end}
    submitted={'start':request.GET.get('start'),'end':request.GET.get('end')} if 'start' in request.GET or 'end' in request.GET else None
    form=StatsFilterForm(submitted,initial=initial);chosen=initial if submitted is None else form.cleaned_data if form.is_valid() else {}
    stats=get_statistics(room,module,chosen['start'],chosen['end']) if chosen else None
    days=(chosen['end']-chosen['start']).days+1 if chosen else 7
    report=analytics7.build_report(member,days,end=chosen.get('end')) if chosen and days in (7,30,90) and chosen['end']<today_date else None
    card=next((c for c in report['module_cards'] if c['key']==schedule7.logical_module(module)),None) if report else None
    periods=[{'label':f'최근 {n}일','url':'?start='+(end-timedelta(days=n-1)).isoformat()+'&end='+end.isoformat(),
              'selected':bool(chosen and chosen['start']==end-timedelta(days=n-1) and chosen['end']==end)} for n in (7,30,90)]
    return render(request,'care7/statistics.html',{'title':MODULES[module].name+' 통계','active_tab':'overview','spec':MODULES[module],
        'stats':stats,'form':form,'periods':periods,'report':report,'comparison':card})


@require_http_methods(['GET','POST'])
def entry(request,room_id,module):
    room,member=services.get_room(request,room_id)
    try:services.require_module(member,module,adding=True)
    except ValidationError as exc:
        return render(request,'care/error.html',{'title':'사용하지 않는 기능이에요','detail':' '.join(exc.messages)},status=409)
    if module in RETIRED_MODULES:return redirect('entry7',room_id=room.pk,module=RETIRED_MODULES[module])
    initial={'client_key':str(uuid.uuid4()),'occurred_at':timezone.localtime(),'status':'done'}
    preset=None
    if request.GET.get('preset'):
        preset=get_object_or_404(RecordPreset,pk=request.GET['preset'],room=room,module=module,active=True)
        initial.update(preset.data)
        if preset.product_id:initial['product']=str(preset.product_id)
    form=RecordForm(request.POST or None,room=room,module=module,member=member,simple=True,initial=initial)
    if module in actions7.TIMERS:
        form.fields['status'].choices=[('done','완료'),('active','진행 중')]
    status=200
    if request.method=='POST' and form.is_valid():
        try:
            if preset:
                from .presets import snapshot
                form.server_metadata['_preset']=snapshot(preset)
            record,created=services.save_activity(member,module,form)
            if ui_forms.in_dialog(request):
                return ui_forms.saved(request,'기록을 저장했어요.' if created else '이미 저장된 기록이에요.',reverse('record_detail',args=[room.pk,record.pk]),id=str(record.pk),created=created)
            messages.success(request,'기록을 저장했어요.' if created else '이미 저장된 기록이에요. 중복으로 추가하지 않았어요.')
            return redirect('record_detail',room_id=room.pk,activity_id=record.pk)
        except ValidationError as exc:form.add_error(None,' '.join(exc.messages));status=409 if isinstance(exc,services.Conflict) else 422
        except OperationalError:form.add_error(None,'지금은 저장할 수 없어요. 입력을 유지했으니 같은 화면에서 다시 시도해 주세요.');status=503
    return ui_forms.render_input(request,'care7/entry.html',{'title':MODULES[module].name+' 기록','active_tab':'modules','spec':MODULES[module],
        'form':form,'preset':preset,'is_timer':module in actions7.TIMERS,'back_url':reverse('modules',args=[room.pk])},status=status)


@require_GET
def presets(request,room_id,module):
    room,member=services.get_room(request,room_id);services.require_module(member,module)
    if module not in PRESET_MODULES:raise Http404()
    return render(request,'care7/presets.html',{'title':MODULES[module].name+' 저장 설정','spec':MODULES[module],'active_tab':'modules',
        'presets':RecordPreset.objects.filter(room=room,module=module).select_related('product')})


@require_http_methods(['GET','POST'])
def preset_edit(request,room_id,module,preset_id=None):
    room,member=services.get_room(request,room_id);services.require_manager(member);services.require_module(member,module)
    if module not in PRESET_MODULES:raise Http404()
    instance=get_object_or_404(RecordPreset,pk=preset_id,room=room,module=module) if preset_id else None
    form=PresetForm(request.POST or None,member=member,module=module,instance=instance)
    form.fields['request_token'].required=instance is None
    status=200
    if request.method=='POST' and form.is_valid():
        try:
            preset=save_preset(member,module,form,instance)
            if ui_forms.in_dialog(request):
                return ui_forms.saved(request,'저장 설정을 반영했어요.',reverse('presets7',args=[room.pk,module]),id=str(preset.pk))
            messages.success(request,'저장 설정을 반영했어요. 이전 기록의 당시 설정은 바뀌지 않아요.')
            return redirect('presets7',room_id=room.pk,module=module)
        except ValidationError as exc:form.add_error(None,' '.join(exc.messages));status=409 if isinstance(exc,services.Conflict) else 422
    return ui_forms.render_input(request,'care7/entry.html',{'title':MODULES[module].name+' 저장 설정 '+('수정' if instance else '등록'),'form':form,
        'spec':MODULES[module],'active_tab':'modules','back_url':reverse('presets7',args=[room.pk,module]),'submit_label':'설정 저장'},status=status)


@require_GET
def routines(request,room_id):
    room,member=services.get_room(request,room_id);day=services.local_date(room)
    schedule=schedule7.opportunities(member,day,day)
    relative=cadences.schedule(member,include_paused=True)
    for row in relative:row['timing']=schedule7.relative_date(row['next_due'],day)
    definitions=[]
    allowed=set(services.visible_modules(member))
    for r in room.routines.prefetch_related('items').all():
        keys=[i.module for i in r.items.all() if i.active]
        visible=[k for k in keys if k in allowed]
        if not visible:continue
        definitions.append({'routine':r,'name':r.name if set(keys)<=allowed else '공유된 루틴 활동',
                            'modules':[MODULES[k].name for k in visible],'editable':member.can_manage,'note':r.note if set(keys)<=allowed else ''})
    return render(request,'care7/schedules.html',{'title':'루틴·반복 알림','active_tab':'routines','schedule_entries':schedule,
        'definitions':definitions,'cadences':relative,'day':day})


@require_POST
def dismiss_notice(request,room_id):
    room,member=services.get_room(request,room_id)
    try:
        data=json.loads(request.body);changed=schedule7.dismiss(member,str(data.get('key','')),str(data.get('stage','')))
        return JsonResponse({'ok':True,'changed':changed,'message':'이 안내만 닫았어요. 일정 자체는 관리 목록에 그대로 있어요.'})
    except (json.JSONDecodeError,AttributeError):return JsonResponse({'ok':False,'message':'안내 요청을 확인해 주세요.'},status=400)
    except ValidationError as exc:return JsonResponse({'ok':False,'message':' '.join(exc.messages)},status=409)


@require_http_methods(['GET','POST'])
def member_panel(request,room_id,member_id):
    room,actor=services.get_room(request,room_id);target=get_object_or_404(Membership,pk=member_id,room=room,active=True)
    status=200;problem='';success=''
    if request.method=='POST':
        action_=request.POST.get('action','')
        try:
            if action_=='profile':
                if target.pk!=actor.pk:raise PermissionDenied()
                name=request.POST.get('name','').strip()
                if not name or len(name)>40:raise ValidationError('표시 이름을 1~40자로 입력해 주세요.')
                with transaction.atomic():
                    fresh=services.fresh_member(actor)
                    enabled=request.POST.get('notices')=='on'
                    if fresh.display_name!=name or fresh.notices_enabled!=enabled:
                        fresh.display_name=name;fresh.notices_enabled=enabled;fresh.save(update_fields=['display_name','notices_enabled'])
                        services.bump(room,fresh,'member.preferences.updated')
                target.refresh_from_db();success='내 이름과 오늘 화면의 안내 표시를 저장했어요.'
            elif action_ in ('promote','demote','remove','role'):
                if action_=='role':
                    services.require_owner(actor)
                    role=request.POST.get('role')
                    if role not in ('admin','guest'):raise ValidationError('변경할 역할을 선택해 주세요.')
                    action_='promote' if role=='admin' else 'demote'
                services.change_member(actor,target.pk,action_,revoke_links=request.POST.get('revoke_links')=='1')
                success='구성원 설정을 반영했어요.';target.refresh_from_db()
            elif action_=='leave':
                if target.pk!=actor.pk:raise PermissionDenied()
                services.leave_room(actor)
                return redirect('home')
            else:raise ValidationError('지원하지 않는 구성원 설정이에요.')
        except ValidationError as exc:problem=' '.join(exc.messages);status=422
    roles={'owner':'기능·일정 설정, 기록 관리, 초대와 구성원 권한을 관리해요.',
           'admin':'기능·일정과 허용된 기록을 관리해요. 권한 변경과 내보내기는 방장만 할 수 있어요.',
           'guest':'허용된 돌봄을 기록하고 내 기록을 수정할 수 있어요. 민감한 기록은 방 설정에 따라 제한돼요.'}
    template='care7/_member_panel.html' if request.headers.get('X-Taily-Panel')=='1' else 'care7/member.html'
    return render(request,template,{'title':target.display_name+' · 구성원','active_tab':'members','target_member':target,
        'is_self':target.pk==actor.pk,'role_description':roles[target.role],'problem':problem,'success':success,
        'profile_name':request.POST.get('name',target.display_name) if problem else target.display_name,
        'profile_notices':request.POST.get('notices')=='on' if problem and request.POST.get('action')=='profile' else target.notices_enabled,
        'record_url':reverse('records',args=[room.pk])+'?author='+str(target.pk)},status=status)


@require_GET
def evidence(request,room_id):
    from datetime import date
    room,member=services.get_room(request,room_id)
    try:
        until=date.fromisoformat(request.GET['end']) if request.GET.get('end') else None
        if until and (until.year<1970 or until>services.local_date(room)):
            raise ValueError()
    except ValueError:
        return render(request,'care/error.html',{'title':'날짜 확인','detail':'올바른 비교 기준일을 선택해 주세요.'},status=400)
    report=analytics7.build_report(member,request.GET.get('days',7),end=until)
    selected=next((m for m in report['measurements'] if m['key']==request.GET.get('metric')),None)
    if selected is None:raise Http404('현재 볼 수 있는 비교 지표가 아니에요.')
    side=request.GET.get('period','all')
    if side not in ('all','before','recent'):raise Http404()
    ids=list(dict.fromkeys(i for k in ('before','recent') if side=='all' or side==k for i in selected[k]['ids']))
    rows=Activity.objects.filter(room=room,pk__in=ids,module__in=services.visible_modules(member),voided=False).select_related('author','performer').order_by('-occurred_at')
    return render(request,'care7/evidence.html',{'title':selected['name']+' · 비교 근거','active_tab':'overview','metric':selected,'report':report,
        'side':side,'records':[record_card(a,member) for a in rows],'evidence_count':len(ids)})
