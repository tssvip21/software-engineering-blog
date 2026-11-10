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
    text("food_name", "먹이 이름", False, "제품을 골랐다면 이름은 다시 적지 않아도 돼요."),
    number("quantity", "준 양", minimum="0.001"),
    choice("unit", "단위", (("g", "g"), ("kg", "kg"), ("oz", "oz"), ("piece", "개"), ("ml", "ml"))),
    number("consumed", "실제로 먹은 양", False, help="준 양과 같은 단위로 적어요. 먹지 않았다면 0, 모르면 빈칸으로 남겨 주세요."),
)
MODULE_LIST = (
    ModuleSpec("meal", "먹이·급여", "일상 돌봄", "bowl", "언제 어떤 먹이를 얼마나 줬는지 남겨요.", FOOD),
    ModuleSpec("treat", "간식", "일상 돌봄", "treat", "준 간식과 양을 남겨요. 식사 기록과 따로 볼 수 있어요.", FOOD + (text("reason", "급여 이유", False),)),
    ModuleSpec("water", "물 관리", "일상 돌봄", "drop", "물을 갈았는지, 마신 양을 쟀는지 구분해 남겨요.", (
        choice("kind", "기록 종류", (("replace", "물 교체"), ("refill", "물 보충"), ("measured", "마신 물의 양 측정"))),
        text("container", "물그릇·급수기", False), number("amount", "측정하거나 보충한 양", False),
        choice("unit", "단위", (("ml", "ml"), ("l", "L"))),
    )),
    ModuleSpec("toilet", "배변", "일상 돌봄", "toilet", "대변·소변을 봤을 때의 상태를 남겨요.", (
        choice("kind", "종류", (("stool", "대변"), ("urine", "소변"), ("observed", "구분하지 않음"))),
        choice("condition", "관찰 상태", (("usual", "평소와 같음"), ("different", "평소와 다름"), ("unknown", "확인하지 못함"))),
        text("detail", "관찰한 내용", False),
    )),
    ModuleSpec("walk", "산책", "활동", "walk", "산책을 시작하고 끝낼 때 눌러 주세요. 지난 산책도 기록할 수 있어요.", (
        number("minutes", "산책 시간 (분)", False, minimum="0.001", maximum="1440", help="끝난 시각을 적으면 산책 시간을 계산해요."),
        number("distance", "이동 거리", False), choice("distance_unit", "거리 단위", (("km", "km"), ("m", "m"))),
    ), ("done", "active")),
    ModuleSpec("play", "놀이", "활동", "ball", "무슨 놀이를 했는지, 반응은 어땠는지 남겨요.", (
        text("kind", "놀이 종류"), number("minutes", "놀이 시간 (분)", minimum="0.001", maximum="1440"),
        choice("reaction", "반응", (("engaged", "잘 참여함"), ("neutral", "보통"), ("little", "관심 적음"), ("unknown", "모름"))),
    ), ("done", "active")),
    ModuleSpec("training", "훈련", "활동", "target", "연습한 내용과 시간을 남겨요. 횟수는 필요한 경우에만 적어요.", (
        text("skill", "훈련 항목"), number("minutes", "훈련 시간 (분)", minimum="0.001", maximum="1440"),
        number("attempts", "시도 횟수", False, minimum="1", maximum="100000"),
        number("successes", "성공 횟수", False, maximum="100000"),
    ), ("done", "active")),
    ModuleSpec("weight", "체중", "건강 기록", "scale", "측정한 체중을 남기고 이전 값과 비교해요.", (
        number("weight", "체중", minimum="0.001"), choice("unit", "단위", (("kg", "kg"), ("g", "g"), ("lb", "lb"))),
        choice("bcs", "체형 점수(BCS, 1–9점)", tuple((str(i), str(i) + " / 9") for i in range(1, 10)), False, "강아지·고양이의 체형을 평가한 적이 있을 때만 적어요. 체중으로 계산하는 값은 아니에요."),
        choice("bcs_by", "체형을 평가한 사람", (("guardian", "보호자 관찰"), ("vet", "수의사 평가")), False),
    )),
    ModuleSpec("hygiene", "위생·손질", "생활 관리", "spark", "목욕·양치부터 빗질·발톱 손질까지 한곳에 남겨요.", (
        choice("task", "관리 항목", (("bath", "목욕"), ("teeth", "양치"), ("ear", "귀 세정"), ("paw", "발 세정"), ("brush", "빗질"), ("hair", "털 손질"), ("nail", "발톱 관리"), ("other", "기타"))),
        day("next_date", "다음 관리 예정일"), text("detail", "관리 내용", False),
    )),
    ModuleSpec("grooming", "미용", "생활 관리", "scissors", "빗질이나 털·발톱을 손질한 날을 남겨요.", (
        choice("task", "손질 항목", (("brush", "빗질"), ("hair", "털 손질"), ("nail", "발톱 관리"), ("other", "기타"))),
        day("next_date", "다음 손질 예정일"), text("detail", "손질 내용", False),
    )),
    ModuleSpec("cleaning", "생활공간 청소", "생활 관리", "clean", "화장실, 케이지, 수조 등을 청소한 날을 남겨요.", (
        choice("target", "청소 대상", (("toilet", "화장실"), ("cage", "케이지"), ("tank", "수조"), ("bedding", "침구"), ("other", "기타"))),
        text("task", "청소 내용"), day("next_date", "다음 청소 예정일"),
    )),
    ModuleSpec("medication", "투약", "건강 기록", "pill", "처방받은 약과 용량, 투약 여부를 남겨요.", (
        text("medicine", "약 이름"), number("dose", "처방·지시받은 용량", minimum="0.001"),
        choice("unit", "단위", (("mg", "mg"), ("ml", "ml"), ("tablet", "정"), ("drop", "방울"), ("dose", "회분"))),
        choice("result", "투약 여부", (("given", "투약함"), ("skipped", "투약하지 못함"))),
        text("instruction", "처방·지시 내용", False, "약의 용량은 처방·지시받은 대로 적어 주세요. 테일리가 용량을 정하지는 않아요."),
    ), ("done", "planned"), True),
    ModuleSpec("observation", "특이사항 메모", "건강 기록", "eye", "상태나 생활에서 달라진 점을 자유롭게 남겨요.", (
        choice("category", "관찰 항목", (("appetite", "식욕"), ("energy", "활동성"), ("behavior", "행동"), ("skin", "피부·털"), ("other", "기타"))),
        choice("condition", "평소와 비교", (("usual", "평소와 같음"), ("different", "변화 있음"), ("unknown", "판단하기 어려움"))),
        text("detail", "관찰 내용"),
    )),
    ModuleSpec("veterinary", "병원·예방접종", "건강 기록", "cross", "병원에 갈 일정과 다녀온 기록을 따로 남겨요.", (
        choice("kind", "방문 종류", (("visit", "진료"), ("vaccine", "예방접종"), ("checkup", "검진"))),
        text("clinic", "병원·기관", False), text("detail", "진료·접종 내용"), day("next_date", "다음 방문 예정일"),
    ), ("done", "planned"), True),
    ModuleSpec("sleep", "수면·휴식", "활동", "moon", "직접 확인한 수면·휴식 시간만 남겨요.", (
        choice("kind", "종류", (("sleep", "수면"), ("rest", "휴식"))),
        number("minutes", "관찰한 시간 (분)", minimum="0.001", maximum="4320", help="시작한 시각과 확인한 시간을 적어요. 같은 시간대가 겹치면 한 번만 계산해요."),
        text("quality", "관찰한 특징", False),
    )),
    ModuleSpec("environment", "사육환경", "환경·생애", "thermometer", "온도나 습도 등을 잰 위치와 값을 남겨요.", (
        choice("metric", "측정 항목", (("temperature", "온도"), ("humidity", "습도"), ("light", "조명 시간"), ("ph", "수질 pH"))),
        number("value", "측정값", minimum="-999"),
        choice("unit", "단위", (("C", "°C"), ("F", "°F"), ("percent", "%"), ("hours", "시간"), ("ph", "pH"))),
        text("location", "측정 위치", False, "예: 케이지 안, 수조. 잰 위치가 다르면 따로 비교해요."),
    )),
    ModuleSpec("reproduction", "생활 변화", "환경·생활", "leaf", "먹이를 바꾸거나 이사하는 등 생활에서 달라진 일을 남겨요.", (
        choice("kind", "변화 종류", (("growth", "성장·생활 단계"), ("food_change", "먹이 변경"), ("environment_change", "생활환경 변경"), ("care_change", "관리 방식 변경"), ("other", "기타 변화"), ("heat", "이전 형식: 발정 관찰"), ("egg", "이전 형식: 산란"))),
        text("detail", "달라진 점", False, "예: 사료를 바꿨어요. 어떤 일이 있었는지 짧게 적어 주세요."),
        day("next_date", "다시 살펴볼 날짜"),
        number("eggs", "이전 기록의 알 개수", False, maximum="100000"),
    )),
    ModuleSpec("shedding", "탈피·털갈이", "환경·생애", "feather", "탈피나 털갈이를 시작한 날과 끝난 날을 남겨요.", (
        choice("kind", "종류", (("molt", "탈피"), ("fur", "털갈이"), ("feather", "깃털갈이"))), text("detail", "관찰 내용", False),
    ), ("active", "done")),
    ModuleSpec("inventory", "용품·재고", "생활 관리", "box", "사 온 용품과 쓴 양을 남겨 얼마나 남았는지 확인해요.", (
        text("item", "품목"), choice("movement", "변동 종류", (("in", "입고"), ("out", "사용"), ("set", "직접 센 수량으로 맞추기"))),
        number("quantity", "수량"), choice("unit", "단위", (("piece", "개"), ("g", "g"), ("kg", "kg"), ("ml", "ml"), ("l", "L"))),
        number("minimum", "구매 알림 기준 수량", False, help="이 수량 이하로 남으면 구매가 필요하다고 표시해요. 원하는 기준을 직접 정해 주세요."),
    )),
    ModuleSpec("expense", "지출", "생활 관리", "wallet", "돌봄에 쓴 비용을 남겨요. 통화별로 따로 모아 볼 수 있어요.", (
        text("item", "지출 내용"), number("amount", "금액", minimum="0.001"),
        choice("currency", "통화", (("KRW", "원 (KRW)"), ("USD", "달러 (USD)"), ("EUR", "유로 (EUR)"), ("JPY", "엔 (JPY)"), ("CNY", "위안 (CNY)"))),
        choice("category", "분류", (("food", "먹이·간식"), ("medical", "병원·건강"), ("supplies", "용품"), ("care", "돌봄 서비스"), ("other", "기타"))),
    ), ("done",), True),
)
MODULES = {m.key: m for m in MODULE_LIST}
# Historical keys stay readable and schedulable; they do not consume a new catalogue slot.
RETIRED_MODULES = {"grooming": "hygiene", "reproduction": "observation"}
SELECTABLE_MODULES = {key: spec for key, spec in MODULES.items() if key not in RETIRED_MODULES}
MAX_MODULES = 20
assert len(SELECTABLE_MODULES) <= MAX_MODULES
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
    original = Decimal(str(value))
    # Unit conversion must not turn a small, genuinely measured value into zero.
    places = max(3, min(12, 2 - original.adjusted())) if original and abs(original) < Decimal("0.001") else 3
    d = original.quantize(Decimal(1).scaleb(-places))
    return format(d, "f").rstrip("0").rstrip(".") if d % 1 else str(int(d))


def payload_display(module, payload, *, status=None):
    rows = []
    if payload.get("routine_title"):
        rows.append(("알림 이름", str(payload["routine_title"])))
    if payload.get("_preset", {}).get("title"):
        rows.append(("사용한 저장 설정", str(payload["_preset"]["title"])))
    for spec in MODULES[module].fields:
        value = payload.get(spec.key)
        if value is None or value == "":
            continue
        if spec.kind == "choice":
            value = dict(spec.choices).get(str(value), value)
        if module == "medication" and spec.key == "result" and payload.get("result") == "given" and status == "planned":
            value = "투약 예정"
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
        if p.get("_entry_kind") == "schedule":
            return str(p.get("routine_title") or "수면·휴식 알림") + " · 확인 완료 (실측 시간 아님)"
        return choice_name("kind") + " · 관찰 " + value("minutes") + "분"
    if module == "inventory":
        return f"{p.get('item', '')} · {choice_name('movement')} {value('quantity')} {unit()}"
    if module == "medication":
        if p.get("_entry_kind") == "routine":
            return str(p.get("routine_title") or "관리 알림") + (" · 건너뜀" if p.get("result") == "skipped" else " · 완료")
        return f"{p.get('medicine', '')} · {value('dose')} {unit()} · {choice_name('result')}"
    if module == "environment":
        return f"{choice_name('metric')} · {value('value')} {unit()} · {p.get('location', '생활공간')}"
    if module == "expense":
        return f"{p.get('item', '')} · {value('amount')} {p.get('currency', '')}"
    if module == "reproduction":
        return choice_name("kind") + (" · " + value("eggs") + "개" if p.get("eggs") is not None else "") + (" · " + p["detail"] if p.get("detail") else "")
    return " · ".join(str(v) for _, v in payload_display(module, payload)[:3])
