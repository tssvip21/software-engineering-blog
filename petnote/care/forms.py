from datetime import timezone as utc_timezone
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import uuid
from .json_inputs import load_json
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from .models import Room, Product, Membership
from .modules import MODULES

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
    breed_key = forms.ChoiceField(label="품종 (선택)", required=False)
    size_origin = forms.ChoiceField(label="체격 선택 방법", choices=[("auto", "품종 자료로 제안"), ("manual", "직접 선택")], required=False)

    timezone = forms.ChoiceField(label="기록에 사용할 시간대", choices=TIMEZONES, initial="Asia/Seoul")
    use_defaults = forms.BooleanField(label="동물 종류에 맞는 돌봄 기능을 미리 선택", required=False, initial=True)
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    class Meta:
        model = Room
        fields = ["name", "species", "breed_key", "subspecies", "sex", "maturity", "size_origin", "size", "birth_date", "timezone", "guest_sensitive"]
        labels = {"name": "반려동물 이름", "species": "동물 종류", "subspecies": "세부 종·품종 (선택)", "size": "강아지 체격", "birth_date": "생일 (모르면 비워 두세요)", "sex": "성별", "guest_sensitive": "투약·병원·지출 기록을 모든 구성원과 공유"}
        widgets = {"birth_date": DateInput(), "name": forms.TextInput(attrs={"autocomplete": "off"})}
        help_texts = {"guest_sensitive": "선택하지 않으면 방장과 관리자만 해당 기록을 보고 남길 수 있어요. 기존 기록은 삭제되지 않아요.", "size": "체격 구분만으로 적정 체중을 정하지 않아요."}
    def __init__(self, *args, member=None, **kwargs):
        super().__init__(*args, **kwargs)
        from .breeds import choices, picker_data
        self.fields["breed_key"].choices = choices()
        self.breed_catalog = picker_data()
        self.fields["maturity"].label = "성장 상태"
        self.fields["subspecies"].label = "직접 적을 품종·종류 (선택)"
        self.fields["size"].help_text = "강아지의 기능을 찾기 위한 구분이에요. 소형은 10kg 이하, 중형은 10kg 초과 25kg 이하, 대형은 25kg 초과예요. 건강 판정 기준은 아니며 모르면 비워 두세요."
        self.fields["maturity"].help_text = "다 자랐다고 확인한 경우에만 성견·성묘 체중 자료와 비교해요."
        if not self.instance.created_at:
            self.fields["size_origin"].initial = "auto"
            self.initial["size_origin"] = "auto"
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
            raise ValidationError("생일은 미래 날짜일 수 없어요.")
        return value
    def clean(self):
        data = super().clean()
        from .breeds import catalogue, suggest_size
        species, key = data.get("species"), data.get("breed_key", "")
        breed = catalogue().get(key)
        if breed and breed["species"] != species:
            self.add_error("breed_key", "동물 종류에 맞는 품종을 선택해 주세요.")
        if species not in ("dog", "cat"):
            data["breed_key"] = ""
            data["size_origin"] = "manual"
        elif breed:
            data["subspecies"] = breed["ko"]
        if not data.get("size_origin"):
            data["size_origin"] = "manual"
        if species == "dog" and data["size_origin"] == "auto":
            proposed = suggest_size(breed, data.get("sex", ""))
            data["size"] = proposed
        if species != "dog":
            data["size"] = ""
            data["size_origin"] = "manual"
        try:
            ZoneInfo(data.get("timezone", "Asia/Seoul"))
        except (ZoneInfoNotFoundError, ValueError):
            self.add_error("timezone", "선택할 수 없는 시간대예요. 목록에서 다시 골라 주세요.")
        return data


class ProductForm(forms.ModelForm):
    request_token = forms.UUIDField(widget=forms.HiddenInput, required=False, initial=uuid.uuid4)
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    class Meta:
        model = Product
        fields = ["name", "brand", "category", "energy", "energy_basis", "grams_per_piece", "protein", "protein_kind", "fat", "fat_kind", "source", "stock_item", "stock_unit", "active"]
        labels = {"name": "제품명", "category": "사용할 기록", "energy": "포장지에 적힌 열량 (선택)", "energy_basis": "열량이 표시된 기준", "grams_per_piece": "개당 무게 (g, 아는 경우만)", "protein": "단백질 (g / 100 g)", "protein_kind": "단백질 표시 방식", "fat": "지방 (g / 100 g)", "fat_kind": "지방 표시 방식", "source": "정보 출처", "active": "기록할 때 이 제품을 선택할 수 있게 하기"}
        help_texts = {"source": "어디서 확인한 정보인지 적어 주세요. 예: 제품 포장지, 제조사 안내", "protein": "수분을 제외한 값(건물 기준)은 넣지 마세요. 제품 그대로 100g에 들어 있는 양을 입력해요.", "active": "사용을 해제해도 이전 기록과 당시 영양 정보는 남아요."}
        widgets = {"source": forms.TextInput(attrs={"placeholder": "예: 제품 포장지, 확인일 2026-11-03"})}
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["version"].initial = self.instance.version or 1
        self.fields["brand"].label = "브랜드 (선택)"
        self.fields["stock_item"].label = "연결할 재고 품목 이름 (선택)"
        self.fields["stock_item"].help_text = "용품·재고에 등록한 이름과 맞춰 주세요. 연결하면 꺼내 준 양을 재고에서 한 번만 차감해요. 비우면 연동하지 않아요."
        self.fields["stock_unit"].label = "해당 재고의 기준 단위"
        self.fields["stock_unit"].help_text = "g/kg 재고는 g, ml/L 재고는 ml로 연결해요. 개수와 무게는 개당 무게가 있을 때만 환산해요."
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
                self.add_error(key, "100 g당 성분량은 100 g을 넘을 수 없어요.")
        if any(data.get(key) is not None for key in ("energy", "protein", "fat")) and not data.get("source", "").strip():
            self.add_error("source", "영양 정보의 출처를 함께 입력해 주세요.")
        if bool(data.get("stock_item", "").strip()) != bool(data.get("stock_unit")):
            self.add_error("stock_item", "재고를 연결하려면 품목 이름과 기준 단위를 함께 지정해 주세요.")
        if data.get("stock_item"):
            data["stock_item"] = " ".join(data["stock_item"].split())
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
    occurred_at = forms.DateTimeField(label="돌봄하거나 측정한 시각", widget=LocalDateTimeInput(), input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"])
    status = forms.ChoiceField(label="상태")
    def __init__(self, *args, room, module, member, instance=None, quick=False, simple=False, **kwargs):
        self.simple = simple or bool(instance and instance.payload.get("_schema") == 7)
        self.server_metadata = {}
        self.quick = quick or self.simple or bool(instance and instance.payload.get("_entry_mode") == "quick")
        self.room, self.spec, self.member, self.instance = room, MODULES[module], member, instance
        initial = kwargs.setdefault("initial", {})
        initial.setdefault("client_key", instance.client_key if instance else uuid.uuid4())
        initial.setdefault("occurred_at", timezone.localtime(instance.occurred_at if instance else timezone.now()))
        initial.setdefault("status", instance.status if instance else "done")
        if module == "shedding" and not instance and not self.simple:
            initial["status"] = "active"
            initial.setdefault("kind", "feather" if room.species == "bird" else "molt" if room.species in ("reptile", "amphibian") else "fur")
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
        state_names = {"done": "완료", "active": "진행 중", "planned": "예정"}
        self.fields["status"].choices = [(s, state_names[s]) for s in self.spec.statuses]
        if len(self.spec.statuses) == 1:
            self.fields["status"].widget = forms.HiddenInput()
        if module in ("walk", "play", "training", "shedding"):
            self.fields["ended_at"] = forms.DateTimeField(label="종료 시각 (선택)", required=False, widget=LocalDateTimeInput(), help_text="아직 진행 중이면 비워 두세요.")
        if module in ("meal", "treat"):
            products = Product.objects.filter(room=room).filter(Q(category__in=(module, "both")))
            original = instance.payload.get("product_id") if instance else None
            products = products.filter(Q(active=True) | Q(pk=original)) if original else products.filter(active=True)
            self.fields["product"] = forms.ModelChoiceField(label="저장한 제품 선택", queryset=products.order_by("name"), required=False, empty_label="제품을 고르지 않고 기록", help_text="영양 정보를 모르면 열량은 비워 두고 기록만 저장해요.")
        for spec in self.spec.fields:
            self.fields[spec.key] = input_field(spec)
        if module in ("meal", "treat") and instance:
            self.fields["refresh_product"] = forms.BooleanField(label="현재 제품 영양 정보로 다시 계산", required=False, help_text="선택하지 않으면 기록을 남겼을 때의 제품 정보를 그대로 사용해요.")
        if module in ("meal", "treat") and instance and instance.payload.get("_stock"):
            self.fields["refresh_stock"] = forms.BooleanField(label="이 기록에 현재 제품의 재고 연결 적용", required=False, help_text="선택하지 않으면 당시 연결한 품목을 유지하고 제공량 변경만 반영해요.")
        if room.species not in ("dog", "cat") and module == "weight":
            self.fields.pop("bcs", None)
            self.fields.pop("bcs_by", None)
        participants = Membership.objects.filter(room=room)
        participants = participants.filter(Q(active=True) | Q(pk=instance.performer_id)) if instance and instance.performer_id else participants.filter(active=True)
        self.fields["performer"] = forms.ModelChoiceField(label="돌본 사람 (선택)", queryset=participants.order_by("display_name"), required=False, empty_label="지정하지 않음", help_text="지금 접속한 구성원 이름을 기록한 사람으로 남겨요.")
        self.fields["note"] = forms.CharField(label="메모 (선택)", max_length=2000, required=False, widget=forms.Textarea(attrs={"rows": 3}))
        if instance:
            self.fields["edit_reason"] = forms.CharField(label="수정 이유 (선택)", max_length=300, required=False)
        if self.quick:
            optional = {
                "meal": ("quantity",), "treat": ("quantity",),
                "play": ("kind", "minutes"), "training": ("skill", "minutes"),
                "cleaning": ("task",), "observation": ("detail",),
            }.get(module, ())
            for key in optional:
                self.fields[key].required = False
            self.fields["occurred_at"].label = "기록 시각"
            self.fields["performer"].label = "돌본 사람"
        if self.simple:
            if module == "water" and not instance:
                self.initial.setdefault("kind", "replace")
            elif module == "toilet":
                self.initial.setdefault("kind", "observed")
                self.initial.setdefault("condition", "unknown")
            elif module == "play":
                self.initial.setdefault("reaction", "unknown")
                self.fields["reaction"].required = False
                self.fields["reaction"].widget = forms.HiddenInput()
            elif module == "medication":
                self.fields["routine_title"] = forms.CharField(label="알림 이름", max_length=100, help_text="예: 영양제, 약, 사상충. 용량 등 필요한 내용은 메모로 남겨요.")
                for key in ("medicine", "dose", "unit", "instruction"):
                    self.fields[key].required = False
                    self.fields[key].widget = forms.HiddenInput()
                self.initial["unit"] = ""
            elif module == "sleep":
                self.fields["minutes"].required = False
                self.fields["minutes"].label = "직접 확인한 시간 (분, 선택)"
                self.fields["minutes"].help_text = "알림 확인만 하는 경우 비워 두세요. 계획한 수면시간이 실제 수면으로 저장되지는 않아요."
                self.initial.setdefault("kind", "rest")
                self.fields["routine_title"] = forms.CharField(label="알림 이름 (선택)", max_length=100, required=False)
            elif module == "observation":
                self.fields["detail"].required = True
                self.fields["detail"].label = "특이사항 메모"
                self.fields["detail"].widget = forms.Textarea(attrs={"rows": 4})
                for key,value in (("category", "other"), ("condition", "unknown")):
                    self.initial.setdefault(key,value)
                    self.fields[key].required = False
                    self.fields[key].widget = forms.HiddenInput()
                self.fields["note"].widget = forms.HiddenInput()
            elif module == "shedding":
                self.initial.setdefault("kind", "feather" if room.species == "bird" else "molt" if room.species in ("reptile", "amphibian") else "fur")
                self.initial["status"] = "done"
                self.fields["status"].widget = forms.HiddenInput()
                self.fields["ended_at"].widget = forms.HiddenInput()
        if module == "medication":
            planned = (self.data.get("status") if self.is_bound else initial.get("status")) == "planned"
            self.fields["result"].choices = [("given", "투약 예정" if planned else "투약함"), ("skipped", "투약하지 못함")]
        if module == "reproduction":
            self.fields["kind"].initial = "growth"
            if not instance or instance.payload.get("kind") != "egg":
                self.fields["eggs"].widget = forms.HiddenInput()
            if not instance or instance.payload.get("kind") not in ("heat", "egg"):
                # Legacy values remain accepted by the field for old clients, but are not offered for new entry.
                self.fields["kind"].widget.choices = [(v, label) for v, label in self.fields["kind"].choices if v not in ("heat", "egg")]

        from .supplies import install_record_fields
        install_record_fields(self)

    def clean(self):
        data = super().clean()
        status = data.get("status")
        occurred = data.get("occurred_at")
        if self.quick and not self.instance and self.spec.key == "sleep" and occurred and data.get("minutes"):
            occurred = (occurred.astimezone(utc_timezone.utc) - timedelta(minutes=float(data["minutes"]))).astimezone(occurred.tzinfo)
            data["occurred_at"] = occurred
        ended = data.get("ended_at")
        now = timezone.now()
        if occurred:
            if status != "planned" and occurred > now + timedelta(minutes=1):
                self.add_error("occurred_at", "완료했거나 진행 중인 활동은 현재보다 뒤의 시각으로 기록할 수 없어요.")
            if status == "planned" and occurred > now + timedelta(days=3660):
                self.add_error("occurred_at", "예정일이 너무 멀어요. 입력한 날짜를 확인해 주세요.")
        if ended:
            if occurred and ended < occurred:
                self.add_error("ended_at", "끝난 시각을 시작한 시각보다 늦게 입력해 주세요.")
            if ended > now + timedelta(minutes=1):
                self.add_error("ended_at", "종료 시각은 미래일 수 없어요.")
            if status != "done":
                self.add_error("ended_at", "종료 시각을 입력하려면 완료 상태로 바꿔 주세요.")
        key = self.spec.key
        if key in ("meal", "treat"):
            if not self.quick and not data.get("product") and not data.get("food_name"):
                self.add_error("food_name", "제품을 선택하거나 먹이 이름을 입력해 주세요.")
            if data.get("consumed") is not None and data.get("quantity") is not None and data["consumed"] > data["quantity"]:
                self.add_error("consumed", "먹은 양이 준 양보다 많아요. 두 값을 다시 확인해 주세요.")
        if key in ("walk", "play", "training") and status == "done":
            if occurred and ended and ended >= occurred:
                duration = Decimal(str((ended.astimezone(utc_timezone.utc) - occurred.astimezone(utc_timezone.utc)).total_seconds())) / Decimal(60)
                data["minutes"] = duration.quantize(Decimal("0.001"))
                if duration <= 0 or duration > 1440:
                    self.add_error("ended_at", "한 번의 산책 시간은 0분 초과, 24시간 이내로 입력해 주세요.")
            if not self.quick and (not data.get("minutes") or data["minutes"] <= 0):
                self.add_error("minutes", "완료한 활동의 시간 또는 종료 시각을 입력해 주세요.")
        if key in ("walk", "play", "training") and status == "active":
            data["minutes"] = None
        if self.simple:
            if key == "medication":
                data["medicine"], data["dose"], data["unit"], data["instruction"] = "", None, "", ""
            elif key == "observation":
                data["category"] = data.get("category") or "other"
                data["condition"] = data.get("condition") or "unknown"
            elif key == "shedding":
                if status != "done":
                    self.add_error("status", "관찰 기록은 완료 상태로 남겨 주세요. 이전 진행 기록은 기존 상세에서 종료할 수 있어요.")
                data["ended_at"] = None
        if key == "water" and data.get("kind") == "measured" and data.get("amount") is None:
            self.add_error("amount", "마신 물의 양을 입력해 주세요.")
        if key == "sleep" and occurred and data.get("minutes"):
            if occurred.astimezone(utc_timezone.utc) + timedelta(minutes=float(data["minutes"])) > now + timedelta(minutes=1):
                self.add_error("minutes", "입력한 시간대로면 아직 끝나지 않은 수면·휴식이에요. 시작 시각과 시간을 다시 확인해 주세요.")
        if key == "training":
            attempts, successes = data.get("attempts"), data.get("successes")
            if (attempts is None) != (successes is None):
                self.add_error("attempts", "성공률을 기록하려면 시도·성공 횟수를 함께 입력해 주세요.")
            if attempts is not None and successes is not None and successes > attempts:
                self.add_error("successes", "성공 횟수는 시도 횟수를 넘을 수 없어요.")
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
                self.add_error("value", "이 항목에 넣을 수 있는 값이 아니에요. 값과 단위를 다시 확인해 주세요.")
            data["location"] = data.get("location") or "생활공간"
        if key == "reproduction":
            if data.get("kind") not in (None, "heat", "egg") and not data.get("detail"):
                self.add_error("detail", "무엇이 달라졌는지 짧게 남겨 주세요.")
            eggs = data.get("eggs")
            if data.get("kind") == "egg" and eggs is None:
                self.add_error("eggs", "관찰한 알 개수를 입력해 주세요.")
            if eggs is not None and eggs % 1:
                self.add_error("eggs", "알 개수는 정수로 입력해 주세요.")
            if data.get("kind") != "egg":
                data["eggs"] = None
        if key == "inventory":
            if data.get("unit") == "piece":
                for field in ("quantity", "minimum"):
                    if data.get(field) is not None and data[field] % 1:
                        self.add_error(field, "개수는 정수로 입력해 주세요. 나누어 쓰는 양은 무게나 부피 단위를 골라 주세요.")
            if data.get("movement") in ("in", "out") and data.get("quantity") == 0:
                self.add_error("quantity", "들어오거나 사용한 수량을 0보다 크게 입력해 주세요.")
        if key == "expense" and data.get("currency") in ("KRW", "JPY") and data.get("amount") is not None and data["amount"] % 1:
            self.add_error("amount", "원·엔 금액은 정수로 입력해 주세요.")
        from .supplies import clean_record
        return clean_record(self, data)

    def cleaned_payload(self):
        result = {"_entry_mode": "quick"} if self.quick else {}
        if self.instance:
            for key in ("_schema", "_entry_kind", "_preset", "_stock", "_gps", "_schedule", "_supply"):
                if key in self.instance.payload:
                    result[key] = self.instance.payload[key]
        if self.simple:
            result["_schema"] = 7
            if self.spec.key == "medication":
                result.update(_entry_kind="routine", routine_title=self.cleaned_data.get("routine_title", ""))
            elif self.spec.key == "sleep" and self.cleaned_data.get("minutes") is None:
                result.update(_entry_kind="schedule", routine_title=self.cleaned_data.get("routine_title", ""))
            elif self.spec.key == "shedding":
                result["_entry_kind"] = "observation"
            elif self.spec.key == "observation":
                result["_entry_kind"] = "note"
        for key,value in self.server_metadata.items():
            if key in ("_schema", "_entry_kind", "_preset", "_stock", "_gps", "_schedule"):
                result[key] = value
        marker = getattr(self, "cadence_marker", None) or (self.instance.payload.get("_cadence_id") if self.instance else None)
        if marker:
            result["_cadence_id"] = str(marker)
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
        from .supplies import record_payload
        return record_payload(self, result)


class InvitationForm(forms.Form):
    hours = forms.TypedChoiceField(label="링크 유효기간", choices=[("1", "1시간"), ("24", "24시간"), ("168", "7일")], coerce=int, initial=168)
    max_uses = forms.IntegerField(label="링크로 참여할 수 있는 인원", min_value=1, max_value=100, initial=10)


class JoinForm(forms.Form):
    token = forms.CharField(max_length=120, min_length=32, widget=forms.HiddenInput)
    display_name = forms.CharField(label="다른 구성원에게 보일 이름", max_length=40, min_length=1, strip=True)
    agree = forms.BooleanField(label="공유되는 정보를 확인했고 함께 돌보는 데 동의해요")


class CancelForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=1)
    reason = forms.CharField(label="취소 이유", max_length=300, min_length=1, widget=forms.Textarea(attrs={"rows": 2}))


class RoutineForm(forms.Form):
    request_token = forms.UUIDField(widget=forms.HiddenInput, required=False, initial=uuid.uuid4)
    start_date = forms.DateField(label="시작일", widget=DateInput(), required=False)
    end_date = forms.DateField(label="종료일 (선택)", widget=DateInput(), required=False)
    note = forms.CharField(label="메모 (선택)", max_length=1000, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    name = forms.CharField(label="루틴 이름", max_length=80)
    at = forms.TimeField(label="예정 시간", widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"))
    weekdays = forms.MultipleChoiceField(label="반복 요일", choices=WEEKDAYS, widget=forms.CheckboxSelectMultiple, initial=[str(i) for i in range(7)])
    assignee = forms.ModelChoiceField(label="담당자 (선택)", queryset=Membership.objects.none(), required=False, empty_label="함께 담당")
    modules = forms.MultipleChoiceField(label="함께 확인할 활동", choices=(), widget=forms.CheckboxSelectMultiple)
    active = forms.BooleanField(label="이 루틴 사용", required=False, initial=True)
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    module_order = forms.CharField(widget=forms.HiddenInput, required=False, max_length=2048)
    def __init__(self, *args, room, visible_keys, instance=None, **kwargs):
        self.room, self.instance = room, instance
        if instance:
            kwargs.setdefault("initial", {}).update({"name": instance.name, "at": instance.at, "weekdays": [str(x) for x in instance.weekdays], "assignee": instance.assignee_id, "modules": list(instance.items.filter(active=True).values_list("module", flat=True)), "active": instance.active, "version": instance.version})
        super().__init__(*args, **kwargs)
        enabled = set(room.module_settings.filter(enabled=True).values_list("key", flat=True))
        if instance:
            enabled |= set(instance.items.filter(active=True).values_list("module", flat=True))
        from .modules import RETIRED_MODULES
        legacy = set(instance.items.values_list("module", flat=True)) if instance else set()
        allowed = (enabled & set(visible_keys)) - (set(RETIRED_MODULES) - legacy)
        self.fields["modules"].choices = [(k, m.name) for k, m in MODULES.items() if k in allowed]
        self.fields["assignee"].queryset = room.members.filter(active=True).order_by("display_name")
        from .models import RecordPreset
        from .services import local_date
        current_preset = instance.items.exclude(preset=None).values_list("preset_id", flat=True).first() if instance else None
        presets = RecordPreset.objects.filter(room=room, module__in=allowed).filter(Q(active=True) | Q(pk=current_preset))
        self.fields["preset"] = forms.ModelChoiceField(label="함께 쓸 저장 설정 (선택)", queryset=presets, required=False, empty_label="완료할 때 선택", initial=current_preset, help_text="여러 활동을 묶으면 선택한 설정과 같은 활동에만 적용해요.")
        self.initial.setdefault("start_date", instance.start_date if instance else local_date(room))
        self.initial.setdefault("end_date", instance.end_date if instance else None)
        self.initial.setdefault("note", instance.note if instance else "")
        if instance:
            self.fields["start_date"].disabled = True
            self.fields["start_date"].help_text = "이전 일정 이력을 보존하기 위해 시작일은 유지해요. 새 기간은 새 일정으로 만들 수 있어요."
    def clean(self):
        data = super().clean()
        values = data.get("modules", [])
        raw = data.get("module_order")
        if raw:
            try:
                order = load_json(raw)
                if not isinstance(order, list) or len(order) != len(values) or set(order) != set(values):
                    raise ValueError()
                data["modules"] = order
            except (ValueError, TypeError):
                self.add_error("modules", "활동 순서를 다시 확인해 주세요.")
        if data.get("assignee") and data["assignee"].room_id != self.room.pk:
            self.add_error("assignee", "이 공간의 구성원만 지정할 수 있어요.")
        from .services import local_date
        data["start_date"] = data.get("start_date") or (self.instance.start_date if self.instance else local_date(self.room))
        if data.get("end_date") and data["end_date"] < data["start_date"]:
            self.add_error("end_date", "종료일은 시작일과 같거나 뒤여야 해요.")
        if data.get("preset") and data["preset"].module not in values:
            self.add_error("preset", "선택한 활동에 맞는 저장 설정을 골라 주세요.")
        return data


class StatsFilterForm(forms.Form):
    start = forms.DateField(label="시작일", widget=DateInput())
    end = forms.DateField(label="종료일", widget=DateInput())
    def clean(self):
        data = super().clean()
        start, end = data.get("start"), data.get("end")
        for field, value in (("start", start), ("end", end)):
            if value and value.year < 1970:
                self.add_error(field, "통계 날짜는 1970년 이후로 선택해 주세요.")
        if start and end:
            if end < start:
                self.add_error("end", "종료일을 시작일과 같거나 뒤의 날짜로 선택해 주세요.")
            elif (end - start).days > 365:
                self.add_error("end", "한 번에 최대 366일을 조회할 수 있어요.")
            if end > timezone.localdate():
                self.add_error("end", "통계 종료일은 오늘까지 선택할 수 있어요.")
        return data
