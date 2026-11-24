"""Human-readable presentation only. Stored values, denominators and geometry stay intact.

Inputs are never rounded by this module. A nonzero value is never displayed as
zero, and an absent value is never treated as a measurement. Body-weight display
uses the pre-existing formatter, as required by the current work order.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Iterable

D = Decimal
MASS = {'mg': D('0.001'), 'g': D(1), 'kg': D(1000), 'oz': D('28.349523125')}
VOLUME = {'ml': D(1), 'l': D(1000), 'L': D(1000)}
DISTANCE = {'m': D(1), 'km': D(1000)}
COUNTS = {'개','건','회','번','일','주','개월','품목','명','개체','점'}
CURRENCY = {'KRW':'원','USD':'USD','EUR':'EUR','JPY':'엔','CNY':'CNY','원':'원','엔':'엔'}
ALIASES = {'piece':'개','percent':'%','hours':'시간','minutes':'분','C':'°C','F':'°F','ph':'pH','tablet':'정','drop':'방울','dose':'회분'}


def decimal_value(raw):
    if raw is None or raw == '' or isinstance(raw, bool):
        return None
    try:
        result = D(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def plain(number):
    text = format(number, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


@dataclass(frozen=True)
class DisplayValue:
    value: str
    unit: str = ''
    exact: str = ''
    rounded: bool = False
    missing: bool = False

    @property
    def text(self):
        return self.value + (' ' + self.unit if self.unit else '')


def number(raw, places=1, *, signed=False, group=True, small_places=3):
    """Round the displayed magnitude, with a bounded nonzero exception."""
    n=decimal_value(raw)
    if n is None:
        return '—' if raw in (None,'') else str(raw)
    sign=('−' if n<0 else '+' if signed and n>0 else '')
    magnitude=abs(n)
    places=max(0,min(12,int(places)))
    maximum=max(places,min(12,int(small_places)))
    with localcontext() as ctx:
        ctx.prec=max(28,len(n.as_tuple().digits)+places+max(0,n.adjusted())+4)
        rounded=magnitude.quantize(D(1).scaleb(-places),rounding=ROUND_HALF_UP)
        while magnitude and not rounded and places<maximum:
            places+=1
            rounded=magnitude.quantize(D(1).scaleb(-places),rounding=ROUND_HALF_UP)
        if magnitude and not rounded:
            return sign+'<'+plain(D(1).scaleb(-places))
        result=format(rounded,',f' if group else 'f')
        if '.' in result:result=result.rstrip('0').rstrip('.')
    return sign+result


def common_unit(values: Iterable, unit: str):
    """Choose one unit for a whole comparison/chart rather than each column."""
    known=[abs(n) for x in values if (n:=decimal_value(x)) is not None]
    largest=max(known,default=D(0))
    if unit in MASS:
        grams=largest*MASS[unit]
        return 'kg' if grams>=1000 else 'mg' if 0<grams<1 else 'g'
    if unit in VOLUME:
        return 'L' if largest*VOLUME[unit]>=1000 else 'ml'
    if unit in DISTANCE:
        return 'km' if largest*DISTANCE[unit]>=1000 else 'm'
    return ALIASES.get(unit,CURRENCY.get(unit,unit))


def duration(raw, unit='분', *, signed=False):
    n=decimal_value(raw)
    if n is None:
        return DisplayValue('—' if raw in (None,'') else str(raw),missing=True)
    multiplier={'시간':D(3600),'hours':D(3600),'분':D(60),'minutes':D(60),'초':D(1)}[unit]
    seconds=abs(n)*multiplier
    sign='−' if n<0 else '+' if signed and n>0 else ''
    exact=plain(n)+' '+ALIASES.get(unit,unit)
    if 0<seconds<D(1):return DisplayValue(sign+'1초 미만',exact=exact,rounded=True)
    whole=int(seconds.quantize(D(1),rounding=ROUND_HALF_UP))
    hours,remainder=divmod(whole,3600);minutes,secs=divmod(remainder,60)
    parts=[]
    if hours:parts.append(f'{hours:,}시간')
    if minutes:parts.append(f'{minutes}분')
    if secs:parts.append(f'{secs}초')
    if not parts:parts=['0분' if unit in ('분','minutes','시간','hours') else '0초']
    return DisplayValue(sign+' '.join(parts),exact=exact,rounded=seconds!=whole)


def measure(raw, unit='', *, kind='', fixed_unit=None, places=None, signed=False):
    n=decimal_value(raw)
    label=ALIASES.get(unit,CURRENCY.get(unit,unit))
    if n is None:
        marker='—' if raw in (None,'') else str(raw)
        return DisplayValue(marker,label if marker not in ('—','미기록','기록 없음','계산 불가') else '',missing=True)
    if kind=='dose':
        return DisplayValue(plain(n),label,plain(n)+' '+label)
    if kind=='weight':
        from ..modules import compact
        value=compact(n)
        if signed and n>0:value='+'+value
        elif signed and n<0:value='−'+compact(abs(n))
        return DisplayValue(value,unit,plain(n)+' '+unit)
    if unit in ('분','minutes','시간','hours','초') and kind not in ('axis','decimal_time'):
        return duration(n,unit,signed=signed)
    target=fixed_unit or (unit if kind=='dose' else common_unit([n],unit))
    converted=n
    factors=MASS if unit in MASS else VOLUME if unit in VOLUME else DISTANCE if unit in DISTANCE else None
    if factors and target in factors:converted=n*factors[unit]/factors[target]
    label=ALIASES.get(target,CURRENCY.get(target,target))
    if places is None:
        if kind=='dose':places=3
        elif unit in CURRENCY:places=0 if unit in ('KRW','JPY','원','엔') else 2
        elif label in COUNTS:places=0 if converted==converted.to_integral_value() else 3 if abs(converted)<1 else 1
        elif label in ('kg','L','pH'):places=2
        else:places=1
    shown=number(converted,places,signed=signed)
    cleaned=shown.replace(',','').replace('−','-').lstrip('+')
    rounded=cleaned.startswith('<') or decimal_value(cleaned)!=converted
    return DisplayValue(shown,label,plain(n)+(' '+ALIASES.get(unit,unit) if unit else ''),rounded)


def decorate_stats(stats):
    """Add human-facing fields without changing existing arithmetic keys."""
    kind='weight' if stats.get('key')=='weight' else ''
    stats['primary_display']=measure(stats.get('_primary_value',stats.get('primary')),stats.get('primary_unit',''),kind=kind)
    for item in stats.get('metrics',[]):
        item['display']=measure(item.get('_raw_value',item.get('value')),item.get('unit',''),kind=kind)
    for item in stats.get('breakdown',[]):
        item['display']=measure(item.get('_raw_value',item.get('value')),item.get('unit',''),kind=kind)
        if item.get('_parts'):
            item['display']=DisplayValue(' · '.join(label+' '+measure(raw,item.get('unit','')).text for label,raw in item['_parts']))
        if '_ratio' in item:
            item['note_display']=measure(item['_ratio'],'%').text if item['_ratio'] is not None else item.get('note','')
        if '_minimum_value' in item:
            item['note_display']=('사용자 기준 '+measure(item['_minimum_value'],item['unit']).text if item['_minimum_value'] is not None else '구매 기준 미설정')+(' · 사용량 미확인 기록 있음' if not item.get('_complete',True) else '')
    for chart in stats.get('charts',[]):
        values=[p.get('_raw_value',p.get('value')) for p in chart['points']]
        target=chart['unit'] if kind=='weight' else common_unit(values,chart['unit'])
        chart['display_unit']=target
        for p in chart['points']:
            p['display']=measure(p.get('_raw_value',p.get('value')),chart['unit'],kind=kind,fixed_unit=target)
        for key in ('min','max'):
            chart['display_'+key]=measure(chart.get('_raw_'+key,chart[key]),chart['unit'],kind=kind or 'axis',fixed_unit=target).value
    return stats


def decorate_tile(tile,module):
    tile['display']=measure(tile.get('_raw_value',tile['value']),tile['unit'],kind='weight' if module=='weight' else '')
    for item in tile.get('secondary',[]):
        item['display']=measure(item.get('_raw_value',item.get('value')),item.get('unit',''))
    return tile


def decorate_report(report):
    """Only display additions; raw values, periods, eligible IDs and formulas are preserved."""
    for m in report.get('measurements',[]):
        kind='weight' if m['module']=='weight' else ''
        unit=m['unit'];values=[p['value'] for p in m['values']]+[m['before']['value'],m['recent']['value']]
        target=unit if kind else common_unit(values,unit)
        for side in ('before','recent'):
            p=m[side];p['display']=DisplayValue(p['text'],unit) if kind else measure(p['value'],unit,fixed_unit=target)
            p['total_display']=measure(p['total'],unit,kind=kind,fixed_unit=target)
        for p in m['values']:p['display']=measure(p['value'],unit,kind=kind,fixed_unit=target)
        c=m['change']
        if c['available']:
            change_unit=c.get('unit_override') or unit
            c['display']=measure(c['delta'],change_unit,kind=kind,fixed_unit=target if change_unit==unit else change_unit,signed=True)
            c['percent_display']=c['percent_text'] if kind else measure(c['percent'],'%',signed=True).text if c['percent'] is not None else c['percent_text']
    for relation in report.get('relations',[]):
        for fact in relation.get('facts',[]):
            target=common_unit([fact.get('before_value',fact['before']),fact.get('recent_value',fact['recent'])],fact['unit'])
            kind='weight' if '체중' in fact['name'] else ''
            fact['before_display']=measure(fact.get('before_value',fact['before']),fact['unit'],kind=kind,fixed_unit=target)
            fact['recent_display']=measure(fact.get('recent_value',fact['recent']),fact['unit'],kind=kind,fixed_unit=target)
    for row in report.get('training_rates',[]):
        for side in ('before','recent'):row[side]['display']=measure(row[side]['value'],'%')
        if row['change']['available']:row['change']['display']=measure(row['change']['delta'],'%p',signed=True)
    for row in report.get('expenses',[]):
        row['before_display']=measure(row['before'],row['currency']);row['recent_display']=measure(row['recent'],row['currency'])
    for item in report.get('inventory',{}).get('items',[]):
        item['balance_display']=measure(item['balance'],item['unit'])
        target=common_unit([item['period']['used'],item['previous_period']['used'],item['period']['linked_used']],item['unit'])
        item['used_display']=measure(item['period']['used'],item['unit'],fixed_unit=target)
        item['previous_used_display']=measure(item['previous_period']['used'],item['unit'],fixed_unit=target)
        item['linked_used_display']=measure(item['period']['linked_used'],item['unit'],fixed_unit=target)
        item['threshold_display']=measure(item['minimum'],item['unit'],fixed_unit=item['balance_display'].unit)
    if report.get('weight_interval'):
        for item in report['weight_interval']['facts']:
            unit=item['unit'];item['display']=measure(item.get('_raw_value',item['value']),'g' if unit=='g 제공' else unit)
            item['basis']='제공량' if unit=='g 제공' else '기록한 시간'
    return report


def field_unit(module,key,payload):
    if key=='minutes':return '분'
    if key=='distance':return payload.get('distance_unit','km')
    if key in ('quantity','consumed','amount','weight','dose','minimum','value'):
        return payload.get('currency','KRW') if module=='expense' else payload.get('unit','')
    if key=='used_quantity':return payload.get('used_unit','')
    if key in ('attempts','successes','eggs'):return '회' if key!='eggs' else '개'
    return ''


def field_measure(module,key,raw,payload):
    return measure(raw,field_unit(module,key,payload),kind='weight' if module=='weight' else 'dose' if module=='medication' and key=='dose' else '')


def display_payload(module,payload,*,status=None):
    from ..modules import MODULES,UNIT_LABELS
    # The weight card/record precision is explicitly outside the requested change.
    if module=='weight':
        from ..modules import _legacy_payload_display
        return _legacy_payload_display(module,payload,status=status)
    rows=[]
    if module=='inventory' and payload.get('_package'):
        from ..packaging import receipt_text,description
        rows.extend([('포장 수량',receipt_text(payload['_package'])),('포장 정보',description(payload['_package']))])
    supply=payload.get('_supply') or {}
    if supply.get('name'):rows.append(('사용한 물품',str(supply['name'])))
    if payload.get('used_quantity') is not None:rows.append(('실제 사용량',field_measure(module,'used_quantity',payload['used_quantity'],payload).text))
    if payload.get('routine_title'):rows.append(('알림 이름',str(payload['routine_title'])))
    if payload.get('_preset',{}).get('title'):rows.append(('사용한 저장 설정',str(payload['_preset']['title'])))
    numeric=[f for f in MODULES[module].fields if f.kind=='number']
    paired_units={field_unit(module,f.key,payload) for f in numeric if payload.get(f.key) is not None}
    for spec in MODULES[module].fields:
        raw=payload.get(spec.key)
        if raw is None or raw=='':continue
        if spec.key in ('unit','distance_unit','currency') and raw in paired_units:continue
        label=spec.label
        if spec.kind=='number':
            shown=field_measure(module,spec.key,raw,payload).text
            if spec.key=='minutes':label=label.replace(' (분)','')
        elif spec.kind=='choice':shown=dict(spec.choices).get(str(raw),raw)
        elif spec.kind=='date':shown=str(raw).replace('-','.')
        else:shown=str(raw)
        if module=='medication' and spec.key=='result' and raw=='given' and status=='planned':shown='투약 예정'
        rows.append((label,str(shown)))
    return rows


def rounded_originals(module,payload):
    from ..modules import MODULES
    if module=='weight':return []
    rows=[]
    if payload.get('used_quantity') is not None:
        shown=field_measure(module,'used_quantity',payload['used_quantity'],payload)
        if shown.rounded:rows.append(('실제 사용량',shown.exact))
    for spec in MODULES[module].fields:
        if spec.kind!='number' or payload.get(spec.key) is None:continue
        shown=field_measure(module,spec.key,payload[spec.key],payload)
        if shown.rounded:rows.append((spec.label,shown.exact))
    return rows


def summary(module,payload):
    from ..modules import MODULES,_legacy_record_summary
    p=payload
    def fact(key):return field_measure(module,key,p.get(key),p).text
    def choice(key):
        f=next((f for f in MODULES[module].fields if f.key==key),None)
        return str(dict(f.choices).get(str(p.get(key)),p.get(key,''))) if f else str(p.get(key,''))
    if module=='weight':return _legacy_record_summary(module,p)
    if module in ('meal','treat'):return f"{p.get('food_name') or '급여 완료'} · {fact('quantity') if p.get('quantity') is not None else '양 미입력'}"
    if module=='water':return choice('kind')+(' · '+fact('amount') if p.get('amount') is not None else '')+(' · '+p['container'] if p.get('container') else '')
    if module=='walk':return ' · '.join(([fact('minutes')] if p.get('minutes') is not None else ['시간 미입력'])+([fact('distance')] if p.get('distance') is not None else []))
    if module in ('play','training'):return str(p.get('kind' if module=='play' else 'skill') or MODULES[module].name)+' · '+(fact('minutes') if p.get('minutes') is not None else '시간 미입력')
    if module=='sleep' and p.get('_entry_kind')!='schedule':return choice('kind')+' · 관찰 '+fact('minutes')
    if module=='inventory':
        from ..packaging import receipt_text
        pack=receipt_text(p.get('_package'))
        return f"{p.get('item','')} · {choice('movement')} "+(pack+' · 합계 ' if pack else '')+fact('quantity')
    if module=='expense':return f"{p.get('item','')} · {fact('amount')}"
    if module=='environment':return f"{choice('metric')} · {fact('value')} · {p.get('location','생활공간')}"
    if module=='medication' and p.get('_entry_kind')!='routine':return f"{p.get('medicine','')} · {fact('dose')} · {choice('result')}"
    return _legacy_record_summary(module,p)
