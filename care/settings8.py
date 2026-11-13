"""Separate pet identity from personal preferences and shared space configuration."""
import hashlib
import hmac
import json
import uuid
from zoneinfo import ZoneInfo

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from . import services
from .breeds import catalogue, choices, picker_data, suggest_size
from .forms import DateInput, TIMEZONES
from .models import ActionReceipt, Room

PET_FIELDS=('name','species','breed_key','subspecies','sex','birth_date','maturity','size','size_origin')


class PetProfileForm(forms.ModelForm):
    breed_key=forms.ChoiceField(label='품종',required=False)
    size_origin=forms.ChoiceField(choices=(('auto','제안값'),('manual','직접 입력')),widget=forms.HiddenInput,required=False)
    version=forms.IntegerField(widget=forms.HiddenInput,required=False)
    request_token=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    use_defaults=forms.BooleanField(label='동물 종류에 맞는 돌봄 기능으로 시작',required=False,initial=True)

    class Meta:
        model=Room
        fields=list(PET_FIELDS)
        labels={'name':'이름','species':'동물 종류','subspecies':'품종·종류 직접 입력','sex':'성별','birth_date':'생일','maturity':'성장 상태','size':'체격'}
        widgets={'birth_date':DateInput(),'name':forms.TextInput(attrs={'maxlength':40,'autocomplete':'off'})}

    def __init__(self,*args,member=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['breed_key'].choices=choices()
        self.breed_catalog=picker_data();self.is_pet_profile=True
        self.fields['birth_date'].help_text='모르면 비워 두세요.'
        self.fields['size'].help_text=''
        self.fields['maturity'].help_text='성장 중에는 성견 체격을 자동 적용하지 않아요.'
        self.fields['size_origin'].initial=self.instance.size_origin or 'manual'
        self.initial['size_origin']=self.instance.size_origin or 'manual'
        if self.instance.created_at:
            self.fields.pop('use_defaults')
            self.initial['version']=self.instance.settings_version
            self.fields['version'].required=True
        self.read_only=bool(member and not member.can_manage)
        if self.read_only:
            for field in self.fields.values():field.disabled=True

    def clean_name(self):
        name=self.cleaned_data['name'].strip()
        if not name:raise ValidationError('이름을 입력해 주세요.')
        return name

    def clean_birth_date(self):
        day=self.cleaned_data.get('birth_date')
        if day and day>timezone.localdate():raise ValidationError('생일은 미래 날짜일 수 없어요.')
        return day

    def clean(self):
        data=super().clean()
        kind,key=data.get('species'),data.get('breed_key','')
        breed=catalogue().get(key)
        if breed and breed['species']!=kind:self.add_error('breed_key','동물 종류에 맞는 품종을 선택해 주세요.')
        if kind not in ('dog','cat'):data['breed_key']=''
        elif breed:data['subspecies']=breed['ko']
        if kind!='dog':data['size']='';data['size_origin']='manual'
        else:
            proposed=suggest_size(breed,data.get('sex','')) if data.get('maturity')=='adult' else ''
            origin=data.get('size_origin') or 'manual'
            selected=data.get('size','')
            # An explicit value wins. No script is needed to keep a manually chosen exception.
            if origin=='auto' and proposed and selected in ('',proposed):
                data['size']=proposed;data['size_origin']='auto'
            else:data['size_origin']='manual'
        return data


class SpacePreferencesForm(forms.Form):
    notices_enabled=forms.BooleanField(label='다가오는 일정 안내 표시',required=False,help_text='다른 구성원의 안내 표시는 바뀌지 않아요.')
    timezone=forms.ChoiceField(label='기록 시간대',choices=TIMEZONES,help_text='공간의 표시 시각·날짜와 루틴 계산에 적용돼요.')
    guest_sensitive=forms.BooleanField(label='투약·병원·지출 기록을 모든 구성원과 공유',required=False,help_text='끄면 방장과 관리자만 해당 기록을 보고 남길 수 있어요.')
    version=forms.IntegerField(widget=forms.HiddenInput)
    personal_before=forms.CharField(widget=forms.HiddenInput)
    request_token=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)

    def __init__(self,*args,member,**kwargs):
        self.member=member
        initial=kwargs.setdefault('initial',{})
        initial.update(notices_enabled=member.notices_enabled,timezone=member.room.timezone,guest_sensitive=member.room.guest_sensitive,
                       version=member.room.settings_version,personal_before='1' if member.notices_enabled else '0')
        super().__init__(*args,**kwargs)
        self.is_space_preferences=True
        self.fields['timezone'].disabled=not member.can_manage
        self.fields['guest_sensitive'].disabled=member.role!='owner'
        if member.role!='owner':self.fields['guest_sensitive'].help_text='공개 범위는 방장만 변경할 수 있어요.'


def fingerprint(form):
    values={k:list(v) for k,v in form.data.lists()} if hasattr(form.data,'lists') else dict(form.data)
    values.pop('csrfmiddlewaretoken',None)
    return hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


@transaction.atomic
def apply_once(member,form,operation,handler):
    """New scoped form actions reuse the existing receipt table, including edit retries."""
    member=services.fresh_member(member)
    if not form.is_valid():raise ValidationError('입력 내용을 확인해 주세요.')
    token=form.cleaned_data.get('request_token')
    if not token:raise ValidationError('입력창을 다시 열어 주세요. 저장 요청 번호가 없어요.')
    digest=fingerprint(form)
    old=ActionReceipt.objects.filter(room=member.room,token=token).first()
    if old:
        if old.actor_id!=member.pk or old.operation!=operation or not hmac.compare_digest(old.request_hash,digest):
            raise services.Conflict('앞서 보낸 저장 요청과 내용이 달라요. 최신 화면에서 다시 확인해 주세요.')
        return dict(old.result,replayed=True)
    result=handler(member)
    ActionReceipt.objects.create(room=member.room,actor=member,token=token,operation=operation,request_hash=digest,result=result)
    return result


def save_pet(member,form):
    services.require_manager(member)
    def change(actor):
        services.require_manager(actor);room=actor.room
        if form.cleaned_data['version']!=room.settings_version:raise services.Conflict('다른 구성원이 설정을 바꿨어요. 입력을 유지했으니 최신 값을 확인해 주세요.')
        changed=[]
        for key in PET_FIELDS:
            v=form.cleaned_data[key]
            if getattr(room,key)!=v:setattr(room,key,v);changed.append(key)
        if changed:
            room.settings_version+=1;room.full_clean();room.save(update_fields=[*changed,'settings_version'])
            services.bump(room,actor,'pet.updated',{'fields':changed})
        return {'id':str(room.pk),'version':room.settings_version,'changed':bool(changed)}
    return apply_once(member,form,'pet.update',change)


def save_preferences(member,form):
    def change(actor):
        data=form.cleaned_data;room=actor.room
        if data['version']!=room.settings_version or data['personal_before']!=('1' if actor.notices_enabled else '0'):
            raise services.Conflict('설정이 다른 화면에서 바뀌었어요. 입력은 유지했으니 최신 설정을 확인해 주세요.')
        new_zone=data['timezone'];ZoneInfo(new_zone)
        if not actor.can_manage and new_zone!=room.timezone:raise PermissionDenied()
        if actor.role!='owner' and data['guest_sensitive']!=room.guest_sensitive:raise PermissionDenied()
        fields=[]
        if actor.can_manage and room.timezone!=new_zone:room.timezone=new_zone;fields.append('timezone')
        if actor.role=='owner' and room.guest_sensitive!=data['guest_sensitive']:
            room.guest_sensitive=data['guest_sensitive'];fields.append('guest_sensitive')
        personal=actor.notices_enabled!=data['notices_enabled']
        if personal:actor.notices_enabled=data['notices_enabled'];actor.save(update_fields=['notices_enabled'])
        if fields:room.settings_version+=1;room.full_clean();room.save(update_fields=[*fields,'settings_version'])
        if fields or personal:services.bump(room,actor,'preferences.updated',{'shared_fields':fields,'personal_notice_changed':personal})
        return {'id':str(room.pk),'version':room.settings_version,'changed':bool(fields or personal)}
    return apply_once(member,form,'preferences.update',change)
