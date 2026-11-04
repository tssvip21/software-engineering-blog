from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch, Mock
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo
import hashlib
import secrets
import time as clock
import uuid
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from care import services
from care.demo_fixtures import record_data
from care.forms import RoomForm, RecordForm, ProductForm, RoutineForm
from care.models import Activity, ActivityRevision, AuditEvent, GoogleAccount, Invitation, Membership, Product, Reference, Room, RoutineRun
from care.modules import MODULES
from care.stats import get_statistics, overview, routine_schedule


class CareCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner_user = User.objects.create_user(username="owner", first_name="보호자")
        cls.admin_user = User.objects.create_user(username="admin", first_name="관리자")
        cls.member_user = User.objects.create_user(username="member", first_name="참여자")
        cls.outsider_user = User.objects.create_user(username="outsider", first_name="외부인")
        form = RoomForm({"name": "테스트 동물", "species": "dog", "timezone": "Asia/Seoul", "size": "small", "use_defaults": "on"})
        assert form.is_valid(), form.errors
        cls.room = services.create_room(cls.owner_user, form)
        cls.owner = cls.room.members.get(role="owner")
        cls.admin = Membership.objects.create(room=cls.room, user=cls.admin_user, display_name="관리자", role="admin")
        cls.member = Membership.objects.create(room=cls.room, user=cls.member_user, display_name="참여자", role="guest")
        cls.guest_key = uuid.uuid4()
        cls.guest = Membership.objects.create(room=cls.room, guest_key=cls.guest_key, display_name="게스트", role="guest")
        cls.room.module_settings.update(enabled=True)
        cls.room.module_versions.update(effective_at=timezone.now() - timedelta(days=70), settings={"enabled": list(MODULES), "order": list(MODULES)})
        other_form = RoomForm({"name": "다른 방", "species": "cat", "timezone": "Asia/Seoul", "use_defaults": "on"})
        assert other_form.is_valid(), other_form.errors
        cls.other_room = services.create_room(cls.outsider_user, other_form)
        cls.product = Product.objects.create(room=cls.room, name="확인한 제품", energy=380, energy_basis="100g", source="제품 포장지")

    def setUp(self):
        timezone.activate(ZoneInfo("Asia/Seoul"))
        self.client.force_login(self.owner_user)
        self.room.refresh_from_db()
        self.owner.refresh_from_db()

    def tearDown(self):
        timezone.deactivate()

    def form(self, module, member=None, instance=None, **changes):
        data = record_data(module, **changes)
        if instance:
            data["version"] = str(instance.version)
            data["client_key"] = str(instance.client_key)
        return RecordForm(data, room=self.room, module=module, member=member or self.owner, instance=instance)

    def make(self, module, member=None, **changes):
        form = self.form(module, member, **changes)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        return services.save_activity(member or self.owner, module, form)[0]

    def client_for(self, member):
        client = Client()
        if member.user_id:
            client.force_login(member.user)
        else:
            session = client.session
            session["guest_identity"] = str(member.guest_key)
            session.save()
        return client

    def routine(self, modules=("meal", "walk")):
        form = RoutineForm({"name": "매일 돌봄", "at": "19:00", "weekdays": [str(i) for i in range(7)], "modules": list(modules), "active": "on"}, room=self.room, visible_keys=list(MODULES))
        self.assertTrue(form.is_valid(), form.errors)
        return services.save_routine(self.owner, form)

    def stats(self, module, start=None, end=None):
        day = services.local_date(self.room)
        return get_statistics(self.room, module, start or day-timedelta(days=5), end or day)


class ModuleCRUDTests(CareCase):
    def test_exactly_twenty_distinct_module_types(self):
        self.assertEqual(len(MODULES), 20)
        self.assertEqual(self.room.module_settings.count(), 20)
        self.assertEqual(len({tuple((f.key, f.choices, f.label) for f in m.fields) for m in MODULES.values()}), 20)


def create_module_test(module):
    def test(self):
        response = self.client.get(reverse("record_new", args=[self.room.pk, module]))
        self.assertEqual(response.status_code, 200)
        data = record_data(module)
        response = self.client.post(reverse("record_new", args=[self.room.pk, module]), data)
        self.assertEqual(response.status_code, 302, response.content[:1500])
        activity = Activity.objects.get(room=self.room, module=module)
        self.assertEqual(activity.author_id, self.owner.pk)
        self.assertEqual(self.client.get(response.url).status_code, 200)
        for name in ("module", "statistics"):
            self.assertEqual(self.client.get(reverse(name, args=[self.room.pk, module])).status_code, 200)
        edit_data = record_data(module, note="실제 수정한 내용", version=str(activity.version), client_key=str(activity.client_key))
        response = self.client.post(reverse("record_edit", args=[self.room.pk, activity.pk]), edit_data)
        self.assertEqual(response.status_code, 302, response.content[:1500])
        activity.refresh_from_db()
        self.assertEqual(activity.note, "실제 수정한 내용")
        self.assertEqual(activity.version, 2)
        self.assertEqual(activity.revisions.count(), 2)
        response = self.client.post(reverse("record_cancel", args=[self.room.pk, activity.pk]), {"version": activity.version, "reason": "검증을 위한 취소"})
        self.assertEqual(response.status_code, 302)
        activity.refresh_from_db()
        self.assertTrue(activity.voided)
        self.assertEqual(activity.revisions.count(), 3)
        self.assertEqual(self.stats(module)["count"], 0)
    return test


for _key in MODULES:
    setattr(ModuleCRUDTests, "test_crud_" + _key, create_module_test(_key))


class AccessTests(CareCase):
    def test_owner_all_main_pages(self):
        for name in ("home", "room_new"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        for name in ("today", "modules", "module_settings", "records", "overview", "products", "product_new", "room_settings", "members", "routines", "routine_new"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name, args=[self.room.pk])).status_code, 200)

    def test_other_room_not_found(self):
        for name in ("today", "modules", "records", "overview", "members", "room_state"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name, args=[self.other_room.pk])).status_code, 404)

    def test_sensitive_modules_hidden_from_guest(self):
        client = self.client_for(self.guest)
        for key in ("expense", "medication", "veterinary"):
            self.make(key)
            for page in ("module", "statistics", "record_new"):
                with self.subTest(key=key, page=page):
                    self.assertEqual(client.get(reverse(page, args=[self.room.pk, key])).status_code, 403)
        self.assertNotContains(client.get(reverse("modules", args=[self.room.pk])), reverse("module", args=[self.room.pk, "expense"]))
        self.assertEqual(client.get(reverse("records", args=[self.room.pk]), {"module": "expense"}).status_code, 403)

    def test_owner_can_enable_sensitive_sharing(self):
        Room.objects.filter(pk=self.room.pk).update(guest_sensitive=True)
        client = self.client_for(self.guest)
        self.assertEqual(client.get(reverse("module", args=[self.room.pk, "expense"])).status_code, 200)

    def test_guest_cannot_change_settings(self):
        client = self.client_for(self.guest)
        self.assertEqual(client.post(reverse("module_settings", args=[self.room.pk]), {}).status_code, 403)
        self.assertEqual(client.post(reverse("invitation_create", args=[self.room.pk]), {}).status_code, 403)
        self.assertEqual(client.get(reverse("product_new", args=[self.room.pk])).status_code, 403)

    def test_guest_can_edit_own_not_other(self):
        own = self.make("meal", member=self.guest)
        other = self.make("meal")
        client = self.client_for(self.guest)
        self.assertEqual(client.get(reverse("record_edit", args=[self.room.pk, own.pk])).status_code, 200)
        self.assertEqual(client.get(reverse("record_edit", args=[self.room.pk, other.pk])).status_code, 403)
        self.assertEqual(client.post(reverse("record_cancel", args=[self.room.pk, other.pk]), {"version": 1, "reason": "취소"}).status_code, 403)

    def test_admin_can_edit_other_but_not_promote(self):
        activity = self.make("meal")
        client = self.client_for(self.admin)
        self.assertEqual(client.get(reverse("record_edit", args=[self.room.pk, activity.pk])).status_code, 200)
        self.assertEqual(client.post(reverse("member_change", args=[self.room.pk, self.member.pk]), {"action": "promote"}).status_code, 403)

    def test_login_does_not_promote(self):
        self.assertEqual(self.client_for(self.member).post(reverse("module_settings", args=[self.room.pk]), {}).status_code, 403)
        self.member.refresh_from_db()
        self.assertEqual(self.member.role, "guest")

    def test_guest_cannot_be_promoted_without_account(self):
        with self.assertRaises(ValidationError):
            services.change_member(self.owner, self.guest.pk, "promote")

    def test_promotion_demotion_and_live_permissions(self):
        services.change_member(self.owner, self.member.pk, "promote")
        self.assertEqual(self.client_for(self.member).get(reverse("module_settings", args=[self.room.pk])).status_code, 200)
        self.member.refresh_from_db()
        stale_admin = self.member
        services.change_member(self.owner, self.member.pk, "demote")
        with self.assertRaises(PermissionDenied):
            services.update_modules(stale_admin, list(MODULES), list(MODULES), self.room.settings_version)

    def test_removed_guest_immediately_loses_access(self):
        client = self.client_for(self.guest)
        self.assertEqual(client.get(reverse("today", args=[self.room.pk])).status_code, 200)
        services.change_member(self.owner, self.guest.pk, "remove", True)
        self.assertEqual(client.get(reverse("today", args=[self.room.pk])).status_code, 404)

    def test_foreign_product_and_performer_rejected(self):
        foreign = Product.objects.create(room=self.other_room, name="다른 방 제품")
        for changes in ({"product": str(foreign.pk)}, {"performer": str(self.other_room.members.get(role="owner").pk)}):
            form = self.form("meal", **changes)
            self.assertFalse(form.is_valid())

    def test_get_never_mutates_or_cancels(self):
        activity = self.make("meal")
        self.assertEqual(self.client.get(reverse("record_cancel", args=[self.room.pk, activity.pk])).status_code, 405)
        self.assertEqual(self.client.get(reverse("invitation_create", args=[self.room.pk])).status_code, 405)

    def test_csrf_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner_user)
        response = client.post(reverse("record_new", args=[self.room.pk, "meal"]), record_data("meal"))
        self.assertEqual(response.status_code, 403)

    def test_private_headers_and_escaped_user_text(self):
        activity = self.make("meal", note='<script>alert("x")</script>')
        response = self.client.get(reverse("record_detail", args=[self.room.pk, activity.pk]))
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, '<script>alert("x")</script>')
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("default-src 'self'", response["Content-Security-Policy"])
        self.assertEqual(response["X-Frame-Options"], "DENY")

    def test_demo_route_not_present_in_normal_mode(self):
        self.assertEqual(self.client.post("/demo/login/", {"identity": "owner"}).status_code, 404)

    def test_unauthenticated_room_creation_redirects(self):
        self.assertEqual(Client().post(reverse("room_new"), {"name": "불가"}).status_code, 302)

    def test_same_display_name_does_not_transfer_identity(self):
        invite, token = services.create_invitation(self.owner, 24, 3)
        first, second = Client(), Client()
        for c in (first, second):
            response = c.post(reverse("join_confirm"), {"token": token, "display_name": "같은 이름", "agree": "on"})
            self.assertEqual(response.status_code, 302)
        people = self.room.members.filter(display_name="같은 이름")
        self.assertEqual(people.count(), 2)
        self.assertEqual(len(set(people.values_list("guest_key", flat=True))), 2)

    def test_database_role_constraints(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(room=self.room, guest_key=uuid.uuid4(), display_name="위조", role="admin")


class StateTests(CareCase):
    def test_idempotent_retry_single_record(self):
        data = record_data("meal")
        form = RecordForm(data, room=self.room, module="meal", member=self.owner)
        self.assertTrue(form.is_valid())
        first, created = services.save_activity(self.owner, "meal", form)
        second, created_again = services.save_activity(self.owner, "meal", form)
        self.assertEqual(first.pk, second.pk)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(Activity.objects.filter(room=self.room).count(), 1)

    def test_reused_id_with_changed_payload_rejected(self):
        a = self.make("meal")
        f = self.form("meal", client_key=str(a.client_key), quantity="99")
        self.assertTrue(f.is_valid())
        with self.assertRaises(services.Conflict):
            services.save_activity(self.owner, "meal", f)

    def test_stale_edit_does_not_overwrite(self):
        a = self.make("meal")
        f1 = self.form("meal", instance=a, note="첫 수정")
        f2 = self.form("meal", instance=a, note="뒤늦은 수정")
        self.assertTrue(f1.is_valid()); self.assertTrue(f2.is_valid())
        services.save_activity(self.owner, "meal", f1, a.pk)
        with self.assertRaises(services.Conflict):
            services.save_activity(self.owner, "meal", f2, a.pk)
        a.refresh_from_db(); self.assertEqual(a.note, "첫 수정")

    def test_module_off_keeps_history_disallows_new(self):
        a = self.make("meal")
        self.room.module_settings.filter(key="meal").update(enabled=False)
        self.assertEqual(self.client.get(reverse("module", args=[self.room.pk, "meal"])).status_code, 200)
        self.assertEqual(self.client.post(reverse("record_new", args=[self.room.pk, "meal"]), record_data("meal")).status_code, 409)
        self.assertEqual(Activity.objects.filter(pk=a.pk).count(), 1)
        self.assertEqual(self.stats("meal")["count"], 1)

    def test_routine_duplicate_completion_blocked(self):
        r = self.routine(("meal",))
        day = services.local_date(self.room)
        item = r.items.get()
        now = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
        changes = {"routine_item": str(item.pk), "routine_date": day.isoformat(), "occurred_at": now}
        a = self.make("meal", **changes)
        f = self.form("meal", **changes)
        self.assertTrue(f.is_valid())
        with self.assertRaises(services.Conflict): services.save_activity(self.owner, "meal", f)
        self.assertEqual(RoutineRun.objects.get(item=item, date=day).activity_id, a.pk)
        self.assertEqual(Activity.objects.filter(room=self.room).count(), 1)

    def test_routine_link_reuses_record_cancel_unlinks(self):
        r = self.routine(("meal",))
        day = services.local_date(self.room)
        a = self.make("meal", occurred_at=timezone.localtime().strftime("%Y-%m-%dT%H:%M"))
        services.link_activity(self.owner, r.items.get().pk, day, a.pk)
        self.assertEqual(Activity.objects.filter(room=self.room).count(), 1)
        self.assertTrue(routine_schedule(self.owner, day)[0]["items"][0]["done"])
        services.cancel_activity(self.owner, a.pk, a.version, "취소")
        self.assertIsNone(RoutineRun.objects.get().activity_id)
        self.assertFalse(routine_schedule(self.owner, day)[0]["items"][0]["done"])

    def test_routine_off_module_pauses(self):
        self.routine(("meal",))
        self.room.module_settings.filter(key="meal").update(enabled=False)
        schedule = routine_schedule(self.owner, services.local_date(self.room))
        self.assertTrue(schedule[0]["items"][0]["paused"])

    def test_invite_limit_expiry_revoke(self):
        invite, token = services.create_invitation(self.owner, 1, 1)
        response = Client().post(reverse("join_confirm"), {"token": token, "display_name": "첫 게스트", "agree": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Client().post(reverse("join_preview"), {"token": token}).status_code, 410)
        invite2, token2 = services.create_invitation(self.owner, 1, 2)
        services.revoke_invitation(self.owner, invite2.pk)
        with self.assertRaises(ValidationError): services.get_invitation(token2)
        invite3, token3 = services.create_invitation(self.owner, 1, 2)
        Invitation.objects.filter(pk=invite3.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(ValidationError): services.get_invitation(token3)

    def test_invite_preview_not_join(self):
        _, token = services.create_invitation(self.owner, 24, 10)
        before = self.room.members.count()
        response = Client().post(reverse("join_preview"), {"token": token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.room.members.count(), before)
        self.assertEqual(Client().get(reverse("join")).status_code, 200)

    def test_guest_account_merge_keeps_records_without_promotion(self):
        a = self.make("meal", member=self.guest)
        user = get_user_model().objects.create_user(username="fresh")
        services.merge_guest_memberships(self.guest_key, user)
        self.guest.refresh_from_db(); a.refresh_from_db()
        self.assertEqual(a.author_id, self.guest.pk)
        self.assertEqual(self.guest.user_id, user.pk)
        self.assertEqual(self.guest.role, "guest")
        self.assertIsNone(self.guest.guest_key)

    def test_guest_merge_into_existing_account_preserves_author(self):
        a = self.make("meal", member=self.guest)
        services.merge_guest_memberships(self.guest_key, self.member_user)
        a.refresh_from_db(); self.guest.refresh_from_db()
        self.assertEqual(a.author_id, self.member.pk)
        self.assertFalse(self.guest.active)
        self.member.refresh_from_db(); self.assertEqual(self.member.role, "guest")

    def test_archive_blocks_room_access_and_invites(self):
        _, token = services.create_invitation(self.owner, 24, 10)
        services.archive_room(self.owner, self.room.name)
        self.assertEqual(self.client.get(reverse("today", args=[self.room.pk])).status_code, 404)
        with self.assertRaises(ValidationError): services.get_invitation(token)

    def test_room_delete_is_scoped(self):
        self.make("meal"); self.routine()
        services.delete_room(self.owner, self.room.name + " 삭제")
        self.assertFalse(Room.objects.filter(pk=self.room.pk).exists())
        self.assertTrue(Room.objects.filter(pk=self.other_room.pk).exists())
        self.assertFalse(Activity.objects.filter(room_id=self.room.pk).exists())

    def test_module_reordering_and_stale_settings(self):
        self.room.refresh_from_db()
        order = list(reversed(MODULES))
        services.update_modules(self.owner, ["meal"], order, self.room.settings_version)
        self.assertEqual(list(self.room.module_settings.values_list("key", flat=True)), order)
        with self.assertRaises(services.Conflict):
            services.update_modules(self.owner, ["meal"], order, self.room.settings_version)


class CalculationTests(CareCase):
    def test_food_unit_conversion_and_snapshot(self):
        a = self.make("meal", product=str(self.product.pk), quantity="0.06", unit="kg")
        self.assertEqual(Decimal(a.nutrition["kcal"]), Decimal("228"))
        Product.objects.filter(pk=self.product.pk).update(energy=999)
        a.refresh_from_db()
        self.assertEqual(Decimal(a.nutrition["kcal"]), Decimal("228"))
        form = self.form("meal", instance=a, product=str(self.product.pk), quantity="0.06", unit="kg", note="메모만 수정")
        self.assertTrue(form.is_valid(), form.errors)
        updated,_ = services.save_activity(self.owner,"meal",form,a.pk)
        self.assertEqual(Decimal(updated.nutrition["kcal"]),Decimal("228"))

    def test_unknown_food_not_zero(self):
        a = self.make("meal")
        self.assertIsNone(a.nutrition["kcal"])
        self.assertEqual(self.stats("meal")["metrics"][1]["value"], "미기록")

    def test_zero_actual_consumption_is_known_zero(self):
        a = self.make("meal", product=str(self.product.pk), consumed="0")
        self.assertEqual(Decimal(a.nutrition["kcal"]), 0)
        self.assertEqual(a.nutrition["basis"], "consumed")

    def test_water_refill_never_becomes_drinking(self):
        self.make("water", kind="refill", amount="1000")
        self.make("water", kind="measured", amount="0.2", unit="l")
        stats = self.stats("water")
        self.assertEqual(stats["primary"], "200")
        self.assertEqual(stats["primary_unit"], "ml")

    def test_weight_is_last_measurement_not_sum(self):
        self.make("weight", weight="8", occurred_at=(timezone.localtime()-timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"))
        self.make("weight", weight="8500", unit="g")
        self.assertEqual(self.stats("weight")["primary"], "8.5")

    def test_expense_currencies_separated(self):
        self.make("expense", amount="1000", currency="KRW")
        self.make("expense", amount="10", currency="USD")
        stats = self.stats("expense")
        values = {(m["label"],m["value"],m["unit"]) for m in stats["metrics"]}
        self.assertIn(("KRW 지출","1000","KRW"),values)
        self.assertIn(("USD 지출","10","USD"),values)
        self.assertEqual(stats["primary_unit"], "개 통화")

    def test_training_weighted_success(self):
        self.make("training", attempts="10", successes="5")
        self.make("training", attempts="2", successes="2")
        rate = next(m for m in self.stats("training")["metrics"] if m["label"] == "성공률")
        self.assertEqual(rate["value"], "58.333")

    def test_inventory_normalization_and_cancellation_rollback(self):
        at = timezone.localtime()-timedelta(days=1)
        incoming = self.make("inventory", quantity="1", unit="kg", occurred_at=at.strftime("%Y-%m-%dT%H:%M"))
        outgoing = self.make("inventory", movement="out", quantity="200", unit="g")
        stats = self.stats("inventory")
        self.assertEqual(stats["breakdown"][0]["value"], "800")
        with self.assertRaises(ValidationError): services.cancel_activity(self.owner,incoming.pk,incoming.version,"입고 취소")
        incoming.refresh_from_db(); self.assertFalse(incoming.voided)
        services.cancel_activity(self.owner,outgoing.pk,outgoing.version,"사용 취소")
        self.assertEqual(self.stats("inventory")["breakdown"][0]["value"], "1000")

    def test_negative_inventory_rejected_atomically(self):
        before = Activity.objects.count()
        with self.assertRaises(ValidationError): self.make("inventory", movement="out", quantity="1", unit="piece")
        self.assertEqual(Activity.objects.count(), before)

    def test_sleep_overlap_and_midnight(self):
        yesterday = services.local_date(self.room)-timedelta(days=1)
        base = datetime.combine(yesterday,time(23,30),ZoneInfo("Asia/Seoul"))
        self.make("sleep", occurred_at=base.strftime("%Y-%m-%dT%H:%M"), minutes="40")
        self.make("sleep", occurred_at=(base+timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M"), minutes="30")
        stat = self.stats("sleep",yesterday,yesterday)
        self.assertEqual(stat["primary"],"30")
        self.assertEqual(self.stats("sleep",yesterday,yesterday+timedelta(days=1))["primary"],"40")

    def test_environment_unit_consistency(self):
        invalid = self.form("environment",metric="humidity",unit="C",value="40")
        self.assertFalse(invalid.is_valid())
        self.make("environment",value="68",unit="F")
        self.assertIn("최근 20", self.stats("environment")["breakdown"][0]["value"])

    def test_planned_and_voided_not_in_completed_totals(self):
        self.make("medication",status="planned")
        a = self.make("medication")
        services.cancel_activity(self.owner,a.pk,a.version,"취소")
        self.assertEqual(self.stats("medication")["count"],0)

    def test_calendar_month_is_not_fixed_thirty_days(self):
        result = overview(self.owner,end_date=date(2026,3,31))
        self.assertEqual(result["dates"][2],date(2026,2,28))

    def test_invalid_values_rejected(self):
        cases = [("meal",{"quantity":"-1"}),("meal",{"consumed":"80"}),("weight",{"weight":"0"}),
                 ("training",{"attempts":"2","successes":"3"}),("reproduction",{"eggs":"1.5"}),
                 ("environment",{"metric":"humidity","unit":"percent","value":"101"}),
                 ("expense",{"amount":"2.5","currency":"KRW"}),("water",{"kind":"measured","amount":""}),
                 ("sleep",{"occurred_at":timezone.localtime().strftime("%Y-%m-%dT%H:%M"),"minutes":"60"})]
        for key, changes in cases:
            with self.subTest(key=key,changes=changes): self.assertFalse(self.form(key,**changes).is_valid())

    def test_references_do_not_invent_weight_cutoffs(self):
        call_command("seed_references",verbosity=0)
        self.assertEqual(Reference.objects.count(),2)
        self.assertFalse(Reference.objects.filter(metric="weight").exists())
        self.make("weight",bcs="5",bcs_by="vet")
        self.assertEqual(self.stats("weight")["reference"]["position"],"참고 범위 안")
        self.assertIn("자동",self.stats("weight")["reference"]["conditions"])

    def test_local_day_boundary(self):
        yesterday=services.local_date(self.room)-timedelta(days=1)
        at=datetime.combine(yesterday,time(23,59),ZoneInfo("Asia/Seoul"))
        self.make("meal",occurred_at=at.strftime("%Y-%m-%dT%H:%M"))
        self.assertEqual(self.stats("meal",yesterday,yesterday)["count"],1)
        self.assertEqual(self.stats("meal",yesterday+timedelta(days=1),yesterday+timedelta(days=1))["count"],0)


@override_settings(GOOGLE_CLIENT_ID="test-client.apps.googleusercontent.com",GOOGLE_CLIENT_SECRET="test-secret",GOOGLE_CONFIGURED=True,DEMO_MODE=False)
class GoogleAuthTests(CareCase):
    def start(self, client):
        response=client.post(reverse("google_start"))
        self.assertEqual(response.status_code,302)
        query=parse_qs(urlparse(response.url).query)
        self.assertEqual(query["code_challenge_method"],["S256"])
        self.assertIn("nonce",query)
        return client.session["google_login"]

    def test_valid_identity_creates_account_and_preserves_guest(self):
        client=self.client_for(self.guest)
        a=self.make("meal",member=self.guest)
        attempt=self.start(client)
        response=Mock();response.raise_for_status.return_value=None;response.json.return_value={"id_token":"signed-test-token"}
        claims={"sub":"verified-subject","email":"person@example.test","email_verified":True,"name":"실제 인증 테스트","nonce":attempt["nonce"],"iss":"https://accounts.google.com","iat":clock.time()-1,"exp":clock.time()+600,"azp":settings.GOOGLE_CLIENT_ID}
        with patch("care.auth.requests.post",return_value=response),patch("care.auth.id_token.verify_oauth2_token",return_value=claims):
            result=client.get(reverse("google_callback"),{"code":"one-time-code","state":attempt["state"]})
        self.assertEqual(result.status_code,302)
        linked=GoogleAccount.objects.get(subject="verified-subject")
        self.guest.refresh_from_db();a.refresh_from_db()
        self.assertEqual(self.guest.user_id,linked.user_id)
        self.assertEqual(self.guest.role,"guest")
        self.assertEqual(a.author_id,self.guest.pk)

    def test_wrong_state_and_replay_rejected(self):
        client=Client();attempt=self.start(client)
        self.assertEqual(client.get(reverse("google_callback"),{"code":"x","state":"wrong"}).status_code,400)
        self.assertEqual(client.get(reverse("google_callback"),{"code":"x","state":attempt["state"]}).status_code,400)

    def test_wrong_nonce_rejected(self):
        client=Client();attempt=self.start(client)
        response=Mock();response.raise_for_status.return_value=None;response.json.return_value={"id_token":"signed-test-token"}
        with patch("care.auth.requests.post",return_value=response),patch("care.auth.id_token.verify_oauth2_token",return_value={"nonce":"bad"}):
            result=client.get(reverse("google_callback"),{"code":"x","state":attempt["state"]})
        self.assertEqual(result.status_code,400)
        self.assertEqual(GoogleAccount.objects.count(),0)

    def test_expired_login_rejected(self):
        client=Client();attempt=self.start(client)
        session=client.session;session["google_login"]={**attempt,"created":clock.time()-601};session.save()
        self.assertEqual(client.get(reverse("google_callback"),{"code":"x","state":attempt["state"]}).status_code,400)

    @override_settings(GOOGLE_CONFIGURED=False)
    def test_missing_google_setup_has_no_fake_login(self):
        client=Client()
        response=client.post(reverse("google_start"))
        self.assertEqual(response.status_code,302)
        self.assertNotIn("_auth_user_id",client.session)
