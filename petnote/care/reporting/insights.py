"""Permission-scoped adapter for the offline pattern engine and transparent evidence."""
from datetime import timedelta
from urllib.parse import urlencode
from dateutil.relativedelta import relativedelta
from django.urls import reverse
from ..models import Activity
from .patterns import analyze
from ..breeds import reference_for
from ..services import local_date, visible_modules, fresh_member
from .stats import bounds, get_statistics


def evidence_url(room, modules, start, end):
    return reverse("records",args=[room.pk])+"?"+urlencode({"modules":",".join(modules),"start":start.isoformat(),"end":end.isoformat(),"state":"done"})


def compact_plot(points):
    if not points:return None
    lo=min(p["value"] for p in points);hi=max(p["value"] for p in points)
    if hi==lo:lo-=max(abs(lo)*.05,.01);hi+=max(abs(hi)*.05,.01)
    first,last=points[0]["date"],points[-1]["date"]
    span=max(1,(last-first).days)
    dots=[{"x":round(20+(p["date"]-first).days/span*600,2),"y":round(150-(p["value"]-lo)/(hi-lo)*124,2),**p} for p in points]
    return {"dots":dots,"line":" ".join(f"{p['x']},{p['y']}" for p in dots),"first":first,"last":last,"low":round(lo,2),"high":round(hi,2)}


def context_for(member, end_date=None):
    member=fresh_member(member)
    room=member.room;today=end_date or local_date(room)
    endpoints=[today,today-timedelta(days=7),today-relativedelta(months=1)]
    allowed=set(visible_modules(member))
    lower,upper=bounds(room,endpoints[-1]-timedelta(days=37),today)
    rows=list(Activity.objects.filter(room=room,module__in=allowed,voided=False,status="done",occurred_at__gte=lower,occurred_at__lt=upper).order_by("occurred_at","id"))
    snapshots=[analyze(rows,day,room.timezone) for day in endpoints]
    for snapshot in snapshots:
        for metric in snapshot["metrics"]:
            metric["url"]=evidence_url(room,[m for m in metric["modules"] if m in allowed],metric["baseline_start"],snapshot["as_of"])
            metric["plot"]=compact_plot(metric["points"])
        for relation in snapshot["relations"]:
            relation["url"]=evidence_url(room,[m for m in relation["modules"] if m in allowed],snapshot["start"],snapshot["as_of"])
        snapshot["observations_url"]=evidence_url(room,[m for m in ("observation","toilet") if m in allowed],snapshot["start"],snapshot["as_of"])
    current=snapshots[0]
    metrics=current["metrics"]
    focus=[m for m in metrics if m["state"]=="changed"] or [m for m in metrics if m["current"] is not None]
    # Purpose-led ordering, never a fixed four-module selection.
    focus.sort(key=lambda m:(m["state"]!="changed",m["family"]!="weight",-m["recent_days"]))
    weight_stats=get_statistics(room,"weight",today-timedelta(days=6),today) if "weight" in allowed else {}
    latest=None
    if weight_stats.get("measurement_date"):
        latest={"value":weight_stats["primary"],"date":weight_stats["measurement_date"]}
    auxiliary=reference_for(room,latest)
    return {"insight":current,"snapshots":snapshots,"focus_metrics":focus,"analysis_metrics":metrics,
            "breed_reference":auxiliary,"body_reference":weight_stats.get("reference"),
            "analysis_method_version":current["version"],"dates":endpoints,"today":today,
            "scope_note":"현재 참여 권한으로 볼 수 있는 완료 기록만 분석해요."}
