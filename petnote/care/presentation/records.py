"""Authorized record-card data shared by page and fragment rendering."""

from care import services
from care.modules import MODULES, record_summary


def record_card(activity, member, *, hidden_names=None):
    from care.inventory.supplies import hidden_stock_names, require_record_access
    if activity.module == "inventory" and hidden_names is None:
        hidden_names = hidden_stock_names(member)
    require_record_access(member, activity, hidden_names=hidden_names)
    activity.spec = MODULES[activity.module]
    activity.can_edit = services.can_edit_record(member, activity, hidden_names=hidden_names) and not activity.voided
    activity.brief = record_summary(activity.module, activity.payload)
    if activity.module == "medication" and activity.status == "planned" and activity.payload.get("result") == "given":
        activity.brief = activity.brief.removesuffix("투약함") + "투약 예정"
    activity.display_status = "취소됨" if activity.voided else activity.get_status_display()
    return activity


def record_cards(activities, member):
    activities = list(activities)
    from care.inventory.supplies import hidden_stock_names
    hidden = hidden_stock_names(member) if any(row.module == "inventory" for row in activities) else set()
    return [record_card(row, member, hidden_names=hidden) for row in activities]
