"""Read-only, purpose-specific summaries shared by Today and the care board."""
from collections import defaultdict
from datetime import timedelta
from urllib.parse import urlencode

from django.db.models import F, Q, Subquery, Window
from django.db.models.functions import RowNumber
from django.urls import reverse
from django.utils import timezone

from .models import Activity
from .modules import record_summary, compact, decimal
from .services import local_date, visible_modules
from .reporting.stats import bounds, get_statistics, label_choice


def board_records(member, day=None, *, module=None):
    """Load the histories that can affect today's board and its recent records.

    Sleep intervals can cross midnight and weight uses the last measured BCS.
    Inventory visibility depends on historical private supply links, so keep its
    history until visibility has been checked. Other modules need today's rows,
    active timers, their last completion and the latest six visible candidates.
    Full reports and inventory balances use their own complete history queries.
    """
    from .schedule import logical_module
    from .supplies import filter_rows

    day = day or local_date(member.room)
    allowed = set(visible_modules(member))
    if module is not None:
        allowed = {key for key in allowed if logical_module(key) == logical_module(module)}
    base = Activity.objects.filter(
        room_id=member.room_id, module__in=allowed, voided=False, occurred_at__lte=timezone.now(),
    )
    retained = ("weight", "sleep", "inventory")
    other = base.exclude(module__in=retained)
    recent = other.order_by("-occurred_at", "-created_at", "-id").values("pk")[:6]
    latest = (
        other.filter(status="done").order_by()
        .annotate(board_rank=Window(
            expression=RowNumber(), partition_by=[F("module")],
            order_by=[F("occurred_at").desc(), F("created_at").desc(), F("id").desc()],
        ))
        .filter(board_rank=1).values("pk")
    )
    lower, _ = bounds(member.room, day, day)
    needed = (
        Q(occurred_at__gte=lower) | Q(status="active") | Q(module__in=retained)
        | Q(pk__in=Subquery(recent)) | Q(pk__in=Subquery(latest))
    )
    return filter_rows(
        member,
        base.filter(needed).select_related("author", "performer").order_by("-occurred_at", "-created_at", "-id"),
    )


def today_tiles(member, cards, records, day=None):
    """A value is never inferred from a completed-but-unmeasured event."""
    day = day or local_date(member.room)
    allowed = set(visible_modules(member))
    grouped = defaultdict(list)
    now = timezone.now()
    for record in records:
        if (record.room_id == member.room_id and record.module in allowed and not record.voided
                and record.occurred_at <= now and (not record.ended_at or record.ended_at <= now)):
            grouped[record.module].append(record)
    lower, upper = bounds(member.room, day, day)
    enriched = []
    for card in cards:
        key = card['spec'].key
        if key not in allowed:
            continue
        all_rows = grouped[key]
        rows = [a for a in all_rows if a.status == 'done' and lower <= a.occurred_at < upper]
        tile = {'value':'—','unit':'','caption':'오늘 완료한 기록이 없어요','empty':True,'secondary':[]}
        stats = get_statistics(member.room,key,day,day,all_records=all_rows,member=member)
        if stats['has_data']:
            tile.update(value=stats['primary'],_raw_value=stats.get('_primary_value',stats['primary']),unit='건' if stats['primary_unit']=='회' and key not in ('medication',) else stats['primary_unit'],caption='오늘 · '+stats['primary_label'],empty=False)
        if key == 'weight' and stats.get('measurement_date'):
            measured=stats['measurement_date'];tile['caption']='오늘 마지막 측정' if measured==day else measured.strftime('%m.%d 마지막 측정')
        if key in ('meal','treat') and rows:
            basis=stats.get('nutrition_basis')
            if basis=='mixed':
                tile.update(value='기준별 표시',_raw_value='기준별 표시',unit='',caption='먹은 양과 준 양의 열량은 합치지 않아요')
                for metric in stats['metrics']:
                    if metric['label'] in ('섭취량 확인분 열량','제공량 기준 예상 열량') and metric['value']!='미기록':
                        tile['secondary'].append({'label':'섭취 확인' if metric['label'].startswith('섭취') else '제공량 예상','value':metric['value'],'_raw_value':metric.get('_raw_value',metric['value']),'unit':metric['unit']})
            missing=sum(a.nutrition.get('kcal') is None for a in rows)
            if missing and basis and basis!='mixed':tile['caption']+=f' · 열량 미확인 {missing}건'
        elif key in ('walk','play','training'):
            missing=sum(a.payload.get('minutes') is None for a in rows)
            if rows and stats['primary_unit']!='분':tile.update(value=str(len(rows)),_raw_value=len(rows),unit='건',caption='오늘 활동 기록 · 시간 미입력',empty=False)
            elif missing:tile['caption']+=f' · 시간 미입력 {missing}건'
            if key=='walk':
                measured=[a for a in rows if a.payload.get('distance') is not None and a.payload.get('distance_unit') in ('m','km')]
                value=sum((decimal(a.payload['distance'])/(1000 if a.payload['distance_unit']=='m' else 1) for a in measured),decimal(0)) if measured else None
                tile['secondary'].append({'label':'걸은 거리' if len(measured)==len(rows) else '입력한 거리만','value':compact(value) if value is not None else '—','_raw_value':value,'unit':'km','note':f'거리 미기록 {len(rows)-len(measured)}건' if rows and len(measured)!=len(rows) else '거리 미기록' if not measured else ''})
            elif key=='training' and rows:
                tracked=[a for a in rows if a.payload.get('attempts') is not None and a.payload.get('successes') is not None]
                skills={a.payload.get('skill','') for a in tracked}
                if len(skills)==1:
                    attempted=sum(decimal(a.payload['attempts']) for a in tracked);success=sum(decimal(a.payload['successes']) for a in tracked)
                    tile['secondary'].append({'label':next(iter(skills))+' · 성공/시도','value':compact(success)+'/'+compact(attempted),'unit':'회'})
        elif key=='water' and rows and stats['primary_unit']!='ml':
            tile['caption']='오늘 물 교체·보충 · 음수량과 별개'
        elif key=='medication' and rows:
            skipped=sum(a.payload.get('result')=='skipped' for a in rows);tile['caption']=f'오늘 실제 투약 · 못한 기록 {skipped}건'
        elif key=='observation' and rows:
            different=sum(a.payload.get('condition')=='different' for a in rows);tile['caption']=f'오늘 관찰 · 평소와 다름 {different}건'
        elif key=='environment' and rows:
            last=max(rows,key=lambda a:(a.occurred_at,a.created_at,str(a.pk)));tile['caption']=label_choice(key,'metric',last.payload.get('metric'))+' · '+last.payload.get('location','생활공간')
            tile['value']=compact(last.payload.get('value'));tile['_raw_value']=last.payload.get('value');tile['unit']=label_choice(key,'unit',last.payload.get('unit'))
        elif key=='inventory' and stats['breakdown']:
            items=stats['breakdown'];tile['caption']='기록을 이어 계산한 현재 잔량'
            if len(items)==1:tile.update(value=items[0]['value'],_raw_value=items[0].get('_raw_value',items[0]['value']),unit=items[0]['unit'],caption=items[0]['label']+' · '+items[0]['note'])
        elif key=='shedding':
            active=[a for a in all_rows if a.status=='active' and a.occurred_at < upper]
            if active:tile.update(value=str(len(active)),_raw_value=len(active),unit='건',caption='진행 중 · 종료 기록을 남길 수 있어요',empty=False)
        from .reporting.display_values import decorate_tile
        decorate_tile(tile,key)
        enriched.append({**card,'tile':tile})
    return enriched


def latest_module_records(member):
    allowed=visible_modules(member)
    rows=(Activity.objects.filter(room_id=member.room_id,module__in=allowed,voided=False,status='done',occurred_at__lte=timezone.now())
        .order_by().annotate(position_in_module=Window(expression=RowNumber(),partition_by=[F('module')],order_by=[F('occurred_at').desc(),F('created_at').desc(),F('id').desc()])).filter(position_in_module=1))
    return {a.module:{'text':record_summary(a.module,a.payload),'at':a.occurred_at} for a in rows}


def statistics_periods(room,module,selected_start,selected_end):
    today=local_date(room);root=reverse('statistics',args=[room.pk,module]);periods=[]
    for days,label in [(7,'최근 7일'),(30,'최근 30일'),(90,'최근 90일')]:
        start=today-timedelta(days=days-1)
        periods.append({'label':label,'url':root+'?'+urlencode({'start':start.isoformat(),'end':today.isoformat()}),'selected':selected_start==start and selected_end==today})
    return periods


def module_board_tiles(member,rows):
    day=local_date(member.room)
    records=board_records(member,day)
    return {card['spec'].key:card['tile'] for card in today_tiles(member,rows,records,day)}
