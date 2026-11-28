"""Bound detail comparisons without changing their displayed evidence or access."""
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.template.loader import render_to_string
from django.utils import timezone

from care import services
from care.demo_fixtures import SAMPLE_FIELDS
from care.models import Activity, Membership
from care.modules import MODULES
from care.reporting.module_reports import build_report
from care.reporting.stats import bounds
from care.services.schedule import logical_module

from .test_functional import CareCase


class ModuleComparisonReadTests(CareCase):
    def activity(self, module, at, **changes):
        payload = {**SAMPLE_FIELDS[module], **changes.pop("payload", {})}
        return Activity.objects.create(
            room=self.room, author=self.owner, performer=self.owner,
            module=module, occurred_at=at, payload=payload, **changes,
        )

    def test_detail_cards_match_full_report_across_periods_and_roles(self):
        now = timezone.now()
        for module in MODULES:
            for days in (1, 3, 9, 40, 140, 200):
                self.activity(module, now-timedelta(days=days))
        self.activity("walk", now-timedelta(days=1), voided=True)
        self.activity("walk", now-timedelta(days=1), status="planned")
        self.activity("walk", now+timedelta(days=1))
        self.activity("meal", now-timedelta(days=2), payload={"quantity": None})
        self.activity("environment", now-timedelta(days=2),
                      payload={"metric": "humidity", "value": "50", "unit": "percent"})
        for member in (self.owner, self.guest):
            for days in (7, 30, 90):
                complete = build_report(member, days)
                for module in MODULES:
                    with self.subTest(role=member.role, days=days, module=module):
                        scoped = build_report(member, days, module=module)
                        key = logical_module(module)
                        expected = next((card for card in complete["module_cards"] if card["key"] == key), None)
                        actual = next((card for card in scoped["module_cards"] if card["key"] == key), None)
                        self.assertEqual(actual, expected)
                        if actual is not None:
                            template = "care/statistics/_module_comparison.html"
                            common = {"room": self.room, "member": member}
                            self.assertEqual(
                                render_to_string(template, {**common, "report": scoped, "comparison": actual}),
                                render_to_string(template, {**common, "report": complete, "comparison": expected}),
                            )

    def test_single_module_avoids_old_and_unrelated_activity_materialization(self):
        now = timezone.now()
        Activity.objects.bulk_create([
            Activity(room=self.room, author=self.owner, performer=self.owner,
                     module="walk" if index % 2 else "meal",
                     occurred_at=now-timedelta(days=200+index),
                     payload=SAMPLE_FIELDS["walk" if index % 2 else "meal"])
            for index in range(300)
        ])
        recent = self.activity("walk", now-timedelta(days=2))
        self.activity("meal", now-timedelta(days=2))
        with patch.object(Activity, "from_db", wraps=Activity.from_db) as materialized:
            report = build_report(self.owner, module="walk")
        self.assertEqual(materialized.call_count, 1)
        self.assertEqual(report["measurements"][0]["recent"]["ids"], [str(recent.pk)])

    def test_sleep_keeps_overlap_and_legacy_quality_context(self):
        today = services.local_date(self.room)
        lower, _ = bounds(self.room, today-timedelta(days=14), today-timedelta(days=1))
        crossing = self.activity("sleep", lower-timedelta(minutes=30), payload={"minutes": "90"})
        missing = self.activity("sleep", lower-timedelta(days=40), payload={"minutes": None})
        self.activity("sleep", lower+timedelta(days=1), payload={"minutes": "40"})
        complete = build_report(self.owner)
        scoped = build_report(self.owner, module="sleep")
        expected = [card for card in complete["module_cards"] if card["key"] == "sleep"]
        self.assertEqual(scoped["module_cards"], expected)
        metric = scoped["measurements"][0]
        self.assertIn(str(crossing.pk), metric["before"]["ids"])
        self.assertEqual(metric["quality"]["missing_records"], 1)
        self.assertNotIn(str(missing.pk), metric["before"]["ids"])

    def test_weight_keeps_prior_measurements_and_stock_history(self):
        now = timezone.now()
        self.activity("weight", now-timedelta(days=100), payload={"weight": "8"})
        self.activity("weight", now-timedelta(days=30), payload={"weight": "9"})
        self.activity("walk", now-timedelta(days=70))
        self.activity("observation", now-timedelta(days=50))
        self.activity("inventory", now-timedelta(days=200))
        self.activity("meal", now-timedelta(days=90),
                      payload={"_stock": {"item": "test-stock", "unit": "g", "quantity": "5"}})
        self.assertEqual(
            build_report(self.owner, module="weight"),
            build_report(self.owner),
        )

    def test_historical_end_and_disabled_module_match_full_report(self):
        now = timezone.now()
        for days in (1, 10, 20, 45):
            self.activity("walk", now-timedelta(days=days))
        end = services.local_date(self.room)-timedelta(days=8)
        complete = build_report(self.owner, end=end)
        scoped = build_report(self.owner, end=end, module="walk")
        self.assertEqual(scoped["measurements"], [
            metric for metric in complete["measurements"] if metric["module"] == "walk"
        ])
        self.room.module_settings.filter(key="walk").update(enabled=False)
        self.assertEqual(build_report(self.owner, module="walk")["module_cards"], [])

    def test_scoped_report_rechecks_current_membership(self):
        Membership.objects.filter(pk=self.admin.pk).update(active=False)
        with self.assertRaises(PermissionDenied):
            build_report(self.admin, module="walk")

    def test_full_report_does_not_materialize_an_author_per_activity(self):
        now = timezone.now()
        for index in range(25):
            self.activity("walk", now-timedelta(days=index+1))
        complete_rows = list(Activity.objects.filter(room=self.room).select_related("author"))
        expected = build_report(self.owner, records=complete_rows)
        with patch.object(Membership, "from_db", wraps=Membership.from_db) as materialized:
            actual = build_report(self.owner)
        self.assertEqual(actual, expected)
        self.assertEqual(materialized.call_count, 1)
