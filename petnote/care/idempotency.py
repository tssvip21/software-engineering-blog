"""Idempotence for configuration creation, including a lost redirect/response."""
import hashlib
import json
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from . import services
from .models import ActionReceipt, Product, Routine, RecordPreset

MODELS={'product.create':Product,'routine.create':Routine,'preset.create':RecordPreset}


def form_fingerprint(form, *, compact=True):
    if hasattr(form.data, 'lists'):
        data = {key: list(values) for key, values in form.data.lists()}
    else:
        data = dict(form.data)
    data.pop('csrfmiddlewaretoken', None)
    # Profile and preferences receipts already in the database used spaced JSON.
    separators = (',', ':') if compact else None
    body = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str, separators=separators)
    return hashlib.sha256(body.encode()).hexdigest()


@transaction.atomic
def once(member,form,operation,create):
    member=services.fresh_member(member);services.require_manager(member)
    try:token=uuid.UUID(str(form.cleaned_data.get('request_token','')))
    except (ValueError,TypeError,AttributeError):raise ValidationError('저장 요청 번호가 없어요. 입력 화면을 새로 열어 주세요.')
    fingerprint=form_fingerprint(form)
    receipt=ActionReceipt.objects.filter(room=member.room,token=token).first()
    if receipt:
        if not receipt.matches(member, operation, fingerprint):
            raise services.Conflict('앞선 저장 요청과 내용이 달라요. 저장한 설정을 확인하고 새 화면에서 수정해 주세요.')
        obj=MODELS[operation].objects.filter(pk=receipt.result.get('object_id'),room=member.room).first()
        if obj is None:raise services.Conflict('이미 처리했던 설정을 지금은 찾을 수 없어요. 관리 목록을 확인해 주세요.')
        return obj
    obj=create()
    ActionReceipt.objects.create(room=member.room,actor=member,token=token,request_hash=fingerprint,operation=operation,
                                result={'object_id':str(obj.pk),'kind':operation})
    return obj
