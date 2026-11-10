"""A single read model for manual stock events and explicitly linked food usage.

A linked meal is itself the consumption event. No second inventory Activity is
created, so retries, corrections and soft cancellation cannot double-deduct it.
Old food records without an explicit binding are never retroactively consumed.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import hashlib

from django.core.exceptions import ValidationError
from django.utils import timezone

ZERO = Decimal(0)
MASS = {"g": Decimal(1), "kg": Decimal(1000), "oz": Decimal("28.349523125")}
VOLUME = {"ml": Decimal(1), "l": Decimal(1000)}


def number(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


def normalized_item(name):
    return " ".join(str(name or "").casefold().split())


def product_binding(product):
    if product is None or not product.stock_item or not product.stock_unit:
        return None
    return {"item": product.stock_item, "unit": product.stock_unit,
            "product_id": str(product.pk), "product_version": product.version,
            "grams_per_piece": str(product.grams_per_piece) if product.grams_per_piece is not None else None}


def food_effect(payload, binding):
    """Convert supplied quantity only; missing quantity stays explicitly unknown."""
    if not binding or not binding.get("item"):
        return None
    target = binding.get("unit")
    if target not in ("g", "ml", "piece"):
        raise ValidationError("연결된 재고의 단위를 확인해 주세요.")
    result = {k: binding.get(k) for k in ("item", "unit", "product_id", "product_version", "grams_per_piece")}
    result.update(schema=1, basis="provided")
    quantity = number(payload.get("quantity"))
    if payload.get("quantity") in (None, ""):
        return {**result, "quantity": None, "unknown": True}
    if quantity is None:
        raise ValidationError("재고에 반영할 제공량을 확인해 주세요.")
    unit = payload.get("unit")
    grams_per_piece = number(binding.get("grams_per_piece"))
    if target == "g" and unit in MASS:
        value = quantity * MASS[unit]
    elif target == "ml" and unit in VOLUME:
        value = quantity * VOLUME[unit]
    elif target == "piece" and unit == "piece":
        value = quantity
    elif target == "g" and unit == "piece" and grams_per_piece:
        value = quantity * grams_per_piece
    elif target == "piece" and unit in MASS and grams_per_piece:
        value = quantity * MASS[unit] / grams_per_piece
    else:
        raise ValidationError("급여 단위와 재고 단위를 바꿀 근거가 없어요. 같은 단위로 연결하거나 제품의 개당 무게를 등록해 주세요.")
    if target == "piece" and value % 1:
        raise ValidationError("개수 재고에는 정수 개수만 반영할 수 있어요. 나누어 쓰는 먹이는 무게 재고로 연결해 주세요.")
    return {**result, "quantity": str(value), "unknown": False}


def records_for(room, *, until=None):
    from .models import Activity
    return Activity.objects.filter(room=room, module__in=("inventory", "meal", "treat"),
                                   status="done", voided=False, occurred_at__lte=until or timezone.now()).order_by("occurred_at", "created_at", "id")


def events_from(records, *, until=None, strict=False):
    """Yield normalized events; invalid legacy values are surfaced, never made zero."""
    output, issues = [], []
    unique = {str(row.pk): row for row in records}
    for row in sorted(unique.values(), key=lambda a: (a.occurred_at, a.created_at, str(a.pk))):
        if row.voided or row.status != "done" or (until and row.occurred_at > until):
            continue
        p = row.payload
        event = None
        if row.module == "inventory":
            unit = p.get("unit")
            factors = {**MASS, **VOLUME, "piece": Decimal(1)}
            quantity = number(p.get("quantity"))
            minimum = number(p.get("minimum"))
            if unit not in factors or quantity is None or not normalized_item(p.get("item")) or p.get("movement") not in ("in", "out", "set"):
                issues.append({"id": str(row.pk), "reason": "재고 기록의 품목·단위·수량 확인 필요"})
                continue
            base = "g" if unit in MASS else "ml" if unit in VOLUME else "piece"
            event = {"name": str(p["item"]), "unit": base, "amount": quantity * factors[unit],
                     "minimum": minimum * factors[unit] if minimum is not None else None,
                     "movement": p["movement"], "linked": False, "unknown": False}
        elif row.module in ("meal", "treat") and p.get("_stock"):
            stock = p["_stock"]
            if not isinstance(stock, dict) or stock.get("unit") not in ("g", "ml", "piece") or not normalized_item(stock.get("item")):
                issues.append({"id": str(row.pk), "reason": "급여 기록의 재고 연결 확인 필요"})
                continue
            value = number(stock.get("quantity"))
            event = {"name": str(stock["item"]), "unit": stock["unit"], "amount": value,
                     "minimum": None, "movement": "out", "linked": True, "unknown": value is None}
        if event is not None:
            event.update(id=str(row.pk), module=row.module, date=row.occurred_at,
                         key=(normalized_item(event["name"]), event["unit"]))
            output.append(event)
    if strict and issues:
        raise ValidationError("기존 재고 연결 중 확인할 값이 있어요. 해당 기록을 수정한 뒤 다시 저장해 주세요.")
    return output, issues


def ledger(records, *, until=None, strict=False):
    events, issues = events_from(records, until=until, strict=strict)
    items = {}
    for event in events:
        key = event["key"]
        item = items.setdefault(key, {"key": key, "id": hashlib.sha256((key[0]+"\0"+key[1]).encode()).hexdigest()[:20],
            "name": event["name"], "unit": event["unit"], "balance": ZERO, "minimum": None,
            "events": [], "unknown_ids": [], "negative": False})
        event = dict(event, before=item["balance"])
        if event["unknown"]:
            item["unknown_ids"].append(event["id"])
        else:
            value = event["amount"]
            item["balance"] = value if event["movement"] == "set" else item["balance"] + (value if event["movement"] == "in" else -value)
            if event["movement"] == "set":
                # A real stocktake anchors subsequent quantity; older unknown use remains in history.
                item["unknown_ids"] = []
            if item["balance"] < 0:
                item["negative"] = True
                if strict:
                    raise ValidationError(f"'{item['name']}'의 재고가 부족해요. 입고·실사 수량 또는 제품의 재고 연결을 확인해 주세요. 기록은 저장하지 않았어요.")
        if event["minimum"] is not None:
            item["minimum"] = event["minimum"]
        event["after"] = item["balance"]
        item["events"].append(event)
    for item in items.values():
        item["low"] = item["minimum"] is not None and item["balance"] <= item["minimum"]
        item["complete"] = not item["unknown_ids"] and not item["negative"]
    return {"items": sorted(items.values(), key=lambda x: (x["key"][0], x["unit"])), "issues": issues, "events": events}


def validate(room):
    ledger(records_for(room), until=timezone.now(), strict=True)


def period_movement(item, lower, upper):
    chosen = [e for e in item["events"] if lower <= e["date"] < upper]
    complete = [e for e in chosen if not e["unknown"]]
    return {"used": sum((e["amount"] for e in complete if e["movement"] == "out"), ZERO),
            "linked_used": sum((e["amount"] for e in complete if e["linked"]), ZERO),
            "received": sum((e["amount"] for e in complete if e["movement"] == "in"), ZERO),
            "adjustments": sum(e["movement"] == "set" for e in complete),
            "unknown": sum(e["unknown"] for e in chosen), "records": len(chosen),
            "ids": [e["id"] for e in chosen]}


def display_quantity(value, unit, *, prefer_large=False):
    from .modules import compact
    if prefer_large and unit == "g":
        return compact(value / 1000), "kg"
    if prefer_large and unit == "ml":
        return compact(value / 1000), "L"
    return compact(value), "개" if unit == "piece" else unit
