"""Reusable record settings with explicit defaults and frozen per-event provenance."""
import uuid
from copy import deepcopy

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from care import services
from care.forms import RecordForm
from care.models import RecordPreset
from care.modules import SELECTABLE_MODULES

PRESET_MODULES = {'meal','treat','water','play','training','hygiene','cleaning','medication','veterinary','sleep'}


class PresetForm(forms.Form):
    request_token = forms.UUIDField(widget=forms.HiddenInput,required=False,initial=uuid.uuid4)
    title = forms.CharField(label='저장 설정 이름',max_length=100,help_text='예: 아침 사료 60g, 닭고기 간식, 양치, 영양제')
    is_default = forms.BooleanField(label='이 기능의 기본 설정으로 사용',required=False)
    active = forms.BooleanField(label='이 저장 설정 사용',required=False,initial=True)
    version = forms.IntegerField(widget=forms.HiddenInput,required=False)

    def __init__(self,*args,member,module,instance=None,**kwargs):
        self.member,self.module,self.instance=member,module,instance
        initial=kwargs.setdefault('initial',{})
        if instance:
            initial.update(instance.data,title=instance.title,is_default=instance.is_default,active=instance.active,version=instance.version)
            if instance.product_id:initial['product']=str(instance.product_id)
        super().__init__(*args,**kwargs)
        spec=RecordForm(room=member.room,module=module,member=member,simple=True)
        fields={'meal':('product','food_name','quantity','unit'), 'treat':('product','food_name','quantity','unit','reason'),
                'water':('kind','container','amount','unit'), 'play':('kind',),'training':('skill',),
                'hygiene':('product','task','used_quantity','used_unit'),'cleaning':('product','target','task','used_quantity','used_unit'),'medication':('product','used_quantity','used_unit'),
                'veterinary':('kind','clinic','detail'),'sleep':('kind',)}[module]
        for key in fields:
            self.fields[key]=deepcopy(spec.fields[key])
            self.fields[key].initial=initial.get(key,spec.initial.get(key))
            if key=='product' and module in ('meal','treat') and instance and instance.product_id:
                from care.models import Product
                self.fields[key].queryset=Product.objects.filter(room=member.room,category__in=(module,'both')).filter(Q(active=True)|Q(pk=instance.product_id))
        if module in ('meal','treat'):
            self.fields['quantity'].required=True
            self.fields['quantity'].help_text='기본으로 꺼내 줄 양이에요. 실제 먹은 양으로 자동 간주하지 않아요.'
        self.fields['note']=forms.CharField(label='메모 (선택)',required=False,max_length=1000,widget=forms.Textarea(attrs={'rows':3}),initial=initial.get('note',''))

    def clean(self):
        data=super().clean()
        if self.module in ('meal','treat') and not data.get('product') and not (data.get('food_name') or '').strip():
            self.add_error('food_name','등록한 제품을 고르거나 먹이 이름을 입력해 주세요.')
        if self.module=='water' and data.get('kind')=='measured':
            self.add_error('kind','실제 음수량은 매번 측정해서 기록해야 해요. 저장 설정은 물 교체·보충에 사용해 주세요.')
        if data.get('is_default') and not data.get('active'):
            self.add_error('is_default','사용하는 설정만 기본값으로 지정할 수 있어요.')
        return data


@transaction.atomic
def save_preset(member,module,form,instance=None,*,_receipt=False):
    member=services.fresh_member(member);services.require_manager(member)
    if module not in PRESET_MODULES or module not in SELECTABLE_MODULES:
        raise ValidationError('이 기능에는 저장 설정을 만들 수 없어요.')
    services.require_module(member,module)
    if not form.is_valid():raise ValidationError('저장 설정의 입력값을 확인해 주세요.')
    data=form.cleaned_data
    if instance is None and data.get('request_token') and not _receipt:
        from care.services.idempotency import once
        return once(member,form,'preset.create',lambda:save_preset(member,module,form,_receipt=True))
    if instance:
        preset=RecordPreset.objects.select_for_update().get(pk=instance.pk,room=member.room,module=module)
        if preset.version!=data.get('version'):raise services.Conflict('다른 사람이 저장 설정을 바꿨어요. 최신 값을 확인해 주세요.')
        preset.version+=1
    else:preset=RecordPreset(room=member.room,module=module)
    product=data.get('product')
    if product and (product.room_id!=member.room_id or (not product.active and product.pk!=getattr(preset,'product_id',None))):
        raise ValidationError('사용 중인 이 공간의 제품을 골라 주세요.')
    preset.title=data['title'];preset.product=product;preset.active=data['active'];preset.is_default=data['is_default']
    excluded={'title','product','is_default','active','version','request_token'}
    preset.data=services.json_safe({k:v for k,v in data.items() if k not in excluded})
    if module=='medication':preset.data.update(routine_title=preset.title,result='given')
    if module=='sleep':preset.data.update(routine_title=preset.title)
    if product:
        preset.data['product']=str(product.pk)
        if module in ('meal','treat'):preset.data['food_name']=product.name
    if preset.is_default:
        RecordPreset.objects.filter(room=member.room,module=module,is_default=True).exclude(pk=preset.pk).update(is_default=False)
    preset.full_clean();preset.save()
    services.bump(member.room,member,'preset.updated' if instance else 'preset.created',{'preset_id':str(preset.pk),'module':module})
    return preset


def snapshot(preset):
    return {'id':str(preset.pk),'version':preset.version,'title':preset.title,'data':deepcopy(preset.data),
            'product_id':str(preset.product_id) if preset.product_id else None}


def input_for(member,module,preset=None,*,at=None):
    """Server-specified defaults only. User measurement values are never guessed."""
    at=at or timezone.now()
    data={'client_key':str(uuid.uuid4()),'status':'done','occurred_at':at.astimezone().isoformat(timespec='seconds'),
          'performer':str(member.pk),'note':''}
    if preset:
        data.update(deepcopy(preset.data))
        if preset.product_id:data['product']=str(preset.product_id)
    else:
        defaults={
          'water':{'kind':'replace','unit':'ml'},
          'walk':{'distance_unit':'km'},
          'toilet':{'kind':'observed','condition':'unknown'},
          'shedding':{'kind':'feather' if member.room.species=='bird' else 'molt' if member.room.species in ('reptile','amphibian') else 'fur'},
          'play':{'kind':'','reaction':'unknown'},'training':{'skill':''},
          'observation':{'category':'other','condition':'unknown'},
          'sleep':{'kind':'rest'},
        }
        data.update(defaults.get(module,{}))
    # Explicitly selected defaults do not mean all supplied food was consumed.
    data.pop('consumed',None)
    if module in ('meal','treat'):
        data.setdefault('unit','g')
    if module == 'play':
        data.setdefault('reaction', 'unknown')
    if module=='medication':
        data.update(result='given',routine_title=preset.title if preset else '')
    return data
