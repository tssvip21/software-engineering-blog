"""Authorization, evidence, time and failure boundaries for grouped records."""
import json
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.db import OperationalError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from care import services
from care.demo_fixtures import record_data
from care.forms import RecordForm
from care.models import ModuleGroup
from care.reporting.insights import context_for

from .test_functional import CareCase

UI={"HTTP_X_TAILY_UI":"1"}


class GroupAccessAndEvidenceTests(CareCase):
    def group_url(self):
        return reverse("group_config",args=[self.room.pk])

    def payload(self):
        self.room.refresh_from_db()
        return {"version":self.room.groups_version,"groups":[{"name":"아침","keys":["meal","walk"]}]}

    def test_group_api_valid_and_stale_preserves_winner(self):
        payload=self.payload()
        response=self.client.post(self.group_url(),json.dumps(payload),content_type="application/json")
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["groups"][0]["name"],"아침")
        payload["groups"][0]["name"]="덮어쓰기"
        self.assertEqual(self.client.post(self.group_url(),json.dumps(payload),content_type="application/json").status_code,409)
        self.assertEqual(self.room.module_groups.get().name,"아침")

    def test_group_api_requires_csrf_and_manager(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.owner_user)
        self.assertEqual(client.post(self.group_url(),json.dumps(self.payload()),content_type="application/json").status_code,403)
        self.assertEqual(self.client_for(self.guest).get(self.group_url()).status_code,403)
        self.assertEqual(self.client_for(self.member).post(self.group_url(),json.dumps(self.payload()),content_type="application/json").status_code,403)
        self.assertEqual(self.client_for(self.admin).post(self.group_url(),json.dumps(self.payload()),content_type="application/json").status_code,200)

    def test_group_malformed_and_busy_are_not_false_success(self):
        for body in ('[]','null','{',json.dumps({"version":True,"groups":[]})):
            self.assertEqual(self.client.post(self.group_url(),body,content_type="application/json").status_code,400)
        with patch("care.views.workspace.organization.save_groups",side_effect=OperationalError("locked")):
            response=self.client.post(self.group_url(),json.dumps(self.payload()),content_type="application/json")
        self.assertEqual(response.status_code,503)
        self.assertFalse(self.room.module_groups.exists())

    def test_guest_group_names_escaped_and_private_groups_not_exposed(self):
        ModuleGroup.objects.create(room=self.room,name='<img src=x onerror=alert(1)>',keys=["meal"])
        response=self.client_for(self.guest).get(reverse("modules",args=[self.room.pk]))
        self.assertNotContains(response,'<img src=x onerror=alert(1)>')
        self.assertContains(response,'&lt;img')
        ModuleGroup.objects.create(room=self.room,name="숨길 그룹 제목",keys=["medication"])
        self.assertNotContains(self.client_for(self.guest).get(reverse("modules",args=[self.room.pk])),"숨길 그룹 제목")

    def test_multiple_module_evidence_is_exactly_scoped(self):
        a=self.make("meal");b=self.make("treat");self.make("walk");self.make("medication")
        url=reverse("records",args=[self.room.pk])+"?modules=meal,treat&state=done"
        response=self.client.get(url)
        ids={str(x["id"]) if isinstance(x,dict) else str(x.id) for x in response.context["page_obj"].object_list}
        self.assertEqual(ids,{str(a.pk),str(b.pk)})
        private=reverse("records",args=[self.room.pk])+"?modules=meal,medication"
        self.assertEqual(self.client_for(self.guest).get(private).status_code,403)
        self.assertEqual(self.client.get(reverse("records",args=[self.room.pk])+"?modules=madeup").status_code,403)

    def test_member_ajax_self_guest_admin_and_removed_access(self):
        own=reverse("member_change",args=[self.room.pk,self.owner.pk])
        self.assertEqual(self.client.post(own,{"action":"remove"},**UI).status_code,400)
        target=reverse("member_change",args=[self.room.pk,self.guest.pk])
        self.assertEqual(self.client.post(target,{"action":"promote"},**UI).status_code,400)
        self.assertEqual(self.client_for(self.admin).post(target,{"action":"remove"},**UI).status_code,403)
        original=self.make("meal",member=self.guest)
        response=self.client.post(target,{"action":"remove","revoke_links":"1"},**UI)
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client_for(self.guest).get(reverse("overview",args=[self.room.pk])).status_code,404)
        original.refresh_from_db();self.assertFalse(original.voided)

    def test_offline_analysis_makes_no_network_calls(self):
        self.make("meal");self.make("weight")
        with patch("socket.socket.connect",side_effect=AssertionError("network forbidden")),patch("requests.sessions.Session.request",side_effect=AssertionError("HTTP forbidden")):
            result=context_for(self.owner)
        self.assertIn(result["insight"]["code"],{"collecting","steady","observed"})

    def test_calendarmonth_and_all_snapshots_share_same_window_definition(self):
        result=context_for(self.owner,end_date=datetime(2026,3,31).date())
        self.assertEqual(result["dates"][2],datetime(2026,2,28).date())
        for snapshot in result["snapshots"]:
            self.assertEqual((snapshot["as_of"]-snapshot["start"]).days,6)
            self.assertEqual((snapshot["baseline_end"]-snapshot["baseline_start"]).days,27)
            self.assertLess(snapshot["baseline_end"],snapshot["start"])

    def test_legacy_egg_metrics_do_not_appear_without_legacy_records(self):
        self.make("reproduction",kind="food_change",eggs="",detail="먹이 변경")
        stats=self.stats("reproduction")
        labels=[m["label"] for m in stats["metrics"]]
        self.assertNotIn("관찰한 알",labels)
        self.assertNotIn("발정 관찰",labels)
        self.make("reproduction",kind="egg",eggs="2")
        self.assertIn("관찰한 알",[m["label"] for m in self.stats("reproduction")["metrics"]])

    def test_walk_elapsed_time_uses_instants_through_dst(self):
        self.room.timezone="America/New_York";self.room.save(update_fields=["timezone"])
        timezone.activate(ZoneInfo(self.room.timezone))
        data=record_data("walk",occurred_at="2026-03-08T01:30",ended_at="2026-03-08T03:30",status="done",minutes="")
        form=RecordForm(data,room=self.room,module="walk",member=self.owner,quick=True)
        self.assertTrue(form.is_valid(),form.errors.as_json())
        self.assertEqual(float(form.cleaned_data["minutes"]),60)

    def test_removing_module_does_not_remove_analysis_history(self):
        self.make("weight")
        before=context_for(self.owner)["insight"]
        self.room.refresh_from_db()
        enabled=list(self.room.module_settings.filter(enabled=True).values_list("key",flat=True));enabled.remove("weight")
        services.update_modules(self.owner,enabled,list(self.room.module_settings.values_list("key",flat=True)),self.room.settings_version,[])
        after=context_for(self.owner)["insight"]
        self.assertEqual(before,after)

    def test_period_cutoff_excludes_later_data_from_old_snapshot(self):
        day=services.local_date(self.room)-timedelta(days=7)
        self.make("weight",occurred_at=timezone.localtime().replace(hour=0,minute=0,second=0).strftime("%Y-%m-%dT%H:%M:%S"))
        old=context_for(self.owner,end_date=day)["insight"]
        self.assertEqual(old["record_count"],0)
        self.assertEqual(old["code"],"collecting")
