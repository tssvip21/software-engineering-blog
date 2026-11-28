import json
import re
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from care import services
from care.demo_fixtures import record_data
from care.forms import RecordForm, RoomForm, RoutineForm
from care.models import (
    Activity,
    AuditEvent,
    CareCadence,
    Invitation,
    Membership,
    Product,
    Room,
    Routine,
)
from care.modules import MODULES, SELECTABLE_MODULES
from care.services import record_actions
from care.services.presets import PresetForm, save_preset
from care.views.demo import SHOWCASE_ROOMS


def remove_examples(rooms):
    # Memberships referenced by these rows are protected against cascading deletion.
    ids = list(rooms.values_list('pk', flat=True))
    if not ids:
        return
    for model in (Activity, Routine, Invitation, AuditEvent):
        model.objects.filter(room_id__in=ids).delete()
    Room.objects.filter(pk__in=ids).delete()


def remove_original_seed_pair(keep):
    owner_rooms = Room.objects.filter(owner__username='demo_owner')
    # Older installs also seeded two rooms before creating the showcase manifest.
    dog = owner_rooms.filter(name='보리', species='dog', routines__name='저녁 돌봄',
                             products__name__in=['사료 A', '시연용 사료 A'])
    bird = owner_rooms.filter(name='초록이', species='bird', activities__module='cleaning',
                              activities__payload__task='케이지 바닥 청소')
    remove_examples((dog | bird).exclude(pk__in=keep).distinct())


def upgrade_local_examples(fixture):
    rooms = fixture['rooms']
    if fixture.get('synthetic') is not True:
        raise CommandError('시연 자료의 생성 목록을 확인해 주세요.')
    owner_rooms = Room.objects.filter(owner__username='demo_owner')
    for key, (name, species) in zip(('rich', 'cat'), SHOWCASE_ROOMS):
        room = owner_rooms.filter(pk=rooms[key], species=species).first()
        if room is None:
            raise CommandError('시연 공간과 생성 목록이 일치하지 않습니다.')
        if re.fullmatch(r'\d+차 검수 · (?:보리|나비|강아지|고양이)', room.name):
            room.name = name
            room.save(update_fields=['name'])
    obsolete = {rooms[k] for k in ('bird', 'empty', 'sparse') if k in rooms}
    if not obsolete:
        return
    if obsolete.intersection((rooms['rich'], rooms['cat'])):
        raise CommandError('시연 공간이 생성 목록에 중복되어 있습니다.')
    targets = owner_rooms.filter(pk__in=obsolete)
    remove_examples(targets)
    for section in ('rooms', 'presets', 'records'):
        if section in fixture:
            fixture[section] = {k: v for k, v in fixture[section].items() if k in ('rich', 'cat')}


def upgrade_public_seed():
    if not settings.PUBLIC_DEMO:
        raise CommandError('공개 시연 전용 DB에서만 실행할 수 있습니다.')
    rooms = Room.objects.filter(owner__username='demo_owner', archived_at__isnull=True)
    expected = set(SHOWCASE_ROOMS) | {('앵무새', 'bird')}
    if Room.objects.count() == len(expected) and set(rooms.values_list('name', 'species')) == expected:
        bird = rooms.filter(name='앵무새', species='bird', members__user__username='demo_admin',
                            members__active=True, members__role='admin')
        remove_examples(bird)


def connect_showcase_admin(fixture):
    admin = get_user_model().objects.get(username='demo_admin')
    for key in ('rich', 'cat'):
        room = Room.objects.get(pk=fixture['rooms'][key], owner__username='demo_owner')
        Membership.objects.get_or_create(room=room, user=admin,
                                         defaults={'display_name': admin.first_name, 'role': 'admin'})


def create_showcase():
    today = timezone.localdate()
    tz = ZoneInfo('Asia/Seoul')
    now = timezone.now()
    owner_user = get_user_model().objects.get(username='demo_owner')
    guest_user = get_user_model().objects.get(username='demo_member')
    fixture = {'synthetic': True, 'created': now.isoformat(), 'day': today.isoformat(), 'rooms': {}, 'presets': {}}

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

    for label,name,species,breed,weight in [
      ('rich','강아지','dog','dog-bichon-frise',Decimal('8.4')),
      ('cat','고양이','cat','',Decimal('4.2')),
      ]:
        if Room.objects.filter(owner=owner_user, name=name, species=species).exists():
            raise CommandError('기존 시연 공간의 생성 목록을 확인해 주세요.')
        f=RoomForm({'name':name,'species':species,'breed_key':breed,'maturity':'adult','sex':'male','timezone':'Asia/Seoul','use_defaults':'on'})
        assert f.is_valid(),f.errors
        room = services.create_room(owner_user, f)
        owner=room.members.get(role='owner')
        Membership.objects.get_or_create(room=room,user=guest_user,defaults={'display_name':'참여자','role':'guest'})
        enabled=list(SELECTABLE_MODULES) if label=='rich' else ['meal','treat','water','weight','play','training','sleep','observation','hygiene','cleaning','environment','inventory']
        order=enabled+[k for k in MODULES if k not in enabled]
        services.update_modules(owner,enabled,order,room.settings_version,[k for k in ['meal','treat','water','weight','walk','observation'] if k in enabled])
        room.module_versions.update(effective_at=now-timedelta(days=200),settings={'enabled':enabled,'order':order,'pinned':[k for k in enabled if k in ['meal','treat','water','weight','walk','observation']]})
        fixture['rooms'][label]=str(room.pk)
        product=Product.objects.create(room=room,name='사료',brand='',category='meal',energy=Decimal('380'),energy_basis='100g',source='합성 데이터 · 실제 영양 권장값 아님',stock_item=name+' 사료',stock_unit='g')
        snack=Product.objects.create(room=room,name='간식',brand='',category='treat',energy=Decimal('320'),energy_basis='100g',source='합성 데이터 · 실제 영양 권장값 아님',stock_item=name+' 간식',stock_unit='g')
        make(owner,'inventory',45,item=product.stock_item,movement='set',quantity='20',unit='kg',minimum='1')
        make(owner,'inventory',45,item=snack.stock_item,movement='set',quantity='3',unit='kg',minimum='.2')
        portion='60' if species=='dog' else '35'
        main=preset(owner,'meal','기본 사료 '+portion+'g',product=str(product.pk),quantity=portion,unit='g')
        fixture['presets'][label]={'meal':str(main.pk)}
        preset(owner,'treat','간식 5g',product=str(snack.pk),quantity='5',unit='g')
        preset(owner,'water','물 교체',kind='replace',unit='ml')
        preset(owner,'hygiene','양치',task='teeth')
        preset(owner,'cleaning','생활공간 청소',target='bedding',task='청소 확인')
        preset(owner,'play','공놀이' if species=='dog' else '장난감 놀이',kind='장난감 놀이')
        preset(owner,'training','기다려',skill='기다려')
        count=16 if label=='rich' else 8
        for ago in range(count,0,-1):
            older=ago>7
            w=weight-(Decimal('.4') if species=='dog' else Decimal('.15')) if older else weight
            make(owner,'weight',ago,weight=str(w),unit='kg',bcs='5',bcs_by='guardian')
            amount=portion if older else str(Decimal(portion)+Decimal('5'))
            make(owner,'meal',ago,product=str(product.pk),quantity=amount,consumed=str(Decimal(amount)-Decimal('2')))
            if ago%2==0:make(owner,'treat',ago,product=str(snack.pk),quantity='5',consumed='5')
            make(owner,'water',ago,kind='measured',amount='250' if species=='dog' else '90',unit='ml')
            make(owner,'water',ago,hour=9,kind='replace',amount='')
            if species=='dog':make(owner,'walk',ago,minutes='30' if older else '25',distance='2' if older else '1.5',distance_unit='km')
            make(owner,'play',ago,kind='장난감 놀이',minutes='12' if older else '18')
            make(owner,'training',ago,skill='기다려',minutes='8',attempts='10',successes='6' if older else '8')
            make(owner,'sleep',ago,hour=11,kind='rest',minutes='45' if older else '60')
            make(owner,'environment',ago,metric='temperature',value='23' if older else '24',unit='C',location='거실')
            if ago in [14,7,1]:
                make(owner,'hygiene',ago,task='teeth')
                make(owner,'cleaning',ago,target='bedding',task='청소')
            if label=='rich':make(owner,'toilet',ago,kind='stool',condition='usual')
        make(owner,'observation',4,simple=True,detail='이 시기에 장난감을 바꿨어요.')
        make(owner,'observation',0,simple=True,detail='오늘 관찰한 내용이에요.')
        make(owner,'weight',0,weight=str(weight),unit='kg',bcs='5',bcs_by='guardian')
        make(owner,'meal',0,product=str(product.pk),quantity=portion,consumed='')
        make(owner,'inventory',0,item=product.stock_item,movement='in',quantity='1',unit='kg',minimum='1')
        if label=='rich':
            make(owner,'medication',1,simple=True,routine_title='영양제 알림',dose='',medicine='',unit='',result='given')
            make(owner,'veterinary',0,status='planned',occurred_at=(timezone.localtime()+timedelta(days=3)).isoformat(),kind='vaccine',detail='예방접종 일정')
            make(owner,'shedding',1,simple=True,kind='fur')
            make(owner,'expense',3,item='소모품',amount='32000',currency='KRW',category='supplies')
            med=preset(owner,'medication','영양제 확인',note='사용자 지정 알림 예시이며 실제 처방이 아님')
            for title,module,setting in [('아침 급여','meal',main),('영양제 확인','medication',med),('휴식 준비','sleep',None)]:
                form=RoutineForm({'name':title,'at':'08:00','weekdays':[str(n) for n in range(7)],'modules':[module],'active':'on',
                                  'start_date':(today-timedelta(days=14)).isoformat(),'end_date':(today+timedelta(days=30)).isoformat(),'preset':str(setting.pk) if setting else ''},room=room,visible_keys=list(MODULES))
                assert form.is_valid(),form.errors
                routine=services.save_routine(owner,form);routine.versions.update(effective_on=today-timedelta(days=14))
                item=routine.items.get(active=True)
                for ago in [6,4,2]:
                    day=today-timedelta(days=ago)
                    data={'token':str(uuid.uuid4()),'operation':'routine_done','module':module,'routine_item':str(item.pk),'date':day.isoformat()}
                    if setting:data['preset_id']=str(setting.pk)
                    record_actions.perform(owner,data)
            CareCadence.objects.create(room=room,module='hygiene',variant_field='task',variant_value='teeth',title='정해 둔 양치 확인',interval=3,unit='days',first_due=today-timedelta(days=1))
            CareCadence.objects.create(room=room,module='cleaning',variant_field='target',variant_value='bedding',title='침구 세탁',interval=7,unit='days',first_due=today+timedelta(days=2))
    fixture['records'] = {key: Activity.objects.filter(room_id=pk).count() for key, pk in fixture['rooms'].items()}
    return fixture


class Command(BaseCommand):
    help = '시연 계정과 강아지·고양이의 돌봄 자료를 준비합니다.'

    def add_arguments(self, parser):
        parser.add_argument('--accounts-only', action='store_true')
        parser.add_argument('--upgrade-public-seed', action='store_true')

    def handle(self, *args, **options):
        if options['upgrade_public_seed']:
            with transaction.atomic():
                upgrade_public_seed()
            return
        if not settings.DEMO_MODE or not settings.DEBUG or not (
            settings.LOCAL_DEMO_POSTGRES or str(settings.DATABASES['default']['NAME']).endswith('demo.sqlite3')
        ):
            raise CommandError('로컬 시연 DB에서만 실행할 수 있습니다.')
        path = settings.DATA_DIR / 'showcase' / 'fixture.json'
        legacy_path = settings.DATA_DIR.parent / 'fixture.json' if settings.DATA_DIR.name == 'preview-data' else path
        with transaction.atomic():
            for key, name in [('owner', '지우'), ('admin', '민서'), ('member', '도윤'), ('outsider', '새 보호자')]:
                user, created = get_user_model().objects.get_or_create(username='demo_' + key, defaults={'first_name': name})
                if created:
                    user.set_unusable_password()
                    user.save(update_fields=['password'])
            if options['accounts_only']:
                return
            owner = get_user_model().objects.select_for_update().get(username='demo_owner')
            marker = AuditEvent.objects.filter(room__owner=owner, action='demo.showcase_initialized').first()
            if marker:
                fixture = marker.details
            elif path.is_file() or legacy_path.is_file():
                source = path if path.is_file() else legacy_path
                fixture = json.loads(source.read_text(encoding='utf-8'))
                upgrade_local_examples(fixture)
            else:
                fixture = create_showcase()
            connect_showcase_admin(fixture)
            if not marker:
                remove_original_seed_pair(fixture['rooms'].values())
                fixture.pop('source', None)
                AuditEvent.objects.create(room_id=fixture['rooms']['rich'], action='demo.showcase_initialized', details=fixture)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(f'.{uuid.uuid4().hex}.tmp')
        temp.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)
