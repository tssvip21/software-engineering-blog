"""Product snapshots, nutrition and inventory validation."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from care.models import Product
from care.modules import decimal
from care.services.access import fresh_member, require_manager
from care.services.common import Conflict, bump, json_safe


@transaction.atomic
def save_product(member, form, product_id=None, *, _receipt=False):
    member = fresh_member(member)
    require_manager(member)
    if not form.is_valid():
        raise ValidationError("제품 입력값을 확인해 주세요.")
    if product_id is None and form.cleaned_data.get("request_token") and not _receipt:
        from care.services.idempotency import once
        return once(member, form, "product.create", lambda: save_product(member, form, _receipt=True))
    if product_id:
        product = Product.objects.select_for_update().get(pk=product_id, room=member.room)
        if product.version != form.cleaned_data.get("version"):
            raise Conflict("제품 정보가 바뀌었어요. 새로고침한 뒤 다시 확인해 주세요.")
        product.version += 1
    else:
        product = Product(room=member.room)
    for key in form.Meta.fields:
        setattr(product, key, form.cleaned_data[key])
    product.full_clean()
    product.save()
    bump(member.room, member, "product.updated" if product_id else "product.created", {"product_id": str(product.pk)})
    return product


def product_snapshot(product):
    return json_safe({"id": product.pk, "name": product.name, "brand": product.brand, "energy": product.energy, "basis": product.energy_basis,
                      "grams_per_piece": product.grams_per_piece, "protein": product.protein, "protein_kind": product.protein_kind,
                      "fat": product.fat, "fat_kind": product.fat_kind, "source": product.source, "version": product.version})


def nutrition_values(payload, snapshot):
    consumed = decimal(payload.get("consumed"))
    amount = consumed if consumed is not None else decimal(payload.get("quantity"))
    unit = payload.get("unit")
    if amount is None:
        return {"basis": "unknown", "kcal": None, "grams": None, "snapshot": snapshot, "nutrients": {}}
    grams = None
    if unit in ("g", "kg", "oz"):
        grams = amount * {"g": Decimal(1), "kg": Decimal(1000), "oz": Decimal("28.349523125")}[unit]
    elif unit == "piece" and snapshot and snapshot.get("grams_per_piece") is not None:
        grams = amount * decimal(snapshot["grams_per_piece"])
    kcal = None
    if snapshot and snapshot.get("energy") is not None:
        energy, basis = decimal(snapshot["energy"]), snapshot["basis"]
        if basis in ("100g", "kg") and grams is not None:
            kcal = energy * grams / (100 if basis == "100g" else 1000)
        elif basis == "piece" and unit == "piece":
            kcal = energy * amount
        elif basis == "piece" and grams is not None and decimal(snapshot.get("grams_per_piece")):
            kcal = energy * grams / decimal(snapshot["grams_per_piece"])
        elif basis == "100ml" and unit == "ml":
            kcal = energy * amount / 100
    nutrients = {}
    if snapshot and grams is not None:
        for key in ("protein", "fat"):
            if snapshot.get(key) is not None:
                nutrients[key] = {"grams": str((grams * decimal(snapshot[key]) / 100).quantize(Decimal("0.001"))), "kind": snapshot.get(key + "_kind", "exact")}
    return {"basis": "consumed" if consumed is not None else "provided", "kcal": str(kcal.quantize(Decimal("0.001"))) if kcal is not None else None,
            "grams": str(grams) if grams is not None else None, "snapshot": snapshot, "nutrients": nutrients}


def inventory_value(payload):
    unit = payload["unit"]
    factor = {"g": Decimal(1), "kg": Decimal(1000), "ml": Decimal(1), "l": Decimal(1000), "piece": Decimal(1)}[unit]
    base_unit = {"kg": "g", "l": "ml"}.get(unit, unit)
    key = (" ".join(payload["item"].casefold().split()), base_unit)
    return key, decimal(payload["quantity"]) * factor, (decimal(payload.get("minimum")) * factor if payload.get("minimum") is not None else None)


def validate_inventory(room):
    from care.inventory.ledger import validate
    validate(room)
