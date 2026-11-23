"""Regression coverage for inline entry, configuration, cadence and preserved history."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo
import json
import uuid
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from care import cadences, services
from care.demo_fixtures import record_data
from care.forms import RecordForm
from care.interactions import config_state
from care.models import Activity, ActivityRevision, CareCadence, Room
from care.modules import MODULES
from care.presentation import integrated_context
from .test_functional import CareCase

UI = {"HTTP_X_TAILY_UI": "1"}


class TailyCase(CareCase):
    def quick(self, key, client=None, data=None, **changes):
        values = data or record_data(key, occurred_at=timezone.localtime().strftime("%Y-%m-%dT%H:%M:%S"), performer=str(self.owner.pk))
        if key == "reproduction" and data is None:
            values.update(kind="food_change", eggs="", detail="새 먹이로 변경")
        values.update(changes)
        return (client or self.client).post(reverse("quick_record", args=[self.room.pk, key]), values, **UI)

    def save_config(self, keys, pins=None, version=None, client=None):
        self.room.refresh_from_db()
        payload = {"enabled": keys, "order": keys, "pinned": pins if pins is not None else keys,
                   "version": self.room.settings_version if version is None else version}
        return (client or self.client).post(reverse("module_config", args=[self.room.pk]), json.dumps(payload), content_type="application/json")

    def cadence(self, key="hygiene:task:teeth", interval=1, unit="days", first_due=None):
        values = {"activity": key, "interval": interval, "unit": unit,
                  "first_due": (first_due or services.local_date(self.room)).isoformat(), "active": "on"}
        form = cadences.CadenceForm(values, member=self.owner)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        return cadences.save(self.owner, form)


class QuickEntryTests(TailyCase):
    def test_food_minimal_completion_unknown_not_zero(self):
        response = self.quick("meal", food_name="", quantity="", product="", consumed="")
        self.assertEqual(response.status_code, 200, response.content)
        row = Activity.objects.get(pk=response.json()["id"])
        self.assertIsNone(row.payload["quantity"])
        self.assertEqual(row.nutrition["basis"], "unknown")
        self.assertIsNone(row.nutrition["kcal"])
        self.assertEqual(row.author_id, self.owner.pk)
        self.assertEqual(row.performer_id, self.owner.pk)
        stats = self.stats("meal")
        self.assertEqual(stats["count"], 1)
        self.assertEqual(next(m for m in stats["metrics"] if m["label"] == "확인된 열량")["value"], "미기록")
        self.assertFalse(stats["charts"][0]["has_data"])

    def test_food_actual_zero_and_missing_quantity_are_distinct(self):
        response = self.quick("meal", product=str(self.product.pk), quantity="", consumed="0")
        self.assertEqual(response.status_code, 200, response.content)
        row = Activity.objects.get(pk=response.json()["id"])
        self.assertEqual(Decimal(row.nutrition["kcal"]), 0)
        self.assertEqual(row.nutrition["basis"], "consumed")

    def test_optional_activity_time_is_not_zero_or_average_denominator(self):
        self.quick("play", kind="", minutes="", reaction="unknown")
        self.quick("play", minutes="20")
        stats = self.stats("play")
        self.assertEqual(stats["primary"], "20")
        self.assertEqual(next(m for m in stats["metrics"] if m["label"] == "기록당 평균")["value"], "20")
        self.assertEqual(next(m for m in stats["metrics"] if m["label"] == "시간 미입력")["value"], "1")

    def test_quick_weight_still_requires_measurement(self):
        response = self.quick("weight", weight="")
        self.assertEqual(response.status_code, 422)
        self.assertIn('name="weight"', response.json()["html"])
        self.assertFalse(Activity.objects.filter(room=self.room).exists())

    def test_recent_quantity_is_not_silently_reused(self):
        existing = self.make("meal", quantity="77", consumed="66")
        url = reverse("quick_record", args=[self.room.pk, "meal"])
        response = self.client.get(url, **UI)
        self.assertIsNone(response.context["form"]["quantity"].value())
        reused = self.client.get(url, {"reuse": str(existing.pk)}, **UI)
        self.assertEqual(str(reused.context["form"]["quantity"].value()), "77")
        self.assertIsNone(reused.context["form"]["consumed"].value())

    def test_wrong_room_recent_value_is_not_reused(self):
        other = self.other_room.members.get(role="owner")
        form = RecordForm(record_data("meal"), room=self.other_room, module="meal", member=other)
        self.assertTrue(form.is_valid())
        row, _ = services.save_activity(other, "meal", form)
        response = self.client.get(reverse("quick_record", args=[self.room.pk, "meal"]), {"reuse": str(row.pk)}, **UI)
        self.assertEqual(response.status_code, 404)

    def test_same_request_retry_only_one_record(self):
        values = record_data("meal", quantity="", food_name="")
        first = self.quick("meal", data=values)
        second = self.quick("meal", data=values)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertFalse(second.json()["created"])
        self.assertEqual(Activity.objects.filter(room=self.room).count(), 1)

    def test_validation_error_keeps_input_and_request_key(self):
        values = record_data("weight", weight="-1", note="입력 보존")
        response = self.quick("weight", data=values)
        self.assertEqual(response.status_code, 422)
        self.assertIn(values["client_key"], response.json()["html"])
        self.assertIn("입력 보존", response.json()["html"])
        self.assertFalse(Activity.objects.filter(room=self.room).exists())

    def test_database_failure_returns_retryable_form(self):
        values = record_data("meal")
        with patch("care.interactions.save_quick", side_effect=OperationalError("locked")):
            response = self.quick("meal", data=values)
        self.assertEqual(response.status_code, 503)
        self.assertIn(values["client_key"], response.json()["html"])
        self.assertEqual(self.quick("meal", data=values).status_code, 200)

    def test_walk_start_finish_preserves_one_record(self):
        values = record_data("walk", status="active", minutes="", occurred_at=(timezone.localtime()-timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S"))
        first = self.quick("walk", data=values)
        self.assertEqual(first.status_code, 200)
        row = Activity.objects.get(pk=first.json()["id"])
        self.assertEqual(row.status, "active")
        values.update(version=row.version, status="done", ended_at=timezone.localtime().strftime("%Y-%m-%dT%H:%M:%S"))
        response = self.client.post(reverse("quick_finish", args=[self.room.pk,row.pk])+"?finish=1", values, **UI)
        self.assertEqual(response.status_code,200,response.content)
        row.refresh_from_db()
        self.assertEqual(row.status,"done")
        self.assertGreater(Decimal(row.payload["minutes"]),2)
        self.assertEqual(Activity.objects.filter(room=self.room,module="walk").count(),1)
        self.assertEqual(row.revisions.count(),2)

    def test_quick_undo_voids_without_deleting(self):
        response = self.quick("meal")
        data = response.json()
        undo = self.client.post(data["undo_url"], {"version":data["version"]}, **UI)
        self.assertEqual(undo.status_code,200)
        row = Activity.objects.get(pk=data["id"])
        self.assertTrue(row.voided)
        self.assertEqual(row.revisions.count(),2)
        self.assertEqual(self.stats("meal")["count"],0)

    def test_unknown_undo_is_not_internal_error(self):
        self.assertEqual(self.client.post(reverse("quick_undo",args=[self.room.pk,uuid.uuid4()]),{"version":1},**UI).status_code,404)

    def test_guest_cannot_undo_other_author(self):
        response=self.quick("meal")
        self.assertEqual(self.client_for(self.guest).post(response.json()["undo_url"],{"version":1},**UI).status_code,403)
        self.assertFalse(Activity.objects.get(pk=response.json()["id"]).voided)

    def test_quick_preserves_sensitive_access_rules(self):
        for module in ("expense","medication","veterinary"):
            with self.subTest(module=module):
                self.assertEqual(self.quick(module,client=self.client_for(self.guest)).status_code,403)
        self.assertEqual(self.quick("meal",client=self.client_for(self.guest),performer=str(self.guest.pk)).status_code,200)

    def test_quick_form_requires_csrf(self):
        c=Client(enforce_csrf_checks=True);c.force_login(self.owner_user)
        self.assertEqual(self.quick("meal",client=c).status_code,403)

    def test_quick_routine_and_undo_use_same_record(self):
        routine=self.routine(("meal",));item=routine.items.get();day=services.local_date(self.room)
        result=self.quick("meal",routine_item=str(item.pk),routine_date=day.isoformat())
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(item.runs.get(date=day).activity_id,uuid.UUID(result.json()["id"]))
        self.client.post(result.json()["undo_url"],{"version":1},**UI)
        self.assertIsNone(item.runs.get(date=day).activity_id)


def quick_module_test(key):
    def test(self):
        response=self.quick(key)
        self.assertEqual(response.status_code,200,response.content)
        row=Activity.objects.get(pk=response.json()["id"])
        self.assertEqual(row.payload["_entry_mode"],"quick")
        for name in ("module","statistics"):
            self.assertEqual(self.client.get(reverse(name,args=[self.room.pk,key])).status_code,200)
        self.assertEqual(self.client.get(reverse("overview",args=[self.room.pk])).status_code,200)
    return test

for _key in MODULES:
    setattr(QuickEntryTests,"test_quick_roundtrip_"+_key,quick_module_test(_key))


class ModuleConfigurationTests(TailyCase):
    def test_added_order_shared_by_today_modules_and_config(self):
        keys=["weight","meal","training","water"]
        response=self.save_config(keys)
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()["order"],keys)
        today=self.client.get(reverse("today",args=[self.room.pk]))
        modules=self.client.get(reverse("modules",args=[self.room.pk]))
        self.assertEqual([r["spec"].key for r in today.context["cards"]],keys)
        self.assertEqual([r["spec"].key for r in modules.context["enabled_modules"]],keys)

    def test_activation_and_pinning_are_independent(self):
        self.assertEqual(self.save_config(["meal","hygiene"],["meal"]).status_code,200)
        today=self.client.get(reverse("today",args=[self.room.pk]))
        self.assertEqual([r["spec"].key for r in today.context["cards"]],["meal"])
        self.assertTrue(self.room.module_settings.get(key="hygiene").enabled)

    def test_removing_module_does_not_remove_history(self):
        old=self.make("meal")
        self.save_config(["water"])
        self.assertTrue(Activity.objects.filter(pk=old.pk).exists())
        self.assertEqual(self.stats("meal")["count"],1)
        self.assertEqual(self.quick("meal").status_code,409)

    def test_duplicate_invalid_keys_and_pin_outside_selection_rejected(self):
        for values in (["meal","meal"],["invalid"]):
            with self.subTest(values=values):self.assertEqual(self.save_config(values).status_code,400)
        self.assertEqual(self.save_config(["meal"],["weight"]).status_code,400)

    def test_settings_conflict_preserves_winning_order(self):
        version=self.room.settings_version
        self.assertEqual(self.save_config(["weight","meal"],version=version).status_code,200)
        self.assertEqual(self.save_config(["water"],version=version).status_code,409)
        self.assertEqual(config_state(self.room)["order"],["weight","meal"])

    def test_guest_cannot_read_or_mutate_configuration(self):
        client=self.client_for(self.guest)
        self.assertEqual(client.get(reverse("module_config",args=[self.room.pk])).status_code,403)
        self.assertEqual(self.save_config(["meal"],client=client).status_code,403)

    def test_empty_selection_and_restoration(self):
        old=self.make("weight")
        self.assertEqual(self.save_config([]).status_code,200)
        self.assertFalse(self.client.get(reverse("today",args=[self.room.pk])).context["cards"])
        self.assertEqual(self.save_config(["weight"]).status_code,200)
        self.assertTrue(Activity.objects.filter(pk=old.pk).exists())


class CadenceTests(TailyCase):
    def test_same_module_separate_activities(self):
        teeth=self.cadence("hygiene:task:teeth",1)
        bath=self.cadence("hygiene:task:bath",2,"weeks")
        self.assertTrue(cadences.state(teeth,self.room)["due"])
        self.assertTrue(cadences.state(bath,self.room)["due"])
        response=self.quick("hygiene",task="teeth",cadence_id=str(teeth.pk),cadence_version=teeth.version)
        self.assertEqual(response.status_code,200,response.content)
        teeth.refresh_from_db()
        self.assertFalse(cadences.state(teeth,self.room)["due"])
        self.assertTrue(cadences.state(bath,self.room)["due"])

    def test_completed_activity_advances_calendar_month_at_month_end(self):
        cadence=self.cadence("hygiene:task:bath",1,"months")
        configured=datetime(2026,1,1,0,0,tzinfo=ZoneInfo(self.room.timezone))
        CareCadence.objects.filter(pk=cadence.pk).update(configured_at=configured,first_due=date(2026,1,31))
        row=self.make("hygiene",task="bath")
        Activity.objects.filter(pk=row.pk).update(created_at=configured+timedelta(days=30,hours=12),occurred_at=configured+timedelta(days=30,hours=12))
        cadence.refresh_from_db()
        self.assertEqual(cadences.state(cadence,self.room,today=date(2026,1,31))["next_due"],date(2026,2,28))

    def test_disabled_module_stops_reminders_without_deletion(self):
        cadence=self.cadence()
        self.room.module_settings.filter(key="hygiene").update(enabled=False)
        self.assertTrue(cadences.state(cadence,self.room)["paused"])
        self.assertFalse(cadences.state(cadence,self.room)["due"])
        self.assertFalse(cadences.schedule(self.owner))
        self.assertTrue(CareCadence.objects.filter(pk=cadence.pk).exists())

    def test_snooze_does_not_change_repeat_interval(self):
        cadence=self.cadence(interval=3,unit="weeks");day=services.local_date(self.room)+timedelta(days=2)
        cadences.snooze(self.owner,cadence.pk,cadence.version,day)
        cadence.refresh_from_db()
        self.assertEqual((cadence.interval,cadence.unit),(3,"weeks"))
        self.assertEqual(cadences.state(cadence,self.room)["next_due"],day)
        self.assertFalse(cadences.state(cadence,self.room)["due"])

    def test_complete_then_cancel_reexposes_due_without_deleting_history(self):
        cadence=self.cadence()
        response=self.quick("hygiene",task="teeth",cadence_id=str(cadence.pk),cadence_version=cadence.version)
        self.assertEqual(response.status_code,200,response.content)
        cadence.refresh_from_db()
        self.assertFalse(cadences.state(cadence,self.room)["due"])
        self.client.post(response.json()["undo_url"],{"version":1},**UI)
        self.assertTrue(cadences.state(cadence,self.room)["due"])
        self.assertEqual(Activity.objects.filter(room=self.room).count(),1)

    def test_stale_duplicate_completion_does_not_create_second_record(self):
        cadence=self.cadence();payload={"task":"teeth","cadence_id":str(cadence.pk),"cadence_version":cadence.version}
        self.assertEqual(self.quick("hygiene",**payload).status_code,200)
        self.assertEqual(self.quick("hygiene",**payload).status_code,409)
        self.assertEqual(Activity.objects.filter(room=self.room).count(),1)

    def test_identical_cadence_retry_returns_saved_record(self):
        cadence=self.cadence();data=record_data("hygiene",occurred_at=timezone.localtime().strftime("%Y-%m-%dT%H:%M:%S"),task="teeth",cadence_id=str(cadence.pk),cadence_version=cadence.version)
        first=self.quick("hygiene",data=data);second=self.quick("hygiene",data=data)
        self.assertEqual(first.status_code,200,first.content)
        self.assertEqual(second.status_code,200,second.content)
        self.assertEqual(first.json()["id"],second.json()["id"])

    def test_wrong_activity_variant_rejected(self):
        cadence=self.cadence()
        response=self.quick("hygiene",task="bath",cadence_id=str(cadence.pk),cadence_version=cadence.version)
        self.assertEqual(response.status_code,422)
        self.assertFalse(Activity.objects.filter(room=self.room).exists())

    def test_guest_can_complete_but_cannot_change_or_snooze(self):
        cadence=self.cadence();client=self.client_for(self.guest)
        self.assertEqual(client.get(reverse("cadence_new",args=[self.room.pk])).status_code,403)
        self.assertEqual(client.post(reverse("cadence_snooze",args=[self.room.pk,cadence.pk]),{"version":1,"until":(services.local_date(self.room)+timedelta(days=1)).isoformat()},**UI).status_code,403)
        self.assertEqual(self.quick("hygiene",client=client,task="teeth",performer=str(self.guest.pk),cadence_id=str(cadence.pk),cadence_version=cadence.version).status_code,200)

    def test_unknown_snooze_does_not_return_internal_error(self):
        self.assertEqual(self.client.post(reverse("cadence_snooze",args=[self.room.pk,uuid.uuid4()]),{"version":1,"until":services.local_date(self.room).isoformat()},**UI).status_code,404)

    def test_invalid_interval_and_duplicate_definition_rejected(self):
        cadence=self.cadence()
        form=cadences.CadenceForm({"activity":"hygiene:task:teeth","interval":25,"unit":"months","first_due":cadence.first_due.isoformat(),"active":"on"},member=self.owner)
        self.assertFalse(form.is_valid())
        with self.assertRaises(ValidationError):self.cadence()

    def test_old_records_do_not_complete_new_schedule(self):
        self.make("hygiene",task="teeth")
        cadence=self.cadence()
        self.assertTrue(cadences.state(cadence,self.room)["due"])

    def test_no_automatic_medical_interval(self):
        form=cadences.CadenceForm(member=self.owner)
        self.assertIsNone(form["interval"].value())
        self.assertEqual(CareCadence.objects.count(),0)


class InformationTests(TailyCase):
    def test_legacy_reproduction_data_remains_readable_and_editable(self):
        old=self.make("reproduction",kind="egg",eggs="2")
        before=(old.payload.copy(),old.pk,old.author_id)
        response=self.client.get(reverse("record_edit",args=[self.room.pk,old.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context["form"]["kind"].value(),"egg")
        self.assertEqual(old.payload,before[0])
        self.assertIn("2개",__import__('care.modules',fromlist=['record_summary']).record_summary("reproduction",old.payload))
        quick=self.client.get(reverse("quick_record",args=[self.room.pk,"reproduction"]),**UI)
        self.assertNotIn('value="egg"',quick.content.decode())
        self.assertIn('value="food_change"',quick.content.decode())

    def test_new_life_change_requires_description(self):
        response=self.quick("reproduction",kind="food_change",detail="")
        self.assertEqual(response.status_code,422)
        self.assertFalse(Activity.objects.filter(room=self.room).exists())

    def test_integrated_calories_separate_actual_and_estimated(self):
        self.quick("meal",product=str(self.product.pk),quantity="10",consumed="5")
        self.quick("meal",product=str(self.product.pk),quantity="20",consumed="")
        text=integrated_context(self.owner)["stories"][0]["text"]
        self.assertIn("19 kcal",text)
        self.assertIn("76 kcal",text)
        self.assertNotIn("95 kcal",text)

    def test_record_history_group_filter_and_sensitive_group(self):
        self.make("meal");self.make("walk");self.make("expense")
        response=self.client.get(reverse("records",args=[self.room.pk]),{"group":"activity"})
        self.assertEqual([r.module for r in response.context["page_obj"]],["walk"])
        guest=self.client_for(self.guest).get(reverse("records",args=[self.room.pk]),{"group":"care"})
        self.assertEqual(guest.context["page_obj"].paginator.count,0)

    def test_sidebar_owner_and_members_have_distinct_roles(self):
        response=self.client.get(reverse("today",args=[self.room.pk]))
        self.assertContains(response,"sidebar-members")
        self.assertContains(response,"방장")
        self.assertContains(response,"관리자")
        self.assertContains(response,"펫노트")

    def test_removed_member_cannot_use_new_apis(self):
        client=self.client_for(self.guest)
        services.change_member(self.owner,self.guest.pk,"remove",True)
        for name,args in (("quick_record",[self.room.pk,"meal"]),("module_config",[self.room.pk]),("cadence_list",[self.room.pk])):
            self.assertEqual(client.get(reverse(name,args=args)).status_code,404)
