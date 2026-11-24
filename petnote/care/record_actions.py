"""Atomic, permission-scoped one-tap actions with retry receipts and honest data meanings."""
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from . import services
from .forms import RecordForm
from .models import ActionReceipt, Activity, RecordPreset, RoutineRun
from .modules import MODULES, SELECTABLE_MODULES
from .presets import input_for, snapshot

TIMERS = {'walk','play','training'}
QUICK_WITHOUT_PRESET = {'water','toilet','shedding'}


def hash_request(request):
    return hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def response_for(member,record,*,created,changed=True,message=None):
    return {'ok':True,'id':str(record.pk),'version':record.version,'module':record.module,
            'created':created,'changed':changed,'status':record.status,
            'message':message or (MODULES[record.module].name+' 시작을 기록했어요.' if record.status=='active' else MODULES[record.module].name+' 기록을 저장했어요.'),
            'url':reverse('record_detail',args=[member.room_id,record.pk]),
            'undo_url':reverse('quick_undo',args=[member.room_id,record.pk]),'undo_allowed':created,
            'started_at':record.occurred_at.isoformat()}


def gps_metadata(data):
    """Only bounded quality metadata, not a covert coordinate history or a healthy-state inference."""
    if not isinstance(data,dict):raise ValidationError('위치 측정 상태를 확인해 주세요.')
    count=data.get('samples',0);gaps=data.get('gaps',0)
    if type(count) is not int or type(gaps) is not int or not 0<=count<=1000000 or not 0<=gaps<=100000:
        raise ValidationError('위치 측정 건수나 중단 횟수가 올바르지 않아요.')
    return {'source':'browser-foreground','samples':count,'gaps':gaps,'screen_hidden':bool(data.get('screen_hidden')),
            'complete':bool(data.get('complete')) and gaps==0 and not data.get('screen_hidden') and count>=2,
            'precision':'GPS 기반 추정 거리; 브라우저가 숨겨지면 누락될 수 있음'}


@transaction.atomic
def perform(member,request):
    member=services.fresh_member(member)
    if not isinstance(request,dict) or set(request)-{'token','operation','module','preset_id','preset_version','activity_id','version','fields','routine_item','date','gps'}:
        raise ValidationError('실행 요청 형식을 확인해 주세요.')
    try:token=uuid.UUID(str(request.get('token','')))
    except (ValueError,TypeError,AttributeError):raise ValidationError('요청 식별값을 확인해 주세요.')
    operation=request.get('operation')
    if operation not in ('quick','start','finish','routine_done','routine_skip'):
        raise ValidationError('지원하지 않는 기록 동작이에요.')
    fields=request.get('fields',{})
    if not isinstance(fields,dict) or any(not isinstance(k,str) or k.startswith('_') or isinstance(v,(dict,list)) for k,v in fields.items()):
        raise ValidationError('기록 입력값을 확인해 주세요.')
    fingerprint=hash_request(request)
    old=ActionReceipt.objects.filter(room=member.room,token=token).select_related('activity').first()
    if old:
        if old.actor_id!=member.pk or not hmac.compare_digest(old.request_hash,fingerprint):
            raise services.Conflict('같은 요청 번호로 다른 내용이 전달됐어요. 현재 기록을 확인한 뒤 새로 기록해 주세요.')
        if old.activity_id:
            services.require_module(member,old.activity.module)
            if old.activity.voided and operation=='routine_done':
                raise services.Conflict('앞서 저장한 완료 기록이 취소됐어요. 일정을 다시 확인해 주세요.')
        elif old.result.get('module'):services.require_module(member,old.result['module'])
        return {**old.result,'replayed':True,'changed':False,'message':'이미 처리한 요청이에요. 중복으로 저장하지 않았어요.'}
    module=request.get('module')
    record=None;created=False;preset=None;slot=None;item=None;snap=None;at=timezone.now()
    if operation=='finish':
        try:record=Activity.objects.select_for_update().get(room=member.room,pk=request.get('activity_id'))
        except (Activity.DoesNotExist,ValueError,ValidationError):raise ValidationError('종료할 활동을 찾지 못했어요.')
        module=record.module
        services.require_module(member,module)
        if module not in TIMERS or record.voided or not services.can_edit_record(member,record):
            raise PermissionDenied('이 활동을 종료할 수 없어요.')
        if record.status!='active':raise services.Conflict('이미 종료되었거나 상태가 바뀐 활동이에요. 최신 기록을 확인해 주세요.')
        if type(request.get('version')) is not int or record.version!=request['version']:
            raise services.Conflict('활동이 이미 변경됐어요. 새로고침한 뒤 다시 확인해 주세요.')
    else:
        services.require_module(member,module,adding=True)
        if module not in SELECTABLE_MODULES and operation not in ('routine_done','routine_skip'):
            raise ValidationError('이전 기능은 통합된 새 기능에서 기록해 주세요.')
    if operation in ('routine_done','routine_skip'):
        try:
            day=date.fromisoformat(str(request.get('date','')))
            item=services.require_routine_slot(member,request.get('routine_item'),day,module)
        except (ValueError,TypeError):raise ValidationError('일정 날짜와 항목을 확인해 주세요.')
        snap=services.effective_routine(item.routine,day)
        slot,_=RoutineRun.objects.get_or_create(item=item,date=day)
        if slot.activity_id and not slot.activity.voided:
            result=response_for(member,slot.activity,created=False,changed=False,message='이 일정에는 이미 기록이 연결돼 있어요.')
            ActionReceipt.objects.create(room=member.room,actor=member,token=token,request_hash=fingerprint,operation=operation,activity=slot.activity,result=result)
            return result
        if operation=='routine_skip':
            changed=slot.skipped_at is None
            if changed:
                slot.skipped_at=at;slot.skip_note=str(fields.get('note',''))[:300];slot.save(update_fields=['skipped_at','skip_note'])
                services.bump(member.room,member,'routine.skipped',{'item_id':str(item.pk),'date':day.isoformat()})
            result={'ok':True,'module':module,'created':False,'changed':changed,'message':'이번 일정은 건너뛴 것으로 남겼어요. 완료 기록으로 계산하지 않아요.'}
            ActionReceipt.objects.create(room=member.room,actor=member,token=token,request_hash=fingerprint,operation=operation,result=result)
            return result
        if day!=services.local_date(member.room):
            from .views import datetime_for_day
            from datetime import time
            at=datetime_for_day(member.room,day,time.fromisoformat(snap['at']))
    preset_id=request.get('preset_id')
    if item and not preset_id:
        entry=next(x for x in snap['items'] if x['id']==str(item.pk))
        preset_id=entry.get('preset_id')
    if preset_id:
        try:preset=RecordPreset.objects.select_related('product').get(pk=preset_id,room=member.room,module=module,active=True)
        except (RecordPreset.DoesNotExist,ValueError,ValidationError):raise ValidationError('이 공간의 사용 중인 저장 설정을 골라 주세요.')
        expected=request.get('preset_version')
        if expected is not None and (type(expected) is not int or expected!=preset.version):
            raise services.Conflict('저장 설정이 바뀌었어요. 적용할 내용을 다시 확인해 주세요.')
    elif operation in ('quick','start'):
        preset=RecordPreset.objects.filter(room=member.room,module=module,active=True,is_default=True).select_related('product').first()
    if operation=='start':
        if module not in TIMERS:raise ValidationError('시간을 측정하는 활동만 시작할 수 있어요.')
        ongoing=Activity.objects.filter(room=member.room,module=module,author=member,status='active',voided=False).order_by('occurred_at').first()
        if ongoing:
            result=response_for(member,ongoing,created=False,changed=False,message='이미 시작한 활동을 이어 보여드려요.')
            ActionReceipt.objects.create(room=member.room,actor=member,token=token,request_hash=fingerprint,operation=operation,activity=ongoing,result=result)
            return result
    data=input_for(member,module,preset,at=at)
    editable={f.key for f in MODULES[module].fields}|{'note','product','routine_title','occurred_at','performer','used_quantity','used_unit'}
    if set(fields)-editable:raise ValidationError('허용되지 않은 기록 입력 항목이 있어요.')
    data.update(fields)
    data['client_key']=str(token)
    if item:
        data.update(routine_item=str(item.pk),routine_date=day.isoformat())
        if module in ('medication','sleep'):
            data.setdefault('routine_title',snap['name'])
            if not data['routine_title']:data['routine_title']=snap['name']
            data['note']=data.get('note') or snap.get('note','')
    if operation=='start':data['status']='active'
    elif operation=='finish':
        original=record.payload
        for spec in MODULES[module].fields:data[spec.key]=original.get(spec.key)
        data.update(fields,status='done',version=record.version,client_key=str(record.client_key),
                    occurred_at=record.occurred_at.isoformat(),ended_at=at.isoformat(),note=fields.get('note',record.note))
    if operation=='quick' and not preset and module not in QUICK_WITHOUT_PRESET and not fields:
        raise ValidationError('처음에는 저장 설정을 만들거나 필요한 내용을 입력해 주세요.')
    form=RecordForm(data,room=member.room,module=module,member=member,instance=record,simple=True)
    if not form.is_valid():
        from .routine_completion import NeedsInput, errors_with_labels
        if item and operation=='routine_done':
            raise NeedsInput(member,item,day,token,form)
        messages=[f"{row['label']}: {' '.join(row['messages'])}" for row in errors_with_labels(form)]
        raise ValidationError(messages)
    if preset:form.server_metadata['_preset']=snapshot(preset)
    if item:form.server_metadata['_schedule']={'item_id':str(item.pk),'date':day.isoformat(),'name':snap['name']}
    if request.get('gps') is not None:
        if module!='walk' or operation!='finish':raise ValidationError('위치 측정 정보는 산책 종료에만 넣을 수 있어요.')
        form.server_metadata['_gps']=gps_metadata(request['gps'])
    record,created=services.save_activity(member,module,form,record.pk if record else None)
    result=response_for(member,record,created=created and operation!='finish')
    if item:
        from .routine_completion import safe_title
        result.update(routine_key=str(item.pk)+':'+day.isoformat(),routine_title=safe_title(member,snap))
        result['message']=safe_title(member,snap)+' 완료 기록을 저장했어요.'
    ActionReceipt.objects.create(room=member.room,actor=member,token=token,request_hash=fingerprint,operation=operation,activity=record,result=result)
    return result
