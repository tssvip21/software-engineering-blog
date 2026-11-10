"""Synthetic phase7 examples in the isolated copy only; never touch prior review rooms."""
from pathlib import Path
from datetime import datetime,time,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import json,os,sys
R=Path(__file__).resolve().parents[1];O=Path(os.environ.get('PHASE7_OUT',str(R/'tmp/handoff/phase7')));D=Path(os.environ.get('PHASE7_DATA_DIR',str(O/'preview-data')));O.mkdir(parents=True,exist_ok=True)
assert D.resolve()!= (R/'data').resolve()
os.environ.update(DJANGO_SETTINGS_MODULE='config.settings',CARE_DATA_DIR=str(D),CARE_DEMO_MODE='1',DJANGO_DEBUG='1',DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost,testserver',PYTHONUTF8='1')
sys.path.insert(0,str(R))
import django;django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db import transaction
from care import services,actions7
from care.forms import RoomForm,RecordForm,RoutineForm
from care.models import Room,Membership,Product,Activity,CareCadence,RecordPreset
from care.modules import MODULES,SELECTABLE_MODULES,RETIRED_MODULES
from care.presets import PresetForm,save_preset
from care.demo_fixtures import record_data
call_command('migrate',interactive=False,verbosity=0)
call_command('seed_demo',verbosity=0)
call_command('seed_references',verbosity=0)
if (O/'fixture.json').exists():
    print('EXISTING_PHASE7_FIXTURE_PRESERVED');raise SystemExit(0)
today=timezone.localdate();tz=ZoneInfo('Asia/Seoul');now=timezone.now()
owner_user=get_user_model().objects.get(username='demo_owner');guest_user=get_user_model().objects.get(username='demo_member')
fixture={'synthetic':True,'created':now.isoformat(),'day':today.isoformat(),'rooms':{},'presets':{},'source':'phase7 fixtures, not clinical norms'}

def at(ago,hour=8):
    dt=datetime.combine(today-timedelta(days=ago),time(hour),tz)
    if dt>timezone.now():dt=timezone.localtime()-timedelta(minutes=10)
    return dt

def make(owner,key,ago=1,hour=8,simple=False,**fields):
    fields.setdefault('occurred_at',at(ago,hour).isoformat())
    data=record_data(key,**fields)
    form=RecordForm(data,room=owner.room,module=key,member=owner,simple=simple)
    assert form.is_valid(),(key,form.errors)
    return services.save_activity(owner,key,form)[0]

def preset(owner,key,title,**data):
    f=PresetForm({'title':title,'active':'on','is_default':'on',**data},member=owner,module=key)
    assert f.is_valid(),(key,f.errors)
    return save_preset(owner,key,f)

with transaction.atomic():
    for label,name,species,breed,weight in [
      ('rich','7차 검수 · 보리','dog','dog-bichon-frise',Decimal('8.4')),
      ('cat','7차 검수 · 나비','cat','',Decimal('4.2')),
      ('bird','7차 검수 · 초록이','bird','',Decimal('.095')),
      ('empty','7차 검수 · 빈 기록','dog','',Decimal('6')),
      ('sparse','7차 검수 · 간헐 체중','dog','mixed',Decimal('11'))]:
        assert not Room.objects.filter(name=name).exists(),'Untracked pre-existing example; do not overwrite'
        f=RoomForm({'name':name,'species':species,'breed_key':breed,'maturity':'adult','sex':'male','timezone':'Asia/Seoul','use_defaults':'on'})
        assert f.is_valid(),f.errors
        room=services.create_room(owner_user,f);owner=room.members.get(role='owner')
        Membership.objects.create(room=room,user=guest_user,display_name='검수 참여자',role='guest')
        enabled=list(SELECTABLE_MODULES) if label=='rich' else ['meal','treat','water','weight','play','training','sleep','observation','hygiene','cleaning','environment','inventory'] if species in ('cat','bird') else ['weight','meal','walk','observation','inventory']
        if label=='empty':enabled=['weight','walk','meal','observation']
        order=enabled+[k for k in MODULES if k not in enabled]
        services.update_modules(owner,enabled,order,room.settings_version,[k for k in ['meal','treat','water','weight','walk','observation'] if k in enabled])
        room.module_versions.update(effective_at=now-timedelta(days=200),settings={'enabled':enabled,'order':order,'pinned':[k for k in enabled if k in ['meal','treat','water','weight','walk','observation']]})
        fixture['rooms'][label]=str(room.pk)
        if label=='empty':continue
        product=Product.objects.create(room=room,name='검수용 사료',brand='가상 브랜드',category='meal',energy=Decimal('380'),energy_basis='100g',source='합성 데이터 · 실제 영양 권장값 아님',stock_item=name+' 사료',stock_unit='g')
        snack=Product.objects.create(room=room,name='검수용 간식',brand='가상 브랜드',category='treat',energy=Decimal('320'),energy_basis='100g',source='합성 데이터 · 실제 영양 권장값 아님',stock_item=name+' 간식',stock_unit='g')
        make(owner,'inventory',45,item=product.stock_item,movement='set',quantity='20',unit='kg',minimum='1')
        make(owner,'inventory',45,item=snack.stock_item,movement='set',quantity='3',unit='kg',minimum='.2')
        portion='60' if species=='dog' else '35' if species=='cat' else '12'
        main=preset(owner,'meal','기본 사료 '+portion+'g',product=str(product.pk),quantity=portion,unit='g')
        fixture['presets'][label]={'meal':str(main.pk)}
        preset(owner,'treat','간식 5g',product=str(snack.pk),quantity='5',unit='g')
        preset(owner,'water','물 교체',kind='replace',unit='ml')
        preset(owner,'hygiene','양치' if species!='bird' else '발톱 확인',task='teeth' if species!='bird' else 'nail')
        preset(owner,'cleaning','생활공간 청소',target='cage' if species=='bird' else 'bedding',task='청소 확인')
        preset(owner,'play','공놀이' if species=='dog' else '장난감 놀이',kind='장난감 놀이')
        preset(owner,'training','기다려' if species!='bird' else '손에 올라오기',skill='기다려' if species!='bird' else '손에 올라오기')
        if label=='sparse':
            make(owner,'weight',60,weight='10',unit='kg',bcs='');make(owner,'weight',2,weight='11',unit='kg',bcs='')
            for ago in [40,30,20,10,2]:make(owner,'meal',ago,product=str(product.pk),quantity=portion,consumed=portion)
            make(owner,'observation',10,simple=True,detail='관찰 간격이 긴 개체의 가상 메모')
            continue
        count=16 if label=='rich' else 8
        for ago in range(count,0,-1):
            older=ago>7
            w=weight-(Decimal('.4') if species=='dog' else Decimal('.15') if species=='cat' else Decimal('.002')) if older else weight
            make(owner,'weight',ago,weight=str(w if species!='bird' else w*1000),unit='kg' if species!='bird' else 'g',bcs='5' if species!='bird' else '',bcs_by='guardian' if species!='bird' else '')
            amount=portion if older else str(Decimal(portion)+Decimal('5'))
            make(owner,'meal',ago,product=str(product.pk),quantity=amount,consumed=str(Decimal(amount)-Decimal('2')))
            if ago%2==0:make(owner,'treat',ago,product=str(snack.pk),quantity='5',consumed='5')
            make(owner,'water',ago,kind='measured',amount='250' if species=='dog' else '90' if species=='cat' else '12',unit='ml')
            make(owner,'water',ago,hour=9,kind='replace',amount='')
            if species=='dog':make(owner,'walk',ago,minutes='30' if older else '25',distance='2' if older else '1.5',distance_unit='km')
            make(owner,'play',ago,kind='장난감 놀이',minutes='12' if older else '18')
            make(owner,'training',ago,skill='기다려' if species!='bird' else '손에 올라오기',minutes='8',attempts='10',successes='6' if older else '8')
            make(owner,'sleep',ago,hour=11,kind='rest',minutes='45' if older else '60')
            make(owner,'environment',ago,metric='temperature',value='23' if older else '24',unit='C',location='거실' if species!='bird' else '케이지')
            if ago in [14,7,1]:
                make(owner,'hygiene',ago,task='teeth' if species!='bird' else 'nail')
                make(owner,'cleaning',ago,target='bedding' if species!='bird' else 'cage',task='청소')
            if label=='rich':make(owner,'toilet',ago,kind='stool',condition='usual')
        make(owner,'observation',4,simple=True,detail='이 시기에 장난감을 바꿨어요. 변화 원인을 판정하는 자료가 아닌 가상 메모예요.')
        make(owner,'observation',0,simple=True,detail='오늘 관찰 내용을 자유롭게 남기는 예시예요.')
        make(owner,'weight',0,weight=str(weight if species!='bird' else weight*1000),unit='kg' if species!='bird' else 'g',bcs='5' if species!='bird' else '',bcs_by='guardian' if species!='bird' else '')
        make(owner,'meal',0,product=str(product.pk),quantity=portion,consumed='')
        make(owner,'inventory',0,item=product.stock_item,movement='in',quantity='1',unit='kg',minimum='1')
        if label=='rich':
            make(owner,'medication',1,simple=True,routine_title='검수용 영양제 알림',dose='',medicine='',unit='',result='given')
            make(owner,'veterinary',0,status='planned',occurred_at=(timezone.localtime()+timedelta(days=3)).isoformat(),kind='vaccine',detail='가상 예방접종 일정')
            make(owner,'shedding',1,simple=True,kind='fur')
            make(owner,'expense',3,item='가상 소모품',amount='32000',currency='KRW',category='supplies')
            med=preset(owner,'medication','영양제 확인',note='사용자 지정 알림 예시이며 실제 처방이 아님')
            for title,module,setting in [('아침 급여','meal',main),('영양제 확인','medication',med),('휴식 준비','sleep',None)]:
                form=RoutineForm({'name':title,'at':'08:00','weekdays':[str(n) for n in range(7)],'modules':[module],'active':'on',
                                  'start_date':(today-timedelta(days=14)).isoformat(),'end_date':(today+timedelta(days=30)).isoformat(),'preset':str(setting.pk) if setting else ''},room=room,visible_keys=list(MODULES))
                assert form.is_valid(),form.errors
                routine=services.save_routine(owner,form);routine.versions.update(effective_on=today-timedelta(days=14))
                item=routine.items.get(active=True)
                for ago in [6,4,2]:
                    day=today-timedelta(days=ago)
                    data={'token':str(__import__('uuid').uuid4()),'operation':'routine_done','module':module,'routine_item':str(item.pk),'date':day.isoformat()}
                    if setting:data['preset_id']=str(setting.pk)
                    actions7.perform(owner,data)
            CareCadence.objects.create(room=room,module='hygiene',variant_field='task',variant_value='teeth',title='정해 둔 양치 확인',interval=3,unit='days',first_due=today-timedelta(days=1))
            CareCadence.objects.create(room=room,module='cleaning',variant_field='target',variant_value='bedding',title='침구 세탁',interval=7,unit='days',first_due=today+timedelta(days=2))
fixture['records']={label:Activity.objects.filter(room_id=pk).count() for label,pk in fixture['rooms'].items()}
(O/'fixture.json').write_text(json.dumps(fixture,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(fixture,ensure_ascii=False))
