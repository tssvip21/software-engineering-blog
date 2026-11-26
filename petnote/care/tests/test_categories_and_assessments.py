"""Category boundaries and independent, synthetic analysis scenarios."""
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode
import json
from django.db import OperationalError
from django.test import Client, SimpleTestCase
from django.urls import reverse
from care.reporting.assessment import assess
from care import services
from care.reporting.status_view import context_for
from .test_functional import CareCase
from .test_patterns import END, record, scenario


class CategoryConfigurationTests(CareCase):
    def url(self, category="living"):
        return reverse("category_config", args=[self.room.pk, category])

    def values(self, selected):
        self.room.refresh_from_db()
        return {"version": self.room.settings_version, "selected": selected}

    def post(self, data, client=None):
        return (client or self.client).post(self.url(), json.dumps(data), content_type="application/json")

    def test_preserves_outside_category_order_pins_and_history(self):
        activity = self.make("cleaning")
        before = {s.key: (s.enabled, s.pinned, s.position) for s in self.room.module_settings.all()}
        response = self.post(self.values(["hygiene"]))
        self.assertEqual(response.status_code, 200)
        for s in self.room.module_settings.all():
            self.assertEqual((s.pinned, s.position), before[s.key][1:])
            if s.key not in {"hygiene", "grooming", "cleaning", "environment", "inventory", "expense"}:
                self.assertEqual(s.enabled, before[s.key][0])
        activity.refresh_from_db()
        self.assertFalse(activity.voided)
        self.assertFalse(self.room.module_settings.get(key="cleaning").enabled)

    def test_other_category_duplicate_and_malformed_requests_rejected(self):
        for selected in (["meal"], ["cleaning", "cleaning"], "cleaning", [None]):
            self.assertEqual(self.post(self.values(selected)).status_code, 400)
        for body in ("null", "[]", "{"):
            self.assertEqual(self.client.post(self.url(), body, content_type="application/json").status_code, 400)
        data = self.values([]); data["version"] = True
        self.assertEqual(self.post(data).status_code, 400)

    def test_stale_choice_does_not_overwrite(self):
        data = self.values(["cleaning"])
        self.assertEqual(self.post(data).status_code, 200)
        data["selected"] = []
        self.assertEqual(self.post(data).status_code, 409)
        self.assertTrue(self.room.module_settings.get(key="cleaning").enabled)

    def test_guests_csrf_and_other_room_are_enforced(self):
        self.assertEqual(self.client_for(self.guest).get(self.url()).status_code, 403)
        self.assertEqual(self.post(self.values([]), self.client_for(self.member)).status_code, 403)
        client = Client(enforce_csrf_checks=True); client.force_login(self.owner_user)
        self.assertEqual(self.post(self.values([]), client).status_code, 403)
        self.assertEqual(self.client.get(reverse("category_config", args=[self.other_room.pk, "living"])).status_code, 404)
        self.assertEqual(self.client.get(self.url("invented")).status_code, 404)
        self.assertEqual(self.post(self.values(["cleaning"]), self.client_for(self.admin)).status_code, 200)

    def test_noop_does_not_create_revision_and_busy_does_not_succeed(self):
        data = self.values(["hygiene", "cleaning", "environment", "inventory", "expense"])
        result = self.post(data).json()
        self.assertEqual(result["version"], data["version"])
        with patch("care.browsing.apply_category", side_effect=OperationalError("locked")):
            self.assertEqual(self.post(data).status_code, 503)


class CategoryRecordTests(CareCase):
    def test_default_browse_is_category_sections_not_a_flat_date_list(self):
        self.make("meal"); self.make("walk"); self.make("expense")
        response = self.client.get(reverse("records", args=[self.room.pk]))
        self.assertEqual(len(response.context["record_sections"]), 4)
        self.assertEqual(response.context["selected_category"], "")
        self.assertEqual(sum(s["count"] for s in response.context["record_sections"]), 3)

    def test_category_module_search_date_filters_and_permission_counts(self):
        a = self.make("meal", note="아침 먹이"); self.make("walk"); self.make("expense")
        url = reverse("records", args=[self.room.pk])
        response = self.client.get(url, {"category": "nutrition", "q": "아침"})
        self.assertEqual([r.pk for r in response.context["page_obj"]], [a.pk])
        guest = self.client_for(self.guest).get(url)
        self.assertEqual(sum(s["count"] for s in guest.context["record_sections"]), 2)
        self.assertEqual(self.client_for(self.guest).get(url, {"modules": "meal,expense"}).status_code, 403)
        self.assertEqual(self.client.get(url, {"category": "bad"}).status_code, 404)

    def test_record_detail_preserves_safe_query_and_rejects_external_return(self):
        a = self.make("meal")
        root = reverse("records", args=[self.room.pk])
        detail = reverse("record_detail", args=[self.room.pk, a.pk])
        wanted = root + "?category=nutrition&module=meal&q=먹이&page=2"
        response = self.client.get(detail, {"return_to": wanted})
        self.assertEqual(response.context["return_to"], wanted)
        for value in ("https://evil.example/", "//evil.example/", "\\evil", reverse("records", args=[self.other_room.pk]), "/\nLocation:bad"):
            self.assertEqual(self.client.get(detail, {"return_to": value}).context["return_to"], root)

    def test_cancel_preserves_context_and_no_record_deleted(self):
        a = self.make("meal"); wanted = reverse("records", args=[self.room.pk]) + "?category=nutrition"
        response = self.client.post(reverse("record_cancel", args=[self.room.pk, a.pk]), {"version": a.version, "reason": "잘못 입력", "return_to": wanted})
        self.assertIn(urlencode({"return_to": wanted}), response.url)
        a.refresh_from_db(); self.assertTrue(a.voided)


class AssessmentScenarioTests(SimpleTestCase):
    def test_stable_single_linked_mixed_and_missing_remain_distinct(self):
        cases = [(scenario(), "steady"), (scenario(weight=11), "single"), (scenario(weight=11, food=150, activity=15), "linked"), (scenario(weight=11, food=60), "mixed"), ([], "collecting")]
        for rows, expected in cases:
            self.assertEqual(assess(rows, END)["code"], expected)

    def test_one_extreme_is_visible_without_claiming_sustained_change(self):
        rows = scenario()
        next(r for r in rows if r.module == "weight").payload["weight"] = 10000
        result = assess(rows, END)
        self.assertEqual(result["code"], "check")
        self.assertEqual(result["changed_count"], 0)
        self.assertIn("weight", result["isolated_changes"])

    def test_record_frequency_change_qualifies_daily_total(self):
        rows = scenario()
        for offset in range(7):
            rows.append(record("walk", END - timedelta(days=offset), 30, hour=18))
        result = assess(rows, END)
        self.assertEqual(result["code"], "recording")
        self.assertIn("activity", result["recording_changes"])
        self.assertFalse(result["relations"])

    def test_different_observation_days_cannot_claim_simultaneous_change(self):
        rows = []
        for offset in range(35):
            day = END - timedelta(days=offset)
            if offset >= 7 or offset in (0, 1, 2):
                rows.append(record("weight", day, 11 if offset < 7 else 10))
            if offset >= 7 or offset in (3, 4, 5):
                rows.append(record("meal", day, 160 if offset < 7 else 100))
        result = assess(rows, END)
        self.assertEqual(result["code"], "multiple")
        self.assertFalse(result["relations"])

    def test_moderate_noise_is_not_declared_a_change(self):
        rows = scenario()
        for i, r in enumerate(rows):
            if r.module == "weight": r.payload["weight"] = 10 + (-.1, 0, .1)[i % 3]
        self.assertEqual(assess(rows, END)["code"], "steady")

    def test_explicit_zero_unknown_and_cancellation_are_distinct(self):
        rows = scenario()
        for r in rows:
            if r.module == "meal": r.nutrition = {"basis": "consumed", "kcal": 0}
        self.assertEqual(assess(rows, END)["code"], "steady")
        added = record("meal", END, None)
        incomplete = assess(rows + [added], END)
        self.assertLess(next(m for m in incomplete["metrics"] if m["family"] == "food")["recent_days"], 7)
        added.voided = True
        self.assertEqual(assess(rows, END), assess(rows + [added], END))

    def test_direct_observation_is_not_hidden_by_stable_numbers(self):
        result = assess(scenario() + [record("observation", END, condition="different")], END)
        self.assertEqual(result["code"], "observed")


class StatusContextTests(CareCase):
    def test_open_today_is_not_compared_to_closed_daily_totals(self):
        self.make("meal")
        result = context_for(self.owner)
        self.assertEqual(result["summary"]["as_of"], services.local_date(self.room) - timedelta(days=1))
        self.assertEqual(result["summary"]["count"], 0)

    def test_today_observation_is_still_shown_immediately(self):
        self.make("observation", condition="different")
        result = context_for(self.owner)
        self.assertEqual(result["summary"]["code"], "observed")
        self.assertIn("오늘", result["summary"]["title"])

    def test_offline_and_sensitive_isolation(self):
        before = context_for(self.guest)
        self.make("medication"); self.make("expense")
        with patch("socket.socket.connect", side_effect=AssertionError("network")), patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP")):
            after = context_for(self.guest)
        self.assertEqual(before, after)

    def test_group_layout_never_changes_status(self):
        from care.organization import save_groups
        before = context_for(self.owner)
        self.room.refresh_from_db()
        save_groups(self.owner, {"version": self.room.groups_version, "groups": [{"name": "아침", "keys": ["meal", "walk"]}]})
        self.assertEqual(before, context_for(self.owner))


class CategoryBoundaryTests(CareCase):
    def test_category_navigation_counts_remain_useful_after_module_selection(self):
        self.make("meal"); self.make("walk"); self.make("expense")
        response = self.client.get(reverse("records", args=[self.room.pk]), {"category":"nutrition", "module":"meal"})
        counts = {c["key"]:c["count"] for c in response.context["record_categories"]}
        self.assertEqual(counts["activity"], 1)
        self.assertEqual(counts["living"], 1)
        self.assertEqual(len(response.context["page_obj"]), 1)
        guest = self.client_for(self.guest).get(reverse("records", args=[self.room.pk]), {"module":"meal"})
        self.assertEqual({c["key"]:c["count"] for c in guest.context["record_categories"]}["living"], 0)

    def test_today_weight_is_visible_but_not_added_to_closed_day_comparison(self):
        row = self.make("weight", weight="8.5", unit="kg")
        result = context_for(self.owner)
        self.assertEqual(str(result["latest_measurement"]["value"]), "8.5")
        self.assertEqual(result["summary"]["count"], 0)
        self.assertNotIn(str(row.pk), {i for m in result["insight"]["metrics"] for i in m["source_ids"]})

    def test_revision_status_uses_korean_label_without_rewriting_saved_history(self):
        from care.models import ActivityRevision
        row = self.make("meal")
        before = list(ActivityRevision.objects.filter(activity=row).values("before", "after"))
        response = self.client.get(reverse("record_detail", args=[self.room.pk,row.pk]))
        self.assertContains(response, "완료")
        self.assertEqual(list(ActivityRevision.objects.filter(activity=row).values("before", "after")), before)

    def test_saved_back_link_includes_author_dates_and_page(self):
        a = self.make("meal")
        query = urlencode({"category":"nutrition","module":"meal","author":str(self.owner.pk),"start":"2026-01-01","q":"보리","page":"2"})
        wanted = reverse("records",args=[self.room.pk])+"?"+query
        response = self.client.get(reverse("record_edit",args=[self.room.pk,a.pk]), {"return_to":wanted})
        self.assertContains(response, "return_to")
        self.assertEqual(response.context["return_to"], wanted)


class AssessmentInterpretationTests(SimpleTestCase):
    def test_same_direction_activity_weight_not_described_as_opposite_directions(self):
        from care.reporting.status_view import plain_result
        result = assess(scenario(weight=11,activity=60), END)
        self.assertEqual(result["code"], "mixed")
        self.assertNotIn("서로 다른 방향", plain_result(result)["title"])

    def test_change_day_overlap_is_not_only_shared_observation_days(self):
        rows=[]
        for offset in range(35):
            day=END-timedelta(days=offset)
            rows.append(record("weight",day,11 if offset in (0,1,2,3,4) else 10))
            rows.append(record("meal",day,150 if offset in (2,3,4,5,6) else 100))
        result=assess(rows,END)
        relation=next(r for r in result["relations"] if r["key"]=="food-weight")
        self.assertEqual(relation["overlap_days"], 3)

    def test_insufficient_measurements_never_gain_confidence_from_grouping(self):
        rows=[record("weight",END,10),record("meal",END,120),record("walk",END,20)]
        result=assess(rows,END)
        self.assertEqual(result["code"],"collecting")
        self.assertEqual(result["relations"],[])
