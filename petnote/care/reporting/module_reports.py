"""Typed, descriptive statistics over authorized records.

Every number has a unit, time window, denominator and evidence IDs. No missing
measurement becomes zero. No app score, causal effect, diagnosis or treatment is
inferred. Observed-day averages describe the user's records, not all-day sensing.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import hashlib

from django.urls import reverse
from django.utils import timezone

from .. import services
from ..breeds import reference_for
from .comparisons import reference_geometry
from ..inventory_ledger import ledger, period_movement, display_quantity
from ..models import Activity
from ..modules import MODULES, SELECTABLE_MODULES, RETIRED_MODULES, compact
from ..schedule import completion_summary, logical_module
from .stats import bounds, union_seconds

ZERO = Decimal(0)
MASS = {'g':Decimal(1),'kg':Decimal(1000),'oz':Decimal('28.349523125')}
WEIGHT = {**MASS,'lb':Decimal('0.45359237')*1000}


@dataclass(frozen=True)
class Definition:
    key: str
    module: str
    name: str
    unit: str
    aggregation: str = 'mean'
    delta_mode: str = 'relative'
    basis: str = '값을 남긴 날의 기록일 평균'
    description: str = ''


def value(raw,*,negative=False):
    try:d=Decimal(str(raw))
    except (InvalidOperation,TypeError,ValueError):return None
    return d if d.is_finite() and (negative or d>=0) else None


def text(number):
    return compact(number) if number is not None else '—'


def signed(number):
    return ('+' if number>0 else '−' if number<0 else '')+compact(abs(number))


def differences(before,recent,mode='relative'):
    if before is None or recent is None:
        return {'available':False,'delta':None,'percent':None,'text':'비교할 기록이 더 필요해요','percent_text':'','direction':'missing'}
    delta=recent-before
    percentage=delta/abs(before)*100 if before!=0 and mode=='relative' else None
    ptext=(signed(percentage if percentage and abs(percentage)<Decimal('.1') else percentage.quantize(Decimal('.1')))+'%') if percentage is not None else '이전 값이 0이라 비율은 계산하지 않아요' if mode=='relative' and before==0 else ''
    return {'available':True,'delta':delta,'percent':percentage,'text':signed(delta),'percent_text':ptext,
            'direction':'up' if delta>0 else 'down' if delta<0 else 'same','unit_override':'%p' if mode=='points' else None}


def chronology(row):return row.occurred_at,row.created_at,str(row.pk)


def row_date(row,tz):return row.occurred_at.astimezone(tz).date()


def eligible(records,room_id,allowed,now):
    unique={str(a.pk):a for a in records if a.room_id==room_id and a.module in allowed and not a.voided and a.status=='done'
            and a.occurred_at<=now and (not a.ended_at or a.ended_at<=now)}
    return sorted(unique.values(),key=chronology)


def daily_numeric(rows,extractor,tz,*,reducer='sum'):
    grouped=defaultdict(list)
    for row in rows:grouped[row_date(row,tz)].append(row)
    points=[];partial=[];missing=[]
    for day,items in sorted(grouped.items()):
        values=[(a,extractor(a)) for a in items]
        known=[(a,n) for a,n in values if n is not None]
        missing.extend(str(a.pk) for a,n in values if n is None)
        if reducer=='last':
            if known:
                a,n=known[-1];points.append({'date':day,'value':n,'ids':[str(a.pk)],'count':1})
            else:partial.append(day)
        elif len(known)==len(values):
            number=sum((n for _,n in known),ZERO)
            if reducer=='mean':number/=len(known)
            points.append({'date':day,'value':number,'ids':[str(a.pk) for a,_ in known],'count':len(known)})
        else:partial.append(day)
    return {'points':points,'partial_days':partial,'missing_ids':missing,'record_days':set(grouped)}


def count_series(rows,tz):
    grouped=defaultdict(list)
    for a in rows:grouped[row_date(a,tz)].append(a)
    return {'points':[{'date':d,'value':Decimal(len(items)),'count':len(items),'ids':[str(a.pk) for a in items]} for d,items in sorted(grouped.items())],
            'partial_days':[],'missing_ids':[],'record_days':set(grouped)}


def sleep_series(rows,tz,lower,upper,now):
    intervals=defaultdict(list);ids=defaultdict(set);missing=[]
    for row in rows:
        if row.payload.get('_entry_kind')=='schedule':continue
        minutes=value(row.payload.get('minutes'))
        if minutes is None:
            missing.append(str(row.pk));continue
        begin=row.occurred_at.timestamp();finish=begin+float(minutes)*60
        # Completed observation may not extend into the future; don't clip it into a false completion.
        if finish>now.timestamp():missing.append(str(row.pk));continue
        begin=max(begin,lower.timestamp());finish=min(finish,upper.timestamp())
        if finish<=begin:continue
        day=datetime.fromtimestamp(begin,tz).date()
        while datetime.combine(day,time.min,tz).timestamp()<finish:
            left=datetime.combine(day,time.min,tz).timestamp();right=datetime.combine(day+timedelta(days=1),time.min,tz).timestamp()
            a,b=max(left,begin),min(right,finish)
            if b>a:intervals[day].append((a,b));ids[day].add(str(row.pk))
            day+=timedelta(days=1)
    return {'points':[{'date':d,'value':union_seconds(values)/60,'ids':sorted(ids[d]),'count':len(ids[d])} for d,values in sorted(intervals.items())],
            'partial_days':[],'missing_ids':missing,'record_days':set(intervals)}


def metric(definition,series,start,end,days,root):
    previous_start=start-timedelta(days=days);previous_end=start-timedelta(days=1)
    def period(lo,hi):
        pts=[p for p in series['points'] if lo<=p['date']<=hi]
        total=sum((p['value'] for p in pts),ZERO) if pts else None
        if definition.aggregation=='sum':number=total
        elif definition.aggregation=='latest':number=pts[-1]['value'] if pts else None
        else:number=total/len(pts) if pts else None
        query={'module':definition.module,'state':'done','start':lo.isoformat(),'end':hi.isoformat()}
        return {'start':lo,'end':hi,'value':number,'text':text(number),'total':total,'total_text':text(total),
                'observed_days':len(pts),'recorded_days':sum(lo<=d<=hi for d in series['record_days']),
                'records':sum(p['count'] for p in pts),'partial_days':sum(lo<=d<=hi for d in series['partial_days']),
                'values':pts,'ids':list(dict.fromkeys(i for p in pts for i in p['ids'])),'url':root+'?'+urlencode(query)}
    before,recent=period(previous_start,previous_end),period(start,end)
    change=differences(before['value'],recent['value'],definition.delta_mode)
    if change['available'] and change['delta'] and before['text']==recent['text']:
        precision=max(3,min(12,1-abs(change['delta']).adjusted()))
        for p in (before,recent):p['text']=format(p['value'].quantize(Decimal(1).scaleb(-precision)),'f').rstrip('0').rstrip('.')
    low_sample=min(before['observed_days'],recent['observed_days'])<3
    note=definition.description
    if definition.aggregation=='mean':note+=' 기록하지 않은 날은 0으로 채우지 않고 값이 유효한 날끼리 평균을 비교해요.'
    if low_sample and change['available']:note+=' 기록일이 적어 한 번의 값에도 차이가 크게 달라질 수 있어요.'
    if before['partial_days'] or recent['partial_days']:note+=' 수치가 빠진 기록일은 해당 평균에서 제외했어요.'
    difference_days=abs(before['observed_days']-recent['observed_days'])
    if difference_days>days/2:note+=' 두 기간의 기록일 수가 많이 달라요.'
    chosen=[p for p in series['points'] if previous_start<=p['date']<=end]
    return {'key':definition.key,'module':definition.module,'name':definition.name,'unit':definition.unit,'basis':definition.basis,
            'aggregation':definition.aggregation,'show_total':definition.module not in ('weight','environment') and definition.aggregation=='mean','before':before,'recent':recent,'change':change,'note':note.strip(),
            'low_sample':low_sample,'has_any':bool(chosen),'values':[{**p,'text':text(p['value']),'recent':p['date']>=start} for p in reversed(chosen)],
            'evidence_url':root+'?'+urlencode({'module':definition.module,'state':'done','start':previous_start.isoformat(),'end':end.isoformat()}),
            'quality':{'missing_records':len(series['missing_ids']),'partial_days':before['partial_days']+recent['partial_days']},
            'definition':definition}


def mass_amount(row,*,consumed=False):
    p=row.payload;amount=value(p.get('consumed' if consumed else 'quantity'))
    if amount is None:return None
    unit=p.get('unit');factor=MASS.get(unit)
    if factor:return amount*factor
    if unit=='piece':
        grams=value((row.nutrition.get('snapshot') or {}).get('grams_per_piece'))
        if grams and grams>0:return amount*grams
    return None


def food_energy(row,*,consumed=False):
    p=row.payload
    if consumed:
        return value(row.nutrition.get('kcal')) if row.nutrition.get('basis')=='consumed' else None
    if value(p.get('quantity')) is None:return None
    snapshot=row.nutrition.get('snapshot')
    if not snapshot:return None
    return value(services.nutrition_values({**p,'consumed':None},snapshot).get('kcal'))


def build_measurements(room,rows,enabled,start,end,days,now):
    tz=ZoneInfo(room.timezone);previous_start=start-timedelta(days=days);lower,upper=bounds(room,previous_start,end)
    root=reverse('records',args=[room.pk]);result=[]
    selected=[a for a in rows if lower<=a.occurred_at<upper and (not a.ended_at or a.ended_at<upper)]
    by_module=defaultdict(list)
    for row in selected:by_module[logical_module(row.module)].append(row)
    def add(defn,data):
        if defn.module in enabled:result.append(metric(defn,data,start,end,days,root))
    if 'weight' in enabled:
        def weight(a):
            n=value(a.payload.get('weight'));factor=WEIGHT.get(a.payload.get('unit'))
            return n*factor/1000 if n and factor else None
        add(Definition('weight','weight','측정일 평균 체중','kg',description='하루 여러 번 측정한 경우 그날 마지막 유효 측정만 사용해요.'),daily_numeric(by_module['weight'],weight,tz,reducer='last'))
    if 'walk' in enabled:
        walk=by_module['walk']
        add(Definition('walk_minutes','walk','산책 시간','분',description='시간을 모두 적은 날의 산책 기록 합계예요. 하루 전체 활동량은 아니에요.'),daily_numeric(walk,lambda a:value(a.payload.get('minutes')),tz))
        def distance(a):
            if a.payload.get('_gps') and not a.payload['_gps'].get('complete'):
                return None
            d=value(a.payload.get('distance'));unit=a.payload.get('distance_unit')
            return d/(1000 if unit=='m' else 1) if d is not None and unit in ('m','km') else None
        add(Definition('walk_distance','walk','산책 거리','km',description='거리 미입력이나 GPS 중단이 있는 날은 완전한 거리 비교에서 제외해요.'),daily_numeric(walk,distance,tz))
    for module in ('meal','treat'):
        if module not in enabled:continue
        data=by_module[module]
        for key,name,unit,extract in [
            ('provided_g','제공량','g',lambda a:mass_amount(a)),('consumed_g','확인한 섭취량','g',lambda a:mass_amount(a,consumed=True)),
            ('provided_kcal','제공량 기준 예상 열량','kcal',lambda a:food_energy(a)),('consumed_kcal','섭취량 확인분 열량','kcal',lambda a:food_energy(a,consumed=True))]:
            series=daily_numeric(data,extract,tz)
            if series['points'] or key=='provided_g':
                add(Definition(module+'_'+key,module,name,unit,description='서로 같은 단위와 제공/섭취 기준만 사용해요. 실제 하루 전체 섭취를 모두 확인한 것은 아니에요.'),series)
    if 'water' in enabled:
        measured=[a for a in by_module['water'] if a.payload.get('kind')=='measured']
        def water(a):
            n=value(a.payload.get('amount'));u=a.payload.get('unit')
            return n*(1000 if u=='l' else 1) if n is not None and u in ('ml','l') else None
        add(Definition('water_measured','water','실제로 측정한 음수량','ml',description='물 교체·보충량은 포함하지 않아요. 측정해서 남긴 기록만 비교해요.'),daily_numeric(measured,water,tz))
    for module in ('play','training'):
        if module not in enabled:continue
        add(Definition(module+'_minutes',module,'기록한 활동 시간','분',description='종류별 기록을 함께 확인해 주세요. 기록 방식이나 활동 구성이 달라지면 단순 비교에 주의가 필요해요.'),daily_numeric(by_module[module],lambda a:value(a.payload.get('minutes')),tz))
    if 'sleep' in enabled:
        add(Definition('sleep_observed','sleep','직접 관찰한 수면·휴식','분',description='겹친 구간은 한 번만, 자정은 날짜별로 나눠요. 알람 확인이나 계획한 시간은 제외해요.'),sleep_series([a for a in rows if a.module=='sleep'],tz,lower,upper,now))
    if 'environment' in enabled:
        grouped=defaultdict(list)
        for a in by_module['environment']:grouped[(a.payload.get('metric'),a.payload.get('location') or '생활공간')].append(a)
        for (kind,location),values in sorted(grouped.items(),key=lambda x:str(x[0])):
            if kind not in ('temperature','humidity','light','ph'):continue
            name,unit={'temperature':('온도','°C'),'humidity':('습도','%'),'light':('조명 시간','시간'),'ph':('수질 pH','pH')}[kind]
            def read(a,kind=kind):
                n=value(a.payload.get('value'),negative=kind=='temperature');u=a.payload.get('unit')
                if n is None:return None
                if kind=='temperature':return (n-32)*Decimal(5)/9 if u=='F' else n if u=='C' else None
                return n if u=={'humidity':'percent','light':'hours','ph':'ph'}.get(kind) else None
            id_=hashlib.sha256((str(kind)+'\0'+location).encode()).hexdigest()[:12]
            series=daily_numeric(values,read,tz,reducer='last' if kind=='ph' else 'mean')
            add(Definition('environment_'+id_,'environment',name+' · '+location,unit,
                aggregation='latest' if kind=='ph' else 'mean',delta_mode='points' if kind=='humidity' else 'absolute',
                basis='각 기간의 마지막 측정' if kind=='ph' else '측정일 평균 · 같은 위치',
                description='pH는 백분율 증감이나 다른 위치의 평균으로 판정하지 않아요.' if kind=='ph' else '같은 위치·항목의 값만 비교해요. 종별 적정 환경을 자동 판정하지 않아요.'),series)
    return result,by_module


def training_rates(rows,start,end,days,room):
    tz=ZoneInfo(room.timezone);groups=defaultdict(list);old_start=start-timedelta(days=days)
    for a in rows:
        if a.module!='training' or not old_start<=row_date(a,tz)<=end:continue
        skill=' '.join(str(a.payload.get('skill') or '').split())
        if skill:groups[skill].append(a)
    result=[]
    for skill,items in sorted(groups.items()):
        def period(lo,hi):
            chosen=[a for a in items if lo<=row_date(a,tz)<=hi];known=[]
            for a in chosen:
                attempts=value(a.payload.get('attempts'));successes=value(a.payload.get('successes'))
                if attempts and successes is not None and successes<=attempts:known.append((a,attempts,successes))
            attempts=sum((n for _,n,_ in known),ZERO);successes=sum((n for _,_,n in known),ZERO)
            return {'attempts':text(attempts),'successes':text(successes),'value':successes/attempts*100 if attempts else None,
                    'text':text(successes/attempts*100) if attempts else '—','records':len(known),'unknown':len(chosen)-len(known),
                    'ids':[str(a.pk) for a,_,_ in known]}
        before=period(old_start,start-timedelta(days=1));recent=period(start,end)
        result.append({'skill':skill,'before':before,'recent':recent,'change':differences(before['value'],recent['value'],'points')})
    return result


def counted_modules(room,rows,enabled,start,end,days):
    tz=ZoneInfo(room.timezone);previous_start=start-timedelta(days=days);groups=defaultdict(list)
    for a in rows:
        key=logical_module(a.module)
        if key in enabled and previous_start<=row_date(a,tz)<=end:groups[key].append(a)
    out=[]
    names={'water':'교체·보충','toilet':'배변 관찰','shedding':'탈피·털갈이 관찰','hygiene':'관리 완료','cleaning':'청소 완료',
           'medication':'알림·투약 기록','veterinary':'방문·접종 완료','observation':'특이사항 메모','sleep':'수면·휴식 알림 확인'}
    for key,label in names.items():
        if key not in enabled:continue
        items=groups[key]
        if key=='water':items=[a for a in items if a.payload.get('kind') in ('replace','refill')]
        if key=='sleep':items=[a for a in items if a.payload.get('_entry_kind')=='schedule']
        before=[a for a in items if row_date(a,tz)<start];recent=[a for a in items if row_date(a,tz)>=start]
        selector={'water':'kind','toilet':'kind','shedding':'kind','hygiene':'task','cleaning':'target','medication':'result','veterinary':'kind','observation':'category','sleep':'kind'}[key]
        categories=Counter(str(a.payload.get(selector) or '구분 없음') for a in recent)
        choices={f.key:dict(f.choices) for f in MODULES[key].fields}
        breakdown=[{'label':choices.get(selector,{}).get(name,name),'count':n} for name,n in categories.most_common()]
        out.append({'module':key,'name':label,'before':len(before),'recent':len(recent),'delta':len(recent)-len(before),
                    'days':len({row_date(a,tz) for a in recent}),'breakdown':breakdown,'has_data':bool(items),
                    'last':max((a.occurred_at for a in items),default=None),
                    'url':reverse('records',args=[room.pk])+'?'+urlencode({'modules':','.join(k for k in MODULES if logical_module(k)==key),'state':'done','start':start.isoformat(),'end':end.isoformat()}),
                    'note':'남긴 기록 횟수예요. 기록이 없다는 사실만으로 실제 활동·관찰이 없었다고 단정하지 않아요.'})
    return out


def inventory_report(room,rows,start,end,days):
    end_at=bounds(room,end,end)[1]-timedelta(microseconds=1)
    report=ledger([a for a in rows if a.module in ('inventory','meal','treat','medication','hygiene','cleaning')],until=end_at)
    lower,upper=bounds(room,start,end);previous_lower=bounds(room,start-timedelta(days=days),start-timedelta(days=1))[0]
    output=[]
    for item in report['items']:
        current=period_movement(item,lower,upper);previous=period_movement(item,previous_lower,lower)
        shown,unit=display_quantity(item['balance'],item['unit'],prefer_large=item['unit'] in ('g','ml'))
        used,shown_unit=display_quantity(current['used'],item['unit'],prefer_large=item['unit'] in ('g','ml'))
        old_used,_=display_quantity(previous['used'],item['unit'],prefer_large=item['unit'] in ('g','ml'))
        linked,_=display_quantity(current['linked_used'],item['unit'],prefer_large=item['unit'] in ('g','ml'))
        factor=Decimal(1000) if item['unit'] in ('g','ml') else Decimal(1)
        output.append({**item,'shown':shown,'display_unit':unit,'used':used,'previous_used':old_used,'linked_used':linked,
                       'period':current,'previous_period':previous,'change':differences(previous['used']/factor,current['used']/factor),
                       'threshold':text(item['minimum']/factor) if item['minimum'] is not None else None,
                       'url':reverse('statistics',args=[room.pk,'inventory'])+'?'+urlencode({'start':start.isoformat(),'end':end.isoformat()})})
    return {'items':output,'issues':report['issues']}


def expense_report(room,rows,start,end,days):
    tz=ZoneInfo(room.timezone);old_start=start-timedelta(days=days);values=defaultdict(lambda:{'before':ZERO,'recent':ZERO,'before_records':0,'recent_records':0,'categories':Counter()})
    for a in rows:
        if a.module!='expense' or not old_start<=row_date(a,tz)<=end:continue
        n=value(a.payload.get('amount'));unit=a.payload.get('currency')
        if n is None or unit not in ('KRW','USD','EUR','JPY','CNY'):continue
        side='recent' if row_date(a,tz)>=start else 'before';group=values[unit];group[side]+=n;group[side+'_records']+=1
        if side=='recent':group['categories'][a.payload.get('category','other')]+=n
    return [{'currency':unit,'before':text(v['before']),'recent':text(v['recent']),'before_records':v['before_records'],'recent_records':v['recent_records'],
             'change':differences(v['before'],v['recent']),'categories':[{'name':k,'value':text(n)} for k,n in v['categories'].items()]} for unit,v in sorted(values.items())]


def context_notes(room,rows,start,end):
    tz=ZoneInfo(room.timezone)
    return [{'date':a.occurred_at,'text':str(a.payload.get('detail') or a.note or '내용 없는 이전 기록')[:500],
             'source':'이전 생활 변화' if a.module=='reproduction' else '특이사항 메모',
             'structured_observation':a.payload.get('condition')=='different',
             'url':reverse('record_detail',args=[room.pk,a.pk])} for a in reversed(rows) if a.module in ('observation','reproduction') and start<=row_date(a,tz)<=end][:20]


def latest_weight_context(room,rows,enabled,start,end):
    if 'weight' not in enabled:return {}
    weights=[]
    for a in rows:
        if a.module!='weight':continue
        n=value(a.payload.get('weight'));factor=WEIGHT.get(a.payload.get('unit'))
        if n and factor:weights.append((a,n*factor/1000))
    if not weights:
        ref=reference_for(room,None)
        if room.species not in ('dog','cat'):ref['message']='이 동물종에 개·고양이의 체중 기준을 적용하지 않아요. 개인 측정 기록을 비교해요.'
        return {'weight_reference':ref,'weight_enabled':True}
    latest,n=weights[-1];previous,old=weights[-2] if len(weights)>1 else (None,None)
    latest_day=row_date(latest,ZoneInfo(room.timezone))
    ref=reference_geometry(reference_for(room,{'value':n,'date':latest_day}))
    if room.species not in ('dog','cat'):ref['message']='이 동물종에 개·고양이의 체중 기준을 적용하지 않아요. 개인 측정 기록을 비교해요.'
    output={'latest_weight':{'text':text(n),'date':latest.occurred_at,'url':reverse('record_detail',args=[room.pk,latest.pk])},
            'previous_weight':{'text':text(old),'date':previous.occurred_at} if previous else None,
            'latest_weight_change':differences(old,n),'weight_reference':ref,'weight_enabled':True}
    if previous:
        interval=[a for a in rows if previous.occurred_at<a.occurred_at<=latest.occurred_at]
        facts=[]
        for module in ('meal','treat','walk','play','training'):
            if module not in enabled:continue
            selected=[a for a in interval if a.module==module]
            if not selected:continue
            extract=(lambda a:mass_amount(a)) if module in ('meal','treat') else (lambda a:value(a.payload.get('minutes')))
            numbers=[extract(a) for a in selected];known=[v for v in numbers if v is not None]
            facts.append({'module':module,'name':MODULES[module].name,'value':text(sum(known,ZERO)) if known else '—','_raw_value':sum(known,ZERO) if known else None,'unit':'g 제공' if module in ('meal','treat') else '분',
                          'records':len(selected),'missing':len(numbers)-len(known)})
        output['weight_interval']={'start':previous.occurred_at,'end':latest.occurred_at,'facts':facts,
            'notes':context_notes(room,interval,previous.occurred_at.astimezone(ZoneInfo(room.timezone)).date(),latest_day),
            'message':'두 실제 측정 사이에 남긴 기록을 함께 살펴봐요. 기록 밖의 급여·활동이나 변화 원인을 뜻하지 않아요.'}
    if room.species in ('dog','cat'):
        bcs=next((a for a in reversed(rows) if a.module=='weight' and str(a.payload.get('bcs','')) in {str(i) for i in range(1,10)}),None)
        if bcs:
            score=int(bcs.payload['bcs']);lo,hi=(4,5) if room.species=='dog' else (5,5)
            output['bcs_card']={'score':score,'date':bcs.occurred_at,'low':lo,'high':hi,
                'assessor':{'vet':'수의사 평가','guardian':'보호자 관찰'}.get(bcs.payload.get('bcs_by'),'평가자 미기록'),
                'label':'자료상 참고 구간 안' if lo<=score<=hi else '자료상 참고 구간보다 낮음' if score<lo else '자료상 참고 구간보다 높음',
                'segments':[{'number':i,'selected':i==score,'reference':lo<=i<=hi} for i in range(1,10)],
                'source':'WSAVA BCS · 2025-06','source_url':'https://wsava.org/global-guidelines/global-nutrition-guidelines/',
                'url':reverse('record_detail',args=[room.pk,bcs.pk])}
    return output


def relationships(metrics,enabled,notes,inventory,weight):
    lookup={m['key']:m for m in metrics};result=[]
    def choose(keys):return next((lookup[k] for k in keys if k in lookup and lookup[k]['change']['available']),None)
    def ready(m):return m and m['change']['available'] and min(m['before']['observed_days'],m['recent']['observed_days'])>=3
    def fact(m):return {'name':m['name'],'before':m['before']['text'],'recent':m['recent']['text'],'before_value':m['before']['value'],'recent_value':m['recent']['value'],'unit':m['unit'],'url':m['evidence_url'],'coverage':f"이전 {m['before']['observed_days']}일 · 최근 {m['recent']['observed_days']}일"}
    weight_metric=lookup.get('weight')
    food=choose(['meal_consumed_kcal','meal_provided_kcal','meal_provided_g']);treat=choose(['treat_consumed_kcal','treat_provided_kcal','treat_provided_g'])
    if 'weight' in enabled and ('meal' in enabled or 'treat' in enabled):
        candidates=[m for m in (weight_metric,food,treat) if m and m['change']['available']]
        sufficient=bool(ready(weight_metric) and (ready(food) or ready(treat)))
        result.append({'key':'food-weight','title':'체중과 급여 기록을 함께 보기','available':sufficient,'facts':[fact(m) for m in candidates],
                       'message':'같은 두 기간의 수치예요. 먹이·간식의 제공량과 실제 먹은 양을 구별하고, 저장 설정이 바뀐 날짜도 함께 확인해 주세요.' if sufficient else '함께 비교할 관측일이 아직 적어요. 최근 두 실제 체중 측정 사이의 기록은 체중 영역에서 확인할 수 있어요.',
                       'action':'급여 설정과 원기록을 확인해 주세요. 이 비교만으로 급여량을 늘리거나 줄이지 않아요.',
                       'source':'AAHA 2021 영양 평가: 체중·식이·활동 맥락을 함께 확인','source_url':'https://www.aaha.org/resources/2021-aaha-nutrition-and-weight-management-guidelines/screening-evaluation/'})
    activity=[lookup[k] for k in ('walk_minutes','play_minutes','training_minutes') if k in lookup and lookup[k]['change']['available']]
    if 'weight' in enabled and activity:
        candidates=([weight_metric] if weight_metric and weight_metric['change']['available'] else [])+activity
        result.append({'key':'activity-weight','title':'체중과 활동 기록','available':bool(ready(weight_metric) and any(ready(m) for m in activity)),
                       'facts':[fact(m) for m in candidates],'message':'체중과 활동의 같은 기간을 나란히 보여줘요. 기록한 산책·놀이 시간이 전체 운동량이나 에너지 소비량은 아니에요.',
                       'action':'기록이 빠진 날이나 활동 종류가 바뀌었는지 확인해 주세요.'})
    rest=lookup.get('sleep_observed')
    if rest and activity:
        result.append({'key':'activity-rest','title':'활동과 관찰한 휴식','available':bool(ready(rest) and any(ready(m) for m in activity)),
                       'facts':[fact(m) for m in activity+([rest] if rest['change']['available'] else [])],
                       'message':'직접 관찰한 수면·휴식만 보여줘요. 알람 시간이나 기록하지 않은 시간을 채우지 않아요.',
                       'action':'관찰한 시간대가 달랐다면 총 수면이나 회복 상태의 변화로 해석하지 마세요.'})
    water=lookup.get('water_measured');env=[m for m in metrics if m['module']=='environment' and m['has_any']]
    if water and env:
        candidates=[m for m in [water]+env[:2] if m['change']['available']]
        sets=[{p['date'] for p in m['values']} for m in candidates]
        overlap=len(set.intersection(*sets)) if len(sets)>1 else 0
        result.append({'key':'water-environment','title':'실측 음수량과 환경 기록','available':overlap>=3 and all(ready(m) for m in candidates),
                       'facts':[fact(m) for m in candidates],'message':f'함께 값이 있는 날짜는 {overlap}일이에요. 물 교체·보충은 이 음수량에 들어가지 않아요.',
                       'action':'측정 위치·방법과 누락을 확인해 주세요. 환경이 음수량 변화의 원인이라고 단정하지 않아요.'})
    if inventory.get('items') and any(m in enabled for m in ('meal','treat')):
        linked=[i for i in inventory['items'] if i['period']['linked_used'] or i['period']['unknown']]
        if linked:result.append({'key':'food-stock','title':'급여에서 연결된 재고 소비','available':True,'facts':[],
            'message':' · '.join(f"{i['name']}: 급여 연결 사용 {i['linked_used']} {i['display_unit']}" for i in linked[:4]),
            'action':'꺼내 준 양만 한 번 차감해요. 급여 수정·취소와 실사 수량을 잔량에서 함께 확인할 수 있어요.'})
    if notes:result.append({'key':'note-context','title':'변화 시기의 특이사항','available':True,'facts':[],
        'message':f'선택한 기간에 {len(notes)}개의 최근 메모를 함께 볼 수 있어요. 이전 생활 변화도 출처를 보존해 연결했어요.',
        'action':'같은 시기의 사건이라는 뜻이며 원인이나 진단을 자동으로 정하지 않아요.'})
    return result


def build_report(member,days=7,*,end=None,records=None):
    member=services.fresh_member(member);room=member.room;now=timezone.now();today=services.local_date(room)
    try:days=int(days)
    except (ValueError,TypeError):days=7
    if days not in (7,30,90):days=7
    end=min(end or today-timedelta(days=1),today-timedelta(days=1));start=end-timedelta(days=days-1)
    allowed=set(services.visible_modules(member))
    enabled={k for k in room.module_settings.filter(enabled=True).values_list('key',flat=True) if k in allowed and k in SELECTABLE_MODULES}
    enabled&=set(SELECTABLE_MODULES)
    if records is None:
        records=list(Activity.objects.filter(room=room,module__in=allowed,voided=False,status='done',occurred_at__lte=now).select_related('author').order_by('occurred_at','created_at','id'))
    from ..supplies import filter_rows
    rows=filter_rows(member,eligible(records,room.pk,allowed,now))
    metrics,grouped=build_measurements(room,rows,enabled,start,end,days,now)
    module_cards=[]
    priority=['weight','walk','meal','treat','water','play','training','sleep','environment']
    for key in priority:
        if key not in enabled:continue
        subset=[m for m in metrics if m['module']==key]
        if subset:module_cards.append({'key':key,'name':MODULES[key].name,'icon':MODULES[key].icon,'metrics':subset,
                       'has_data':any(m['has_any'] for m in subset),'detail_url':reverse('module',args=[room.pk,key])})
    evidence_root=reverse('statistics_evidence',args=[room.pk])
    for m in metrics:
        base={'metric':m['key'],'days':days,'end':end.isoformat()}
        m['evidence_url']=evidence_root+'?'+urlencode({**base,'period':'all'})
        for side in ('before','recent'):
            m[side]['url']=evidence_root+'?'+urlencode({**base,'period':side})
    notes=context_notes(room,rows,start,end) if 'observation' in enabled else []
    inventory=inventory_report(room,rows,start,end,days) if 'inventory' in enabled else {'items':[],'issues':[]}
    weight=latest_weight_context(room,rows,enabled,start,end)
    schedule=completion_summary(member,start,end,now=now)
    result={'days':days,'start':start,'end':end,'previous_start':start-timedelta(days=days),'previous_end':start-timedelta(days=1),
            'today':today,'module_cards':module_cards,'measurements':metrics,'notes':notes,
            'counts':counted_modules(room,rows,enabled,start,end,days),'inventory':inventory,
            'expenses':expense_report(room,rows,start,end,days) if 'expense' in enabled else [],
            'training_rates':training_rates(rows,start,end,days,room) if 'training' in enabled else [],
            'schedule':schedule,'relations':relationships(metrics,enabled,notes,inventory,weight),
            'periods':[{'days':n,'label':f'{n}일','url':'?days='+str(n),'selected':n==days} for n in (7,30,90)],
            'enabled':enabled,'selected_module_count':len(enabled),'comparison_module_count':len(module_cards),
            'record_count':sum(start<=row_date(a,ZoneInfo(room.timezone))<=end for a in rows),
            'today_observations':[{'date':a.occurred_at,'text':str(a.payload.get('detail') or a.note or '직접 남긴 변화 관찰'),'url':reverse('record_detail',args=[room.pk,a.pk])} for a in reversed(rows) if a.module in ('observation','toilet') and a.payload.get('condition')=='different' and row_date(a,ZoneInfo(room.timezone))==today][:5],
            'profile_url':reverse('room_settings',args=[room.pk]),'can_edit_profile':member.can_manage,**weight}
    from .display_values import decorate_report
    return decorate_report(result)
