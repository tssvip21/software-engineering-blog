"""Typed input definitions shared by forms, validation and presentation."""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str = "text"
    required: bool = True
    choices: tuple = ()
    minimum: str | None = None
    maximum: str = "999999999"
    help: str = ""


def text(key, label, required=True, help=""):
    return FieldSpec(key, label, "text", required, help=help)


def number(key, label, required=True, minimum="0", maximum="999999999", help=""):
    return FieldSpec(key, label, "number", required, minimum=minimum, maximum=maximum, help=help)


def choice(key, label, values, required=True, help=""):
    return FieldSpec(key, label, "choice", required, tuple(values), help=help)


def day(key, label, required=False):
    return FieldSpec(key, label, "date", required)


@dataclass(frozen=True)
class ModuleSpec:
    key: str
    name: str
    group: str
    icon: str
    description: str
    fields: tuple
    statuses: tuple = ("done",)
    sensitive: bool = False


FOOD = (
    text("food_name", "제품·먹이 이름", False, "등록한 제품을 선택했다면 비워 두어도 됩니다."),
    number("quantity", "제공한 양", minimum="0.001"),
    choice("unit", "단위", (("g", "g"), ("kg", "kg"), ("oz", "oz"), ("piece", "개"), ("ml", "ml"))),
    number("consumed", "확인한 실제 섭취량", False, help="모르면 비워 두세요. 제공량과 같은 단위입니다. 0도 입력할 수 있어요."),
)
MODULE_LIST = (
    ModuleSpec("meal", "식사", "일상 돌봄", "bowl", "먹이와 급여량, 확인 가능한 영양 정보를 기록해요.", FOOD),
    ModuleSpec("treat", "간식", "일상 돌봄", "treat", "식사와 구분해서 간식의 양과 이유를 남겨요.", FOOD + (text("reason", "급여 이유", False),)),
    ModuleSpec("water", "물 관리", "일상 돌봄", "drop", "물 교체와 실제로 측정한 음수량을 구분해요.", (
        choice("kind", "기록 종류", (("replace", "물 교체"), ("refill", "물 보충"), ("measured", "실제 음수량 측정"))),
        text("container", "물그릇·급수기", False), number("amount", "측정하거나 보충한 양", False),
        choice("unit", "단위", (("ml", "ml"), ("l", "L"))),
    )),
    ModuleSpec("toilet", "배변", "일상 돌봄", "toilet", "배변 횟수와 직접 관찰한 상태를 남겨요.", (
        choice("kind", "종류", (("stool", "대변"), ("urine", "소변"))),
        choice("condition", "관찰 상태", (("usual", "평소와 같음"), ("different", "평소와 다름"), ("unknown", "확인하지 못함"))),
        text("detail", "관찰한 내용", False),
    )),
    ModuleSpec("walk", "산책", "활동", "walk", "산책을 시작하고 종료하거나 지난 산책을 기록해요.", (
        number("minutes", "산책 시간 (분)", False, minimum="0.001", maximum="1440", help="종료 시각을 입력하면 시작 시각과의 차이로 계산해요."),
        number("distance", "이동 거리", False), choice("distance_unit", "거리 단위", (("km", "km"), ("m", "m"))),
    ), ("done", "active")),
    ModuleSpec("play", "놀이", "활동", "ball", "놀이 종류, 시간과 반응을 기록해요.", (
        text("kind", "놀이 종류"), number("minutes", "놀이 시간 (분)", minimum="0.001", maximum="1440"),
        choice("reaction", "반응", (("engaged", "잘 참여함"), ("neutral", "보통"), ("little", "관심 적음"), ("unknown", "모름"))),
    )),
    ModuleSpec("training", "훈련", "활동", "target", "훈련 항목과 연습 시간, 확인한 시도·성공을 남겨요.", (
        text("skill", "훈련 항목"), number("minutes", "훈련 시간 (분)", minimum="0.001", maximum="1440"),
        number("attempts", "시도 횟수", False, minimum="1", maximum="100000"),
        number("successes", "성공 횟수", False, maximum="100000"),
    )),
    ModuleSpec("weight", "체중", "건강 기록", "scale", "최근 측정값과 변화를 확인해요. 체중은 합산하지 않아요.", (
        number("weight", "체중", minimum="0.001"), choice("unit", "단위", (("kg", "kg"), ("g", "g"), ("lb", "lb"))),
        choice("bcs", "체형 점수 BCS (9점 척도)", tuple((str(i), str(i) + " / 9") for i in range(1, 10)), False, "개·고양이만 해당합니다. 평가한 적이 없다면 비워 두세요. 체중에서 자동 계산하지 않아요."),
        choice("bcs_by", "BCS 평가자", (("guardian", "보호자 관찰"), ("vet", "수의사 평가")), False),
    )),
    ModuleSpec("hygiene", "위생", "생활 관리", "spark", "목욕·양치 등의 관리일과 다음 일정을 확인해요.", (
        choice("task", "관리 항목", (("bath", "목욕"), ("teeth", "양치"), ("ear", "귀 세정"), ("paw", "발 세정"), ("other", "기타"))),
        day("next_date", "다음 관리 예정일"), text("detail", "관리 내용", False),
    )),
    ModuleSpec("grooming", "미용", "생활 관리", "scissors", "털과 발톱 손질 이력을 기록해요.", (
        choice("task", "손질 항목", (("brush", "빗질"), ("hair", "털 손질"), ("nail", "발톱 관리"), ("other", "기타"))),
        day("next_date", "다음 손질 예정일"), text("detail", "손질 내용", False),
    )),
    ModuleSpec("cleaning", "생활공간 청소", "생활 관리", "clean", "화장실·케이지·수조 등 대상별 청소를 남겨요.", (
        choice("target", "청소 대상", (("toilet", "화장실"), ("cage", "케이지"), ("tank", "수조"), ("bedding", "침구"), ("other", "기타"))),
        text("task", "청소 내용"), day("next_date", "다음 청소 예정일"),
    )),
    ModuleSpec("medication", "투약", "건강 기록", "pill", "처방·지시받은 용량과 실제 투약을 기록해요.", (
        text("medicine", "약 이름"), number("dose", "처방·지시받은 용량", minimum="0.001"),
        choice("unit", "단위", (("mg", "mg"), ("ml", "ml"), ("tablet", "정"), ("drop", "방울"), ("dose", "회분"))),
        choice("result", "투약 여부", (("given", "투약함·투약 예정"), ("skipped", "투약하지 못함"))),
        text("instruction", "처방·지시 내용", False, "이 서비스는 투약 용량을 추천하거나 계산하지 않아요."),
    ), ("done", "planned"), True),
    ModuleSpec("observation", "상태 관찰", "건강 기록", "eye", "식욕·행동 등 직접 관찰한 변화를 기록해요.", (
        choice("category", "관찰 항목", (("appetite", "식욕"), ("energy", "활동성"), ("behavior", "행동"), ("skin", "피부·털"), ("other", "기타"))),
        choice("condition", "평소와 비교", (("usual", "평소와 같음"), ("different", "변화 있음"), ("unknown", "판단하기 어려움"))),
        text("detail", "관찰 내용"),
    )),
    ModuleSpec("veterinary", "병원·예방접종", "건강 기록", "cross", "예약과 방문·접종 완료 이력을 구분해요.", (
        choice("kind", "방문 종류", (("visit", "진료"), ("vaccine", "예방접종"), ("checkup", "검진"))),
        text("clinic", "병원·기관", False), text("detail", "진료·접종 내용"), day("next_date", "다음 방문 예정일"),
    ), ("done", "planned"), True),
    ModuleSpec("sleep", "수면·휴식", "활동", "moon", "직접 관찰한 시간만 기록해요. 빈 구간은 추정하지 않아요.", (
        choice("kind", "종류", (("sleep", "수면"), ("rest", "휴식"))),
        number("minutes", "관찰한 시간 (분)", minimum="0.001", maximum="4320", help="활동 시각은 관찰 시작 시각입니다. 같은 종류의 겹치는 관찰 구간은 통계에서 한 번만 계산해요."),
        text("quality", "관찰한 특징", False),
    )),
    ModuleSpec("environment", "사육환경", "환경·생애", "thermometer", "같은 지표와 위치의 측정값끼리 비교합니다.", (
        choice("metric", "측정 항목", (("temperature", "온도"), ("humidity", "습도"), ("light", "조명 시간"), ("ph", "수질 pH"))),
        number("value", "측정값", minimum="-999"),
        choice("unit", "단위", (("C", "°C"), ("F", "°F"), ("percent", "%"), ("hours", "시간"), ("ph", "pH"))),
        text("location", "측정 위치", False, "예: 케이지 안, 수조. 위치가 다르면 통계를 분리합니다."),
    )),
    ModuleSpec("reproduction", "생활 변화", "환경·생활", "leaf", "성장이나 먹이·환경·관리 방식의 변화를 남겨요.", (
        choice("kind", "변화 종류", (("growth", "성장·생활 단계"), ("food_change", "먹이 변경"), ("environment_change", "생활환경 변경"), ("care_change", "관리 방식 변경"), ("other", "기타 변화"), ("heat", "이전 형식: 발정 관찰"), ("egg", "이전 형식: 산란"))),
        text("detail", "달라진 점", False, "예: 새 먹이로 바꿨어요. 변화 전후 상태는 다른 기록과 함께 확인해요."),
        day("next_date", "다시 살펴볼 날짜"),
        number("eggs", "이전 기록의 알 개수", False, maximum="100000"),
    )),
    ModuleSpec("shedding", "탈피·털갈이", "환경·생애", "feather", "시작과 종료, 진행 상태를 기록해요.", (
        choice("kind", "종류", (("molt", "탈피"), ("fur", "털갈이"))), text("detail", "관찰 내용", False),
    ), ("active", "done")),
    ModuleSpec("inventory", "용품·재고", "생활 관리", "box", "입고·사용·실사 조정을 구분해 잔량을 계산해요.", (
        text("item", "품목"), choice("movement", "변동 종류", (("in", "입고"), ("out", "사용"), ("set", "실사 수량으로 조정"))),
        number("quantity", "수량"), choice("unit", "단위", (("piece", "개"), ("g", "g"), ("kg", "kg"), ("ml", "ml"), ("l", "L"))),
        number("minimum", "구매 알림 기준 수량", False, help="사용자가 정한 기준입니다. 재고가 이 수량 이하이면 구매 필요로 표시해요."),
    )),
    ModuleSpec("expense", "지출", "생활 관리", "wallet", "통화가 다른 지출은 별도로 합산해요.", (
        text("item", "지출 내용"), number("amount", "금액", minimum="0.001"),
        choice("currency", "통화", (("KRW", "원 (KRW)"), ("USD", "달러 (USD)"), ("EUR", "유로 (EUR)"), ("JPY", "엔 (JPY)"), ("CNY", "위안 (CNY)"))),
        choice("category", "분류", (("food", "먹이·간식"), ("medical", "병원·건강"), ("supplies", "용품"), ("care", "돌봄 서비스"), ("other", "기타"))),
    ), ("done",), True),
)
MODULES = {m.key: m for m in MODULE_LIST}
MODULE_CHOICES = tuple((m.key, m.name) for m in MODULE_LIST)
SENSITIVE_KEYS = tuple(m.key for m in MODULE_LIST if m.sensitive)
DEFAULT_MODULES = {
    "dog": ("meal", "treat", "water", "walk", "weight"),
    "cat": ("meal", "treat", "water", "toilet", "weight"),
    "bird": ("cleaning", "meal", "training", "water", "weight"),
    "reptile": ("meal", "environment", "cleaning", "shedding"),
    "amphibian": ("meal", "environment", "cleaning", "observation"),
    "other": ("meal", "water", "observation"),
}
UNIT_LABELS = {"g": "g", "kg": "kg", "oz": "oz", "piece": "개", "ml": "ml", "l": "L", "lb": "lb", "tablet": "정", "drop": "방울", "dose": "회분", "percent": "%", "hours": "시간", "C": "°C", "F": "°F", "ph": "pH"}


def decimal(value):
    if value in (None, ""):
        return None
    return Decimal(str(value))


def compact(value):
    if value is None:
        return "미기록"
    d = Decimal(str(value)).quantize(Decimal("0.001"))
    return format(d, "f").rstrip("0").rstrip(".") if d % 1 else str(int(d))


def payload_display(module, payload):
    rows = []
    for spec in MODULES[module].fields:
        value = payload.get(spec.key)
        if value is None or value == "":
            continue
        if spec.kind == "choice":
            value = dict(spec.choices).get(str(value), value)
        rows.append((spec.label, str(value)))
    return rows


def record_summary(module, payload):
    """Short activity descriptions retain quantities and units."""
    p = payload
    def value(key):
        v = p.get(key)
        return compact(v) if v is not None else "미기록"
    def choice_name(key):
        field = next((f for f in MODULES[module].fields if f.key == key), None)
        return str(dict(field.choices).get(str(p.get(key)), p.get(key, ""))) if field else str(p.get(key, ""))
    unit = lambda key="unit": UNIT_LABELS.get(p.get(key), p.get(key, ""))
    if module in ("meal", "treat"):
        return f"{p.get('food_name') or '급여 완료'} · {value('quantity') + ' ' + unit() if p.get('quantity') is not None else '양 미입력'}"
    if module == "water":
        amount = f" · {value('amount')} {unit()}" if p.get("amount") is not None else ""
        return choice_name("kind") + amount + (" · " + p["container"] if p.get("container") else "")
    if module == "walk":
        parts = [value("minutes") + "분"] if p.get("minutes") is not None else ["시간 미입력"]
        if p.get("distance") is not None:
            parts.append(value("distance") + " " + p.get("distance_unit", "km"))
        return " · ".join(parts)
    if module in ("play", "training"):
        name = p.get("kind" if module == "play" else "skill", "")
        return f"{name or MODULES[module].name} · {value('minutes') + '분' if p.get('minutes') is not None else '시간 미입력'}"
    if module == "weight":
        return value("weight") + " " + unit() + (" · BCS " + str(p["bcs"]) + "/9" if p.get("bcs") else "")
    if module == "sleep":
        return choice_name("kind") + " · 관찰 " + value("minutes") + "분"
    if module == "inventory":
        return f"{p.get('item', '')} · {choice_name('movement')} {value('quantity')} {unit()}"
    if module == "medication":
        return f"{p.get('medicine', '')} · {value('dose')} {unit()} · {choice_name('result')}"
    if module == "environment":
        return f"{choice_name('metric')} · {value('value')} {unit()} · {p.get('location', '생활공간')}"
    if module == "expense":
        return f"{p.get('item', '')} · {value('amount')} {p.get('currency', '')}"
    if module == "reproduction":
        return choice_name("kind") + (" · " + value("eggs") + "개" if p.get("eggs") is not None else "") + (" · " + p["detail"] if p.get("detail") else "")
    return " · ".join(str(v) for _, v in payload_display(module, payload)[:3])
