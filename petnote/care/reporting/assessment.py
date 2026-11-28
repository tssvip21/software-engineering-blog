"""Offline review of within-pet changes, with recording-context safeguards.

This is descriptive software, not a diagnostic model. Design choices and sources
are kept in docs/15_테일리_4차설계근거.md; user-facing language is a separate adapter.
"""
from collections import Counter
from statistics import median
from zoneinfo import ZoneInfo

from .patterns import analyze, canonical_records

VERSION = "personal-patterns-2"


def assess(records, as_of, timezone_name="Asia/Seoul", threshold_scale=1.0):
    rows = canonical_records(records, as_of, timezone_name)
    result = analyze(rows, as_of, timezone_name, threshold_scale)
    result["version"] = VERSION
    tz = ZoneInfo(timezone_name)
    start = result["start"]
    changed_keys, quality, excursions = set(), [], []
    for metric in result["metrics"]:
        baseline = metric["baseline"]
        threshold = metric["threshold"]
        metric["change_dates"] = [p["date"] for p in metric["points"] if p["recent"] and baseline is not None and metric["direction"] and metric["direction"] * (p["value"] - baseline) >= threshold - 1e-9]
        metric["recording_changed"] = False
        # A daily total can change solely because a new kind or number of entries
        # is being logged. Keep the values but do not turn that ambiguity into a
        # strong combined lifestyle conclusion.
        if metric["family"] in ("food", "activity", "water", "rest"):
            old, recent = [], []
            for row in rows:
                day = row.occurred_at.astimezone(tz).date()
                if row.module not in metric["modules"] or not result["baseline_start"] <= day <= as_of:
                    continue
                if metric["family"] == "water" and row.payload.get("kind") != "measured":
                    continue
                (recent if day >= start else old).append(row)
            def shape(entries):
                counts = Counter(r.occurred_at.astimezone(tz).date() for r in entries)
                return ({r.module for r in entries}, median(counts.values()) if counts else None)
            old_types, old_count = shape(old)
            new_types, new_count = shape(recent)
            count_changed = bool(old_count and new_count and abs(new_count - old_count) >= max(1, old_count * .5))
            if old and recent and (old_types != new_types or count_changed):
                metric["recording_changed"] = True
                metric["recording_note"] = "기록한 활동 종류나 횟수가 달라졌어요. 양이나 시간의 차이를 함께 확인해 주세요."
                quality.append(metric["key"])
        if metric["state"] == "changed" and not metric["recording_changed"]:
            changed_keys.add(metric["key"])
        if metric["excursion_days"] and metric["state"] == "steady" and not metric["recording_changed"]:
            excursions.append(metric["key"])
    lookup = {m["key"]: m for m in result["metrics"]}
    relations = []
    for relation in result["relations"]:
        # Observation text is handled separately, without disclosing its content
        # or inventing temporal overlap with a numerical observation.
        if relation["kind"] == "observed":
            continue
        if not set(relation["metric_keys"]) <= changed_keys:
            continue
        dates = [set(lookup[k]["change_dates"]) for k in relation["metric_keys"]]
        common = set.intersection(*dates)
        if len(common) >= 2:
            relations.append({**relation, "overlap_days": len(common), "overlap_dates": sorted(common)})
    result["relations"] = relations
    result["recording_changes"] = quality
    result["isolated_changes"] = excursions
    result["confirmed_changed_keys"] = sorted(changed_keys)
    if result["observations"]:
        code = "observed"
    elif any(r["kind"] == "mixed" for r in relations):
        code = "mixed"
    elif relations:
        code = "linked"
    elif len(changed_keys) > 1:
        code = "multiple"
    elif changed_keys:
        code = "single"
    elif excursions:
        code = "check"
    elif quality:
        code = "recording"
    elif result["comparable_count"]:
        code = "steady"
    else:
        code = "collecting"
    result["code"] = code
    result["changed_count"] = len(changed_keys)
    return result
