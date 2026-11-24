"""Presentation boundaries for the card board and library; no replacement of care rules."""
from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from care import services
from care.dashboard import module_board_tiles, today_tiles
from care.models import Activity
from care.modules import MODULES
from care.reporting.status_view import context_for
from .test_functional import CareCase


class StudioBoardTests(CareCase):
    def rows(self):
        return [{"spec": spec} for spec in MODULES.values()]

    def test_library_is_closed_and_independent_of_used_cards(self):
        response = self.client.get(reverse("module_settings", args=[self.room.pk]))
        self.assertContains(response, 'id="module-library"')
        self.assertNotContains(response, 'id="module-library" open')
        self.assertContains(response, 'data-layout-preview')
        self.assertContains(response, 'data-library-open')
        self.assertContains(response, 'data-place-module')
        self.assertNotContains(response, 'data-order-step=')

    def test_board_contains_data_cards_not_only_recent_text(self):
        self.make("walk", minutes="28")
        response = self.client.get(reverse("modules", args=[self.room.pk]))
        self.assertContains(response, 'class="module-card-grid"')
        self.assertContains(response, 'class="board-reading')
        row = next(r for s in response.context["sections"] for r in s["rows"] if r["spec"].key == "walk")
        self.assertEqual((row["tile"]["value"], row["tile"]["unit"]), ("28", "분"))

    def test_board_reuses_today_quantity_and_missingness_rules(self):
        self.make("meal", product=str(self.product.pk))
        self.make("water", kind="refill", amount="250")
        rows = list(Activity.objects.filter(room=self.room))
        expected = {r["spec"].key: r["tile"] for r in today_tiles(self.owner, self.rows(), rows)}
        self.assertEqual(module_board_tiles(self.owner, self.rows()), expected)
        self.assertTrue(expected["walk"]["empty"])
        self.assertEqual(expected["water"]["unit"], "건")

    def test_board_historical_weight_keeps_measured_date(self):
        date = timezone.localtime() - timedelta(days=4)
        self.make("weight", weight="7.4", occurred_at=date.strftime("%Y-%m-%dT%H:%M"))
        tile = module_board_tiles(self.owner, self.rows())["weight"]
        self.assertIn(date.strftime("%m.%d"), tile["caption"])
        self.assertNotIn("오늘", tile["caption"])

    def test_board_sensitive_readings_do_not_reach_guest(self):
        self.make("expense", item="비공개 구매 내역")
        self.make("medication", medicine="비공개 약 이름")
        tiles = module_board_tiles(self.guest, self.rows())
        self.assertNotIn("expense", tiles)
        self.assertNotIn("medication", tiles)
        response = self.client_for(self.guest).get(reverse("modules", args=[self.room.pk]))
        self.assertNotContains(response, "비공개 구매 내역")
        self.assertNotContains(response, "비공개 약 이름")
        self.assertNotContains(response, "보드 편집")

    def test_tiles_exclude_cancelled_and_future_values(self):
        row = self.make("walk", minutes="90")
        services.cancel_activity(self.owner, row.pk, row.version, "테스트 취소")
        row = self.make("weight", weight="99")
        Activity.objects.filter(pk=row.pk).update(occurred_at=timezone.now()+timedelta(days=1))
        result = module_board_tiles(self.owner, self.rows())
        self.assertTrue(result["walk"]["empty"])
        self.assertTrue(result["weight"]["empty"])

    def test_screen_reads_do_not_change_analysis_or_saved_measurements(self):
        self.make("meal", product=str(self.product.pk))
        original = list(Activity.objects.values()), context_for(self.owner)
        for name in ["modules", "module_settings", "today", "overview", "records"]:
            self.assertEqual(self.client.get(reverse(name, args=[self.room.pk])).status_code, 200)
        self.assertEqual(original, (list(Activity.objects.values()), context_for(self.owner)))

    def test_routine_ring_has_zero_without_invented_goal(self):
        response = self.client.get(reverse("today", args=[self.room.pk]))
        self.assertEqual(response.context["routine_progress"], 0)
        self.assertEqual(response.context["routine_total"], 0)
        self.assertNotContains(response, 'class="routine-orbit"')

    def test_new_surface_loads_local_fonts_and_shared_interaction_only(self):
        response = self.client.get(reverse("modules", args=[self.room.pk]))
        self.assertContains(response, "care/petnote.css")
        self.assertContains(response, "care/studio.js")
        self.assertNotContains(response, "fonts.googleapis.com")
        self.assertNotContains(response, "care/phase5.css")
