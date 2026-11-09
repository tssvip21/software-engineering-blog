"""Presentation values and navigation must preserve the existing record semantics."""
from datetime import date, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.urls import reverse
from django.utils import timezone

from care import services
from care.dashboard import latest_module_records, statistics_periods, today_tiles
from care.demo_fixtures import record_data
from care.forms import RecordForm
from care.models import Activity, ActivityRevision
from care.modules import MODULES
from care.status_view import context_for
from care.templatetags.care_tags import module_category, result_category
from .test_functional import CareCase


class DashboardValuesTests(CareCase):
    def tile(self, key, member=None, records=None):
        rows = list(self.room.activities.all()) if records is None else records
        result = today_tiles(member or self.owner, [{"spec": MODULES[key], "count": 0}], rows)
        return result[0]["tile"] if result else None

    def quick(self, key, **changes):
        form = RecordForm(record_data(key, **changes), room=self.room, module=key, member=self.owner, quick=True)
        self.assertTrue(form.is_valid(), form.errors)
        return services.save_activity(self.owner, key, form)[0]

    def test_no_measurement_is_not_displayed_as_zero(self):
        value = self.tile("weight")
        self.assertTrue(value["empty"])
        self.assertEqual(value["value"], "—")
        self.assertEqual(value["unit"], "")

    def test_explicit_zero_consumption_is_still_known_energy(self):
        self.make("meal", product=str(self.product.pk), consumed="0", quantity="10")
        tile = self.tile("meal")
        self.assertEqual((tile["value"], tile["unit"], tile["empty"]), ("0", "kcal", False))
        self.assertIn("먹은 양", tile["caption"])

    def test_offered_energy_has_a_distinct_caption(self):
        self.make("meal", product=str(self.product.pk), quantity="100")
        tile = self.tile("meal")
        self.assertEqual((tile["value"], tile["unit"]), ("380", "kcal"))
        self.assertIn("준 양 기준 예상", tile["caption"])

    def test_mixed_bases_are_not_described_as_actual_consumption(self):
        self.make("meal", product=str(self.product.pk), quantity="100")
        self.make("meal", product=str(self.product.pk), quantity="100", consumed="50")
        tile = self.tile("meal")
        self.assertEqual(tile["value"], "570")
        self.assertIn("혼합", tile["caption"])

    def test_partial_energy_discloses_unknown_records(self):
        self.make("meal", product=str(self.product.pk), quantity="100")
        self.make("meal", food_name="열량 모르는 먹이", quantity="20")
        tile = self.tile("meal")
        self.assertEqual(tile["value"], "380")
        self.assertIn("열량 미확인 1건", tile["caption"])

    def test_unknown_food_energy_uses_record_count_not_invented_calories(self):
        self.quick("meal", quantity="", product="", food_name="")
        tile = self.tile("meal")
        self.assertEqual((tile["value"], tile["unit"]), ("1", "건"))
        self.assertIn("열량 미확인", tile["caption"])

    def test_water_replacement_is_not_intake(self):
        self.make("water", kind="replace", amount="500")
        tile = self.tile("water")
        self.assertEqual((tile["value"], tile["unit"]), ("1", "건"))
        self.assertIn("교체", tile["caption"])

    def test_measured_water_and_litres_preserve_units(self):
        self.make("water", kind="measured", amount="0.25", unit="l")
        self.make("water", kind="refill", amount="900", unit="ml")
        tile = self.tile("water")
        self.assertEqual((tile["value"], tile["unit"]), ("250", "ml"))
        self.assertIn("측정", tile["caption"])

    def test_missing_activity_time_is_not_zero_minutes(self):
        self.quick("play", kind="", minutes="")
        tile = self.tile("play")
        self.assertEqual((tile["value"], tile["unit"]), ("1", "건"))
        self.assertIn("시간 미입력", tile["caption"])

    def test_known_time_and_unknown_time_stay_distinct(self):
        self.make("play", minutes="15")
        self.quick("play", kind="", minutes="")
        tile = self.tile("play")
        self.assertEqual((tile["value"], tile["unit"]), ("15", "분"))
        self.assertIn("시간 미입력 1건", tile["caption"])

    def test_previous_weight_has_its_actual_measurement_date(self):
        yesterday = timezone.localtime() - timedelta(days=1)
        self.make("weight", weight="4200", unit="g", occurred_at=yesterday.strftime("%Y-%m-%dT%H:%M"))
        tile = self.tile("weight")
        self.assertEqual((tile["value"], tile["unit"]), ("4.2", "kg"))
        self.assertIn(yesterday.strftime("%m.%d"), tile["caption"])
        self.assertNotIn("오늘", tile["caption"])

    def test_cancelled_planned_and_future_records_are_not_added(self):
        a = self.make("meal", product=str(self.product.pk))
        services.cancel_activity(self.owner, a.pk, a.version, "검사 취소")
        self.make("medication", status="planned")
        future = self.make("weight")
        Activity.objects.filter(pk=future.pk).update(occurred_at=timezone.now()+timedelta(days=1))
        self.assertTrue(self.tile("meal")["empty"])
        self.assertTrue(self.tile("medication")["empty"])
        self.assertTrue(self.tile("weight")["empty"])

    def test_room_boundary_and_sensitive_cards_are_rechecked(self):
        a = self.make("weight", weight="5")
        a.room_id = self.other_room.pk
        self.assertTrue(self.tile("weight", records=[a])["empty"])
        self.make("expense")
        self.assertIsNone(self.tile("expense", member=self.guest))

    def test_rendering_does_not_mutate_records_or_analysis(self):
        self.make("meal", product=str(self.product.pk))
        before = list(Activity.objects.values()), list(ActivityRevision.objects.values()), context_for(self.owner)
        response = self.client.get(reverse("today", args=[self.room.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(before, (list(Activity.objects.values()), list(ActivityRevision.objects.values()), context_for(self.owner)))


class DiscoveryPresentationTests(CareCase):
    def test_latest_preview_is_one_completed_visible_record_per_module(self):
        earlier = timezone.localtime()-timedelta(days=1)
        self.make("meal", food_name="이전 먹이", occurred_at=earlier.strftime("%Y-%m-%dT%H:%M"))
        self.make("meal", food_name="최근 먹이")
        self.make("expense", item="비공개 지출 이름")
        hidden = self.make("walk")
        services.cancel_activity(self.owner, hidden.pk, hidden.version, "취소")
        with self.assertNumQueries(1):
            result = latest_module_records(self.guest)
        self.assertEqual(set(result), {"meal"})
        self.assertIn("최근 먹이", result["meal"]["text"])
        self.assertNotIn("이전 먹이", result["meal"]["text"])

    def test_latest_preview_escapes_user_text(self):
        self.make("meal", food_name="<script>private()</script>")
        response = self.client.get(reverse("modules", args=[self.room.pk]))
        self.assertContains(response, "&lt;script&gt;private()&lt;/script&gt;")
        self.assertNotContains(response, "<script>private()</script>")

    def test_mobile_navigation_and_direct_position_controls_exist(self):
        for name in ["today", "modules", "records", "routines", "overview"]:
            response = self.client.get(reverse(name, args=[self.room.pk]))
            self.assertContains(response, 'aria-label="모바일 주요 화면"')
            self.assertContains(response, "care/phase5.css")
        config = self.client.get(reverse("module_settings", args=[self.room.pk]))
        self.assertContains(config, 'id="module-to-move"')
        self.assertContains(config, 'data-place-module')
        self.assertNotContains(config, 'data-order-step=')
        self.assertEqual(self.client_for(self.guest).get(reverse("module_settings", args=[self.room.pk])).status_code, 403)

    def test_period_links_use_room_calendar_and_show_selected_range(self):
        with patch("care.dashboard.local_date", return_value=date(2026, 1, 31)):
            result = statistics_periods(self.room, "walk", date(2026, 1, 25), date(2026, 1, 31))
        self.assertEqual([r["selected"] for r in result], [True, False, False])
        first = parse_qs(urlsplit(result[0]["url"]).query)
        self.assertEqual(first, {"start": ["2026-01-25"], "end": ["2026-01-31"]})
        self.assertIn(str(self.room.pk), result[0]["url"])

    def test_invalid_statistics_dates_do_not_appear_selected(self):
        response = self.client.get(reverse("statistics", args=[self.room.pk, "walk"]), {"start":"bad", "end":"bad"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(any(p["selected"] for p in response.context["periods"]))
        self.assertIsNone(response.context["stats"])

    def test_category_color_mapping_never_replaces_labels_or_analysis(self):
        self.assertEqual(module_category("meal"), "nutrition")
        self.assertEqual(module_category("medication"), "wellbeing")
        self.assertEqual(module_category("invented"), "default")
        self.assertEqual(result_category("food_actual"), "nutrition")
        self.assertEqual(result_category("rest"), "activity")
        self.assertEqual(result_category("weight"), "nutrition")
