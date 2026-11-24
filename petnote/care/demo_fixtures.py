"""Synthetic inputs for local demo and automated tests; never imported by request handlers."""
from datetime import timedelta
import uuid
from django.utils import timezone

SAMPLE_FIELDS = {
    "meal": {"food_name": "사료", "quantity": "60", "unit": "g"},
    "treat": {"food_name": "간식", "quantity": "8", "unit": "g", "reason": "놀이 후"},
    "water": {"kind": "measured", "container": "거실 물그릇", "amount": "210", "unit": "ml"},
    "toilet": {"kind": "stool", "condition": "usual", "detail": "평소와 비슷함"},
    "walk": {"minutes": "25", "distance": "1.5", "distance_unit": "km"},
    "play": {"kind": "공놀이", "minutes": "15", "reaction": "engaged"},
    "training": {"skill": "기다려", "minutes": "8", "attempts": "6", "successes": "5"},
    "weight": {"weight": "8.4", "unit": "kg", "bcs": "5", "bcs_by": "guardian"},
    "hygiene": {"task": "teeth", "detail": "양치 완료"},
    "grooming": {"task": "brush", "detail": "빗질 완료"},
    "cleaning": {"target": "bedding", "task": "침구 교체"},
    "medication": {"medicine": "처방 기록", "dose": "1", "unit": "tablet", "result": "given", "instruction": "실제 처방이 아닌 가상 데이터"},
    "observation": {"category": "energy", "condition": "usual", "detail": "평소처럼 활동함"},
    "veterinary": {"kind": "checkup", "clinic": "병원", "detail": "정기 검진"},
    "sleep": {"kind": "sleep", "minutes": "40", "quality": "관찰한 구간만 기록"},
    "environment": {"metric": "temperature", "value": "23.5", "unit": "C", "location": "생활공간"},
    "reproduction": {"kind": "egg", "eggs": "1", "detail": "기능 확인용 시연 기록"},
    "shedding": {"kind": "fur", "detail": "털갈이 기록"},
    "inventory": {"item": "사료", "movement": "in", "quantity": "2", "unit": "kg", "minimum": "0.3"},
    "expense": {"item": "사료 구매", "amount": "32000", "currency": "KRW", "category": "food"},
}

def record_data(module, **changes):
    data = {"client_key": str(uuid.uuid4()), "status": "done", "occurred_at": timezone.localtime(timezone.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"), "note": ""}
    data.update(SAMPLE_FIELDS[module])
    data.update(changes)
    return data
