"""Deterministic within-pet pattern analysis. No networking and no health-risk score.

Mathematical sources and application choices are documented in docs/12_종합상태_분석방법.md.
Inputs must already be permission-scoped. Display groups and population references are not inputs.
"""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from math import ceil, isfinite
from statistics import median
from zoneinfo import ZoneInfo

VERSION = "personal-patterns-1"
MINIMUMS = {"weight": (3, 2)}
# These are engineering noise floors, not clinical normal ranges.
SPECS = {
    "weight": ("체중", "kg", "weight", ("weight",), .03, .01),
    "food_actual": ("확인한 섭취 열량", "kcal/기록일", "food", ("meal", "treat"), .20, 1),
    "food_offered": ("제공량 기준 예상 열량", "kcal/기록일", "food", ("meal", "treat"), .20, 1),
    "food_mass": ("기록한 제공량", "g/기록일", "food", ("meal", "treat"), .20, 1),
    "meal_gap": ("같은 날 기록한 식사 간격", "시간", "timing", ("meal",), .25, .25),
    "activity": ("기록한 활동 시간", "분/기록일", "activity", ("walk", "play", "training"), .25, 1),
    "rest": ("관찰한 수면·휴식", "분/관찰일", "rest", ("sleep",), .20, 1),
    "water": ("실측 음수량", "ml/기록일", "water", ("water",), .25, 1),
}


def number(value):
    try:
        n = float(value)
        return n if isfinite(n) else None
    except (ValueError, TypeError, OverflowError):
        return None


def rounded(value):
    return round(value, 3) if value is not None else None


def theil_sen(points):
    """Median of pairwise slopes using actual distinct calendar-day distances."""
    slopes = [(y2-y1)/(d2-d1).days for i,(d1,y1) in enumerate(points) for d2,y2 in points[i+1:] if d2 != d1]
    return median(slopes) if slopes else None


def union_minutes(intervals):
    merged = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start,end))
    return sum(end-start for start,end in merged)/60


def canonical_records(records, as_of, timezone_name):
    tz = ZoneInfo(timezone_name)
    upper = datetime.combine(as_of+timedelta(days=1), time.min, tz).timestamp()
    unique = {}
    for r in records:
        if r.voided or r.status != "done" or r.occurred_at.timestamp() >= upper:
            continue
        if r.ended_at and r.ended_at.timestamp() >= upper:
            continue
        unique[str(r.pk)] = r
    return sorted(unique.values(), key=lambda r:(r.occurred_at.timestamp(), str(r.pk)))


def extract_series(records, as_of, timezone_name):
    tz = ZoneInfo(timezone_name)
    start = as_of-timedelta(days=34)
    lower = datetime.combine(start,time.min,tz).timestamp()
    upper = datetime.combine(as_of+timedelta(days=1),time.min,tz).timestamp()
    series = {key:{} for key in SPECS}
    partial = Counter()
    days = defaultdict(list)
    sleep = defaultdict(list)
    source_ids = defaultdict(set)
    for r in records:
        day = r.occurred_at.astimezone(tz).date()
        if start <= day <= as_of:
            days[day].append(r)
        if r.module == "sleep":
            duration = number(r.payload.get("minutes"))
            if duration is None or duration < 0:
                continue
            a, b = max(lower,r.occurred_at.timestamp()), min(upper,r.occurred_at.timestamp()+duration*60)
            while a < b:
                day = datetime.fromtimestamp(a,tz).date()
                stop = min(b,datetime.combine(day+timedelta(days=1),time.min,tz).timestamp())
                sleep[day].append((a,stop)); source_ids["rest"].add(str(r.pk)); a=stop
    for day, rows in sorted(days.items()):
        weights=[r for r in rows if r.module=="weight"]
        if weights:
            r=weights[-1]; value=number(r.payload.get("weight"))
            factor={"kg":1,"g":.001,"lb":.45359237}.get(r.payload.get("unit"))
            if value is not None and value>0 and factor is not None:
                series["weight"][day]=value*factor;source_ids["weight"].add(str(r.pk))
        foods=[r for r in rows if r.module in ("meal","treat")]
        if foods:
            for key,basis in (("food_actual","consumed"),("food_offered","provided")):
                values=[number(r.nutrition.get("kcal")) if r.nutrition.get("basis")==basis else None for r in foods]
                if all(v is not None and v>=0 for v in values):
                    series[key][day]=sum(values);source_ids[key].update(str(r.pk) for r in foods)
                else:partial[key]+=1
            grams=[]
            for r in foods:
                value=number(r.payload.get("quantity"));factor={"g":1,"kg":1000,"oz":28.349523125}.get(r.payload.get("unit"))
                grams.append(value*factor if value is not None and value>=0 and factor is not None else None)
            if all(v is not None for v in grams):
                series["food_mass"][day]=sum(grams);source_ids["food_mass"].update(str(r.pk) for r in foods)
            else:partial["food_mass"]+=1
        meals=[r for r in rows if r.module=="meal"]
        instants=sorted(set(r.occurred_at.timestamp() for r in meals))
        gaps=[(b-a)/3600 for a,b in zip(instants,instants[1:]) if b>a]
        if gaps:
            series["meal_gap"][day]=median(gaps);source_ids["meal_gap"].update(str(r.pk) for r in meals)
        activities=[r for r in rows if r.module in ("walk","play","training")]
        if activities:
            values=[number(r.payload.get("minutes")) for r in activities]
            if all(v is not None and v>=0 for v in values):
                series["activity"][day]=sum(values);source_ids["activity"].update(str(r.pk) for r in activities)
            else:partial["activity"]+=1
        waters=[r for r in rows if r.module=="water" and r.payload.get("kind")=="measured"]
        if waters:
            values=[number(r.payload.get("amount")) for r in waters]
            if all(v is not None and v>=0 for v in values):
                series["water"][day]=sum(v*(1000 if r.payload.get("unit")=="l" else 1) for v,r in zip(values,waters));source_ids["water"].update(str(r.pk) for r in waters)
    series["rest"]={day:union_minutes(spans) for day,spans in sleep.items()}
    return series,partial,source_ids


def compare(key, data, as_of, partial=0, threshold_scale=1.0):
    label,unit,family,modules,relative,absolute=SPECS[key]
    start=as_of-timedelta(days=6)
    older=sorted((d,v) for d,v in data.items() if as_of-timedelta(days=34)<=d<start)
    recent=sorted((d,v) for d,v in data.items() if start<=d<=as_of)
    minimum_old,minimum_new=MINIMUMS.get(key,(7,3))
    enough=len(older)>=minimum_old and len(recent)>=minimum_new
    baseline=median(v for _,v in older) if older else None
    current=median(v for _,v in recent) if recent else None
    mad=median(abs(v-baseline) for _,v in older) if older else None
    delta=current-baseline if baseline is not None and current is not None else None
    floor=max(absolute,abs(baseline or 0)*relative)*threshold_scale
    statistical=3.5*mad/.6745 if mad else 0
    limit=max(floor,statistical)
    direction=0;hits=0
    if delta is not None and delta!=0:
        sign=1 if delta>0 else -1
        hits=sum(sign*(v-baseline)>=limit for _,v in recent)
        if enough and abs(delta)>=limit and hits>=max(2,ceil(len(recent)*.6)):
            direction=sign
    # Mark individual excursions without turning one outlier into persistent change.
    excursions=[d for d,v in recent if baseline is not None and abs(v-baseline)>=limit]
    state="changed" if direction else "steady" if enough else "limited"
    note="관측된 날짜만 비교해요. 빈 날짜를 0으로 넣지 않았어요."
    if partial:note+=f" 의미·수치가 불완전한 {partial}일은 이 지표의 비교에서 제외했어요."
    if enough and mad==0:note+=" 기준 구간의 변동이 없어 최소 차이와 반복 관측으로 확인했어요."
    if excursions and not direction:note+=f" 최근 {len(excursions)}일의 개별 차이가 있으나 지속 변화 조건은 충족하지 않았어요."
    pct=100*delta/abs(baseline) if delta is not None and baseline else None
    return {"key":key,"label":label,"unit":unit,"family":family,"modules":modules,"state":state,"direction":direction,
            "baseline":rounded(baseline),"current":rounded(current),"delta":rounded(delta),"percent":rounded(pct),
            "baseline_days":len(older),"recent_days":len(recent),"minimum_old":minimum_old,"minimum_new":minimum_new,
            "baseline_start":as_of-timedelta(days=34),"baseline_end":start-timedelta(days=1),"start":start,"end":as_of,
            "mad":rounded(mad),"threshold":rounded(limit),"repeat_days":hits,"excursion_days":excursions,
            "slope":rounded(theil_sen(older+recent)),"note":note,"recent_dates":[d for d,_ in recent],
            "points":[{"date":d,"value":rounded(v),"recent":d>=start} for d,v in older+recent]}


def analyze(records, as_of, timezone_name="Asia/Seoul", threshold_scale=1.0):
    if not .5<=threshold_scale<=2:
        raise ValueError("Threshold sensitivity outside test range")
    rows=canonical_records(records,as_of,timezone_name)
    series,partial,source_ids=extract_series(rows,as_of,timezone_name)
    all_metrics={key:compare(key,values,as_of,partial[key],threshold_scale) for key,values in series.items()}
    # One amount/energy representation per nutrition family; never count it three times.
    food_keys=("food_actual","food_offered","food_mass")
    food_key=next((k for k in food_keys if all_metrics[k]["state"]!="limited"),None)
    if food_key is None:
        food_key=max(food_keys,key=lambda k:(all_metrics[k]["recent_days"],all_metrics[k]["baseline_days"]))
    keys=["weight",food_key,"meal_gap","activity","rest","water"]
    metrics=[all_metrics[k] for k in keys]
    for m in metrics:m["source_ids"]=sorted(source_ids[m["key"]])
    usable=[m for m in metrics if m["state"]!="limited"]
    changed=[m for m in usable if m["direction"]]
    by_family={m["family"]:m for m in metrics}
    tz=ZoneInfo(timezone_name);start=as_of-timedelta(days=6)
    recent=[r for r in rows if start<=r.occurred_at.astimezone(tz).date()<=as_of]
    observations=[r for r in recent if r.module in ("observation","toilet") and r.payload.get("condition")=="different"]
    events=[r for r in recent if r.module=="reproduction"]
    relations=[]
    for left,right,title in (("food","weight","급여와 체중"),("activity","weight","활동과 체중"),("food","activity","급여와 활동"),("activity","rest","활동과 관찰한 휴식"),("food","timing","급여량과 식사 간격")):
        a,b=by_family[left],by_family[right]
        overlap=set(a["recent_dates"])&set(b["recent_dates"])
        if not a["direction"] or not b["direction"] or len(overlap)<2:
            continue
        conflicting=(left=="food" and right=="weight" and a["direction"]!=b["direction"]) or (left=="activity" and right=="weight" and a["direction"]==b["direction"])
        directions=lambda m:"늘었어요" if m["direction"]>0 else "줄었어요"
        text=f"{a['label']}은 {directions(a)}. 같은 최근 구간의 {b['label']}도 {'증가' if b['direction']>0 else '감소'}했어요."
        if conflicting:text+=" 한 방향의 변화로 묶지 않고 서로 다른 신호로 살펴봐요."
        else:text+=" 같은 시기의 변화이며 서로의 원인이라는 뜻은 아니에요."
        relations.append({"key":left+"-"+right,"title":title,"text":text,"kind":"mixed" if conflicting else "linked","metric_keys":[a["key"],b["key"]],"overlap_days":len(overlap),"modules":sorted(set(a["modules"]+b["modules"]))})
    if observations and changed:
        relations.append({"key":"observed-change","title":"수치 변화와 직접 관찰","text":f"수치의 변화가 나타난 최근 구간에 평소와 다른 관찰도 {len(observations)}건 있어요. 날짜와 내용을 함께 확인해 주세요. 같은 원인이라고 단정하지 않아요.","kind":"observed","metric_keys":[m["key"] for m in changed],"overlap_days":None,"modules":sorted({r.module for r in observations}|{k for m in changed for k in m["modules"]})})
    mixed=any(r["kind"]=="mixed" for r in relations)
    if mixed:code,title="mixed","서로 다른 방향의 변화를 함께 살펴봐요"
    elif relations:code,title="linked","관련된 기록에서 변화가 함께 나타났어요"
    elif observations:code,title="observed","평소와 다른 관찰을 확인해 주세요"
    elif len(changed)>1:code,title="multiple","여러 생활 지표의 패턴이 달라졌어요"
    elif changed:code,title="single",changed[0]["label"]+"의 패턴이 달라졌어요"
    elif usable:code,title="steady","비교 가능한 기록은 평소와 비슷해요"
    else:code,title="collecting","우리 반려동물의 평소 패턴을 모으고 있어요"
    if changed:
        names=" · ".join(m["label"] for m in changed)
        explanation=f"{names}에서 이전 28일의 패턴과 다른 변화가 여러 관측일에 반복됐어요."
    elif observations:explanation="직접 남긴 변화 관찰이 있어요. 수치 비교가 부족하더라도 이 기록은 별도로 확인할 수 있어요."
    elif usable:explanation=f"{len(usable)}개 지표에서 지속적인 변화 조건은 확인되지 않았어요. 비교할 수 없었던 항목까지 평소와 같다는 뜻은 아니에요."
    else:explanation="지금 남긴 기록과 최근 값은 확인할 수 있어요. 비교할 날짜가 모이면 같은 반려동물의 평소 패턴과 연결해요."
    active_families={m["family"] for m in usable}
    coverage="넓은 범위" if len(active_families)>=4 else "일부 범위" if usable else "비교 준비 중"
    changed_ids={m["key"] for m in changed}
    context=[]
    if events:context.append(f"같은 기간에 생활 변화 기록 {len(events)}건이 있어요. 변화의 원인으로 자동 해석하지 않아요.")
    activity_sets=[]
    for lower,upper in ((as_of-timedelta(days=34),start-timedelta(days=1)),(start,as_of)):
        activity_sets.append({r.module for r in rows if lower<=r.occurred_at.astimezone(tz).date()<=upper and r.module in ("walk","play","training")})
    if all(activity_sets) and activity_sets[0]!=activity_sets[1]:context.append("비교 기간 사이에 기록한 활동 종류가 달라요. 활동 시간 차이에 기록 방식의 변화가 포함될 수 있어요.")
    return {"version":VERSION,"as_of":as_of,"start":start,"baseline_start":as_of-timedelta(days=34),"baseline_end":start-timedelta(days=1),
            "code":code,"title":title,"explanation":explanation,"metrics":metrics,"relations":relations,"coverage":coverage,
            "comparable_count":len(usable),"changed_count":len(changed_ids),"observations":len(observations),"observation_ids":[str(r.pk) for r in observations],
            "context":context,"record_count":len(recent),"recorded_days":len({r.occurred_at.astimezone(tz).date() for r in recent}),
            "condition_note":"이 결과는 기록의 패턴 분석이며 질병 진단이나 건강 보증이 아니에요.",
            "selected_nutrition_basis":food_key,"latest_weight":next(({"value":v,"date":d} for d,v in sorted(series["weight"].items(),reverse=True)),None)}
