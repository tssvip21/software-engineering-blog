from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import uuid
from django.test import SimpleTestCase
from care.patterns import analyze, compare, theil_sen

END=date(2026,11,5)
TZ=ZoneInfo("Asia/Seoul")


def record(key, day, value=None, unit=None, hour=12, **payload):
    p={"weight":value,"unit":unit or "kg"} if key=="weight" else {"minutes":value} if key in ("walk","play","training","sleep") else {"quantity":value,"unit":unit or "g"} if key in ("meal","treat") else {}
    p.update(payload)
    return SimpleNamespace(pk=uuid.uuid4(),module=key,payload=p,nutrition={},occurred_at=datetime.combine(day,time(hour),TZ),ended_at=None,status="done",voided=False)


def scenario(weight=10, food=100, activity=30, rest=60):
    rows=[]
    for offset in range(35):
        day=END-timedelta(days=offset);new=offset<7
        for key,value in (("weight",weight if new else 10),("meal",food if new else 100),("walk",activity if new else 30),("sleep",rest if new else 60)):
            rows.append(record(key,day,value))
    return rows


class PatternTests(SimpleTestCase):
    def test_stable_data_has_positive_scoped_result(self):
        result=analyze(scenario(),END)
        self.assertEqual(result["code"],"steady")
        self.assertEqual(result["comparable_count"],4)
        self.assertEqual(result["changed_count"],0)

    def test_single_change_not_arbitrary_card_selection(self):
        result=analyze(scenario(weight=11),END)
        self.assertEqual(result["code"],"single")
        self.assertEqual(result["changed_count"],1)
        self.assertIn("체중",result["title"])

    def test_linked_change_changes_overall_reason(self):
        result=analyze(scenario(weight=11,food=150,activity=15),END)
        self.assertEqual(result["code"],"linked")
        self.assertGreaterEqual(len(result["relations"]),2)
        self.assertEqual(result["changed_count"],3)
        self.assertTrue(any(r["key"]=="food-weight" for r in result["relations"]))

    def test_conflicting_changes_not_averaged_away(self):
        result=analyze(scenario(weight=11,food=60),END)
        self.assertEqual(result["code"],"mixed")
        self.assertTrue(any(r["kind"]=="mixed" for r in result["relations"]))

    def test_sparse_not_zero_and_not_normal(self):
        result=analyze([record("meal",END,None)],END)
        self.assertEqual(result["code"],"collecting")
        amount=next(m for m in result["metrics"] if m["family"]=="food")
        self.assertIsNone(amount["current"])
        self.assertEqual(result["record_count"],1)

    def test_explicit_zero_is_present(self):
        rows=scenario()
        for r in rows:
            if r.module=="meal":r.nutrition={"basis":"consumed","kcal":0}
        result=analyze(rows,END)
        m=next(m for m in result["metrics"] if m["family"]=="food")
        self.assertEqual(m["current"],0)
        self.assertEqual(m["state"],"steady")

    def test_single_extreme_measurement_not_persistent(self):
        rows=scenario()
        next(r for r in rows if r.module=="weight").payload["weight"]=10000
        result=analyze(rows,END)
        self.assertEqual(result["code"],"steady")
        w=next(m for m in result["metrics"] if m["key"]=="weight")
        self.assertEqual(len(w["excursion_days"]),1)

    def test_unknown_quantities_and_partial_days_not_summed(self):
        rows=scenario()
        for offset in range(7):rows.append(record("treat",END-timedelta(days=offset),None))
        result=analyze(rows,END)
        m=next(m for m in result["metrics"] if m["family"]=="food")
        self.assertEqual(m["recent_days"],0)
        self.assertEqual(m["state"],"limited")

    def test_actual_and_provided_energy_not_combined(self):
        rows=scenario()
        for r in rows:
            if r.module=="meal":r.nutrition={"basis":"consumed" if r.occurred_at.date()>END-timedelta(days=7) else "provided","kcal":300}
        result=analyze(rows,END)
        self.assertEqual(result["selected_nutrition_basis"],"food_mass")

    def test_duplicate_record_ids_do_not_double_count(self):
        rows=scenario()
        self.assertEqual(analyze(rows,END),analyze(rows+rows,END))

    def test_converted_units_same_pattern(self):
        rows=scenario()
        for r in rows:
            if r.module=="weight":r.payload.update(weight=10000,unit="g")
            if r.module=="meal":r.payload.update(quantity=.1,unit="kg")
        result=analyze(rows,END)
        self.assertEqual(result["code"],"steady")
        self.assertEqual(next(m for m in result["metrics"] if m["key"]=="weight")["current"],10)

    def test_cancelled_and_future_do_not_affect_snapshot(self):
        rows=scenario();original=analyze(rows,END)
        cancelled=record("weight",END,100);cancelled.voided=True
        rows += [cancelled,record("weight",END+timedelta(days=1),200)]
        self.assertEqual(original,analyze(rows,END))

    def test_observation_not_lost_without_numeric_baseline(self):
        result=analyze([record("observation",END,condition="different")],END)
        self.assertEqual(result["code"],"observed")
        self.assertEqual(result["observations"],1)

    def test_overlapping_sleep_is_union(self):
        rows=[record("sleep",END,120,hour=10),record("sleep",END,120,hour=11)]
        m=next(m for m in analyze(rows,END)["metrics"] if m["key"]=="rest")
        self.assertEqual(m["current"],180)

    def test_water_replacement_not_measured_intake(self):
        result=analyze([record("water",END,kind="replace",amount=500,unit="ml")],END)
        self.assertIsNone(next(m for m in result["metrics"] if m["key"]=="water")["current"])

    def test_calendar_distances_and_robust_slope(self):
        points=[(END-timedelta(days=10),10),(END-timedelta(days=5),15),(END,20)]
        self.assertEqual(theil_sen(points),1)

    def test_threshold_sensitivity_retains_clear_joint_scenario(self):
        for scale in (.8,1,1.2):
            self.assertEqual(analyze(scenario(weight=12,food=160,activity=10),END,threshold_scale=scale)["code"],"linked")

    def test_same_day_measurements_do_not_inflate_day_count(self):
        rows=[record("weight",END,10,hour=h) for h in range(12)]
        w=next(m for m in analyze(rows,END)["metrics"] if m["key"]=="weight")
        self.assertEqual(w["recent_days"],1)
        self.assertEqual(w["state"],"limited")

    def test_mad_zero_finite_and_boundary_exact(self):
        values={END-timedelta(days=i):(10.5 if i<7 else 10) for i in range(35)}
        m=compare("weight",values,END)
        self.assertEqual(m["direction"],1)
        self.assertEqual(m["mad"],0)
        self.assertGreater(m["threshold"],0)
