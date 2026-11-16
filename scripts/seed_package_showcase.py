"""Add realistic packaging to existing LOCAL fictional examples; never touch production.
Run without arguments to rehearse on a copy, then --apply once. No automatic startup seeding.
"""
from pathlib import Path
from datetime import datetime,timedelta,time
from zoneinfo import ZoneInfo
import argparse,os,sys,json,sqlite3,hashlib,uuid
ROOT=Path(__file__).resolve().parents[1]
LIVE=ROOT/'tmp/handoff/phase7/preview-data/demo.sqlite3'
WORK=ROOT/'tmp/handoff/package-showcase'
IDS=['024b42be-590a-4c19-a98c-2228429c11a3','537cec39-91b3-4757-ad35-18b59ec7cd00','b74c27c7-a65a-4427-b6bf-3c4b43f5dbaf']
# Known branded capacities follow the existing notes/receipts. Other capacities are fictional household examples.
PACKAGES=[
 ('로얄캐닌 미니 어덜트','봉지','2','kg'),('동결건조 닭가슴살 큐브','통','100','g'),
 ('산책 배변봉투','팩','20','piece'),('배변패드 60×45cm','팩','50','piece'),('반려견 효소 치약 닭고기향','튜브','75','g'),
 ('로얄캐닌 인도어','봉지','2','kg'),('닭가슴살 동결건조 트릿','통','50','g'),
 ('무향 벤토나이트 모래','봉지','5','kg'),('급수기 교체 필터','상자','4','piece'),
 ('해리슨 어덜트 라이프타임 파인','봉지','454','g'),('껍질 벗긴 기장','봉지','100','g'),
 ('케이지 바닥용 무인쇄 종이','팩','50','piece'),('종이 포징 장난감 리필','팩','6','piece')]
NAMESPACE=uuid.UUID('424f5bbd-ff04-4911-b2f6-266a15536938')

def main():
 args=argparse.ArgumentParser(description=__doc__);args.add_argument('--apply',action='store_true');apply=args.parse_args().apply
 WORK.mkdir(exist_ok=True,parents=True)
 if apply:
  report=json.loads((WORK/'rehearsal.json').read_text(encoding='utf8'));assert report['validated']
 backup=ROOT/'backups'/('before-package-examples-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3');backup.parent.mkdir(exist_ok=True)
 with sqlite3.connect(LIVE.as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(backup) as b:a.backup(b)
 if apply:db=LIVE
 else:
  folder=WORK/('rehearsal-'+uuid.uuid4().hex[:8]);folder.mkdir();db=folder/'demo.sqlite3'
  with sqlite3.connect(backup) as a,sqlite3.connect(db) as b:a.backup(b)
 os.environ.update(DJANGO_SETTINGS_MODULE='config.settings',CARE_DATA_DIR=str(db.parent),CARE_DEMO_MODE='1',DJANGO_DEBUG='1',POSTGRES_HOST='',GOOGLE_CLIENT_ID='',GOOGLE_CLIENT_SECRET='')
 sys.path.insert(0,str(ROOT))
 import django;django.setup()
 from django.conf import settings
 from django.db import transaction,connection
 from django.apps import apps
 from django.utils import timezone
 from care.models import Room,Product,Activity,AuditEvent
 from care.supplies import SupplyForm,save_supply,stock_view
 from care.forms import RecordForm
 from care.demo_fixtures import record_data
 from care import services
 from care.inventory_ledger import validate
 assert settings.DEMO_MODE and Path(connection.settings_dict['NAME']).resolve()==db.resolve()
 assert set(IDS)==set(json.loads((ROOT/'tmp/handoff/phase7/demo-navigation.json').read_text())['showcase'])
 assert Room.objects.filter(pk__in=IDS,owner__username='demo_owner').count()==3
 assert not AuditEvent.objects.filter(room_id__in=IDS,action='demo.package_examples').exists(),'Examples already applied; existing edits preserved.'
 def snapshot():
  result={}
  for model in apps.get_app_config('care').get_models():
   result[model.__name__]={str(row[model._meta.pk.attname]):row for row in model.objects.values()}
  return result
 before=snapshot();changed=[];created=[];receipts=[]
 owners={rid:Room.objects.get(pk=rid).members.get(role='owner') for rid in IDS}
 with transaction.atomic():
  for rid,name in zip(IDS,['강아지','고양이','앵무새']):
   member=owners[rid];member.room.name=name
   Room.objects.filter(pk=rid).update(name=name)
  for name,label,amount,unit in PACKAGES:
   p=Product.objects.get(room_id__in=IDS,name=name);member=owners[str(p.room_id)]
   data={k:getattr(p,k) if getattr(p,k) is not None else '' for k in SupplyForm.Meta.fields}
   data.update(track_stock='on',version=p.version,request_token=str(uuid.uuid5(NAMESPACE,'product-'+str(p.pk))),package_label=label,package_amount=amount,package_unit=unit)
   f=SupplyForm(data,member=member,instance=p);assert f.is_valid(),(name,f.errors)
   save_supply(member,f,p);changed.append(str(p.pk))
  # One unopened spare bottle provides a real count + nominal capacity example.
  member=owners[IDS[0]]
  data=dict(name='반려견 저자극 샴푸',brand='',category='hygiene',notes='욕실 수납장 · 현재 쓰는 샴푸를 다 쓰면 꺼낼 미개봉 예비 한 병',package_label='병',package_amount='500',package_unit='ml',track_stock='on',stock_item='반려견 저자극 샴푸',stock_unit='piece',active='on',request_token=str(uuid.uuid5(NAMESPACE,'shampoo')))
  f=SupplyForm(data,member=member);assert f.is_valid(),f.errors
  saved=save_supply(member,f);shampoo=Product.objects.get(pk=saved['id']);created.append(str(shampoo.pk))
  tz=ZoneInfo('Asia/Seoul');today=timezone.now().astimezone(tz).date()
  events=[
   (IDS[0],'로얄캐닌 미니 어덜트','in','1','package',2,'18:20','장 보면서 다음 봉지 한 개를 사서 수납장에 넣음'),
   (IDS[0],'반려견 저자극 샴푸','in','1','package',2,'18:25','현재 쓰는 제품을 다 쓰면 개봉할 예비 한 병'),
   (IDS[1],'무향 벤토나이트 모래','in','1','package',2,'17:40','모래가 얼마 안 남아 5kg 한 봉지 보충'),
   (IDS[1],'무향 벤토나이트 모래','out','800','g',1,'20:30','화장실 두 곳에 400g씩 보충'),
   (IDS[2],'케이지 바닥용 무인쇄 종이','in','1','package',2,'15:10','트레이에 맞춰 자른 종이 50장 한 묶음을 보관함'),
   (IDS[2],'케이지 바닥용 무인쇄 종이','out','2','piece',1,'08:15','아침 청소 때 바닥 종이 두 장 교체')]
  for index,(rid,name,movement,qty,unit,days,clock,note) in enumerate(events):
   p=Product.objects.get(room_id=rid,name=name);at=datetime.combine(today-timedelta(days=days),time.fromisoformat(clock),tz)
   data=record_data('inventory',item=p.stock_item,product=str(p.pk),movement=movement,quantity=qty,unit=unit,minimum='',note=note,occurred_at=at.isoformat(),client_key=str(uuid.uuid5(NAMESPACE,'receipt-'+str(index))),package_version=p.version)
   f=RecordForm(data,room=p.room,module='inventory',member=owners[rid]);assert f.is_valid(),(name,f.errors)
   row,_=services.save_activity(owners[rid],'inventory',f);receipts.append(str(row.pk))
  for rid,member in owners.items():
   validate(member.room)
   services.bump(member.room,member,'demo.package_examples',{'synthetic':True,'scope':'local showcase only','package_example_version':1})
  after=snapshot()
  mutable={'Product':{'package_label','package_amount','package_unit','version','updated_at'},'Room':{'revision','name'}}
  for model,rows in before.items():
   for pk,old in rows.items():
    assert pk in after[model],('removed',model,pk)
    allowed=mutable.get(model,set()) if (model=='Product' and pk in changed or model=='Room' and pk in IDS) else set()
    assert all(old[k]==after[model][pk][k] for k in old if k not in allowed),('existing data changed',model,pk)
  report={'validated':True,'synthetic':True,'database':str(db),'backup':str(backup),'products_enriched':len(changed),'new_products':created,'new_inventory_records':receipts,'existing_records_preserved':True,'rooms':[]}
  for rid,member in owners.items():
   stock=stock_view(member)
   assert all(item['complete'] for item in stock['items'])
   report['rooms'].append({'id':rid,'name':member.room.name,'stock':[{'name':x['name'],'balance':str(x['balance']),'unit':x['unit'],'context':x['context']} for x in stock['items']]})
 (WORK/('applied.json' if apply else 'rehearsal.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf8')
 print(json.dumps({'applied':apply,'enriched_products':len(changed),'new_products':len(created),'inventory_records':len(receipts),'existing_records_preserved':True,'backup':str(backup)},ensure_ascii=False))

if __name__=='__main__':main()