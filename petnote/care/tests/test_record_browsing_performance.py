"""Record category previews stay bounded and permission-scoped as history grows."""
import uuid
from datetime import timedelta
from typing import ClassVar

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from care.models import Activity, Product
from care.services.organization import CATEGORIES

from .test_functional import CareCase


class RecordBrowsingPerformanceTests(CareCase):
    modules: ClassVar[dict[str, tuple[str, str]]] = {
        "nutrition": ("meal", "water"),
        "activity": ("walk", "play"),
        "wellbeing": ("toilet", "observation"),
        "living": ("cleaning", "hygiene"),
    }

    def record(self, module, *, at=None, author=None, room=None, **extra):
        author = author or self.owner
        return Activity.objects.create(
            room=room or self.room, author=author, performer=author,
            module=module, occurred_at=at or timezone.now(), **extra,
        )

    def seed_category(self, category, count=6):
        return [
            self.record(
                self.modules[category][index % 2],
                at=timezone.now() - timedelta(minutes=index + 1),
                note="matching preview" if index % 2 else "other preview",
            )
            for index in range(count)
        ]

    def response(self, client=None, **params):
        return (client or self.client).get(
            reverse("records", args=[self.room.pk]),
            {"layout": "desktop", **params},
        )

    def sections(self, response):
        self.assertEqual(response.status_code, 200)
        return {
            section["key"]: [row.pk for row in section["recent"]]
            for section in response.context["record_sections"]
        }

    def test_more_populated_categories_do_not_add_database_round_trips(self):
        self.seed_category("nutrition")
        self.response()  # Warm template/session setup before comparing identical requests.
        with CaptureQueriesContext(connection) as one:
            self.response()
        for key in ("activity", "wellbeing", "living"):
            self.seed_category(key)
        with CaptureQueriesContext(connection) as all_categories:
            response = self.response()
        self.assertEqual(len(self.sections(response)), 4)
        self.assertLessEqual(len(all_categories), len(one))

    def test_three_latest_visible_rows_and_full_counts_are_preserved(self):
        for key in self.modules:
            self.seed_category(key, count=9)
        cancelled = self.record("meal", voided=True)
        planned = self.record("walk", status="planned")
        response = self.response()
        sections = self.sections(response)
        for key, (_, modules) in CATEGORIES.items():
            expected = list(Activity.objects.filter(
                room=self.room, module__in=modules, voided=False,
            ).order_by("-occurred_at", "-created_at", "-id").values_list("pk", flat=True)[:3])
            self.assertEqual(sections[key], expected)
        self.assertNotIn(cancelled.pk, sections["nutrition"])
        self.assertIn(planned.pk, sections["activity"])
        self.assertEqual(response.context["page_obj"].paginator.count, 37)
        self.assertEqual(len(response.context["page_obj"].object_list), 20)
        counts = {row["key"]: row["count"] for row in response.context["record_categories"]}
        self.assertEqual(counts, {"nutrition": 9, "activity": 10, "wellbeing": 9, "living": 9})

    def test_search_dates_author_state_and_module_filters_apply_before_preview_limit(self):
        day = timezone.localdate()
        for key in self.modules:
            self.seed_category(key)
        chosen = self.record(
            "walk", at=timezone.now() - timedelta(minutes=12),
            author=self.member, note="matching preview", status="done",
        )
        self.record("walk", author=self.member, note="matching preview", status="planned")
        self.record("walk", author=self.member, note="unrelated", status="done")
        self.record("walk", author=self.owner, note="matching preview", status="done")
        self.record("play", author=self.member, note="matching preview", status="done")
        self.record(
            "walk", at=timezone.now() - timedelta(days=2),
            author=self.member, note="matching preview", status="done",
        )
        response = self.response(
            q="matching", author=str(self.member.pk), state="done", module="walk",
            start=day.isoformat(), end=day.isoformat(),
        )
        # A module also selects its category; its full list still uses every filter.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["record_sections"], [])
        self.assertEqual([row.pk for row in response.context["page_obj"]], [chosen.pk])
        response = self.response(
            q="matching", author=str(self.member.pk), state="done", modules="walk",
            start=day.isoformat(), end=day.isoformat(),
        )
        sections = self.sections(response)
        self.assertEqual(sections["activity"], [chosen.pk])
        self.assertTrue(all(not ids for key, ids in sections.items() if key != "activity"))

    def test_guest_private_stock_and_other_rooms_do_not_consume_preview_slots(self):
        Product.objects.create(
            room=self.room, name="restricted supply", category="medicine",
            stock_item="restricted inventory", stock_unit="piece",
        )
        hidden_ids = set()
        for index in range(8):
            hidden_ids.add(self.record(
                "inventory", payload={"item": "restricted inventory", "movement": "set", "quantity": "3", "unit": "piece"},
                at=timezone.now() - timedelta(minutes=index + 1),
            ).pk)
            hidden_ids.add(self.record("medication", note="private treatment").pk)
        visible = [
            self.record(
                "inventory", payload={"item": "public inventory", "movement": "set", "quantity": str(index + 1), "unit": "piece"},
                at=timezone.now() - timedelta(days=1, minutes=index),
            )
            for index in range(4)
        ]
        outsider = self.other_room.members.get(role="owner")
        foreign = self.record("cleaning", room=self.other_room, author=outsider)
        response = self.response(client=self.client_for(self.guest))
        sections = self.sections(response)
        self.assertEqual(sections["living"], [row.pk for row in visible[:3]])
        displayed = {pk for ids in sections.values() for pk in ids}
        self.assertFalse(displayed & hidden_ids)
        self.assertNotIn(foreign.pk, displayed)
        counts = {row["key"]: row["count"] for row in response.context["record_categories"]}
        self.assertEqual(counts["living"], 4)
        self.assertEqual(counts["wellbeing"], 0)

    def test_category_page_keeps_pagination_without_category_previews(self):
        rows = self.seed_category("activity", count=27)
        self.seed_category("nutrition", count=5)
        response = self.response(category="activity", page="2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["record_sections"], [])
        self.assertEqual(response.context["page_obj"].paginator.count, 27)
        self.assertEqual([row.pk for row in response.context["page_obj"]], [row.pk for row in rows[20:]])

    def test_empty_and_invalid_filter_results_leave_all_sections_empty(self):
        self.seed_category("activity")
        for params in ({"q": "does-not-exist"}, {"start": "not-a-date"}, {"author": "not-a-uuid"}):
            with self.subTest(params=params):
                response = self.response(**params)
                self.assertTrue(all(not ids for ids in self.sections(response).values()))
                self.assertEqual(response.context["page_obj"].paginator.count, 0)

    def test_equal_timestamps_have_a_stable_tiebreaker(self):
        moment = timezone.now() - timedelta(hours=1)
        ids = [uuid.UUID(int=value) for value in (10, 50, 30, 20, 40)]
        rows = [self.record("walk", at=moment, id=pk) for pk in ids]
        Activity.objects.filter(pk__in=[row.pk for row in rows]).update(created_at=moment)
        sections = self.sections(self.response())
        self.assertEqual(sections["activity"], sorted(ids, reverse=True)[:3])

    def test_cancelled_and_all_states_keep_their_original_visibility(self):
        active = self.record("walk")
        cancelled = self.record("walk", voided=True)
        self.assertEqual(self.sections(self.response(state="cancelled"))["activity"], [cancelled.pk])
        self.assertEqual(self.sections(self.response(state="all"))["activity"], [cancelled.pk, active.pk])
