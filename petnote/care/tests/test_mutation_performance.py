"""Quick actions retain one atomic failure boundary and a live room lock."""
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, connection, transaction
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from care import record_actions, services
from care.models import ActionReceipt, Activity, ActivityRevision, AuditEvent, Membership, RoutineRun
from .test_functional import CareCase


class MutationTransactionGuards(SimpleTestCase):
    def test_internal_cores_refuse_to_run_without_an_atomic_owner(self):
        with self.assertRaises(RuntimeError):
            services._lock_member(None)
        with self.assertRaises(RuntimeError):
            services._save_activity_locked(None, None, None)


class QuickMutationTests(CareCase):
    def payload(self, module="water", operation="quick", **changes):
        return {"token": str(uuid.uuid4()), "module": module, "operation": operation, **changes}

    def snapshot(self):
        self.room.refresh_from_db()
        return (
            Activity.objects.count(), ActivityRevision.objects.count(),
            ActionReceipt.objects.count(), AuditEvent.objects.count(), self.room.revision,
        )

    def test_quick_action_has_one_room_lock_and_bounded_database_roundtrips(self):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.post(
                f"/rooms/{self.room.pk}/actions/",
                json.dumps(self.payload(fields={})), content_type="application/json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertLessEqual(len(captured), 18)
        room_reads = [
            query for query in captured
            if query["sql"].lstrip().upper().startswith("SELECT")
            and 'FROM "care_room"' in query["sql"]
        ]
        self.assertEqual(len(room_reads), 1)
        self.assertEqual((Activity.objects.count(), ActivityRevision.objects.count(), ActionReceipt.objects.count()), (1, 1, 1))

    def test_receipt_failure_rolls_back_record_revision_audit_and_room_version(self):
        payload = self.payload()
        with transaction.atomic():
            marker = AuditEvent.objects.create(room=self.room, actor=self.owner, action="test.before")
            before = self.snapshot()
            with patch.object(ActionReceipt.objects, "create", side_effect=OperationalError("receipt unavailable")):
                with self.assertRaises(OperationalError):
                    record_actions.perform(self.owner, payload)
            self.assertEqual(self.snapshot(), before)
            self.assertTrue(AuditEvent.objects.filter(pk=marker.pk).exists())
            result = record_actions.perform(self.owner, payload)
            self.assertTrue(result["created"])
        replay = record_actions.perform(self.owner, payload)
        self.assertTrue(replay["replayed"])
        self.assertEqual((Activity.objects.count(), ActivityRevision.objects.count(), ActionReceipt.objects.count()), (1, 1, 1))

    def test_public_save_keeps_its_savepoint_when_outer_caller_recovers(self):
        form = self.form("water")
        self.assertTrue(form.is_valid(), form.errors)
        with transaction.atomic():
            marker = AuditEvent.objects.create(room=self.room, actor=self.owner, action="test.before")
            before = self.snapshot()
            # bump() has already updated Room.revision when this insert fails.
            with patch.object(AuditEvent.objects, "create", side_effect=OperationalError("audit unavailable")):
                with self.assertRaises(OperationalError):
                    services.save_activity(self.owner, "water", form)
            self.assertEqual(self.snapshot(), before)
            self.assertTrue(AuditEvent.objects.filter(pk=marker.pk).exists())
            after = AuditEvent.objects.create(room=self.room, actor=self.owner, action="test.after")
        self.assertTrue(AuditEvent.objects.filter(pk=after.pk).exists())

    def test_failed_routine_receipt_also_rolls_back_new_schedule_slot(self):
        routine = self.routine(("water",))
        item = routine.items.get(active=True)
        payload = self.payload(
            operation="routine_done", routine_item=str(item.pk),
            date=services.local_date(self.room).isoformat(),
        )
        before = self.snapshot()
        with patch.object(ActionReceipt.objects, "create", side_effect=OperationalError("receipt unavailable")):
            with self.assertRaises(OperationalError):
                record_actions.perform(self.owner, payload)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(RoutineRun.objects.filter(item=item).exists())
        result = record_actions.perform(self.owner, payload)
        self.assertEqual(str(RoutineRun.objects.get(item=item).activity_id), result["id"])

    def test_failed_finish_receipt_restores_active_timer_and_version(self):
        start = record_actions.perform(self.owner, self.payload("walk", "start"))
        activity = Activity.objects.get(pk=start["id"])
        Activity.objects.filter(pk=activity.pk).update(occurred_at=timezone.now()-timedelta(minutes=5))
        finish = self.payload("walk", "finish", activity_id=str(activity.pk), version=activity.version)
        before = self.snapshot()
        with patch.object(ActionReceipt.objects, "create", side_effect=OperationalError("receipt unavailable")):
            with self.assertRaises(OperationalError):
                record_actions.perform(self.owner, finish)
        activity.refresh_from_db()
        self.assertEqual((activity.status, activity.version, activity.ended_at), ("active", 1, None))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(record_actions.perform(self.owner, finish)["status"], "done")

    def test_reused_member_is_rechecked_for_new_action_and_receipt_replay(self):
        payload = self.payload("medication", fields={"routine_title": "기록 확인"})
        record_actions.perform(self.admin, payload)
        before = self.snapshot()
        Membership.objects.filter(pk=self.admin.pk).update(role="guest")
        for request in (payload, self.payload("medication", fields={"routine_title": "기록 확인"})):
            with self.subTest(replay=request is payload), self.assertRaises(PermissionDenied):
                record_actions.perform(self.admin, request)
        self.assertEqual(self.snapshot(), before)

    def test_removed_actor_cannot_reuse_an_old_success_receipt(self):
        payload = self.payload()
        record_actions.perform(self.guest, payload)
        before = self.snapshot()
        Membership.objects.filter(pk=self.guest.pk).update(active=False)
        with self.assertRaises(PermissionDenied):
            record_actions.perform(self.guest, payload)
        self.assertEqual(self.snapshot(), before)

    def test_identity_link_change_rejects_the_stale_guest_actor(self):
        payload = self.payload()
        record_actions.perform(self.guest, payload)
        Membership.objects.filter(pk=self.guest.pk).update(user=self.outsider_user, guest_key=None)
        before = self.snapshot()
        with self.assertRaises(PermissionDenied):
            record_actions.perform(self.guest, payload)
        self.assertEqual(self.snapshot(), before)

    def test_changed_module_setting_is_not_reused_by_next_action(self):
        record_actions.perform(self.owner, self.payload())
        self.room.module_settings.filter(key="water").update(enabled=False)
        before = self.snapshot()
        with self.assertRaises(ValidationError):
            record_actions.perform(self.owner, self.payload())
        self.assertEqual(self.snapshot(), before)

    def test_other_performer_is_revalidated_after_a_form_was_opened(self):
        form = self.form("water", performer=str(self.member.pk))
        self.assertTrue(form.is_valid(), form.errors)
        Membership.objects.filter(pk=self.member.pk).update(active=False)
        before = self.snapshot()
        with self.assertRaises(ValidationError):
            services.save_activity(self.owner, "water", form)
        self.assertEqual(self.snapshot(), before)

    def test_public_save_revalidates_an_actor_after_a_form_was_opened(self):
        form = self.form("water", member=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        Membership.objects.filter(pk=self.admin.pk).update(active=False)
        before = self.snapshot()
        with self.assertRaises(PermissionDenied):
            services.save_activity(self.admin, "water", form)
        self.assertEqual(self.snapshot(), before)
