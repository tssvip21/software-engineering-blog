"""Plain Korean results and short, permission-scoped supporting data."""
from datetime import timedelta
from dateutil.relativedelta import relativedelta
from django.urls import reverse
from django.utils import timezone
from .assessment import assess
from .breeds import reference_for
from .insights import evidence_url
from .models import Activity
from .modules import compact
from .services import fresh_member, local_date, visible_modules
from .stats import bounds, get_statistics

NAMES = {
    "weight": ("체중", "kg"), "food_actual": ("먹은 음식의 열량", "kcal"),
    "food_offered": ("준 음식의 예상 열량", "kcal"), "food_mass": ("준 먹이의 양", "g"),
    "meal_gap": ("기록한 식사 사이의 간격", "시간"), "activity": ("기록한 활동 시간", "분"),
    "rest": ("기록한 수면·휴식 시간", "분"), "water": ("측정한 음수량", "ml"),
}


def describe_metric(metric):
    name, unit = NAMES[metric["key"]]
    direction = metric["direction"]
    if metric.get("recording_changed"):
        title = name + ": 기록한 방식도 달라졌어요"
        detail = "기록한 종류나 횟수가 달라 양과 시간의 차이를 따로 확인할 필요가 있어요."
        state = "recording"
    elif direction:
        title = name + ("이 늘었어요" if metric["key"] != "weight" else "이 늘었어요") if direction > 0 else name + ("이 줄었어요")
        # Korean subjects, not a generic English-to-Korean suffix concatenation.
        stems = {"weight": "체중이", "food_actual": "먹은 음식의 열량이", "food_offered": "준 음식의 예상 열량이", "food_mass": "준 먹이의 양이", "meal_gap": "기록한 식사 간격이", "activity": "기록한 활동 시간이", "rest": "기록한 수면·휴식 시간이", "water": "측정한 음수량이"}
        title = stems[metric["key"]] + (" 늘었어요" if direction > 0 else " 줄었어요")
        detail = "최근 기록에서 같은 방향의 변화가 이어졌어요."
        state = "up" if direction > 0 else "down"
    elif metric["excursion_days"] and metric["state"] == "steady":
        title = name + ": 평소와 크게 다른 날이 있어요"
        detail = "계속된 변화는 아니에요. 해당 날짜의 기록값을 확인해 주세요."
        state = "check"
    elif metric["state"] == "steady":
        title = name + ": 최근에도 비슷해요"
        detail = "비교할 수 있는 기록에서 두드러진 변화는 없었어요."
        state = "steady"
    else:
        title = name + ": 비교할 기록이 더 필요해요"
        detail = "지금까지 남긴 값은 아래에서 확인할 수 있어요."
        state = "limited"
    old = [p for p in metric["points"] if not p["recent"]]
    new = [p for p in metric["points"] if p["recent"]]
    def value_range(points):
        if not points:
            return "기록 없음"
        low, high = min(p["value"] for p in points), max(p["value"] for p in points)
        return compact(low) + ("–" + compact(high) if low != high else "") + " " + unit
    return {"key": metric["key"], "name": name, "title": title, "detail": detail, "state": state,
            "old_range": value_range(old), "recent_range": value_range(new), "old_days": len(old), "recent_days": len(new),
            "daily_total": metric["key"] not in ("weight", "meal_gap"),
            "values": [{"date": p["date"], "value": compact(p["value"]), "unit": unit,
                        "different": p["date"] in metric["excursion_days"]} for p in reversed(new[-7:])],
            "baseline_start": metric["baseline_start"], "baseline_end": metric["baseline_end"],
            "start": metric["start"], "end": metric["end"], "modules": metric["modules"]}


def plain_result(result):
    metrics = result["metrics"]
    changed = [m for m in metrics if m["key"] in result["confirmed_changed_keys"]]
    code = result["code"]
    titles = {
        "observed": "평소와 다르다고 남긴 기록이 있어요",
        "mixed": "서로 다른 방향의 변화가 함께 나타났어요",
        "linked": "같은 시기에 함께 달라진 기록이 있어요",
        "multiple": "여러 기록에서 변화가 이어졌어요",
        "single": describe_metric(changed[0])["title"] if changed else "최근 기록에 변화가 있어요",
        "check": "평소와 크게 다른 기록값을 확인해 주세요",
        "recording": "기록한 방식이 달라져 비교에 주의가 필요해요",
        "steady": "비교한 기록은 평소와 비슷해요",
        "collecting": "아직은 최근과 이전을 비교하기 어려워요",
    }
    if changed:
        description = " · ".join(describe_metric(m)["title"].removesuffix("어요") for m in changed) + "요."
        # Independent clauses avoid misleading one-cause or causal language.
        description = " ".join(describe_metric(m)["title"] + "." for m in changed)
    elif code == "check":
        description = "한 번의 큰 차이와 계속된 변화를 구분했어요. 입력값이나 측정 상황을 확인해 주세요."
    elif code == "recording":
        description = "기록한 활동 종류나 횟수도 달라졌어요. 실제 돌봄이 변했는지는 기록을 함께 살펴봐야 해요."
    elif code == "observed":
        description = "수치가 많지 않아도 직접 남긴 관찰은 따로 확인할 수 있어요."
    elif code == "steady":
        description = "기록이 충분한 항목에서 두드러진 변화는 없었어요. 기록이 없는 항목까지 확인한 것은 아니에요."
    else:
        description = "기록을 더 모으면 우리 반려동물의 평소 모습과 비교할 수 있어요. 기록하지 않은 날을 0으로 계산하지 않았어요."
    return {"code": code, "title": titles[code], "description": description,
            "as_of": result["as_of"], "start": result["start"], "baseline_start": result["baseline_start"],
            "baseline_end": result["baseline_end"], "count": result["record_count"], "days": result["recorded_days"]}


def context_for(member, end_date=None):
    member = fresh_member(member)
    room = member.room
    today = local_date(room)
    # Incomplete days are not compared with complete historical daily totals.
    end = end_date or today - timedelta(days=1)
    if end >= today:
        end = today - timedelta(days=1)
    endpoints = [end, end - timedelta(days=7), end - relativedelta(months=1)]
    allowed = set(visible_modules(member))
    lower, upper = bounds(room, endpoints[-1] - timedelta(days=37), end)
    rows = list(Activity.objects.filter(room=room, module__in=allowed, voided=False, status="done", occurred_at__gte=lower, occurred_at__lt=upper).order_by("occurred_at", "id"))
    results = [assess(rows, day, room.timezone) for day in endpoints]
    current = results[0]
    cards = [describe_metric(m) for m in current["metrics"] if m["current"] is not None or m["baseline"] is not None]
    priority = {"up": 0, "down": 0, "check": 1, "recording": 2, "steady": 3, "limited": 4}
    cards.sort(key=lambda c: (priority[c["state"]], c["key"] != "weight"))
    for card in cards:
        card["url"] = evidence_url(room, [m for m in card["modules"] if m in allowed], card["baseline_start"], end)
    relationships = []
    labels = {m["key"]: NAMES[m["key"]][0] for m in current["metrics"]}
    for relation in current["relations"]:
        relationships.append({"title": " · ".join(labels[k] for k in relation["metric_keys"]),
                              "text": "서로 다른 방향의 변화예요. 한쪽 값만 보고 판단하지 마세요." if relation["kind"] == "mixed" else "같은 날짜에 변화가 함께 나타났어요. 서로의 원인이라는 뜻은 아니에요.",
                              "days": relation["overlap_dates"], "url": evidence_url(room, [m for m in relation["modules"] if m in allowed], current["start"], end)})
    now_lower, now_upper = bounds(room, today, today)
    new_observations = list(Activity.objects.filter(room=room, module__in=allowed & {"observation", "toilet"}, voided=False, status="done", payload__condition="different", occurred_at__gte=now_lower, occurred_at__lt=now_upper, occurred_at__lte=timezone.now()).order_by("-occurred_at")[:5])
    observations = list(Activity.objects.filter(room=room, pk__in=current["observation_ids"], module__in=allowed).order_by("-occurred_at")[:5])
    summary = plain_result(current)
    if new_observations:
        summary.update(code="observed", title="오늘 평소와 다르다고 남긴 기록이 있어요", description="오늘 남긴 관찰부터 확인해 주세요. 이전 날짜의 변화는 아래에서 함께 볼 수 있어요.")
    observation_rows = [{"date": a.occurred_at, "name": "배변" if a.module == "toilet" else "상태 관찰", "url": reverse("record_detail", args=[room.pk, a.pk])} for a in new_observations + observations]
    weight = get_statistics(room, "weight", today - timedelta(days=6), today) if "weight" in allowed else {}
    latest = {"value": weight["primary"], "date": weight["measurement_date"]} if weight.get("measurement_date") else None
    return {"summary": summary, "status_cards": cards, "relationships": relationships,
            "status_periods": [plain_result(r) for r in results], "observations": observation_rows,
            "breed_reference": reference_for(room, latest), "body_reference": weight.get("reference"),
            "day": today, "insight": current, "dates": endpoints, "today": today}
