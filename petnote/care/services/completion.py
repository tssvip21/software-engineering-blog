"""Validate routine input and save completion receipts atomically."""

import uuid
from datetime import time
from urllib.parse import urlencode

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from care import services
from care.forms import RecordForm
from care.models import ActionReceipt, RecordPreset, RoutineItem, RoutineRun
from care.modules import MODULES
from care.services.idempotency import form_fingerprint
from care.services.presets import input_for, snapshot


def safe_title(member,snap):
    allowed=set(services.visible_modules(member))
    return '공유된 루틴 활동' if any(i['module'] not in allowed for i in snap['items']) else snap['name']


def errors_with_labels(form):
    return [{'name':key,'label':str(form.fields[key].label) if key in form.fields else '기록 내용',
             'messages':[str(message) for message in messages]} for key,messages in form.errors.items()]


class NeedsInput(ValidationError):
    def __init__(self,member,item,day,token,form):
        self.fields=errors_with_labels(form)
        self.form_url=reverse('routine_complete',args=[member.room_id,item.pk])+'?'+urlencode({'date':day.isoformat(),'token':str(token)})
        names=' · '.join(x['label'] for x in self.fields)
        super().__init__(names+'을(를) 확인하면 완료할 수 있어요.')


def request_context(member,item_id,day):
    item=get_object_or_404(RoutineItem.objects.select_related('routine'),pk=item_id,routine__room=member.room)
    services.require_module(member,item.module)
    services.require_routine_slot(member,item.pk,day,item.module)
    snap=services.effective_routine(item.routine,day)
    entry=next(x for x in snap['items'] if x['id']==str(item.pk))
    preset=None
    if entry.get('preset_id'):
        preset=RecordPreset.objects.filter(pk=entry['preset_id'],room=member.room,module=item.module,active=True).select_related('product').first()
    return item,snap,preset


def completion_form(member,item,snap,preset,day,token,data=None):
    at=timezone.now()
    if day!=services.local_date(member.room):
        from care.services.common import datetime_for_day
        at=datetime_for_day(member.room,day,time.fromisoformat(snap['at']))
    initial=input_for(member,item.module,preset,at=at)
    # A datetime-local control rejects ISO strings containing an offset. Keep the aware
    # value until Django formats it for the room timezone; the stored instant is unchanged.
    initial['occurred_at']=timezone.localtime(at)
    initial.update(client_key=str(token),routine_item=str(item.pk),routine_date=day.isoformat(),
                   routine_version=item.routine.version,preset_version=preset.version if preset else 0,
                   completion_preset=str(preset.pk) if preset else '')
    if item.module in ('medication','sleep'):
        initial['routine_title']=initial.get('routine_title') or safe_title(member,snap)
        initial['note']=initial.get('note') or (snap.get('note','') if safe_title(member,snap)==snap['name'] else '')
    form=RecordForm(data,room=member.room,module=item.module,member=member,simple=True,initial=initial)
    form.fields['occurred_at'].widget.format='%Y-%m-%dT%H:%M:%S'
    form.fields['occurred_at'].widget.attrs['step']='1'
    form.fields['routine_version']=forms.IntegerField(widget=forms.HiddenInput,initial=item.routine.version)
    form.fields['preset_version']=forms.IntegerField(widget=forms.HiddenInput,initial=preset.version if preset else 0)
    form.fields['completion_preset']=forms.CharField(widget=forms.HiddenInput,required=False,initial=str(preset.pk) if preset else '')
    form.fields['status'].choices=[('done','완료')]
    form.fields['status'].widget=forms.HiddenInput()
    form.fields['performer'].widget=forms.HiddenInput()
    # Metadata follows the actual task; unknown measurements stay optional exactly as before.
    order=[f.key for f in MODULES[item.module].fields]
    if 'product' in form.fields:order.insert(0,'product')
    form.order_fields(['routine_title']+order+['used_quantity','used_unit','note','occurred_at','ended_at'])
    return form


@transaction.atomic
def save_completion(member,item_id,day,form):
    member=services.fresh_member(member)
    item=get_object_or_404(RoutineItem.objects.select_related('routine'),pk=item_id,routine__room=member.room)
    services.require_module(member,item.module)
    # A receipt belongs to the original routine/date, not just the actor and request body.
    if str(form.data.get('routine_item',''))!=str(item.pk) or str(form.data.get('routine_date',''))!=day.isoformat():
        raise ValidationError('완료 요청의 루틴과 날짜가 달라요. 원래 일정에서 다시 확인해 주세요.')
    try:token=uuid.UUID(str(form.data.get('client_key','')))
    except (ValueError,TypeError,AttributeError):raise ValidationError('요청 번호를 확인할 수 없어요. 입력창을 다시 열어 주세요.')
    fingerprint=form_fingerprint(form)
    receipt=ActionReceipt.objects.filter(room=member.room,token=token).select_related('activity').first()
    if receipt:
        if not receipt.matches(member, 'routine.form', fingerprint):
            raise services.Conflict('이미 사용한 요청 번호의 내용과 달라요. 원래 기록을 확인해 주세요.')
        if receipt.activity_id:
            services.require_module(member,receipt.activity.module)
            if receipt.activity.module!=item.module or receipt.activity.room_id!=member.room_id:
                raise services.Conflict('이 완료 요청은 다른 기록에 연결돼 있어요.')
        if receipt.activity_id and receipt.activity.voided:
            raise services.Conflict('앞서 남긴 완료 기록이 취소됐어요. 입력창을 다시 열어 주세요.')
        return {**receipt.result,'created':False,'changed':False,'replayed':True}
    if not form.is_valid():raise ValidationError('입력한 항목을 확인해 주세요.')
    services.require_module(member,item.module,adding=True)
    if form.cleaned_data.get('routine_item')!=item.pk or form.cleaned_data.get('routine_date')!=day:
        raise ValidationError('루틴과 날짜가 달라요. 원래 일정에서 다시 열어 주세요.')
    item,snap,preset=request_context(member,item.pk,day)
    if form.cleaned_data['routine_version']!=item.routine.version:
        raise services.Conflict('루틴 설정이 바뀌었어요. 입력은 유지했으니 최신 일정을 확인해 주세요.')
    if form.cleaned_data['completion_preset']!=(str(preset.pk) if preset else '') or form.cleaned_data['preset_version']!=(preset.version if preset else 0):
        raise services.Conflict('연결된 저장 설정이 바뀌었어요. 최신 설정을 확인해 주세요.')
    from care.services.record_actions import response_for
    run=RoutineRun.objects.filter(item=item,date=day).select_related('activity').first()
    if run and run.activity_id and not run.activity.voided:
        record=run.activity;result=response_for(member,record,created=False,changed=False,message='이미 이 일정에 기록이 연결돼 있어요.')
    else:
        if preset:form.server_metadata['_preset']=snapshot(preset)
        title=safe_title(member,snap)
        form.server_metadata['_schedule']={'item_id':str(item.pk),'date':day.isoformat(),'name':title}
        record,created=services.save_activity(member,item.module,form)
        result=response_for(member,record,created=created,message=title+' 완료 기록을 저장했어요.')
    if record.status=='done':result.update(routine_key=str(item.pk)+':'+day.isoformat(),routine_title=safe_title(member,snap))
    ActionReceipt.objects.create(room=member.room,actor=member,token=token,request_hash=fingerprint,operation='routine.form',activity=record,result=result)
    return result
