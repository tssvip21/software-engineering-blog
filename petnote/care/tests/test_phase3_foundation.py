from django.core.exceptions import ValidationError, PermissionDenied
from care import organization, services
from care.breeds import catalogue, suggest_size, reference_for
from care.forms import RoomForm
from care.models import ModuleGroup
from care.insights import context_for
from .test_functional import CareCase


class BreedTests(CareCase):
    def room_form(self, **kwargs):
        return RoomForm({"name":"테일리","species":"dog","timezone":"Asia/Seoul",**kwargs})

    def test_bilingual_catalogue_unique_and_known_sources(self):
        rows=catalogue()
        self.assertGreaterEqual(sum(r["species"]=="dog" for r in rows.values()),40)
        self.assertGreaterEqual(sum(r["species"]=="cat" for r in rows.values()),35)
        self.assertTrue(all(r["ko"] and r["en"] and r["source"].startswith("https://") for r in rows.values()))

    def test_dog_suggestion_persists_and_can_override(self):
        form=self.room_form(breed_key="dog-pomeranian",size_origin="auto")
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data["size"],"small")
        created=services.create_room(self.owner_user,form)
        self.assertEqual(created.breed_key,"dog-pomeranian")
        form=self.room_form(breed_key="dog-pomeranian",size_origin="manual",size="medium")
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data["size"],"medium")

    def test_mixed_unknown_manual_and_no_required_size(self):
        for key in ("mixed","unknown","custom",""):
            form=self.room_form(breed_key=key,size_origin="manual",size="large")
            self.assertTrue(form.is_valid(),form.errors)
            self.assertEqual(form.cleaned_data["size"],"large")
            self.assertTrue(self.room_form(breed_key=key).is_valid())

    def test_cat_does_not_use_dog_size(self):
        form=self.room_form(species="cat",breed_key="cat-british-shorthair",size_origin="auto",size="large")
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data["size"],"")

    def test_wrong_species_breed_is_rejected(self):
        form=self.room_form(breed_key="cat-british-shorthair")
        self.assertFalse(form.is_valid())
        self.assertIn("breed_key",form.errors)

    def test_non_catalogue_input_uses_explicit_custom(self):
        self.assertFalse(self.room_form(breed_key="invented").is_valid())
        form=self.room_form(breed_key="custom",subspecies="우리 동네 믹스")
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data["subspecies"],"우리 동네 믹스")

    def test_no_reference_means_no_auto_size(self):
        self.assertEqual(suggest_size(catalogue()["dog-maltese"]),"")
        self.assertEqual(suggest_size(catalogue()["cat-ragdoll"]),"")

    def test_adult_requirement_and_sex_range(self):
        self.room.species="cat";self.room.breed_key="cat-british-shorthair"
        self.room.sex="female";self.room.maturity=""
        self.assertFalse(reference_for(self.room)["available"])
        self.room.maturity="growing"
        self.assertFalse(reference_for(self.room)["available"])
        self.room.maturity="adult"
        ref=reference_for(self.room)
        self.assertTrue(ref["available"])
        self.assertAlmostEqual(float(ref["low"]),8*.45359237,places=3)
        self.assertAlmostEqual(float(ref["high"]),11*.45359237,places=3)


class DisplayGroupTests(CareCase):
    def save(self,groups,member=None,version=None):
        self.room.refresh_from_db()
        return organization.save_groups(member or self.owner,{"version":self.room.groups_version if version is None else version,"groups":groups})

    def test_create_rename_reorder_remove_preserves_records(self):
        record=self.make("meal")
        result=self.save([{"name":"아침","keys":["meal","walk"]},{"name":"밤","keys":["sleep"]}])
        a,b=result["groups"]
        b["name"]="저녁"
        changed=self.save([b,a])
        self.assertEqual([g["name"] for g in changed["groups"]],["저녁","아침"])
        self.save([a])
        record.refresh_from_db()
        self.assertFalse(record.voided)
        self.assertEqual(self.room.module_groups.count(),1)

    def test_atomic_group_name_swap(self):
        result=self.save([{"name":"하나","keys":["meal"]},{"name":"둘","keys":["walk"]}])
        a,b=result["groups"];a["name"],b["name"]=b["name"],a["name"]
        self.assertEqual([x["name"] for x in self.save([a,b])["groups"]],["둘","하나"])

    def test_guest_cannot_manage_and_stale_version_rejected(self):
        with self.assertRaises(PermissionDenied):self.save([],self.guest)
        result=self.save([{"name":"기본","keys":["meal"]}])
        with self.assertRaises(services.Conflict):self.save([],version=result["version"]-1)
        self.assertEqual(self.room.module_groups.count(),1)

    def test_invalid_duplicate_foreign_group_does_not_write(self):
        foreign=ModuleGroup.objects.create(room=self.other_room,name="외부",keys=["meal"])
        cases=[[{"id":str(foreign.pk),"name":"외부","keys":["meal"]}],[{"name":"중복","keys":["meal","meal"]}],[{"name":"같음","keys":[]},{"name":"같음","keys":[]}]]
        for groups in cases:
            with self.assertRaises(ValidationError):self.save(groups)
        self.assertEqual(self.room.module_groups.count(),0)

    def test_private_group_name_not_visible_to_guest(self):
        self.save([{"name":"투약 관련 개인 그룹","keys":["medication","meal"]},{"name":"일상","keys":["meal"]}])
        self.assertEqual([g.name for g in organization.groups_for(self.guest)],["일상"])

    def test_display_groups_do_not_change_analysis(self):
        self.make("meal")
        before=context_for(self.owner)["insight"]
        self.save([{"name":"새 분류","keys":["weight","meal","walk"]}])
        after=context_for(self.owner)["insight"]
        self.assertEqual(before,after)

    def test_private_records_cannot_change_guest_result(self):
        self.make("meal")
        before=context_for(self.guest)["insight"]
        self.make("medication");self.make("expense");self.make("veterinary")
        self.assertEqual(before,context_for(self.guest)["insight"])
