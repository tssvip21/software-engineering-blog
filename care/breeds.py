"""Local, versioned breed catalogue. Reference ranges are not individual targets."""
from functools import lru_cache
from pathlib import Path
from decimal import Decimal
import json

SPECIAL = (("", "나중에 선택"), ("mixed", "믹스 / Mixed breed"), ("unknown", "품종 미상 / Unknown"), ("custom", "목록에 없음 · 직접 입력"))
SIZE_LABELS = {"small": "소형", "medium": "중형", "large": "대형"}


@lru_cache(maxsize=1)
def catalogue():
    data = json.loads((Path(__file__).parent / "data/breeds.json").read_text(encoding="utf-8"))
    rows = data["breeds"]
    if len({row["key"] for row in rows}) != len(rows):
        raise ValueError("Duplicate breed keys")
    return {row["key"]: row for row in rows}


def weight_range(breed, sex=""):
    ref = breed.get("reference") or {}
    weights = ref.get("weights") or {}
    selected = [weights[sex]] if sex in weights else list(weights.values())
    if not selected or any(len(pair) != 2 or pair[0] >= pair[1] for pair in selected):
        return None
    factor = Decimal("0.45359237") if ref.get("unit") == "lb" else Decimal(1)
    return min(Decimal(str(p[0])) for p in selected)*factor, max(Decimal(str(p[1])) for p in selected)*factor


def suggest_size(breed, sex=""):
    if not breed or breed["species"] != "dog":
        return ""
    interval = weight_range(breed, sex)
    if not interval:
        return ""
    low, high = interval
    # Navigation bands, not medical classifications. Cross-band breeds need a choice.
    band = lambda kg: "small" if kg <= 10 else "medium" if kg <= 25 else "large"
    return band(low) if band(low) == band(high) else ""


def choices():
    return list(SPECIAL) + [(b["key"], b["ko"] + " · " + b["en"]) for b in sorted(catalogue().values(), key=lambda b: (b["species"], b["ko"]))]


def picker_data():
    return [{"key": b["key"], "species": b["species"], "ko": b["ko"], "en": b["en"], "aliases": b.get("aliases", []),
             "sizes": {s: suggest_size(b,s) for s in ("", "male", "female")}} for b in catalogue().values()]


def reference_for(room, latest_weight=None):
    breed = catalogue().get(getattr(room, "breed_key", ""))
    result = {"available": False, "breed": breed, "message": "품종을 몰라도 기록을 남기고 최근 변화를 볼 수 있어요."}
    if not breed or breed["species"] != room.species:
        return result
    ref = breed.get("reference")
    if not ref or not weight_range(breed, room.sex):
        result["message"] = "이 품종은 비교할 체중 자료가 아직 없어요."
        return result
    low, high = weight_range(breed, room.sex)
    result.update(source=ref["source"], url=ref["url"], checked=ref["checked"], low=round(low,3), high=round(high,3), kind=ref["kind"], sex_label="수컷" if room.sex=="male" else "암컷" if room.sex=="female" else "성별을 몰라 암수 범위를 함께 표시")
    if getattr(room,"maturity", "") != "adult":
        result["message"] = "다 자란 반려동물의 자료예요. 아직 자라는 중이거나 성장 상태를 모르면 이 범위와 비교하지 않아요."
        return result
    result["available"] = True
    result["message"] = "품종 안내에 나온 체중 범위예요. 평균이나 우리 반려동물에게 알맞은 체중을 뜻하지는 않아요."
    if latest_weight:
        kg = Decimal(str(latest_weight["value"]))
        result.update(value=round(kg,3), date=latest_weight["date"], position="참고 범위 안" if low<=kg<=high else "참고 범위보다 아래" if kg<low else "참고 범위보다 위")
    return result
