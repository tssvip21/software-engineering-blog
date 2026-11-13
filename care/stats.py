"""Statistics never treat absent measurements as zero or sum incompatible units."""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from .models import Activity, Reference, RoutineRun
from .modules import MODULES, UNIT_LABELS, compact, decimal
from .services import inventory_value, local_date, visible_modules

ZERO = Decimal(0)


def bounds(room, start, end):
    tz = ZoneInfo(room.timezone)
    return datetime.combine(start, time.min, tz), datetime.combine(end + timedelta(days=1), time.min, tz)


def dates(start, end):
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def label_choice(module, field, value):
    spec = next((x for x in MODULES[module].fields if x.key == field), None)
    return dict(spec.choices).get(str(value), value) if spec else value


def metric(label, value, unit="", note=""):
    if isinstance(value, (int, float, Decimal)):
        shown = compact(value)
    else:
        shown = value if value is not None else "미기록"
    return {"label": label, "value": shown, "unit": unit if value is not None else "", "note": note}


def chart(title, unit, mapping, start, end, kind="bar"):
    days = list(dates(start, end))
    values = [decimal(mapping.get(d)) for d in days]
    present = [v for v in values if v is not None]
    minimum = min(present) if present and kind == "line" else min([ZERO] + present)
    maximum = max(present) if present else Decimal(1)
    if maximum == minimum:
        margin = max(abs(maximum) * Decimal("0.05"), Decimal(1))
        minimum = minimum - margin if kind == "line" else min(minimum, ZERO)
        maximum += margin
    width, height = Decimal(700), Decimal(176)
    point_rows, segments, segment = [], [], []
    for index, (day, value) in enumerate(zip(days, values)):
        x = Decimal(16) + Decimal(index + Decimal("0.5")) * width / len(days)
        if value is None:
            if segment:
                segments.append(" ".join(segment)); segment = []
            point_rows.append({"date": day, "label": day.strftime("%m.%d"), "value": None, "x": compact(x)})
            continue
        y = Decimal(14) + height - (value - minimum) / (maximum - minimum) * height
        baseline = Decimal(14) + height - (ZERO - minimum) / (maximum - minimum) * height
        baseline = max(Decimal(14), min(Decimal(190), baseline))
        point_rows.append({"date": day, "label": day.strftime("%m.%d"), "value": compact(value), "x": compact(x), "y": compact(y),
                           "bar_x": compact(x - min(Decimal(12), width / len(days) * Decimal("0.32"))),
                           "bar_y": compact(min(y, baseline)), "bar_height": compact(max(Decimal("0.5"), abs(baseline - y))),
                           "bar_width": compact(min(Decimal(24), width / len(days) * Decimal("0.64")))})
        segment.append(f"{compact(x)},{compact(y)}")
    if segment:
        segments.append(" ".join(segment))
    return {"title": title, "unit": unit, "kind": kind, "points": point_rows, "segments": segments,
            "min": compact(minimum), "max": compact(maximum), "has_data": bool(present), "start": start, "end": end}


def union_seconds(intervals):
    merged = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return sum((Decimal(str(end - start)) for start, end in merged), ZERO)


def get_statistics(room, module, start, end, all_records=None, *, member=None):
    tz = ZoneInfo(room.timezone)
    lower, upper = bounds(room, start, end)
    if all_records is None:
        all_records = list(Activity.objects.filter(room=room, module=module, voided=False, occurred_at__lt=upper).order_by("occurred_at", "created_at", "id"))
    else:
        all_records = [a for a in all_records if a.module == module and not a.voided and a.occurred_at < upper]
        all_records.sort(key=lambda a: (a.occurred_at, a.created_at, str(a.pk)))
    all_records = [a for a in all_records if a.room_id == room.pk and (a.status == "planned" or a.occurred_at <= timezone.now())]
    if member is not None:
        from .supplies import filter_rows
        all_records = filter_rows(member, all_records)
    completed = [a for a in all_records if a.status == "done" and (not a.ended_at or a.ended_at <= timezone.now())]
    rows = [a for a in completed if lower <= a.occurred_at < upper]
    day_of = lambda a: a.occurred_at.astimezone(tz).date()
    count_daily = Counter(day_of(a) for a in rows)
    stats = {"key": module, "spec": MODULES[module], "count": len(rows), "has_data": bool(rows), "metrics": [], "breakdown": [], "charts": [],
             "notes": [], "primary": "미기록", "primary_unit": "", "primary_label": "기간 내 완료 기록", "reference": None,
             "reference_note": "기록한 내용과 완료 이력을 보여드려요. 외부 평균과 비교하는 항목은 아니에요."}
    add = lambda label, value, unit="", note="": stats["metrics"].append(metric(label, value, unit, note))
    def primary(value, unit=""):
        stats["primary"] = compact(value) if isinstance(value, (Decimal, int, float)) else str(value)
        stats["primary_unit"] = unit
    def groups(field, title=None):
        c = Counter(a.payload.get(field, "미입력") for a in rows)
        for key, value in c.most_common():
            stats["breakdown"].append({"label": str(label_choice(module, field, key)), "value": compact(value), "unit": "회"})
    def series_from_payload(field, unit, title, multiplier=None):
        mapping = defaultdict(lambda: ZERO)
        for a in rows:
            value = decimal(a.payload.get(field))
            if value is not None:
                mapping[day_of(a)] += value * (multiplier(a) if multiplier else 1)
        stats["charts"].append(chart(title, unit, mapping, start, end))
    if rows:
        primary(len(rows), "회")
    add("완료 기록", len(rows) if rows else None, "건")
    if module in ("meal", "treat"):
        known = [a for a in rows if a.nutrition.get("kcal") is not None]
        kcal = sum((decimal(a.nutrition["kcal"]) for a in known), ZERO)
        missing = len(rows) - len(known)
        bases = {a.nutrition.get("basis") for a in known}
        if known and len(bases) == 1 and bases <= {"consumed", "provided"}:
            primary(kcal, "kcal")
            stats["nutrition_basis"] = next(iter(bases))
            stats["primary_label"] = "먹은 양으로 계산한 열량" if "consumed" in bases else "준 양 기준 예상 열량"
            add("확인된 열량", kcal, "kcal", stats["primary_label"] + (f" · 열량 미확인 {missing}건" if missing else ""))
        elif known:
            primary("기준별 보기")
            stats["primary_label"] = "섭취량·제공량 기준을 나눠 표시"
            stats["nutrition_basis"] = "mixed"
        else:
            stats["primary_label"] = "급여 완료 · 열량 미확인"
            add("확인된 열량", None, "kcal", "제품 정보나 양을 몰라 계산하지 않았어요.")
        provided = Counter(a.nutrition.get("basis", "provided") for a in rows)
        for basis, label in (("consumed", "섭취량 확인분 열량"), ("provided", "제공량 기준 예상 열량")):
            selected = [a for a in known if a.nutrition.get("basis") == basis]
            add(label, sum((decimal(a.nutrition["kcal"]) for a in selected), ZERO) if selected else None, "kcal", "확인된 해당 기준의 기록만 합산")
        add("실제 섭취량을 확인한 기록", provided["consumed"] if rows else None, "건")
        add("제공량 기준 기록", provided["provided"] if rows else None, "건")
        amounts = defaultdict(lambda: ZERO)
        for a in rows:
            p = a.payload
            unit = p["unit"]
            quantity = decimal(p.get("quantity"))
            if quantity is None:
                continue
            if unit in ("g", "kg", "oz"):
                quantity *= {"g": Decimal(1), "kg": Decimal(1000), "oz": Decimal("28.349523125")}[unit]
                unit = "g"
            amounts[unit] += quantity
        for unit, amount in amounts.items():
            add("제공량 합계", amount, UNIT_LABELS.get(unit, unit))
        if rows:
            add("양 미입력", sum(a.payload.get("quantity") is None for a in rows), "건", "급여 완료는 남기고 양·열량 계산에서는 제외해요.")
        for nutrient, label in (("protein", "단백질"), ("fat", "지방")):
            nutrient_groups = defaultdict(lambda: ZERO)
            for a in rows:
                detail = a.nutrition.get("nutrients", {}).get(nutrient)
                if detail:
                    nutrient_groups[(a.nutrition.get("basis", "unknown"), detail["kind"])] += decimal(detail["grams"])
            for (basis, kind), amount in nutrient_groups.items():
                prefix = {"min": "최소 보증값", "max": "최대 보증값", "exact": "표시값"}[kind]
                basis_label = "섭취 확인" if basis == "consumed" else "제공량 예상" if basis == "provided" else "기준 미확인"
                add(label + " · " + basis_label, amount, "g", prefix + "끼리만 합산; 누락 기록은 제외")
        for basis, label in (("consumed", "먹은 양 기준 열량"), ("provided", "준 양 기준 예상 열량")):
            daily = defaultdict(lambda: ZERO)
            for a in known:
                if a.nutrition.get("basis") == basis:
                    daily[day_of(a)] += decimal(a.nutrition["kcal"])
            if daily:
                stats["charts"].append(chart(label + " · 해당 기준 기록 합계", "kcal", daily, start, end))
        if not known:
            stats["charts"].append(chart("열량 정보가 있는 기록", "kcal", {}, start, end))
        for name, count in Counter(a.payload.get("food_name", "이름 없음") for a in rows).most_common():
            stats["breakdown"].append({"label": name, "value": str(count), "unit": "회"})
        stats["notes"].append("먹은 양을 모르면 준 양으로 열량을 예상해요. 제품의 열량을 모르는 기록은 0 kcal로 채우지 않아요.")
        if provided["consumed"] and provided["provided"]:
            stats["notes"].append("섭취량 확인분과 제공량 예상분은 의미가 달라 합치지 않고 각각 표시해요.")
    elif module == "water":
        measured = [a for a in rows if a.payload["kind"] == "measured"]
        value = sum((decimal(a.payload["amount"]) * (1000 if a.payload["unit"] == "l" else 1) for a in measured), ZERO)
        add("마신 물의 양", value if measured else None, "ml")
        if measured:
            primary(value, "ml")
            stats["primary_label"] = "실제로 측정한 음수량"
        kinds = Counter(a.payload["kind"] for a in rows)
        add("물 교체", kinds["replace"] if rows else None, "회")
        add("물 보충", kinds["refill"] if rows else None, "회")
        daily = defaultdict(lambda: ZERO)
        for a in measured:
            daily[day_of(a)] += decimal(a.payload["amount"]) * (1000 if a.payload["unit"] == "l" else 1)
        stats["charts"].append(chart("마신 물의 양", "ml", daily, start, end))
        stats["notes"].append("갈아주거나 보충한 물을 마신 양으로 계산하지 않아요.")
        groups("kind")
    elif module == "toilet":
        for key, label in (("stool", "대변"), ("urine", "소변")):
            add(label, sum(a.payload["kind"] == key for a in rows) if rows else None, "회")
        add("변화 관찰", sum(a.payload["condition"] == "different" for a in rows) if rows else None, "건")
        groups("condition")
    elif module in ("walk", "play", "training"):
        timed = [a for a in rows if a.payload.get("minutes") is not None]
        minutes = sum((decimal(a.payload["minutes"]) for a in timed), ZERO)
        add("활동 시간", minutes if timed else None, "분", f"시간을 입력한 {len(timed)}건만 합산")
        if timed:
            primary(minutes, "분")
            stats["primary_label"] = "시간을 남긴 완료 기록 합계"
        elif rows:
            primary("시간 미입력")
        add("기록당 평균", minutes / len(timed) if timed else None, "분", "시간을 입력한 기록 기준")
        if rows:
            add("시간 미입력", len(rows) - len(timed), "건")
        series_from_payload("minutes", "분", "날짜별 활동 시간")
        if module == "walk":
            known = [a for a in rows if a.payload.get("distance") is not None]
            distance = sum((decimal(a.payload["distance"]) / (1000 if a.payload["distance_unit"] == "m" else 1) for a in known), ZERO)
            add("확인된 이동 거리", distance if known else None, "km", f"거리 미기록 {len(rows) - len(known)}건")
            series_from_payload("distance", "km", "날짜별 확인된 거리", lambda a: Decimal("0.001") if a.payload["distance_unit"] == "m" else Decimal(1))
        elif module == "play":
            groups("kind")
            stats["notes"].append("놀이 반응은 보호자가 보고 남긴 내용이에요. 시간 기록과 함께 봐 주세요.")
        else:
            tracked = [a for a in rows if a.payload.get("attempts") is not None and a.payload.get("successes") is not None]
            attempts = sum((decimal(a.payload["attempts"]) for a in tracked), ZERO)
            successes = sum((decimal(a.payload["successes"]) for a in tracked), ZERO)
            add("시도 횟수", attempts if tracked else None, "회")
            add("성공률", successes / attempts * 100 if attempts else None, "%", "시도 횟수로 가중한 성공률")
            groups("skill")
            by_skill = defaultdict(list)
            for a in tracked:
                by_skill[a.payload.get("skill") or "항목 미기록"].append(a)
            for skill, records in by_skill.items():
                tried = sum((decimal(a.payload["attempts"]) for a in records), ZERO)
                succeeded = sum((decimal(a.payload["successes"]) for a in records), ZERO)
                stats["breakdown"].append({"label":skill + " · 시도 대비 성공", "value":compact(succeeded) + " / " + compact(tried), "unit":"회", "note":compact(succeeded / tried * 100) + "%" if tried else "시도 미기록"})
            stats["notes"].append("훈련 항목이 다르면 난이도도 달라요. 전체 성공률을 능력 점수로 해석하지 않고 항목별 시도와 함께 봐요.")
    elif module == "weight":
        if completed:
            latest = completed[-1]
            kg = decimal(latest.payload["weight"]) * {"kg": Decimal(1), "g": Decimal("0.001"), "lb": Decimal("0.45359237")}[latest.payload["unit"]]
            stats["has_data"] = True
            primary(kg, "kg")
            stats["primary_label"] = "마지막 실제 측정"
            add("마지막 측정 체중", kg, "kg", latest.occurred_at.astimezone(tz).strftime("%Y.%m.%d %H:%M"))
            stats["measurement_date"] = day_of(latest)
            if day_of(latest) < start:
                stats["notes"].append("선택 기간에 새 측정이 없어 이전의 마지막 측정값을 표시해요.")
        daily = {}
        for a in rows:
            daily[day_of(a)] = decimal(a.payload["weight"]) * {"kg": Decimal(1), "g": Decimal("0.001"), "lb": Decimal("0.45359237")}[a.payload["unit"]]
        stats["charts"].append(chart("체중 추이 (같은 날 마지막 측정)", "kg", daily, start, end, "line"))
        if len(rows) >= 2:
            convert = lambda a: decimal(a.payload["weight"]) * {"kg": Decimal(1), "g": Decimal("0.001"), "lb": Decimal("0.45359237")}[a.payload["unit"]]
            add("기간 내 첫·마지막 측정 차이", convert(rows[-1]) - convert(rows[0]), "kg")
        bcs_rows = [a for a in completed if a.payload.get("bcs")]
        ref = Reference.objects.filter(species=room.species, module="weight", metric="bcs", size__in=("", room.size)).order_by("-size").first()
        if bcs_rows and ref:
            latest_bcs = bcs_rows[-1]
            value = Decimal(latest_bcs.payload["bcs"])
            position = "참고 범위 안" if ref.low <= value <= ref.high else "참고 범위보다 낮음" if value < ref.low else "참고 범위보다 높음"
            stats["reference"] = {"title": ref.label, "value": compact(value), "unit": ref.unit,
                                  "range": compact(ref.low) if ref.low == ref.high else compact(ref.low) + "–" + compact(ref.high),
                                  "position": position, "source": ref.source_name, "url": ref.source_url, "conditions": ref.conditions,
                                  "date": day_of(latest_bcs), "assessor": label_choice(module, "bcs_by", latest_bcs.payload.get("bcs_by"))}
        stats["reference_note"] = "평균 체중과 다르다고 저체중이나 비만인 것은 아니에요. 평가한 체형 점수(BCS)가 있다면 따로 비교해요."
        stats["notes"].append("체중은 더하지 않고 측정할 때마다 비교해요. 체형 점수(BCS)는 직접 평가한 값을 사용해요.")
    elif module in ("hygiene", "grooming", "cleaning"):
        field = "target" if module == "cleaning" else "task"
        groups(field)
        last_by_type = {}
        for a in completed:
            last_by_type[a.payload[field]] = a
        for kind, a in last_by_type.items():
            add(str(label_choice(module, field, kind)) + " 최근 관리", a.occurred_at.astimezone(tz).strftime("%m.%d"), "", "마지막 완료 기록")
            if a.payload.get("next_date"):
                stats["notes"].append(str(label_choice(module, field, kind)) + " 다음 예정: " + a.payload["next_date"])
    elif module == "medication":
        given = [a for a in rows if a.payload["result"] == "given"]
        add("투약함", len(given) if rows else None, "회")
        if rows:
            primary(len(given), "회")
        stats["primary_label"] = "실제로 투약한 횟수"
        add("투약하지 못함", len(rows) - len(given) if rows else None, "회")
        per_medicine = Counter((a.payload.get("routine_title") or a.payload.get("medicine") or "이름 없는 이전 기록", a.payload.get("unit") or "") for a in given)
        for (name, unit), count in per_medicine.items():
            stats["breakdown"].append({"label": name + (" · " + UNIT_LABELS.get(unit, unit) if unit else " · 알림 완료"), "value": str(count), "unit": "회"})
        stats["charts"].append(chart("날짜별 실제 투약 횟수", "회", Counter(day_of(a) for a in given), start, end))
        stats["notes"].append("약이나 용량 단위가 다르면 더하지 않아요. 투약 예정만 남긴 기록도 완료 횟수에서 빼요.")
    elif module == "observation":
        add("평소와 다른 관찰", sum(a.payload["condition"] == "different" for a in rows) if rows else None, "건")
        groups("category")
        stats["notes"].append("보호자가 어떤 상태를 기록했는지 모아 봤어요. 질병을 진단하거나 위험도를 매긴 결과는 아니에요.")
    elif module == "veterinary":
        groups("kind")
        if completed:
            add("최근 방문·접종", completed[-1].occurred_at.astimezone(tz).strftime("%Y.%m.%d"))
        stats["notes"].append("다녀온 기록만 완료 횟수에 넣어요. 예약은 따로 확인해 주세요.")
    elif module == "sleep":
        intervals = defaultdict(list)
        for a in completed:
            if a.payload.get("_entry_kind") == "schedule" or a.payload.get("minutes") is None:
                continue
            begin = a.occurred_at.timestamp()
            finish = begin + float(decimal(a.payload["minutes"]) * 60)
            clipped = (max(begin, lower.timestamp()), min(finish, upper.timestamp(), timezone.now().timestamp()))
            if clipped[1] > clipped[0]:
                intervals[a.payload["kind"]].append(clipped)
        observed = union_seconds([i for values in intervals.values() for i in values]) / 60
        if intervals:
            stats["has_data"] = True
            primary(observed, "분")
            stats["primary_label"] = "겹침을 뺀 관찰 시간"
        add("관찰 구간 합계", observed if intervals else None, "분", "서로 겹치는 구간은 한 번만 계산")
        for kind, values in intervals.items():
            add("수면" if kind == "sleep" else "휴식", union_seconds(values) / 60, "분")
            daily = {}
            for day in dates(start, end):
                a, b = bounds(room, day, day)
                pieces = [(max(x, a.timestamp()), min(y, b.timestamp())) for x, y in values if y > a.timestamp() and x < b.timestamp()]
                if pieces:
                    daily[day] = union_seconds(pieces) / 60
            stats["charts"].append(chart("관찰한 " + ("수면" if kind == "sleep" else "휴식") + " 시간", "분", daily, start, end))
        confirmations = [a for a in rows if a.payload.get("_entry_kind") == "schedule"]
        if confirmations:
            add("수면·휴식 알림 확인", len(confirmations), "건", "실제 수면 시간으로 계산하지 않아요")
            if not intervals:
                primary(len(confirmations), "건")
                stats["primary_label"] = "알림 확인 · 실측 시간 아님"
        stats["notes"].append("직접 확인한 시간만 모았어요. 자정을 지나면 날짜별로 나누고 겹친 시간은 한 번만 계산해요.")
    elif module == "environment":
        dimensions = defaultdict(list)
        for a in rows:
            p = a.payload
            value = decimal(p["value"])
            unit = p["unit"]
            if p["metric"] == "temperature" and unit == "F":
                value = (value - 32) * Decimal(5) / 9
                unit = "C"
            dimensions[(p["metric"], p.get("location", "생활공간"), unit)].append((a, value))
        for (kind, location, unit), readings in dimensions.items():
            name = str(label_choice(module, "metric", kind)) + " · " + location
            values = [v for _, v in readings]
            mean = sum(values, ZERO) / len(values)
            stats["breakdown"].append({"label": name, "value": f"최근 {compact(values[-1])} · 평균 {compact(mean)} · 최소 {compact(min(values))} · 최대 {compact(max(values))}", "unit": UNIT_LABELS.get(unit, unit)})
            daily = {}
            for a, value in readings:
                daily[day_of(a)] = value
            if len(stats["charts"]) < 12:
                stats["charts"].append(chart(name, UNIT_LABELS.get(unit, unit), daily, start, end, "line"))
        stats["notes"].append("같은 장소에서 같은 항목을 잰 값끼리 비교해요. 그래프에는 하루의 마지막 측정값, 평균에는 모든 측정값을 사용해요.")
    elif module == "reproduction":
        eggs = [a for a in rows if a.payload["kind"] == "egg"]
        egg_count = sum((decimal(a.payload["eggs"]) for a in eggs), ZERO)
        if eggs:
            add("관찰한 알", egg_count if eggs else None, "개")
        if any(a.payload.get("kind")=="heat" for a in rows):
            add("발정 관찰", sum(a.payload["kind"] == "heat" for a in rows) if rows else None, "건")
        daily = defaultdict(lambda: ZERO)
        for a in eggs:
            daily[day_of(a)] += decimal(a.payload["eggs"])
        if eggs:
            stats["charts"].append(chart("이전 형식: 날짜별 관찰한 알", "개", daily, start, end))
        changes = [a for a in rows if a.payload.get("kind") not in ("heat", "egg")]
        add("생활 변화", len(changes) if changes else None, "건")
        stats["charts"].insert(0, chart("생활 변화가 기록된 날", "건", Counter(day_of(a) for a in changes), start, end))
        stats["notes"].append("생활에서 달라진 일을 모았어요. 다른 변화의 원인이라고 단정하지 않아요. 이전 형식으로 남긴 기록도 볼 수 있어요.")
        stats["events"] = [{"date": day_of(a), "kind": label_choice(module, "kind", a.payload.get("kind")), "detail": a.payload.get("detail", ""), "id": a.pk} for a in reversed(rows)]
        groups("kind")
    elif module == "shedding":
        ongoing = [a for a in all_records if a.status == "active" and not a.voided]
        add("진행 중", len(ongoing) if all_records else None, "건")
        durations = [Decimal(str((a.ended_at.astimezone(dt_timezone.utc) - a.occurred_at.astimezone(dt_timezone.utc)).total_seconds())) / Decimal(86400) for a in rows if a.ended_at]
        add("종료 기록 평균 기간", sum(durations, ZERO) / len(durations) if durations else None, "일", "시작과 종료 시각이 모두 있는 기록만 사용")
        groups("kind")
    elif module == "inventory":
        from .inventory_ledger import records_for, ledger, period_movement, display_quantity
        # The stock read model includes explicitly linked food consumption as well as manual movements.
        cutoff = min(upper - timedelta(microseconds=1), timezone.now())
        stock_rows = records_for(room, until=cutoff)
        if member is not None:
            from .supplies import filter_rows
            stock_rows = filter_rows(member, list(stock_rows))
        result = ledger(stock_rows, until=cutoff)
        low = 0
        for item in result["items"]:
            movement = period_movement(item, lower, upper)
            amount, unit = display_quantity(item["balance"], item["unit"])
            low += int(item["low"])
            stats["breakdown"].append({"label": item["name"] + (" · 구매 필요" if item["low"] else ""), "value": amount, "unit": unit,
                "note": ("사용자 기준 " + compact(item["minimum"]) if item["minimum"] is not None else "구매 기준 미설정") + (" · 사용량 미확인 기록 있음" if not item["complete"] else "")})
            add(item["name"] + " 기간 사용량", movement["used"], unit, "직접 사용 기록과 연결된 급여 제공량을 한 번씩 합산")
            if item["unit"] == "g":
                add(item["name"] + " kg 잔량", item["balance"] / 1000, "kg")
            daily = {}
            current = ZERO
            for event in item["events"]:
                if event["date"] < lower:
                    current = event["after"]
            for day in dates(start,end):
                for event in item["events"]:
                    if event["date"].astimezone(tz).date() == day:
                        current = event["after"]
                if item["events"] and day >= item["events"][0]["date"].astimezone(tz).date():
                    daily[day] = current
            if len(stats["charts"]) < 12:
                stats["charts"].append(chart(item["name"] + " 잔량", unit, daily, start, end, "line"))
        if result["items"]:
            stats["has_data"] = True
            primary(len(result["items"]), "품목")
            stats["primary_label"] = "잔량을 기록한 품목"
        add("구매 필요", low if result["items"] else None, "품목")
        stats["notes"].append("명시적으로 연결한 급여는 꺼내 제공한 양으로 한 번 차감해요. 수정·취소는 같은 원기록에서 재계산하며, 이전의 연결되지 않은 급여를 소급 차감하지 않아요.")
        stats["notes"].extend(issue["reason"] for issue in result["issues"])
    elif module == "expense":
        totals, categories, daily = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO), defaultdict(lambda: defaultdict(lambda: ZERO))
        for a in rows:
            p = a.payload
            amount = decimal(p["amount"])
            totals[p["currency"]] += amount
            categories[(p["category"], p["currency"])] += amount
            daily[p["currency"]][day_of(a)] += amount
        for currency, amount in totals.items():
            add(currency + " 지출", amount, currency)
            stats["charts"].append(chart("날짜별 지출", currency, daily[currency], start, end))
        if len(totals) == 1:
            currency, amount = next(iter(totals.items()))
            primary(amount, currency)
        elif len(totals) > 1:
            primary(len(totals), "개 통화")
        for (category, currency), amount in categories.items():
            stats["breakdown"].append({"label": str(label_choice(module, "category", category)), "value": compact(amount), "unit": currency})
        stats["notes"].append("통화별로 따로 합산해요. 환율을 가정해 다른 통화로 바꾸지 않아요.")
    if not stats["charts"]:
        stats["charts"].append(chart("날짜별 완료 기록", "건", count_daily, start, end))
    stats["notes"].append("완료한 기록만 모았어요. 취소했거나 예정·진행 중인 기록은 제외해요. 빈 날짜는 기록이 없다는 뜻이지 활동을 하지 않았다는 뜻은 아니에요.")
    return stats


def overview(member, end_date=None):
    room = member.room
    today = end_date or local_date(room)
    comparisons = [today, today - timedelta(days=7), today - relativedelta(months=1)]
    allowed = visible_modules(member)
    rows = list(Activity.objects.filter(room=room, module__in=allowed, voided=False, occurred_at__lt=bounds(room, today, today)[1]))
    settings = list(room.module_settings.all())
    used = {a.module for a in rows}
    cards = []
    for setting in settings:
        key = setting.key
        if key not in allowed or not (setting.enabled or key in used):
            continue
        samples = [get_statistics(room, key, endpoint - timedelta(days=6), endpoint, rows) for endpoint in comparisons]
        cards.append({"key": key, "spec": MODULES[key], "enabled": setting.enabled, "current": samples[0], "previous": samples[1], "month": samples[2]})
    return {"cards": cards, "dates": comparisons, "today": today,
            "period_note": "횟수·시간·양은 각 기준일을 포함한 최근 7일을 비교합니다. 체중은 해당 기준일까지의 마지막 측정값, 재고는 해당 기준일 말의 잔량입니다. 1개월 전은 달력 기준입니다."}


def routine_schedule(member, day):
    room = member.room
    allowed = set(visible_modules(member))
    result = []
    enabled_now = set(room.module_settings.filter(enabled=True).values_list("key", flat=True))
    day_end = bounds(room, day, day)[1]
    past_config = room.module_versions.filter(effective_at__lt=day_end).first()
    enabled = enabled_now if day >= local_date(room) else set(past_config.settings.get("enabled", [])) if past_config else set()
    routines = list(room.routines.filter(start_date__lte=day).prefetch_related("versions"))
    runs = {str(r.item_id): r for r in RoutineRun.objects.filter(item__routine__room=room, date=day).select_related("activity")}
    participants = {str(m.pk): m.display_name for m in room.members.all()}
    for routine in routines:
        versions = [v for v in routine.versions.all() if v.effective_on <= day]
        if not versions:
            continue
        snap = versions[0].snapshot
        if not snap["active"] or day.weekday() not in snap["weekdays"] or (snap.get("end_date") and day.isoformat() > snap["end_date"]):
            continue
        items = []
        for item in snap["items"]:
            key = item["module"]
            if key not in allowed:
                continue
            run = runs.get(item["id"])
            activity = run.activity if run and run.activity and not run.activity.voided else None
            skipped = bool(run and run.skipped_at and not activity)
            paused = key not in enabled and activity is None and not skipped
            items.append({"skipped": skipped, "id": item["id"], "key": key, "spec": MODULES[key], "activity": activity,
                          "paused": paused, "done": bool(activity and activity.status == "done"),
                          "status": "완료" if activity and activity.status == "done" else "진행 중" if activity else "건너뜀" if skipped else "기능 사용 안 함" if paused else "완료 기록 없음"})
        if not items:
            continue
        pending_count = sum(not i["done"] and not i["paused"] and not i["skipped"] for i in items)
        scheduled_at = datetime.combine(day, time.fromisoformat(snap["at"]), ZoneInfo(room.timezone))
        result.append({"id": routine.pk, "name": snap["name"] if all(i["module"] in allowed for i in snap["items"]) else "공유된 루틴 활동",
                       "time": snap["at"][:5], "assignee": participants.get(snap.get("assignee_id"), "함께 담당"),
                       "items": items, "done": sum(i["done"] for i in items), "total": sum(not i["paused"] for i in items),
                       "overdue": scheduled_at < timezone.now() and pending_count > 0})
    return sorted(result, key=lambda row: (row["time"], row["name"]))
