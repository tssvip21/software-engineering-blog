from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from care import services
from care.forms import RoomForm, RecordForm, RoutineForm
from care.models import Room, Membership, Product, ModuleConfigVersion, RoutineVersion
from care.modules import MODULES
from care.demo_fixtures import record_data

class Command(BaseCommand):
    help = "별도 로컬 시연 DB에만 가상 데이터를 추가합니다. 기존 데이터는 지우지 않습니다."
    def add_arguments(self, parser):
        parser.add_argument("--accounts-only", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEMO_MODE or not settings.DEBUG or not (settings.LOCAL_DEMO_POSTGRES or str(settings.DATABASES["default"]["NAME"]).endswith("demo.sqlite3")):
            raise CommandError("로컬 전용 시연 데이터베이스에서만 실행할 수 있습니다.")
        User = get_user_model()
        users = {}
        for key, name in (("owner", "지우"), ("admin", "민서"), ("member", "도윤"), ("outsider", "새 보호자")):
            u, created = User.objects.get_or_create(username="demo_" + key, defaults={"first_name": name})
            if created:
                u.set_unusable_password(); u.save(update_fields=["password"])
            users[key] = u
        if options["accounts_only"]:
            return
        if Room.objects.filter(owner=users["owner"]).exists():
            self.stdout.write("Demo already initialized; existing records preserved.")
            return
        def pet(name, species):
            form = RoomForm({"name": name, "species": species, "size": "small" if species == "dog" else "", "timezone": "Asia/Seoul", "use_defaults": "on"})
            if not form.is_valid(): raise CommandError(str(form.errors))
            return services.create_room(users["owner"], form)
        room = pet("보리", "dog")
        bird = pet("초록이", "bird")
        owner = room.members.get(role="owner")
        Membership.objects.create(room=room, user=users["admin"], display_name="민서", role="admin")
        Membership.objects.create(room=room, user=users["member"], display_name="도윤", role="guest")
        room.module_settings.update(enabled=True)
        today = services.local_date(room)
        earliest = datetime.combine(today - timedelta(days=40), time(0), ZoneInfo(room.timezone))
        ModuleConfigVersion.objects.filter(room=room).update(effective_at=earliest, settings={"enabled": list(MODULES), "order": list(MODULES)})
        product = Product.objects.create(room=room, name="사료 A", category="meal", energy=380, energy_basis="100g", protein=24, fat=12, source="기능 설명을 위한 가상 영양 정보")
        for offset in (35, 29, 14, 7, 6, 5, 4, 3, 2, 1):
            for key in ("meal", "walk", "water", "weight"):
                at = datetime.combine(today - timedelta(days=offset), time(18, 0), ZoneInfo(room.timezone))
                values = {"occurred_at": at.strftime("%Y-%m-%dT%H:%M")}
                if key == "meal": values.update({"product": str(product.pk), "quantity": str(55 + offset % 3 * 5)})
                if key == "walk": values.update({"minutes": str(22 + offset % 5 * 4), "distance": str(Decimal("1.2") + Decimal(offset % 5) / 10)})
                if key == "weight": values["weight"] = str(Decimal("8.4") + Decimal(offset % 6) / 20)
                form = RecordForm(record_data(key, **values), room=room, module=key, member=owner)
                if not form.is_valid(): raise CommandError(str(form.errors))
                services.save_activity(owner, key, form)
        for key in MODULES:
            if key == "reproduction": continue
            values = {"product": str(product.pk)} if key == "meal" else {}
            if key != "sleep":
                values["occurred_at"] = timezone.localtime(timezone.now()-timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
            form = RecordForm(record_data(key, **values), room=room, module=key, member=owner)
            if not form.is_valid(): raise CommandError(key + ": " + str(form.errors))
            services.save_activity(owner, key, form)
        birdowner = bird.members.get(role="owner")
        bird.module_settings.filter(key__in=("reproduction", "cleaning", "meal", "training", "water", "weight")).update(enabled=True)
        for key in ("cleaning", "training", "reproduction"):
            values = {"target": "cage", "task": "케이지 바닥 청소"} if key == "cleaning" else {"skill": "손에 올라오기"} if key == "training" else {}
            form = RecordForm(record_data(key, **values), room=bird, module=key, member=birdowner)
            if not form.is_valid(): raise CommandError(str(form.errors))
            services.save_activity(birdowner, key, form)
        routine_form = RoutineForm({"name": "저녁 돌봄", "at": "19:00", "weekdays": [str(i) for i in range(7)], "modules": ["meal", "walk"], "active": "on"}, room=room, visible_keys=list(MODULES))
        if not routine_form.is_valid(): raise CommandError(str(routine_form.errors))
        services.save_routine(owner, routine_form)
        self.stdout.write("Demo ready: 2 rooms, 4 identities, 20 module types represented.")
