import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q, F
from django.utils import timezone
from .modules import MODULE_CHOICES

SPECIES = [("dog", "강아지"), ("cat", "고양이"), ("bird", "조류·앵무새"), ("reptile", "파충류"), ("amphibian", "양서류"), ("other", "그 밖의 동물")]
SIZES = [("", "모름·해당 없음"), ("small", "소형"), ("medium", "중형"), ("large", "대형")]
ROLES = [("owner", "방장"), ("admin", "관리자"), ("guest", "참여자")]
STATES = [("done", "완료"), ("active", "진행 중"), ("planned", "예정")]

class GoogleAccount(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    subject = models.CharField(max_length=255, unique=True)

class Room(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField(max_length=40)
    species = models.CharField(max_length=16, choices=SPECIES)
    subspecies = models.CharField(max_length=80, blank=True)
    breed_key = models.CharField(max_length=64, blank=True)
    size_origin = models.CharField(max_length=8, choices=[("auto", "품종 자료로 제안"), ("manual", "직접 선택")], default="manual")
    maturity = models.CharField(max_length=8, choices=[("", "모름"), ("growing", "성장 중"), ("adult", "성장 완료")], blank=True)
    groups_version = models.PositiveIntegerField(default=1)
    size = models.CharField(max_length=10, choices=SIZES, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    sex = models.CharField(max_length=10, choices=[("", "모름"), ("male", "수컷"), ("female", "암컷")], blank=True)
    timezone = models.CharField(max_length=64, default="Asia/Seoul")
    guest_sensitive = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)
    settings_version = models.PositiveIntegerField(default=1)
    revision = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self):
        return self.name

class Membership(models.Model):
    notices_enabled = models.BooleanField(default=True)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    guest_key = models.UUIDField(null=True, blank=True)
    display_name = models.CharField(max_length=40)
    role = models.CharField(max_length=10, choices=ROLES, default="guest")
    active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [
            models.CheckConstraint(condition=(Q(user__isnull=False, guest_key__isnull=True) | Q(user__isnull=True, guest_key__isnull=False)), name="member_exactly_one_identity"),
            models.CheckConstraint(condition=(Q(role="guest") | Q(user__isnull=False)), name="privileged_member_authenticated"),
            models.UniqueConstraint(fields=["room", "user"], condition=Q(user__isnull=False), name="one_user_per_room"),
            models.UniqueConstraint(fields=["room", "guest_key"], condition=Q(guest_key__isnull=False), name="one_guest_per_room"),
            models.UniqueConstraint(fields=["room"], condition=Q(role="owner"), name="one_owner_per_room"),
        ]
    @property
    def can_manage(self):
        return self.active and self.role in ("owner", "admin") and self.user_id is not None
    @property
    def role_label(self):
        return "게스트" if self.role == "guest" and self.user_id is None else self.get_role_display()
    def __str__(self):
        return self.display_name

class ModuleSetting(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="module_settings")
    key = models.CharField(max_length=24)
    enabled = models.BooleanField(default=False)
    pinned = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)
    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["room", "key"], name="unique_room_module")]

class Product(models.Model):
    brand = models.CharField(max_length=100, blank=True)
    stock_item = models.CharField(max_length=100, blank=True)
    stock_unit = models.CharField(max_length=8, blank=True, choices=(("g", "g"), ("ml", "ml"), ("piece", "개")))
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="products")
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=8, choices=[("meal", "식사"), ("treat", "간식"), ("both", "공용")], default="meal")
    energy = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    energy_basis = models.CharField(max_length=10, choices=[("100g", "kcal / 100 g"), ("kg", "kcal / kg"), ("piece", "kcal / 개"), ("100ml", "kcal / 100 ml")], default="100g")
    grams_per_piece = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    protein = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    protein_kind = models.CharField(max_length=8, choices=[("exact", "표시값"), ("min", "최소"), ("max", "최대")], default="min")
    fat = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    fat_kind = models.CharField(max_length=8, choices=[("exact", "표시값"), ("min", "최소"), ("max", "최대")], default="min")
    source = models.CharField(max_length=300, blank=True)
    active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return self.name

class Activity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="activities")
    module = models.CharField(max_length=24)
    author = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="authored")
    performer = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True, blank=True, related_name="performed")
    occurred_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATES, default="done")
    payload = models.JSONField(default=dict)
    nutrition = models.JSONField(default=dict)
    note = models.CharField(max_length=2000, blank=True)
    voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=300, blank=True)
    version = models.PositiveIntegerField(default=1)
    client_key = models.UUIDField(default=uuid.uuid4)
    fingerprint = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ["-occurred_at", "-created_at"]
        indexes = [models.Index(fields=["room", "module", "occurred_at"]), models.Index(fields=["room", "status", "voided"])]
        constraints = [models.UniqueConstraint(fields=["room", "client_key"], name="unique_room_request"), models.CheckConstraint(condition=(Q(ended_at__isnull=True) | Q(ended_at__gte=F("occurred_at"))), name="end_not_before_start")]

class ActivityRevision(models.Model):
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="revisions")
    actor = models.ForeignKey(Membership, on_delete=models.PROTECT)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    reason = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at", "-id"]

class Invitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="invitations")
    token_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(Membership, on_delete=models.PROTECT)
    expires_at = models.DateTimeField()
    max_uses = models.PositiveIntegerField(default=10)
    uses = models.PositiveIntegerField(default=0)
    revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    @property
    def usable(self):
        return not self.revoked and self.expires_at > timezone.now() and self.uses < self.max_uses and self.room.archived_at is None

class Routine(models.Model):
    end_date = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=1000, blank=True)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="routines")
    name = models.CharField(max_length=80)
    at = models.TimeField()
    weekdays = models.JSONField(default=list)
    assignee = models.ForeignKey(Membership, null=True, blank=True, on_delete=models.PROTECT)
    start_date = models.DateField()
    active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["at", "name"]

class RoutineItem(models.Model):
    preset = models.ForeignKey("RecordPreset", null=True, blank=True, on_delete=models.SET_NULL, related_name="routine_items")
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    routine = models.ForeignKey(Routine, on_delete=models.CASCADE, related_name="items")
    module = models.CharField(max_length=24)
    position = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)
    class Meta:
        ordering = ["position", "id"]

class RoutineVersion(models.Model):
    routine = models.ForeignKey(Routine, on_delete=models.CASCADE, related_name="versions")
    effective_on = models.DateField()
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-effective_on", "-id"]

class RoutineRun(models.Model):
    skipped_at = models.DateTimeField(null=True, blank=True)
    skip_note = models.CharField(max_length=300, blank=True)
    item = models.ForeignKey(RoutineItem, on_delete=models.CASCADE, related_name="runs")
    date = models.DateField()
    activity = models.OneToOneField(Activity, null=True, blank=True, on_delete=models.SET_NULL, related_name="routine_run")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["item", "date"], name="one_routine_execution")]

class Reference(models.Model):
    key = models.CharField(max_length=80, unique=True)
    species = models.CharField(max_length=16, choices=SPECIES)
    size = models.CharField(max_length=10, blank=True)
    module = models.CharField(max_length=24)
    metric = models.CharField(max_length=30)
    label = models.CharField(max_length=120)
    low = models.DecimalField(max_digits=12, decimal_places=3)
    high = models.DecimalField(max_digits=12, decimal_places=3)
    unit = models.CharField(max_length=24)
    source_name = models.CharField(max_length=160)
    source_url = models.URLField(max_length=500)
    conditions = models.CharField(max_length=1000)
    checked_on = models.DateField()

class AuditEvent(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="audit")
    actor = models.ForeignKey(Membership, null=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=60)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at", "-id"]

class RateBucket(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    period = models.BigIntegerField()
    count = models.PositiveIntegerField(default=0)


class ModuleConfigVersion(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="module_versions")
    effective_at = models.DateTimeField(default=timezone.now)
    settings = models.JSONField(default=dict)
    class Meta:
        ordering = ["-effective_at", "-id"]


class CareCadence(models.Model):
    """User-defined care intervals, separate from module availability and daily routines."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="cadences")
    module = models.CharField(max_length=32)
    variant_field = models.CharField(max_length=32, blank=True)
    variant_value = models.CharField(max_length=80, blank=True)
    title = models.CharField(max_length=80)
    interval = models.PositiveSmallIntegerField()
    unit = models.CharField(max_length=8, choices=[("days", "일"), ("weeks", "주"), ("months", "개월")])
    first_due = models.DateField()
    configured_at = models.DateTimeField(default=timezone.now)
    snoozed_until = models.DateField(null=True, blank=True)
    snoozed_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["first_due", "title", "id"]
        constraints = [
            models.UniqueConstraint(fields=["room", "module", "variant_field", "variant_value"], name="unique_care_cadence_activity"),
            models.CheckConstraint(condition=models.Q(interval__gte=1, interval__lte=366), name="cadence_interval_range"),
        ]


class ModuleGroup(models.Model):
    """A room's display organization; never changes analytical meaning or record ownership."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="module_groups")
    name = models.CharField(max_length=40)
    keys = models.JSONField(default=list)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["room", "name"], name="unique_room_module_group_name")]


class RecordPreset(models.Model):
    """Reusable input; every created activity keeps its own immutable preset snapshot."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="record_presets")
    module = models.CharField(max_length=32, choices=MODULE_CHOICES)
    title = models.CharField(max_length=100)
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL, related_name="record_presets")
    data = models.JSONField(default=dict)
    is_default = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "title", "id"]
        constraints = [models.UniqueConstraint(fields=["room", "module"], condition=models.Q(active=True, is_default=True), name="one_active_default_preset")]
        indexes = [models.Index(fields=["room", "module", "active"])]

    def __str__(self):
        return self.title


class ActionReceipt(models.Model):
    """One logical request, one outcome, including retry after a lost HTTP response."""
    id = models.BigAutoField(primary_key=True)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="action_receipts")
    actor = models.ForeignKey(Membership, null=True, on_delete=models.SET_NULL, related_name="action_receipts")
    token = models.UUIDField()
    request_hash = models.CharField(max_length=64)
    operation = models.CharField(max_length=40)
    activity = models.ForeignKey(Activity, null=True, blank=True, on_delete=models.SET_NULL, related_name="action_receipts")
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["room", "token"], name="one_action_per_room_token")]
        indexes = [models.Index(fields=["room", "created_at"])]


class NoticeDismissal(models.Model):
    """Personal acknowledgement, never deletion or completion of the underlying schedule."""
    member = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="notice_dismissals")
    notice_key = models.CharField(max_length=160)
    stage = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["member", "notice_key", "stage"], name="one_notice_ack_per_stage")]
