"""Room-scoped operations exposed to views and background tasks."""

from .access import _lock_member as _lock_member
from .access import (
    can_edit_record,
    fresh_member,
    get_room,
    guest_identity,
    my_memberships,
    rate_limit,
    read_member,
    read_module_settings,
    request_memberships,
    require_manager,
    require_module,
    require_owner,
    visible_modules,
)
from .common import Conflict, bump, json_safe, local_date
from .memberships import (
    change_member,
    create_invitation,
    get_invitation,
    join_room,
    leave_room,
    merge_guest_memberships,
    revoke_invitation,
)
from .products import (
    inventory_value,
    nutrition_values,
    product_snapshot,
    save_product,
    validate_inventory,
)
from .records import _save_activity_locked as _save_activity_locked
from .records import activity_snapshot, cancel_activity, save_activity
from .rooms import archive_room, create_room, delete_room, update_modules
from .routines import (
    effective_routine,
    link_activity,
    module_enabled_on,
    require_routine_slot,
    routine_snapshot,
    save_routine,
)

__all__ = [
    "Conflict",
    "activity_snapshot",
    "archive_room",
    "bump",
    "can_edit_record",
    "cancel_activity",
    "change_member",
    "create_invitation",
    "create_room",
    "delete_room",
    "effective_routine",
    "fresh_member",
    "get_invitation",
    "get_room",
    "guest_identity",
    "inventory_value",
    "join_room",
    "json_safe",
    "leave_room",
    "link_activity",
    "local_date",
    "merge_guest_memberships",
    "module_enabled_on",
    "my_memberships",
    "nutrition_values",
    "product_snapshot",
    "rate_limit",
    "read_member",
    "read_module_settings",
    "request_memberships",
    "require_manager",
    "require_module",
    "require_owner",
    "require_routine_slot",
    "revoke_invitation",
    "routine_snapshot",
    "save_activity",
    "save_product",
    "save_routine",
    "update_modules",
    "validate_inventory",
    "visible_modules",
]
