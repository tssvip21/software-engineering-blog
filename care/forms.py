from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import uuid
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from .models import Room, Product, Membership
from .modules import MODULES, MODULE_CHOICES, DEFAULT_MODULES

TIMEZONES = [(x, x) for x in ("Asia/Seoul", "Asia/Tokyo", "UTC", "America/New_York", "America/Los_Angeles", "Europe/London", "Australia/Sydney")]
WEEKDAYS = [(str(i), label) for i, label in enumerate(("월", "화", "수", "목", "금", "토", "일"))]


class DateInput(forms.DateInput):
    input_type = "date"


class LocalDateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("format", "%Y-%m-%dT%H:%M")
        super().__init__(*args, **kwargs)


class RoomForm(forms.ModelForm):
    timezone = forms.ChoiceField(label="기록 기준 시간대", choices=TIMEZONES, initial="Asia/Seoul")
    use_defaults = forms.BooleanField(label="종에 맞는 기본 모듈로 시작", required=False, initial=True)
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    class Meta:
        model = Room
        fields = ["name", "species", "subspecies", "size", "birth_date", "sex", "timezone", "guest_sensitive"]
        labels = {"name": "반려동물 이름", "species": "동물 종류", "subspecies": "세부 종·품종 (선택)", "size": "체격 분류 (강아지)", "birth_date": "생일 (모르면 비워 두세요)", "sex": "성별", "guest_sensitive": "일반 참여자에게 투약·병원·지출 기록도 공유"}
        widgets = {"birth_date": DateInput(), "name": forms.TextInput(attrs={"autocomplete": "off", "placeholder": "예: 보리"})}
        help_texts = {"guest_sensitive": "끄면 방장·관리자만 해당 모듈을 열람하고 기록합니다. 기존 기록은 삭제하지 않습니다.", "size": "체격만으로 적정 체중을 판정하지 않습니다."}
    def __init__(self, *args, member=None, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.created_at:
            self.fields.pop("use_defaults", None)
            self.fields["version"].initial = self.instance.settings_version
        if member and member.role != "owner":
            self.fields.pop("guest_sensitive", None)
    def clean_name(self):
        value = self.cleaned_data["name"].strip()
        if not value:
            raise ValidationError("이름을 입력해 주세요.")
        return value
    def clean_birth_date(self):
        value = self.cleaned_data.get("birth_date")
        if value and value > timezone.localdate():
            raise ValidationError("생일은 미래 날짜일 수 없습니다.")
        return value
    def clean(self):
        data = super().clean()
        if data.get("species") != "dog":
            data["size"] = ""
        try:
            ZoneInfo(data.get("timezone", "Asia/Seoul"))
        except (ZoneInfoNotFoundError, ValueError):
            self.add_error("timezone", "지원하지 않는 시간대입니다.")
        return data


class ProductForm(forms.ModelForm):
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    class Meta:
        model = Product
        fields = ["name", "category", "energy", "energy_basis", "grams_per_piece", "protein", "protein_kind", "fat", "fat_kind", "source", "active"]
        labels = {"name": "제품명", "category": "사용 모듈", "energy": "표시된 열량 (모르면 비워 두세요)", "energy_basis": "열량의 기준 단위", "grams_per_piece": "개당 무게 (g, 아는 경우만)", "protein": "단백질 (g / 100 g)", "protein_kind": "단백질 값의 의미", "fat": "지방 (g / 100 g)", "fat_kind": "지방 값의 의미", "source": "정보 출처", "active": "새 기록에서 사용할 제품"}
        help_texts = {"source": "제품 포장지, 제조사 표시 또는 확인한 출처를 적어 주세요.", "protein": "건물 기준 값이 아닌 제품 그대로의 중량 기준만 입력합니다.", "active": "해제해도 과거 기록과 당시 계산값은 유지됩니다."}
        widgets = {"source": forms.TextInput(attrs={"placeholder": "예: 제품 포장지, 확인일 2026-11-03"})}
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["version"].initial = self.instance.version or 1
        for key in ("energy", "protein", "fat", "grams_per_piece"):
            self.fields[key].min_value = Decimal("0.001") if key == "grams_per_piece" else Decimal("0")
            self.fields[key].widget.attrs.update({"min": str(self.fields[key].min_value), "step": "0.001"})
        for key in ("protein", "fat"):
            self.fields[key].max_value = Decimal("100")
    def clean(self):
        data = super().clean()
        for key in ("energy", "protein", "fat", "grams_per_piece"):
            value = data.get(key)
            if value is not None and (value < 0 or (key == "grams_per_piece" and value <= 0)):
                self.add_error(key, "0 이상인 올바른 값을 입력해 주세요.")
        for key in ("protein", "fat"):
            value = data.get(key)
            if value is not None and value > 100:
                self.add_error(key, "100 g당 성분량은 100 g을 넘을 수 없습니다.")
        if any(data.get(key) is not None for key in ("energy", "protein", "fat")) and not data.get("source", "").strip():
            self.add_error("source", "영양 정보의 출처를 함께 입력해 주세요.")
        return data


def input_field(spec):
    args = {"label": spec.label, "required": spec.required, "help_text": spec.help}
    if spec.kind == "number":
        return forms.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal(spec.minimum) if spec.minimum is not None else None, max_value=Decimal(spec.maximum), widget=forms.NumberInput(attrs={"step": "0.001", "inputmode": "decimal"}), **args)
    if spec.kind == "choice":
        options = list(spec.choices)
        if not spec.required:
            options.insert(0, ("", "모름·입력하지 않음"))
        return forms.ChoiceField(choices=options, **args)
    if spec.kind == "date":
        return forms.DateField(widget=DateInput(), **args)
    return forms.CharField(max_length=300, strip=True, **args)


class RecordForm(forms.Form):
    client_key = forms.UUIDField(widget=forms.HiddenInput)
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    routine_item = forms.UUIDField(widget=forms.HiddenInput, required=False)
    routine_date = forms.DateField(widget=forms.HiddenInput, required=False)
    occurred_at = forms.DateTimeField(label="활동·측정 시각", widget=LocalDateTimeInput(), input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"])
    status = forms.ChoiceField(label="상태")
    def __init__(self, *args, room, module, member, instance=None, **kwargs):
        self.room, self.spec, self.member, self.instance = room, MODULES[module], member, instance
        initial = kwargs.setdefault("initial", {})
        initial.setdefault("client_key", instance.client_key if instance else uuid.uuid4())
        initial.setdefault("occurred_at", timezone.localtime(instance.occurred_at if instance else timezone.now()))
        initial.setdefault("status", instance.status if instance else "done")
        if module == "shedding" and not instance:
            initial["status"] = "active"
        if instance:
            initial.update(instance.payload)
            initial.update({"version": instance.version, "note": instance.note, "performer": instance.performer_id, "ended_at": timezone.localtime(instance.ended_at) if instance.ended_at else None})
            if instance.payload.get("product_id"):
                initial["product"] = instance.payload["product_id"]
        else:
            initial.setdefault("performer", member.pk)
            defaults = {"unit": "kg" if module == "weight" else "g" if module in ("meal", "treat") else "ml" if module == "water" else "C" if module == "environment" else "piece" if module == "inventory" else "mg", "distance_unit": "km", "currency": "KRW", "condition": "unknown", "reaction": "unknown", "result": "given"}
            for k, v in defaults.items():
                initial.setdefault(k, v)
        super().__init__(*args, **kwargs)
        state_names = {"done": "완료·측정함", "active": "진행 중", "planned": "예정 (완료 통계에 넣지 않음)"}
        self.fields["status"].choices = [(s, state_names[s]) for s in self.spec.statuses]
        if len(self.spec.statuses) == 1:
            self.fields["status"].widget = forms.HiddenInput()
        if module in ("walk", "shedding"):
            self.fields["ended_at"] = forms.DateTimeField(label="종료 시각 (선택)", required=False, widget=LocalDateTimeInput(), help_text="진행 중일 때는 비워 둡니다.")
        if module in ("meal", "treat"):
            products = Product.objects.filter(room=room).filter(Q(category__in=(module, "both")))
            original = instance.payload.get("product_id") if instance else None
            products = products.filter(Q(active=True) | Q(pk=original)) if original else products.filter(active=True)
            self.fields["product"] = forms.ModelChoiceField(label="등록된 제품", queryset=products.order_by("name"), required=False, empty_label="직접 입력·제품 정보 없음", help_text="제품 영양 정보가 없으면 열량을 0으로 취급하지 않습니다.")
        for spec in self.spec.fields:
            self.fields[spec.key] = input_field(spec)
        if module in ("meal", "treat") and instance:
            self.fields["refresh_product"] = forms.BooleanField(label="현재 제품 영양 정보로 다시 계산", required=False, help_text="기본값은 이 기록에 저장된 당시 제품 정보입니다.")
        if room.species not in ("dog", "cat") and module == "weight":
            self.fields.pop("bcs", None)
            self.fields.pop("bcs_by", None)
        participants = Membership.objects.filter(room=room)
        participants = participants.filter(Q(active=True) | Q(pk=instance.performer_id)) if instance and instance.performer_id else participants.filter(active=True)
        self.fields["performer"] = forms.ModelChoiceField(label="실제 수행자 (선택)", queryset=participants.order_by("display_name"), required=False, empty_label="별도 지정하지 않음", help_text="기록 작성자는 현재 참여자로 따로 저장됩니다.")
        self.fields["note"] = forms.CharField(label="메모 (선택)", max_length=2000, required=False, widget=forms.Textarea(attrs={"rows": 3}))
        if instance:
            self.fields["edit_reason"] = forms.CharField(label="수정 이유 (선택)", max_length=300, required=False)

    def clean(self):
        data = super().clean()
        status = data.get("status")
        occurred = data.get("occurred_at")
        ended = data.get("ended_at")
        now = timezone.now()
        if occurred:
            if status != "planned" and occurred > now + timedelta(minutes=1):
                self.add_error("occurred_at", "완료·진행 기록의 활동 시각은 미래일 수 없습니다.")
            if status == "planned" and occurred > now + timedelta(days=3660):
                self.add_error("occurred_at", "예정일이 너무 멉니다. 입력한 날짜를 확인해 주세요.")
        if ended:
            if occurred and ended < occurred:
                self.add_error("ended_at", "종료 시각은 활동 시각 이후여야 합니다.")
            if ended > now + timedelta(minutes=1):
                self.add_error("ended_at", "종료 시각은 미래일 수 없습니다.")
            if status != "done":
                self.add_error("ended_at", "종료 시각을 입력하려면 완료 상태로 바꿔 주세요.")
        key = self.spec.key
        if key in ("meal", "treat"):
            if not data.get("product") and not data.get("food_name"):
                self.add_error("food_name", "제품을 선택하거나 먹이 이름을 입력해 주세요.")
            if data.get("consumed") is not None and data.get("quantity") is not None and data["consumed"] > data["quantity"]:
                self.add_error("consumed", "실제 섭취량은 제공량보다 클 수 없습니다.")
        if key == "walk" and status == "done":
            if occurred and ended and ended >= occurred:
                duration = Decimal(str((ended - occurred).total_seconds())) / Decimal(60)
                data["minutes"] = duration.quantize(Decimal("0.001"))
            if not data.get("minutes") or data["minutes"] <= 0:
                self.add_error("minutes", "완료한 산책의 시간 또는 종료 시각을 입력해 주세요.")
        if key == "walk" and status == "active":
            data["minutes"] = None
        if key == "water" and data.get("kind") == "measured" and data.get("amount") is None:
            self.add_error("amount", "실제로 측정한 음수량을 입력해 주세요.")
        if key == "sleep" and occurred and data.get("minutes"):
            if occurred + timedelta(minutes=float(data["minutes"])) > now + timedelta(minutes=1):
                self.add_error("minutes", "관찰 시작 시각과 시간을 합친 종료 시각이 미래입니다. 실제로 관찰한 구간만 입력해 주세요.")
        if key == "training":
            attempts, successes = data.get("attempts"), data.get("successes")
            if (attempts is None) != (successes is None):
                self.add_error("attempts", "성공률을 기록하려면 시도·성공 횟수를 함께 입력해 주세요.")
            if attempts is not None and successes is not None and successes > attempts:
                self.add_error("successes", "성공 횟수는 시도 횟수를 넘을 수 없습니다.")
            for k in ("attempts", "successes"):
                if data.get(k) is not None and data[k] % 1:
                    self.add_error(k, "횟수는 정수로 입력해 주세요.")
        if key == "weight" and data.get("bcs") and not data.get("bcs_by"):
            self.add_error("bcs_by", "BCS를 누가 평가했는지 선택해 주세요.")
        if key == "medication" and status == "planned" and data.get("result") == "skipped":
            self.add_error("result", "예정은 실제 투약 여부와 구분해 주세요.")
        if key == "environment":
            units = {"temperature": ("C", "F"), "humidity": ("percent",), "light": ("hours",), "ph": ("ph",)}
            metric, unit, value = data.get("metric"), data.get("unit"), data.get("value")
            if metric and unit not in units[metric]:
                self.add_error("unit", "선택한 측정 항목에 맞는 단위로 바꿔 주세요.")
            limit = {"humidity": (0, 100), "light": (0, 24), "ph": (0, 14)}.get(metric)
            if limit and value is not None and not limit[0] <= value <= limit[1]:
                self.add_error("value", "이 항목의 입력 가능 범위를 벗어났습니다.")
            data["location"] = data.get("location") or "생활공간"
        if key == "reproduction":
            eggs = data.get("eggs")
            if data.get("kind") == "egg" and eggs is None:
                self.add_error("eggs", "관찰한 알 개수를 입력해 주세요.")
            if eggs is not None and eggs % 1:
                self.add_error("eggs", "알 개수는 정수로 입력해 주세요.")
            if data.get("kind") != "egg":
                data["eggs"] = None
        if key == "inventory":
            if data.get("movement") in ("in", "out") and data.get("quantity") == 0:
                self.add_error("quantity", "입고·사용 수량은 0보다 커야 합니다.")
        if key == "expense" and data.get("currency") in ("KRW", "JPY") and data.get("amount") is not None and data["amount"] % 1:
            self.add_error("amount", "원·엔 금액은 정수로 입력해 주세요.")
        return data

    def cleaned_payload(self):
        result = {}
        for spec in self.spec.fields:
            value = self.cleaned_data.get(spec.key)
            if isinstance(value, Decimal):
                value = str(value)
            elif hasattr(value, "isoformat"):
                value = value.isoformat()
            result[spec.key] = value
        if self.spec.key in ("meal", "treat"):
            product = self.cleaned_data.get("product")
            result["product_id"] = str(product.pk) if product else None
            if product:
                result["food_name"] = product.name
        return result


class InvitationForm(forms.Form):
    hours = forms.TypedChoiceField(label="링크 유효기간", choices=[("1", "1시간"), ("24", "24시간"), ("168", "7일")], coerce=int, initial=168)
    max_uses = forms.IntegerField(label="참여 가능 횟수", min_value=1, max_value=100, initial=10)


class JoinForm(forms.Form):
    token = forms.CharField(max_length=120, min_length=32, widget=forms.HiddenInput)
    display_name = forms.CharField(label="함께 돌볼 때 사용할 이름", max_length=40, min_length=1, strip=True)
    agree = forms.BooleanField(label="표시된 공유 범위를 확인했고 이 방에 참여합니다")


class CancelForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=1)
    reason = forms.CharField(label="취소 이유", max_length=300, min_length=1, widget=forms.Textarea(attrs={"rows": 2}))


class RoutineForm(forms.Form):
    name = forms.CharField(label="루틴 이름", max_length=80)
    at = forms.TimeField(label="예정 시간", widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"))
    weekdays = forms.MultipleChoiceField(label="반복 요일", choices=WEEKDAYS, widget=forms.CheckboxSelectMultiple, initial=[str(i) for i in range(7)])
    assignee = forms.ModelChoiceField(label="담당자 (선택)", queryset=Membership.objects.none(), required=False, empty_label="공동 담당")
    modules = forms.MultipleChoiceField(label="루틴에 포함할 활동", choices=(), widget=forms.CheckboxSelectMultiple)
    active = forms.BooleanField(label="이 루틴 사용", required=False, initial=True)
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    module_order = forms.CharField(widget=forms.HiddenInput, required=False)
    def __init__(self, *args, room, visible_keys, instance=None, **kwargs):
        self.room, self.instance = room, instance
        if instance:
            kwargs.setdefault("initial", {}).update({"name": instance.name, "at": instance.at, "weekdays": [str(x) for x in instance.weekdays], "assignee": instance.assignee_id, "modules": list(instance.items.filter(active=True).values_list("module", flat=True)), "active": instance.active, "version": instance.version})
        super().__init__(*args, **kwargs)
        enabled = set(room.module_settings.filter(enabled=True).values_list("key", flat=True))
        if instance:
            enabled |= set(instance.items.filter(active=True).values_list("module", flat=True))
        allowed = enabled & set(visible_keys)
        self.fields["modules"].choices = [(k, m.name) for k, m in MODULES.items() if k in allowed]
        self.fields["assignee"].queryset = room.members.filter(active=True).order_by("display_name")
    def clean(self):
        data = super().clean()
        values = data.get("modules", [])
        raw = data.get("module_order")
        if raw:
            try:
                order = json.loads(raw)
                if not isinstance(order, list) or len(order) != len(values) or set(order) != set(values):
                    raise ValueError()
                data["modules"] = order
            except (ValueError, TypeError):
                self.add_error("modules", "활동 순서를 다시 확인해 주세요.")
        if data.get("assignee") and data["assignee"].room_id != self.room.pk:
            self.add_error("assignee", "이 방의 참여자만 지정할 수 있습니다.")
        return data


class StatsFilterForm(forms.Form):
    start = forms.DateField(label="시작일", widget=DateInput())
    end = forms.DateField(label="종료일", widget=DateInput())
    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end"):
            if data["end"] < data["start"]:
                self.add_error("end", "종료일은 시작일 이후여야 합니다.")
            if (data["end"] - data["start"]).days > 365:
                self.add_error("end", "한 번에 최대 366일을 조회할 수 있습니다.")
            if data["end"] > timezone.localdate():
                self.add_error("end", "통계 종료일은 오늘까지 선택할 수 있습니다.")
        return data
