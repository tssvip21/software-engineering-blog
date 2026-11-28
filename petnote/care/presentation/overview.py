"""Purpose-specific views over the same activity data; no synthetic health scores."""
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.urls import reverse

from care.models import Activity
from care.modules import MODULES, compact, decimal
from care.reporting.stats import bounds, overview
from care.services import visible_modules

GROUPS = {
    "daily": ("식사·물·배변", ("meal", "treat", "water", "toilet")),
    "activity": ("활동·휴식", ("walk", "play", "training", "sleep")),
    "health": ("건강·생활 변화", ("weight", "medication", "observation", "veterinary", "reproduction", "shedding")),
    "care": ("생활·환경 관리", ("hygiene", "grooming", "cleaning", "environment", "inventory", "expense")),
}


def integrated_context(member):
    room = member.room
    context = overview(member)
    today = context["today"]
    start = today - timedelta(days=6)
    lower, upper = bounds(room, start, today)
    allowed = set(visible_modules(member))
    records = list(Activity.objects.filter(room=room, module__in=allowed, voided=False, status="done", occurred_at__gte=lower, occurred_at__lt=upper).order_by("-occurred_at"))
    tz = ZoneInfo(room.timezone)
    lookup = {card["key"]: card for card in context["cards"]}
    stories = []
    def story(title, text, note, module, detail=None, modules=None):
        stories.append({"title": title, "text": text, "note": note, "module": module,
                        "url": (reverse("records", args=[room.pk]) + "?" + urlencode({"modules": ",".join(sorted(set(modules))), "start": start.isoformat(), "end": today.isoformat(), "state": "done"}) if modules else reverse("statistics", args=[room.pk, module]) + "?" + urlencode({"start": start.isoformat(), "end": today.isoformat()})), "reference": detail})
    foods = [a for a in records if a.module in ("meal", "treat")]
    if foods:
        known = [a for a in foods if a.nutrition.get("kcal") is not None]
        text = f"식사·간식을 {len(foods)}번 기록했어요."
        for basis, label in (("consumed", "섭취량을 확인한 기록"), ("provided", "제공량 기준 예상치")):
            selected = [a for a in known if a.nutrition.get("basis") == basis]
            if selected:
                kcal = sum((decimal(a.nutrition["kcal"]) for a in selected), Decimal(0))
                text += f" {label} {len(selected)}건은 {compact(kcal)} kcal예요."
        story("먹은 것과 기록한 양", text, f"열량 미확인 {len(foods)-len(known)}건. 실제 섭취량과 제공량 기준 예상치는 따로 읽어 주세요.", foods[0].module, modules=[a.module for a in foods])
    activities = [a for a in records if a.module in ("walk", "play", "training")]
    if activities:
        timed = [a for a in activities if a.payload.get("minutes") is not None]
        minutes = sum((decimal(a.payload["minutes"]) for a in timed), Decimal(0))
        text = f"산책·놀이·훈련을 {len(activities)}번 기록했어요."
        text += f" 시간을 남긴 활동은 총 {compact(minutes)}분이에요." if timed else " 활동 시간은 아직 입력하지 않았어요."
        story("함께 보낸 활동 시간", text, f"시간 미입력 {len(activities)-len(timed)}건은 0분으로 계산하지 않아요.", max(Counter(a.module for a in activities), key=Counter(a.module for a in activities).get), modules=[a.module for a in activities])
    weight = lookup.get("weight")
    if weight and weight["current"].get("measurement_date"):
        current = weight["current"]
        note = "체중만으로 적정 체형을 판단하지 않아요."
        if current["measurement_date"] < start:
            note = "선택 기간에 새 측정이 없어 이전 마지막 측정값을 표시해요. " + note
        story("최근 측정한 체중", f"마지막 체중은 {current['primary']} kg이에요. ({current['measurement_date'].strftime('%m.%d')} 측정)", note, "weight", current.get("reference"))
    observations = [a for a in records if a.module == "observation" and a.payload.get("condition") == "different"]
    changes = [a for a in records if a.module == "reproduction" and a.payload.get("kind") not in ("heat", "egg")]
    if observations or changes:
        story("살펴볼 변화", f"평소와 다른 상태 관찰 {len(observations)}건, 생활 변화 {len(changes)}건을 남겼어요.", "기록 시점을 함께 살펴보되, 변화의 원인이나 질병을 자동 판정하지 않아요.", "observation" if observations else "reproduction")
    care = [a for a in records if a.module in ("hygiene", "grooming", "cleaning", "environment")]
    if care:
        names = Counter(MODULES[a.module].name for a in care)
        description = ", ".join(f"{name} {n}건" for name, n in names.items())
        story("생활을 돌본 기록", description + "을 남겼어요.", "서로 다른 관리 활동의 수행 횟수예요. 관리 효과를 점수로 환산하지 않아요.", care[0].module, modules=[a.module for a in care])
    for key in ("medication", "veterinary", "inventory"):
        card = lookup.get(key)
        if not card or not card["current"]["has_data"]:
            continue
        if key == "inventory":
            low = next((m["value"] for m in card["current"]["metrics"] if m["label"] == "구매 필요"), "0")
            text = f"사용자가 정한 구매 기준에 해당하는 품목이 {low}개 있어요."
            note = "재고는 이전 입고·사용 기록까지 반영한 잔량이에요."
        else:
            text = f"최근 7일의 완료 기록이 {card['current']['count']}건 있어요."
            note = "예정과 완료를 구분하며, 기록하지 않은 방문·투약은 추정하지 않아요."
        story(MODULES[key].name + " 확인", text, note, key)
    columns = [today-timedelta(days=d) for d in reversed(range(7))]
    distribution = []
    for group, (name, keys) in GROUPS.items():
        permitted = set(keys) & allowed
        group_rows = [a for a in records if a.module in permitted]
        counts = Counter(a.occurred_at.astimezone(tz).date() for a in group_rows)
        cells = []
        for day in columns:
            count = counts[day]
            cells.append({"day": day, "count": count, "density": min(4, count),
                          "url": reverse("records", args=[room.pk])+"?"+urlencode({"group": group, "start": day.isoformat(), "end": day.isoformat(), "state": "done"})})
        distribution.append({"name": name, "cells": cells, "total": len(group_rows)})
    primary_keys = ("meal", "walk", "weight", "water", "sleep", "expense")
    primary = [lookup[k] for k in primary_keys if k in lookup]
    other = [card for card in context["cards"] if card["key"] not in primary_keys]
    # Keep the first screen bounded without losing less frequent care information.
    priority = {"observation": 0, "reproduction": 0, "weight": 1, "meal": 2, "treat": 2,
                "walk": 3, "play": 3, "training": 3}
    ranked = sorted(stories, key=lambda item: priority.get(item["module"], 4))
    context.update({"stories": stories, "summary_primary": ranked[:4], "summary_additional": ranked[4:],
                    "distribution": distribution, "distribution_days": columns,
                    "comparison_primary": primary, "comparison_other": other, "record_count": len(records),
                    "recorded_days": len({a.occurred_at.astimezone(tz).date() for a in records}), "start": start,
                    "life_events": changes[:4], "has_records": bool(records or stories)})
    return context
