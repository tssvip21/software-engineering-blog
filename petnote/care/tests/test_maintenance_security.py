"""Regression checks for untrusted input, private records, and bounded queries."""
import json
import uuid
from unittest.mock import patch

from django.db import OperationalError, connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from care.forms import StatsFilterForm
from care.inventory.supplies import (
    filter_rows,
    record_visible,
    stock_view,
    visible_records,
)
from care.models import Activity, Product, RecordPreset
from care.tests.test_functional import CareCase


class RequestBoundaryTests(CareCase):
    def test_invalid_record_selectors_are_not_server_errors(self):
        self.client.raise_request_exception = False
        url = reverse("record_entry", args=[self.room.pk, "meal"])
        for field in ("preset", "product"):
            for value in ("not-a-uuid", "[]", "0", "f" * 500):
                with self.subTest(field=field, value=value[:20]):
                    response = self.client.get(url, {field: value})
                    self.assertEqual(response.status_code, 404)

    def test_record_selectors_cannot_cross_rooms(self):
        product = Product.objects.create(room=self.other_room, name="Other food")
        preset = RecordPreset.objects.create(
            room=self.other_room, module="meal", title="Other preset"
        )
        url = reverse("record_entry", args=[self.room.pk, "meal"])
        for field, value in (("product", product.pk), ("preset", preset.pk)):
            with self.subTest(field=field):
                self.assertEqual(self.client.get(url, {field: value}).status_code, 404)

    def test_action_rejects_non_string_module_without_writing(self):
        self.client.raise_request_exception = False
        url = reverse("record_action", args=[self.room.pk])
        before = Activity.objects.count()
        for value in ([], {}, ["meal"], {"name": "meal"}, None, True, 2):
            with self.subTest(value=value):
                response = self.client.post(
                    url,
                    json.dumps({"token": str(uuid.uuid4()), "operation": "quick", "module": value}),
                    content_type="application/json",
                )
                self.assertIn(response.status_code, (400, 404, 422))
        self.assertEqual(Activity.objects.count(), before)

    def test_json_endpoints_reject_invalid_encoding_and_excessive_depth(self):
        self.client.raise_request_exception = False
        paths = [
            reverse("record_action", args=[self.room.pk]),
            reverse("notice_dismiss", args=[self.room.pk]),
            reverse("module_config", args=[self.room.pk]),
            reverse("group_config", args=[self.room.pk]),
            reverse("category_config", args=[self.room.pk, "nutrition"]),
        ]
        bodies = (b"\xff", b"{", ("[" * 1500 + "]" * 1500).encode())
        before = self.room.revision
        for path in paths:
            for body in bodies:
                with self.subTest(path=path, body=body[:12]):
                    response = self.client.post(path, body, content_type="application/json")
                    self.assertEqual(response.status_code, 400)
        self.room.refresh_from_db()
        self.assertEqual(self.room.revision, before)
        self.assertFalse(Activity.objects.exists())

    def test_actions_reject_non_finite_and_unpaired_unicode_values(self):
        self.client.raise_request_exception = False
        url = reverse("record_action", args=[self.room.pk])
        for bad in (float("nan"), float("inf"), "\ud800"):
            with self.subTest(bad=repr(bad)):
                payload = {
                    "token": str(uuid.uuid4()), "operation": "quick",
                    "module": "water", "fields": {"note": bad},
                }
                response = self.client.post(url, json.dumps(payload), content_type="application/json")
                self.assertEqual(response.status_code, 400)
        self.assertFalse(Activity.objects.exists())

    def test_deep_module_order_is_a_form_error(self):
        self.client.raise_request_exception = False
        url = reverse("routine_new", args=[self.room.pk])
        response = self.client.post(url, {
            "name": "Daily", "at": "19:00", "weekdays": ["0"],
            "modules": ["meal"], "active": "on",
            "module_order": "[" * 1500 + "]" * 1500,
        })
        self.assertEqual(response.status_code, 422)
        self.assertFalse(self.room.routines.exists())

    def test_statistics_rejects_unsupported_calendar_boundary(self):
        form = StatsFilterForm({"start": "0001-01-01", "end": "0001-01-01"})
        self.assertFalse(form.is_valid())
        self.client.raise_request_exception = False
        response = self.client.get(
            reverse("statistics", args=[self.room.pk, "walk"]),
            {"start": "0001-01-01", "end": "0001-01-01"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1970")

    def test_mutation_endpoints_require_post(self):
        paths = [
            reverse("record_action", args=[self.room.pk]),
            reverse("notice_dismiss", args=[self.room.pk]),
            reverse("room_delete", args=[self.room.pk]),
            reverse("room_archive", args=[self.room.pk]),
            reverse("room_leave", args=[self.room.pk]),
            reverse("logout"),
            reverse("google_start"),
            reverse("join_preview"),
            reverse("join_confirm"),
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 405)

    def test_json_mutations_require_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner_user)
        paths = [
            reverse("record_action", args=[self.room.pk]),
            reverse("module_config", args=[self.room.pk]),
            reverse("group_config", args=[self.room.pk]),
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(
                    client.post(path, "{}", content_type="application/json").status_code, 403
                )

    def test_private_pages_keep_security_headers_and_same_origin_gps(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response["Cache-Control"], "no-store, private")
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertIn("geolocation=(self)", response["Permissions-Policy"])
        self.assertIn("camera=()", response["Permissions-Policy"])
        self.assertIn("microphone=()", response["Permissions-Policy"])

    @override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False)
    def test_health_check_reports_unavailable_without_details(self):
        with patch("care.views.system.connection.cursor", side_effect=OperationalError("private database credentials")):
            self.client.raise_request_exception = False
            response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotContains(response, "credentials", status_code=503)

    def test_health_check_still_checks_database(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class RecordPrivacyAndPerformanceTests(CareCase):
    def raw_record(self, module="inventory", *, room=None, author=None, **payload):
        return Activity.objects.create(
            room=room or self.room, author=author or self.owner,
            module=module, payload=payload, occurred_at=timezone.now(),
        )

    def test_record_visibility_requires_same_room_and_active_membership(self):
        foreign_owner = self.other_room.members.get(role="owner")
        foreign = self.raw_record("meal", room=self.other_room, author=foreign_owner)
        self.assertFalse(record_visible(self.member, foreign))
        self.assertEqual(filter_rows(self.member, [foreign]), [])
        own = self.raw_record("meal")
        self.member.active = False
        self.assertFalse(record_visible(self.member, own))
        self.assertEqual(filter_rows(self.member, [own]), [])

    def test_visible_query_enforces_room_and_sensitive_module_scope(self):
        public = self.raw_record("meal")
        self.raw_record("medication", drug="Private item")
        foreign_owner = self.other_room.members.get(role="owner")
        self.raw_record("meal", room=self.other_room, author=foreign_owner)
        self.assertEqual(
            list(visible_records(self.member, Activity.objects.all()).values_list("pk", flat=True)),
            [public.pk],
        )
        self.member.active = False
        self.assertFalse(visible_records(self.member, Activity.objects.all()).exists())

    def test_stock_warnings_do_not_reveal_private_record_identifiers(self):
        secret = self.raw_record("medication", _stock={"item": "Private item", "unit": "invalid"})
        public = self.raw_record("inventory", item="Bedding", unit="piece", quantity="bad", movement="in")
        guest_result = stock_view(self.member)
        self.assertEqual([issue["id"] for issue in guest_result["issues"]], [str(public.pk)])
        self.assertIn(str(secret.pk), [issue["id"] for issue in stock_view(self.owner)["issues"]])

    def test_stock_results_do_not_retain_hidden_events(self):
        self.raw_record("inventory", item="Private item", unit="piece", quantity="10", movement="in",
                        _supply_private=True)
        private = self.raw_record("medication", _stock={"item": "Private item", "unit": "piece", "quantity": "1"})
        self.assertEqual(stock_view(self.member)["items"], [])
        self.assertEqual(stock_view(self.member)["events"], [])
        self.assertIn(str(private.pk), [row["id"] for row in stock_view(self.owner)["events"]])

    def test_guest_record_list_queries_do_not_grow_per_inventory_record(self):
        client = self.client_for(self.member)
        url = reverse("records", args=[self.room.pk])
        self.raw_record(item="Bedding", unit="piece", quantity="1", movement="in")
        with CaptureQueriesContext(connection) as first:
            self.assertEqual(client.get(url, {"category": "living"}).status_code, 200)
        for index in range(10):
            self.raw_record(item=f"Bedding {index}", unit="piece", quantity="1", movement="in")
        with CaptureQueriesContext(connection) as many:
            self.assertEqual(client.get(url, {"category": "living"}).status_code, 200)
        self.assertLessEqual(len(many), len(first) + 2, (len(first), len(many)))

    def test_stock_warning_queries_do_not_grow_per_issue(self):
        self.raw_record(item="Bedding", unit="piece", quantity="bad", movement="in")
        with CaptureQueriesContext(connection) as first:
            self.assertEqual(len(stock_view(self.member)["issues"]), 1)
        for index in range(10):
            self.raw_record(item=f"Bedding {index}", unit="piece", quantity="bad", movement="in")
        with CaptureQueriesContext(connection) as many:
            self.assertEqual(len(stock_view(self.member)["issues"]), 11)
        self.assertLessEqual(len(many), len(first) + 2, (len(first), len(many)))
