"""Routine completion form requests and recoverable input errors."""

import uuid
from datetime import date

from django import forms
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from care import services
from care.models import ActionReceipt, RoutineItem
from care.modules import MODULES
from care.presentation import forms as ui_forms
from care.services.completion import (
    completion_form,
    request_context,
    safe_title,
    save_completion,
)


@require_http_methods(['GET','POST'])
def complete(request,room_id,item_id):
    room,member=services.get_room(request,room_id)
    try:
        day=date.fromisoformat(request.GET.get('date',''))
        token=uuid.UUID(request.POST.get('client_key') or request.GET.get('token') or str(uuid.uuid4()))
    except (ValueError,TypeError,AttributeError):raise Http404('일정 날짜 또는 요청 번호가 올바르지 않아요.')
    try:
        item,snap,preset=request_context(member,item_id,day)
    except ValidationError as exc:
        item=get_object_or_404(RoutineItem.objects.select_related('routine'),pk=item_id,routine__room=room)
        services.require_module(member,item.module)
        snap=services.effective_routine(item.routine,day) or services.routine_snapshot(item.routine)
        form=completion_form(member,item,snap,None,day,token,request.POST if request.method=='POST' else None)
        if not form.is_bound:form.cleaned_data={}
        form.add_error(None,' '.join(exc.messages)+' 입력 내용은 유지했어요. 오늘 일정에서 다시 확인해 주세요.')
        return ui_forms.render_input(request,'care/space/settings_page.html',{'title':'일정을 다시 확인해 주세요','room':room,
            'form':form,'read_only':True,'back_url':reverse('today',args=[room.pk]),'active_tab':'routines'},status=409)
    form=completion_form(member,item,snap,preset,day,token,request.POST if request.method=='POST' else None)
    status=200
    if request.method=='POST':
        # Resolve a lost-response retry before revalidating mutable preset/product choices.
        retry=ActionReceipt.objects.filter(room=room,token=token,actor=member,operation='routine.form').exists()
        if retry or form.is_valid():
            try:
                result=save_completion(member,item_id,day,form)
                if ui_forms.in_dialog(request):return ui_forms.saved(request,result.pop('message'),result.pop('url'),**{k:v for k,v in result.items() if k!='ok'})
                return redirect(result['url'])
            except ValidationError as exc:form.add_error(None,' '.join(exc.messages));status=409 if isinstance(exc,services.Conflict) else 422
            except OperationalError:form.add_error(None,'저장 결과를 확인하지 못했어요. 입력을 유지하고 같은 요청으로 다시 확인해 주세요.');status=503
        else:status=422
    probe=completion_form(member,item,snap,preset,day,token)
    missing=[str(field.label) for key,field in probe.fields.items() if field.required and not isinstance(field.widget,forms.HiddenInput) and probe[key].value() in (None,'')]
    return ui_forms.render_input(request,'care/records/entry.html',{'title':safe_title(member,snap)+' 완료 기록','spec':MODULES[item.module],
        'form':form,'routine_completion':True,'completion_name':safe_title(member,snap),'missing_labels':missing,
        'submit_label':'저장하고 완료','back_url':reverse('today',args=[room.pk]),'active_tab':'routines'},status=status)
