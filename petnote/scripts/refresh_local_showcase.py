"""Explicit, one-time replacement of the three local synthetic showcase rooms.

Never runs at application startup. SQLite backup, copy rehearsal, validation and
--apply are required. Product names are real; all care events and costs are fiction.
"""
import argparse
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import random
import sqlite3
import sys
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / 'tmp/handoff/phase7/preview-data/demo.sqlite3'
WORK = ROOT / 'tmp/handoff/realistic-showcase'
IDS = ['024b42be-590a-4c19-a98c-2228429c11a3', '537cec39-91b3-4757-ad35-18b59ec7cd00', 'b74c27c7-a65a-4427-b6bf-3c4b43f5dbaf']
SOURCES = [
    'https://www.royalcanin.com/kr/dogs/products/retail-products/mini-adult-3001',
    'https://www.royalcanin.com/kr/cats/products/retail-products/indoor-27-2529',
    'https://www.harrisonsbirdfoods.com/product/adult-lifetime-fine/',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    if args.apply:
        evidence = json.loads((WORK / 'rehearsal.json').read_text(encoding='utf8'))
        assert evidence['validated'] and evidence['day'] == datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = ROOT / 'backups' / f'showcase-before-{stamp}.sqlite3'
    with sqlite3.connect(f'file:{LIVE.as_posix()}?mode=ro', uri=True) as source:
        with sqlite3.connect(backup) as target:
            source.backup(target)
    if args.apply:
        db = LIVE
    else:
        db = WORK / stamp / 'demo.sqlite3'
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(backup) as source, sqlite3.connect(db) as target:
            source.backup(target)
    os.environ.update(DJANGO_SETTINGS_MODULE='config.settings', CARE_DATA_DIR=str(db.parent),
                      CARE_DEMO_MODE='1', DJANGO_DEBUG='1', POSTGRES_HOST='',
                      GOOGLE_CLIENT_ID='', GOOGLE_CLIENT_SECRET='', DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost,testserver')
    sys.path.insert(0, str(ROOT))
    import django
    django.setup()
    from django.apps import apps
    from django.conf import settings
    from django.db import connection, transaction
    from django.test import Client
    from django.utils import timezone
    from care import services, presets, schedule7
    from care.forms import RecordForm, RoutineForm
    from care.models import (Room, Membership, Product, Activity, Routine, RoutineRun, RecordPreset,
                             CareCadence, AuditEvent, ActionReceipt, NoticeDismissal)
    from care.modules import MODULES
    from care.inventory_ledger import ledger, records_for
    assert settings.DEMO_MODE and Path(connection.settings_dict['NAME']).resolve() == db.resolve()
    assert set(IDS) == set(json.loads((ROOT/'tmp/handoff/phase7/demo-navigation.json').read_text())['showcase'])
    assert Room.objects.filter(pk__in=IDS, owner__username='demo_owner').count() == 3
    assert not AuditEvent.objects.filter(room_id__in=IDS, action='demo.realistic_refresh').exists(), 'Already applied; refusing to erase subsequent edits.'
    tz = ZoneInfo('Asia/Seoul')
    now = timezone.now().astimezone(tz)
    today = now.date()
    start = today - timedelta(days=27)
    rng = random.Random(20261114)

    def other_rows():
        """Check every app model outside the three targets, including child rows."""
        output = {}
        indirect = {'ActivityRevision': 'activity__room_id', 'RoutineItem': 'routine__room_id',
                    'RoutineVersion': 'routine__room_id', 'RoutineRun': 'item__routine__room_id',
                    'NoticeDismissal': 'member__room_id'}
        for model in apps.get_app_config('care').get_models():
            fields = {f.name for f in model._meta.fields}
            path = 'pk' if model is Room else 'room_id' if 'room' in fields else indirect.get(model.__name__)
            qs = model.objects.all()
            if path:
                qs = qs.exclude(**{path+'__in': IDS})
            rows = list(qs.order_by('pk').values())
            output[model.__name__] = hashlib.sha256(json.dumps(rows, default=str, sort_keys=True).encode()).hexdigest()
        return output

    before = other_rows()
    report = {'synthetic': True, 'day': str(today), 'database': str(db), 'backup': str(backup),
              'sources': SOURCES, 'rooms': [], 'validated': False}
    with transaction.atomic():
        for index, room_id in enumerate(IDS):
            room = Room.objects.get(pk=room_id)
            owner = room.members.get(role='owner')
            helper = room.members.get(user__username='demo_member')
            # Only the three explicitly selected synthetic rooms are replaced.
            NoticeDismissal.objects.filter(member__room=room).delete()
            ActionReceipt.objects.filter(room=room).delete()
            Routine.objects.filter(room=room).delete()
            Activity.objects.filter(room=room).delete()
            RecordPreset.objects.filter(room=room).delete()
            Product.objects.filter(room=room).delete()
            CareCadence.objects.filter(room=room).delete()
            room.audit.all().delete()
            owner.display_name = '지우'; owner.notices_enabled = True; owner.save()
            helper.display_name = '도윤'; helper.notices_enabled = True; helper.save()
            room.birth_date = [date(2022, 4, 18), date(2023, 5, 9), date(2024, 3, 12)][index]
            room.sex = ['male', 'female', ''][index]
            room.maturity = 'adult'
            room.subspecies = ['비숑 프리제', '코리안 쇼트헤어', '그린칙 코뉴어'][index]
            room.breed_key = ['dog-bichon-frise', 'custom', ''][index]
            room.size_origin = 'manual'; room.size = 'small' if index == 0 else ''
            room.save()
            enabled = [
                ['meal','treat','water','walk','toilet','play','training','weight','hygiene','cleaning','observation','veterinary','inventory','expense'],
                ['meal','treat','water','toilet','play','weight','hygiene','cleaning','observation','sleep','inventory','expense'],
                ['meal','treat','water','play','training','weight','cleaning','observation','sleep','environment','shedding','inventory','expense'],
            ][index]
            room.refresh_from_db()
            services.update_modules(owner, enabled, enabled+[k for k in MODULES if k not in enabled], room.settings_version, enabled[:5])
            room.module_versions.update(effective_at=datetime.combine(start-timedelta(days=1),time(),tz),
                                        settings={'enabled':enabled,'order':enabled,'pinned':enabled[:5]})
            # Preserve existing user grouping but exclude modules not used by this animal.
            for group in room.module_groups.all():
                group.keys = [k for k in group.keys if k in enabled]; group.save(update_fields=['keys'])
            products, planned, routines = {}, [], {}

            def event(key, day, clock, member=None, preset=None, routine=None, note='', **fields):
                at = datetime.combine(day, time.fromisoformat(clock), tz)
                end = at + timedelta(minutes=float(fields.get('minutes') or 0)) if key in ('walk','play','training','sleep') else at
                if fields.get('status','done') != 'planned' and max(at,end) > now:
                    return
                data = {'client_key':str(uuid.uuid4()), 'status':'done', 'occurred_at':at.isoformat(),
                        'performer':str((member or owner).pk), 'note':note, **fields}
                if preset:
                    data = {**preset.data, **data}
                    if preset.product_id: data['product'] = str(preset.product_id)
                if routine:
                    item = routines[routine].items.get(active=True)
                    data.update(routine_item=str(item.pk), routine_date=str(day))
                planned.append((at,key,member or owner,data,preset,routine))

            def product(key, name, category, stock, unit, minimum, brand='', notes='', source='', **fields):
                p = Product(room=room,name=name,category=category,stock_item=name,stock_unit=unit,brand=brand,
                            notes=notes,source=source or '시연용 가상 구매·사용 기록. 실제 제품 표시값 미입력.',**fields)
                p.full_clean();p.save();products[key]=p
                event('inventory',start,'06:00',item=name,movement='set',quantity=str(stock),unit=unit,minimum=str(minimum),note='기록을 시작하며 남아 있는 양 확인')
                return p

            def preset(key, module, title, **data):
                default = '' if room.record_presets.filter(module=module,is_default=True).exists() else 'on'
                form = presets.PresetForm({'title':title,'active':'on','is_default':default,**data},member=owner,module=module)
                assert form.is_valid(), (title,form.errors)
                return presets.save_preset(owner,module,form)

            def routine(key, module, title, clock, setting=None, who=None, weekdays=range(7), note=''):
                form = RoutineForm({'name':title,'at':clock,'weekdays':[str(d) for d in weekdays],
                    'assignee':str((who or owner).pk),'modules':[module],'active':'on','start_date':str(start),
                    'note':note,'preset':str(setting.pk) if setting else ''},room=room,visible_keys=enabled)
                assert form.is_valid(),(title,form.errors)
                r = services.save_routine(owner,form)
                r.versions.update(effective_on=start)
                routines[key]=r
                return r

            def cadence(module,title,interval,unit,offset,field='',value=''):
                c = CareCadence(room=room,module=module,title=title,interval=interval,unit=unit,
                                first_due=today+timedelta(days=offset),variant_field=field,variant_value=value,
                                configured_at=now)
                c.full_clean();c.save()

            def buy(key,day,amount,price):
                p=products[key]
                event('inventory',day,'18:05',item=p.name,movement='in',quantity=str(amount),unit=p.stock_unit,note='배송 도착, 보관함에 넣음')
                event('expense',day,'18:07',item=p.name+' 구매',amount=str(price),currency='KRW',category='food' if p.category in ('meal','treat') else 'supplies',note='배송비 포함 결제 금액')

            if index == 0:
                food=product('food','로얄캐닌 미니 어덜트','meal',1750,'g',450,'로얄캐닌','2kg 봉지 · 계량컵 대신 주방저울로 덜어 둠',SOURCES[0],protein=25,fat=14)
                snack=product('snack','동결건조 닭가슴살 큐브','treat',90,'g',20,notes='작게 나눠 산책과 기다려 연습 때 사용')
                product('bags','산책 배변봉투','supply',45,'piece',12,notes='현관 리드줄 옆 서랍')
                product('pads','배변패드 60×45cm','hygiene',65,'piece',15,notes='밤에는 베란다 앞에 한 장')
                product('toothpaste','반려견 효소 치약 닭고기향','hygiene',55,'g',10,notes='사용량을 재지 않아 매번 차감하지 않음')
                breakfast=preset('am','meal','아침 사료 55g',product=str(food.pk),quantity='55',unit='g')
                dinner=preset('pm','meal','저녁 사료 55g',product=str(food.pk),quantity='55',unit='g')
                water=preset('water','water','주방 물그릇 씻고 새 물',kind='replace',container='주방 옆 물그릇',unit='ml')
                teeth=preset('teeth','hygiene','잠들기 전 양치',task='teeth',note='입술을 살짝 들고 바깥쪽부터 짧게')
                brush=preset('brush','hygiene','겨드랑이·다리 빗질',task='brush')
                preset('treat','treat','닭가슴살 큐브 2g',product=str(snack.pk),quantity='2',unit='g',reason='산책 후 기다려 연습')
                preset('play','play','거실에서 터그놀이',kind='터그놀이')
                preset('training','training','현관에서 기다려',skill='현관에서 기다려')
                routine('water','water','아침 물그릇 교체','07:20',water)
                routine('am','meal','보리 아침밥 55g','07:30',breakfast)
                routine('walkam','walk','출근 전 동네 한 바퀴','07:55',who=helper,note='주말에는 조금 늦게 나가도 괜찮음')
                routine('pm','meal','보리 저녁밥 55g','18:40',dinner,who=helper)
                routine('walkpm','walk','저녁 공원 산책','19:30',note='횡단보도에서 기다렸다가 이동')
                routine('teeth','hygiene','자기 전 양치','21:20',teeth)
                routine('brush','hygiene','엉킨 털 풀어주기','20:45',brush,weekdays=[1,3,6])
                cadence('weight','일요일 아침 몸무게 재기',1,'weeks',1)
                cadence('cleaning','보리 방석 커버 세탁',2,'weeks',2,'target','bedding')
                cadence('hygiene','발톱 길이 확인',3,'weeks',5,'task','nail')
                buy('food',today-timedelta(days=12),2000,29800)
            elif index == 1:
                food=product('food','로얄캐닌 인도어','meal',1200,'g',350,'로얄캐닌','2kg 봉지 · 아침과 저녁에 나눠 계량',SOURCES[1],protein=27,fat=13,protein_kind='exact',fat_kind='exact')
                snack=product('snack','닭가슴살 동결건조 트릿','treat',40,'g',10,notes='놀이 후 손으로 잘게 부숴 줌')
                product('litter','무향 벤토나이트 모래','hygiene',3500,'g',700,notes='화장실 두 개에 보충 · 덜어 넣은 양만 기록')
                product('filter','급수기 교체 필터','supply',2,'piece',1,notes='서랍 오른쪽 칸 · 기존 필터와 규격 확인')
                am=preset('am','meal','아침 건사료 28g',product=str(food.pk),quantity='28',unit='g')
                pm=preset('pm','meal','저녁 건사료 28g',product=str(food.pk),quantity='28',unit='g')
                water=preset('water','water','급수기 세척·물 교체',kind='replace',container='거실 세라믹 급수기',unit='ml')
                litter=preset('clean','cleaning','화장실 두 곳 감자 치우기',target='toilet',task='모래 속 배변을 걷어내고 바닥에 튄 모래 쓸기')
                play=preset('play','play','깃털 낚싯대 놀이',kind='깃털 낚싯대')
                brush=preset('brush','hygiene','등·옆구리 빗질',task='brush')
                preset('treat','treat','놀이 후 닭가슴살 1g',product=str(snack.pk),quantity='1',unit='g',reason='낚싯대 놀이 마무리')
                routine('am','meal','나비 아침밥 28g','07:40',am)
                routine('water','water','급수기 물 갈기','07:45',water)
                routine('pm','meal','나비 저녁밥 28g','19:00',pm)
                routine('litter','cleaning','화장실 두 곳 치우기','21:00',litter)
                routine('play','play','자기 전 낚싯대 놀이','21:20',play)
                routine('brush','hygiene','나비 빗질','20:30',brush,weekdays=[0,2,5])
                cadence('weight','일요일 급여 전 체중 확인',1,'weeks',1)
                cadence('hygiene','앞발 발톱 길이 확인',2,'weeks',2,'task','nail')
                buy('food',today-timedelta(days=10),2000,32900)
            else:
                food=product('food','해리슨 어덜트 라이프타임 파인','meal',220,'g',60,'해리슨','기존에 먹던 펠렛 · 봉투를 밀봉해 보관',SOURCES[2])
                snack=product('snack','껍질 벗긴 기장','treat',40,'g',10,notes='짧은 손 타기 연습 때 조금씩 사용')
                product('paper','케이지 바닥용 무인쇄 종이','supply',35,'piece',10,notes='바닥 트레이 크기로 잘라 둔 종이')
                product('toy','종이 포징 장난감 리필','supply',4,'piece',1,notes='먹이 일부를 숨기는 종이컵 리필')
                pellet=preset('pellet','meal','아침 펠렛 10g',product=str(food.pk),quantity='10',unit='g')
                veg=preset('veg','meal','브로콜리·파프리카 잘게 썬 것 5g',food_name='브로콜리·파프리카',quantity='5',unit='g',note='점심에 소량 준비하고 남은 것은 치움')
                water=preset('water','water','스테인리스 물그릇 세척',kind='replace',container='케이지 안 물그릇',unit='ml')
                clean=preset('clean','cleaning','바닥 종이 교체',target='cage',task='종이 갈고 트레이의 먹이 부스러기 닦기',product=str(products['paper'].pk),used_quantity='1',used_unit='piece')
                play=preset('play','play','거실에서 케이지 밖 놀이',kind='횃대 이동·종이 뜯기')
                train=preset('train','training','손에 올라오기',skill='손에 올라오기')
                sleep=preset('sleep','sleep','밤잠 준비',kind='rest',note='거실 조명을 낮추고 조용히 쉬도록 준비')
                preset('treat','treat','훈련용 기장 0.3g',product=str(snack.pk),quantity='0.3',unit='g',reason='손에 올라오기 연습')
                routine('pellet','meal','초록이 아침 펠렛','08:00',pellet)
                routine('water','water','아침 물그릇 씻기','08:10',water)
                routine('veg','meal','점심 채소 준비','12:20',veg,who=helper)
                routine('play','play','케이지 밖에서 함께 놀기','17:00',play,who=helper,note='창문·현관을 닫고 사람 있는 동안만')
                routine('train','training','짧게 손 타기 연습','18:20',train,weekdays=[0,2,4])
                routine('clean','cleaning','바닥 종이 교체','19:00',clean)
                routine('sleep','sleep','조명 낮추고 밤잠 준비','20:30',sleep)
                cadence('weight','토요일 아침 횃대 저울 확인',1,'weeks',0)
                wash=preset('wash','cleaning','횃대·트레이 물세척',target='cage',task='횃대와 트레이를 씻고 충분히 말리기')
                routine('wash','cleaning','일요일 횃대·트레이 씻기','10:00',wash,weekdays=[6])
                buy('food',today-timedelta(days=13),454,25500)

            for n in range(28):
                day=start+timedelta(days=n); ago=(today-day).days; weekend=day.weekday()>=5
                def clock(h,m,jitter=7):
                    return (datetime.combine(day,time(h,m))+timedelta(minutes=rng.randint(-jitter,jitter))).strftime('%H:%M')
                if index==0:
                    event('water',day,clock(7,20),preset=water,routine='water')
                    for key,setting,h,m,actor in [('am',breakfast,7,30,owner),('pm',dinner,18,40,helper)]:
                        eaten=52 if ago in (6,18) and key=='am' else 55
                        event('meal',day,clock(h,m),member=actor,preset=setting,routine=key,consumed=str(eaten),note='조금 남겨서 그릇 비울 때 잰 양 제외' if eaten<55 else '')
                    for key,h,m,actor in [('walkam',9 if weekend else 7,10 if weekend else 55,helper),('walkpm',19,30,owner)]:
                        short=ago in (5,17) and key=='walkpm'
                        minutes=rng.randint(12,17) if short else rng.randint(28,43) if weekend else rng.randint(20,30)
                        distance=round(minutes*rng.uniform(.043,.057),2)
                        event('walk',day,clock(h,m),member=actor,routine=key,minutes=str(minutes),distance=str(distance),distance_unit='km',
                              note='비가 내려 아파트 주변만 짧게 돌아옴' if short else rng.choice(['하천길에서 냄새 맡으며 천천히','동네 공원 한 바퀴','그늘 쪽 산책로로 다녀옴','']))
                        if key=='walkam':
                            event('toilet',day,clock(h,m+3),member=actor,kind='stool',condition='usual',detail='산책 중 한 번, 평소와 비슷한 형태')
                        event('inventory',day,clock(h,m+4),item=products['bags'].name,movement='out',quantity='1',unit='piece')
                    if n==12:buy('bags',day,60,4900)
                    event('inventory',day,'22:15',item=products['pads'].name,movement='out',quantity='1',unit='piece',note='밤 패드 교체')
                    event('hygiene',day,clock(21,20),preset=teeth,routine='teeth',detail='앞니와 송곳니 바깥쪽부터 짧게 닦음')
                    if day.weekday() in (1,3,6):event('hygiene',day,clock(20,45),preset=brush,routine='brush',detail='겨드랑이에 뭉친 털을 나눠 빗음')
                    if n%3==0:
                        event('training',day,'20:05',skill='현관에서 기다려',minutes='4',attempts='5',successes=str(rng.choice([3,4,4,5])))
                        event('treat',day,'20:10',product=str(snack.pk),quantity='2',consumed='2',unit='g',reason='기다려 연습')
                    if n%3==1:event('play',day,'20:00',kind='터그놀이',minutes=str(rng.randint(7,12)),reaction='engaged')
                    if day.weekday()==6:event('weight',day,'07:10',weight=str(rng.choice(['6.28','6.31','6.30','6.26'])),unit='kg',note='아침밥 전, 안고 잰 값에서 내 체중을 뺌')
                    if ago in (16,2):event('cleaning',day,'11:30',target='bedding',task='방석 커버와 담요 세탁',note='속방석은 햇볕에 말림')
                    if ago==9:event('hygiene',day,'15:00',task='bath',detail='목욕 후 발가락 사이까지 말림')
                    notes={18:'아침밥을 조금 남겼다. 저녁에는 평소처럼 먹음.',5:'저녁에 비가 와서 산책을 짧게 끝냈다. 집에서 터그놀이로 마무리.',2:'새 방석 커버를 씌웠는데 바로 올라가서 누움.'}
                elif index==1:
                    for key,setting,h,m in [('am',am,7,40),('pm',pm,19,0)]:
                        # Dry food is left available; consumption is deliberately unknown.
                        event('meal',day,clock(h,m),preset=setting,routine=key,consumed='',note='그릇에 덜어둠. 나눠 먹어서 섭취량은 따로 재지 않음' if n%5==0 else '')
                    event('water',day,clock(7,45),preset=water,routine='water')
                    event('cleaning',day,clock(21,0),preset=litter,routine='litter',note='큰 화장실 소변 덩어리 2개, 작은 쪽 대변 1회' if n%4==0 else '')
                    if ago!=4:
                        event('play',day,clock(21,20),preset=play,routine='play',minutes=str(rng.randint(6,16)),reaction='little' if ago in (8,19) else 'engaged',note='처음엔 쳐다보기만 하다가 마지막에 두 번 뛰어옴' if ago in (8,19) else '')
                    else:
                        item=routines['play'].items.get(active=True)
                        RoutineRun.objects.create(item=item,date=day,skipped_at=datetime.combine(day,time(22),tz),skip_note='귀가가 늦어서 오늘은 쉬기')
                    if day.weekday() in (0,2,5):event('hygiene',day,clock(20,30),preset=brush,routine='brush',detail='등과 옆구리 위주로, 싫어하면 멈춤')
                    if n%3==0:event('treat',day,'21:40',product=str(snack.pk),quantity='1',consumed='1',unit='g',reason='낚싯대 놀이 마무리')
                    if day.weekday()==6:
                        event('weight',day,'07:15',weight=str(rng.choice(['4.18','4.20','4.22','4.19'])),unit='kg',note='같은 체중계로 아침 급여 전 측정')
                        event('inventory',day,'21:10',item=products['litter'].name,movement='out',quantity='800',unit='g',note='낮아진 곳에 모래 보충')
                    if n%4==0:event('toilet',day,'08:10',kind='stool',condition='usual',detail='화장실에서 직접 확인, 형태는 평소와 비슷함')
                    if weekend and ago>0:event('sleep',day,'14:00',kind='sleep',minutes=str(rng.randint(40,75)),quality='창가 쿠션에서 쉬는 동안 확인한 구간만 기록')
                    if ago==7:event('inventory',day,'10:20',item=products['filter'].name,movement='out',quantity='1',unit='piece',note='급수기 필터 교체')
                    notes={19:'새 깃털 장난감은 처음에 낯설어했다. 바닥에서 천천히 움직이니 조금 따라옴.',8:'택배 벨이 울린 뒤 한동안 숨었다가 저녁에 나와서 먹음.',4:'늦게 귀가해서 낚싯대 놀이는 건너뜀. 밥과 화장실은 확인함.',1:'소파 옆 스크래처를 창가로 옮겼더니 두 번 사용함.'}
                else:
                    event('meal',day,clock(8,0),preset=pellet,routine='pellet',consumed='',note='부스러기와 흘린 것이 있어 실제 섭취량은 미측정')
                    event('meal',day,clock(12,20),member=helper,preset=veg,routine='veg',consumed='',note='브로콜리와 파프리카를 잘게 썰어 줌. 남은 채소는 뒤에 치움')
                    event('water',day,clock(8,10),preset=water,routine='water')
                    if n%3==0:event('water',day,'13:30',kind='replace',container='케이지 안 물그릇',unit='ml',note='먹이 조각이 들어가 다시 갈아줌')
                    event('play',day,clock(17,0),member=helper,preset=play,routine='play',minutes=str(rng.randint(25,45)),reaction='engaged',note='창문 닫고 옆에서 지켜봄. 횃대와 테이블 사이를 오감')
                    if day.weekday() in (0,2,4):
                        event('training',day,clock(18,20),preset=train,routine='train',minutes=str(rng.randint(3,5)),attempts='5',successes=str(rng.choice([3,4,4,5])))
                        event('treat',day,'18:30',product=str(snack.pk),quantity='0.3',unit='g',consumed='',reason='손에 올라오기 연습 보상')
                    event('cleaning',day,clock(19,0),preset=clean,routine='clean')
                    event('sleep',day,clock(20,30),preset=sleep,routine='sleep',simple=True,routine_title='조명 낮추고 밤잠 준비',note='조명을 낮추고 TV 소리 줄임. 실제 수면시간은 재지 않음')
                    if day.weekday() in (2,5):event('weight',day,'07:50',weight=str(rng.choice(['71','72','72','73'])),unit='g',note='펠렛 주기 전 횃대 저울 영점 맞추고 측정')
                    if day.weekday()==6:event('cleaning',day,'10:05',preset=wash,routine='wash')
                    if n%2==0:
                        event('environment',day,'08:15',metric='temperature',value=str(rng.choice(['23.0','23.4','23.8','24.1'])),unit='C',location='케이지 옆 온습도계')
                        event('environment',day,'08:16',metric='humidity',value=str(rng.randint(44,53)),unit='percent',location='케이지 옆 온습도계')
                    if ago==10:
                        event('inventory',day,'17:05',item=products['toy'].name,movement='out',quantity='1',unit='piece',note='뜯어진 종이컵 교체')
                        event('shedding',day,'19:10',kind='feather',detail='바닥 종이 갈면서 작은 깃털 몇 개 확인',status='done')
                    notes={20:'오늘은 파프리카부터 집어 들었다. 채소는 조금 남겨서 치움.',10:'포징 종이컵을 다 뜯어 새것으로 교체. 다음에는 더 작게 접어 넣기.',3:'청소기 소리에 놀라서 바로 끄고 잠시 떨어져 기다렸다. 이후 횃대로 돌아옴.'}
                if ago in notes:event('observation',day,'22:20' if index<2 else '19:45',category='behavior',condition='different',detail=notes[ago])

            if index==0:
                event('veterinary',today+timedelta(days=6),'15:30',kind='checkup',clinic='다니던 동물병원',detail='예약한 정기검진. 최근 체중 기록 가져가기',status='planned',note='이동장과 진료수첩 전날 챙기기')
            for at,key,actor,data,setting,routine_key in sorted(planned,key=lambda row:row[0]):
                simple=data.pop('simple',False)
                form=RecordForm(data,room=room,module=key,member=actor,simple=simple)
                assert form.is_valid(),(room.name,key,at,form.errors)
                if setting:form.server_metadata['_preset']=presets.snapshot(setting)
                if routine_key:
                    form.server_metadata['_schedule']={'item_id':data['routine_item'],'date':data['routine_date'],'name':routines[routine_key].name}
                services.save_activity(actor,key,form)
            services.validate_inventory(room)
            assert not room.activities.filter(status='done',occurred_at__gt=now).exists()
            assert all('검수' not in (p.get('food_name') or '') for p in room.activities.values_list('payload',flat=True))
            for run in RoutineRun.objects.filter(item__routine__room=room).select_related('activity','item'):
                if run.activity:
                    assert run.activity.room_id==room.pk and run.activity.module==run.item.module
                    assert run.activity.occurred_at.astimezone(tz).date()==run.date
            # Every routine must be completable with its stored defaults, without fake measurements.
            for r in routines.values():
                item=r.items.get(active=True)
                data=presets.input_for(owner,item.module,item.preset,at=now)
                if item.module=='sleep':data['routine_title']=r.name
                form=RecordForm(data,room=room,module=item.module,member=owner,simple=True)
                assert form.is_valid(),('routine cannot complete',r.name,form.errors)
            stock=ledger(records_for(room),strict=True)
            AuditEvent.objects.create(room=room,actor=owner,action='demo.realistic_refresh',details={
                'synthetic':True,'backup':backup.name,'period_start':str(start),'period_end':str(today),
                'note':'사용자가 요청한 로컬 가상 예시 교체. 실제 보호자 이용이나 의료 권장량을 나타내지 않음.'})
            report['rooms'].append({'id':str(room.pk),'name':room.name,'records':room.activities.count(),
                'products':room.products.count(),'routines':room.routines.count(),'cadences':room.cadences.count(),
                'linked_completions':RoutineRun.objects.filter(item__routine__room=room,activity__isnull=False).count(),
                'stock':[{'item':x['name'],'balance':str(x['balance']),'unit':x['unit'],'low':x['low']} for x in stock['items']],
                'notices':[{'title':x.get('title'),'key':x.get('key')} for x in schedule7.notices(owner)]})
            print(json.dumps(report['rooms'][-1],ensure_ascii=False),flush=True)
        assert before==other_rows(), 'Unrelated app data changed; rolling back.'
        client=Client();client.force_login(owner.user)
        pages=[]
        for room_id in IDS:
            for suffix in ('','modules/','records/','overview/','routines/','supplies/','settings/'):
                url=f'/rooms/{room_id}/{suffix}'
                response=client.get(url)
                assert response.status_code==200,(url,response.status_code)
                pages.append(url)
        report.update(validated=True,unrelated_data_unchanged=True,pages_checked=len(pages))
    report_path=WORK/('applied.json' if args.apply else 'rehearsal.json')
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print('VALIDATED '+str(report_path),flush=True)


if __name__=='__main__':
    main()
