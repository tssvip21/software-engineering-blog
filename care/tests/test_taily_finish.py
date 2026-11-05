"""Focused checks for overview disclosure and recoverable cadence failures."""
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from django.db import OperationalError
from django.urls import reverse
from care import services
from care.models import CareCadence
from care.presentation import integrated_context
from care.status_view import context_for
from .test_taily import TailyCase, UI


class OverviewDisclosureTests(TailyCase):
    def populate(self):
        for key in ("meal", "weight", "walk", "hygiene", "medication", "veterinary", "inventory"):
            response = self.quick(key)
            self.assertEqual(response.status_code, 200, response.content)
        self.quick("observation", condition="different", detail="관찰한 변화")

    def test_plain_summary_exposes_results_not_methods(self):
        self.populate()
        context = context_for(self.owner)
        response = self.client.get(reverse("overview", args=[self.room.pk]))
        self.assertContains(response, context["summary"]["title"])
        self.assertContains(response, "오늘 평소와 다르다고")
        self.assertNotContains(response, "data-summary-primary")
        for term in ("중앙값", "실측", "MAD", "기울기", "계산 방법과 한계"):
            self.assertNotContains(response, term)
        for item in context["status_cards"]:
            self.assertContains(response, item["title"])
            self.assertEqual(self.client.get(item["url"]).status_code, 200)
        self.assertEqual(self.client.get(reverse("analysis_method", args=[self.room.pk])).status_code, 302)

    def test_guest_summary_and_distribution_do_not_reveal_sensitive_records(self):
        self.populate()
        owner = integrated_context(self.owner)
        guest = integrated_context(self.guest)
        forbidden = {"medication", "veterinary", "expense"}
        self.assertFalse(forbidden & {x["module"] for x in guest["stories"]})
        self.assertFalse(forbidden & {x["key"] for x in guest["cards"]})
        self.assertLess(guest["record_count"], owner["record_count"])
        self.assertEqual(sum(x["total"] for x in guest["distribution"]), guest["record_count"])
        response = self.client_for(self.guest).get(reverse("overview", args=[self.room.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '투약 확인')
        self.assertNotContains(response, '병원·예방접종 확인')

    def test_empty_summary_has_no_fabricated_values(self):
        context = integrated_context(self.owner)
        self.assertEqual(context["summary_primary"], [])
        self.assertEqual(context["summary_additional"], [])
        self.assertEqual(context["record_count"], 0)
        response = self.client.get(reverse("overview", args=[self.room.pk]))
        self.assertContains(response, '아직은 최근과 이전을 비교하기 어려워요')
        self.assertNotContains(response, 'class="summary-more"')

    def test_summary_detail_links_keep_same_room_and_period(self):
        self.populate()
        context = integrated_context(self.owner)
        for story in context["stories"]:
            url = urlparse(story["url"])
            self.assertIn(str(self.room.pk), url.path)
            query = parse_qs(url.query)
            self.assertEqual(query["start"], [context["start"].isoformat()])
            self.assertEqual(query["end"], [context["today"].isoformat()])
            self.assertEqual(self.client.get(story["url"]).status_code, 200)


class CadenceFailureRecoveryTests(TailyCase):
    def test_busy_save_preserves_form_and_retry_creates_one_cadence(self):
        data = {"activity": "hygiene:task:teeth", "title": "재시도 양치", "interval": 2,
                "unit": "days", "first_due": services.local_date(self.room).isoformat(), "active": "on"}
        url = reverse("cadence_new", args=[self.room.pk])
        with patch("care.interactions.cadences.save", side_effect=OperationalError("busy")):
            response = self.client.post(url, data)
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, '재시도 양치', status_code=503)
        self.assertContains(response, '입력한 내용은 남아 있으니 다시 저장해 주세요', status_code=503)
        self.assertEqual(CareCadence.objects.filter(room=self.room).count(), 0)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(CareCadence.objects.filter(room=self.room).count(), 1)

    def test_busy_snooze_does_not_change_schedule(self):
        cadence = self.cadence()
        old_version = cadence.version
        data = {"version": cadence.version, "until": (services.local_date(self.room)+timedelta(days=2)).isoformat()}
        url = reverse("cadence_snooze", args=[self.room.pk, cadence.pk])
        with patch("care.interactions.cadences.snooze", side_effect=OperationalError("busy")):
            response = self.client.post(url, data, **UI)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])
        cadence.refresh_from_db()
        self.assertEqual(cadence.version, old_version)
        self.assertIsNone(cadence.snoozed_until)
        self.assertEqual(self.client.post(url, data, **UI).status_code, 200)
        cadence.refresh_from_db()
        self.assertEqual(cadence.snoozed_until.isoformat(), data["until"])
