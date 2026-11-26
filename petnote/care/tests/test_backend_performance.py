"""Request-scoped read reuse must preserve authorization and historical summaries."""
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.http import Http404
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from care import services
from care.care_views import board_data
from care.dashboard import board_records, today_tiles
from care.models import Activity, Membership, Product, Room
from care.modules import MODULES
from care.reporting.module_reports import build_report
from care.reporting.stats import bounds, get_statistics
from care.supplies import filter_rows
from .test_functional import CareCase


class RequestReadTests(CareCase):
    def request(self, user=None):
        request = RequestFactory().get("/")
        request.user = user or self.owner_user
        request.session = {}
        return request

    def test_navigation_reuses_room_lookup_but_next_request_rechecks_membership(self):
        request = self.request()
        with self.assertNumQueries(1):
            room, member = services.get_room(request, self.room.pk)
            navigation = services.request_memberships(request)
        self.assertEqual(room.pk, self.room.pk)
        self.assertEqual([row.pk for row in navigation], [member.pk])

        Membership.objects.filter(pk=member.pk).update(active=False)
        with self.assertRaises(Http404):
            services.get_room(self.request(), self.room.pk)

    def test_request_identity_change_does_not_reuse_another_users_memberships(self):
        request = self.request()
        services.request_memberships(request)
        request.user = self.outsider_user
        self.assertEqual(
            [row.room_id for row in services.request_memberships(request)],
            [self.other_room.pk],
        )

    def test_settings_reuse_stays_inside_the_get_and_never_authorizes_writes(self):
        room, member = services.get_room(self.request(), self.room.pk)
        with self.assertNumQueries(1):
            first = services.read_module_settings(room)
            again = services.read_module_settings(room)
        self.assertEqual(first, again)
        room.module_settings.filter(key="walk").update(enabled=False)

        # A form opened before this change must still fail the live write check.
        with self.assertRaises(ValidationError):
            services.require_module(member, "walk", adding=True)
        current_room, _ = services.get_room(self.request(), self.room.pk)
        current = {setting.key: setting.enabled for setting in services.read_module_settings(current_room)}
        self.assertFalse(current["walk"])
        fresh = services.fresh_member(member)
        self.assertFalse(hasattr(fresh.room, "_care_read_cache"))

    def test_read_authorization_is_live_without_a_mutation_lock(self):
        self.admin.room = self.room
        Membership.objects.filter(pk=self.admin.pk).update(role="guest")
        with CaptureQueriesContext(connection) as captured:
            current = services.read_member(self.admin)
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0]["sql"].lstrip().upper().startswith("SELECT"))
        self.assertNotIn("FOR UPDATE", captured[0]["sql"].upper())
        self.assertFalse(current.can_manage)
        self.assertNotIn("medication", services.visible_modules(current))
        Membership.objects.filter(pk=self.admin.pk).update(active=False)
        with self.assertRaises(PermissionDenied):
            services.read_member(self.admin)

    def test_read_authorization_rejects_changed_identity_and_archived_room(self):
        Membership.objects.filter(pk=self.admin.pk).update(user=self.outsider_user)
        with self.assertRaises(PermissionDenied):
            services.read_member(self.admin)
        Room.objects.filter(pk=self.room.pk).update(archived_at=timezone.now())
        with self.assertRaises(PermissionDenied):
            services.read_member(self.owner)

    def test_reports_recheck_stale_privileged_membership_without_locking(self):
        Membership.objects.filter(pk=self.admin.pk).update(role="guest")
        with CaptureQueriesContext(connection) as captured:
            result = build_report(self.admin)
        self.assertNotIn("medication", result["enabled"])
        self.assertNotIn("expense", result["enabled"])
        statements = [query["sql"].upper() for query in captured]
        self.assertFalse(any("FOR UPDATE" in sql or sql.startswith("SAVEPOINT") for sql in statements))


class BoardHistoryTests(CareCase):
    def activity(self, module, at, payload, **extra):
        return Activity.objects.create(
            room=self.room, author=self.owner, performer=self.owner,
            module=module, occurred_at=at, payload=payload, **extra,
        )

    def all_visible(self, member):
        return filter_rows(member, Activity.objects.filter(
            room=self.room, voided=False, occurred_at__lte=timezone.now(),
        ).select_related("author", "performer").order_by("-occurred_at", "-created_at", "-id"))

    def test_old_daily_events_do_not_expand_board_materialization(self):
        self.owner.room = self.room
        now = timezone.now()
        Activity.objects.bulk_create([
            Activity(room=self.room, author=self.owner, performer=self.owner,
                     module="walk", occurred_at=now-timedelta(days=index+2),
                     payload={"minutes": "20", "distance": "1", "distance_unit": "km"})
            for index in range(300)
        ])
        today = self.activity("water", now-timedelta(minutes=20), {"kind": "replace"})
        active = self.activity("walk", now-timedelta(days=3), {}, status="active")
        expected = self.all_visible(self.owner)
        with self.assertNumQueries(1):
            rows = board_records(self.owner)
        self.assertLessEqual(len(rows), 9)
        self.assertEqual([row.pk for row in rows[:6]], [row.pk for row in expected[:6]])
        self.assertIn(today.pk, {row.pk for row in rows})
        self.assertIn(active.pk, {row.pk for row in rows})
        with self.assertNumQueries(0):
            self.assertTrue(all(row.author.display_name and row.performer.display_name for row in rows))

    def test_private_inventory_cannot_consume_visible_recent_slots(self):
        now = timezone.now()
        Product.objects.create(
            room=self.room, name="비공개 물품", category="medicine",
            stock_item="비공개 재고", stock_unit="piece", active=False,
        )
        private_ids = set()
        for index in range(12):
            private_ids.add(self.activity(
                "inventory", now-timedelta(days=1, minutes=index),
                {"item": "비공개 재고", "movement": "set", "quantity": "3", "unit": "piece"},
            ).pk)
        for index in range(10):
            self.activity("walk", now-timedelta(days=2, minutes=index),
                          {"minutes": "10", "distance": "1", "distance_unit": "km"})
        public = self.activity(
            "inventory", now-timedelta(days=2, minutes=2, seconds=30),
            {"item": "공개 재고", "movement": "set", "quantity": "4", "unit": "piece"},
        )
        expected = self.all_visible(self.guest)
        rows = board_records(self.guest)
        self.assertEqual([row.pk for row in rows[:6]], [row.pk for row in expected[:6]])
        self.assertIn(public.pk, {row.pk for row in rows[:6]})
        self.assertFalse(private_ids & {row.pk for row in rows})

    def test_previous_weight_bcs_and_sleep_crossing_midnight_are_preserved(self):
        day = services.local_date(self.room)
        midnight, _ = bounds(self.room, day, day)
        self.activity("weight", midnight-timedelta(days=70),
                      {"weight": "8", "unit": "kg", "bcs": "5", "bcs_by": "vet"})
        self.activity("weight", midnight-timedelta(days=9), {"weight": "8.5", "unit": "kg"})
        self.activity("sleep", midnight-timedelta(minutes=30), {"kind": "sleep", "minutes": "90"})
        complete = self.all_visible(self.owner)
        bounded = board_records(self.owner)
        cards = [{"spec": MODULES[key]} for key in ("weight", "sleep")]
        self.assertEqual(today_tiles(self.owner, cards, bounded, day),
                         today_tiles(self.owner, cards, complete, day))
        for key in ("weight", "sleep"):
            with self.subTest(module=key):
                self.assertEqual(
                    get_statistics(self.room, key, day, day, all_records=bounded, member=self.owner),
                    get_statistics(self.room, key, day, day, all_records=complete, member=self.owner),
                )

    def test_board_inventory_keeps_opening_balance_before_today(self):
        now = timezone.now()
        self.activity("inventory", now-timedelta(days=120),
                      {"item": "먹이", "movement": "set", "quantity": "1000", "unit": "g"})
        self.activity("inventory", now-timedelta(minutes=10),
                      {"item": "먹이", "movement": "out", "quantity": "100", "unit": "g"})
        complete = self.all_visible(self.owner)
        cards = [{"spec": MODULES["inventory"]}]
        expected = today_tiles(self.owner, cards, complete)
        self.assertEqual(today_tiles(self.owner, cards, board_records(self.owner)), expected)
        self.assertEqual(expected[0]["tile"]["value"], "900")

    def test_single_module_board_keeps_merged_history_and_excludes_other_work(self):
        now = timezone.now()
        self.activity("grooming", now-timedelta(minutes=20), {"task": "brush"})
        self.activity("hygiene", now-timedelta(minutes=10), {"task": "teeth"})
        self.activity("walk", now-timedelta(minutes=5),
                      {"minutes": "10", "distance": "1", "distance_unit": "km"})
        cards, records = board_data(self.owner, module="grooming")
        self.assertEqual([card["spec"].key for card in cards], ["hygiene"])
        self.assertEqual({record.module for record in records}, {"grooming", "hygiene"})
        self.assertEqual(cards[0]["tile"]["value"], "2")

    def test_period_only_statistics_do_not_decode_unrelated_old_rows(self):
        now = timezone.now()
        Activity.objects.bulk_create([
            Activity(room=self.room, author=self.owner, performer=self.owner, module="walk",
                     occurred_at=now-timedelta(days=index+30),
                     payload={"minutes": "20", "distance": "1", "distance_unit": "km"})
            for index in range(100)
        ])
        self.activity("walk", now-timedelta(days=1),
                      {"minutes": "15", "distance": "2", "distance_unit": "km"})
        start = services.local_date(self.room)-timedelta(days=6)
        end = services.local_date(self.room)
        expected = get_statistics(self.room, "walk", start, end,
                                  all_records=self.all_visible(self.owner), member=self.owner)
        with patch.object(Activity, "from_db", wraps=Activity.from_db) as materialized:
            actual = get_statistics(self.room, "walk", start, end, member=self.owner)
        self.assertEqual(actual, expected)
        self.assertEqual(materialized.call_count, 1)
