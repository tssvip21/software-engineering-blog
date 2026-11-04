from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from care.modules import compact
register = template.Library()

# Simple, original line symbols; all share the same coordinate grid and stroke.
PATHS = {
    "home": '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "list": '<path d="M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01"/>',
    "chart": '<path d="M3 3v18h18M7 15l4-5 4 2 5-7"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
    "users": '<circle cx="9" cy="8" r="3"/><path d="M3 21v-2a6 6 0 0 1 12 0v2M17 5a3 3 0 0 1 0 6M19 15a5 5 0 0 1 3 4v2"/>',
    "settings": '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="9" cy="18" r="2"/>',
    "plus": '<path d="M12 4v16M4 12h16"/>',
    "arrow": '<path d="M5 12h14m-5-5 5 5-5 5"/>',
    "back": '<path d="M19 12H5m5-5-5 5 5 5"/>',
    "check": '<path d="m4 12 5 5L20 6"/>',
    "bowl": '<path d="M3 11h18l-2 8H5ZM7 7c-2-2 2-2 0-4M12 7c-2-2 2-2 0-4M17 7c-2-2 2-2 0-4"/>',
    "treat": '<path d="m8 6 10 10M5 3a3 3 0 0 1 5 2l9 9a3 3 0 1 1-2 5l-9-9A3 3 0 1 1 5 3Z"/>',
    "drop": '<path d="M12 3c-3 4-7 8-7 12a7 7 0 0 0 14 0c0-4-4-8-7-12Z"/>',
    "toilet": '<path d="M4 10h16v4a7 7 0 0 1-7 7h-2a7 7 0 0 1-7-7ZM8 3v4M12 3v4M16 3v4"/>',
    "walk": '<path d="m12 7-3 6 4 2 2 6M9 13l-5 7M12 7l4 5h4M12 7 8 9H4"/><circle cx="14" cy="3" r="2"/>',
    "ball": '<circle cx="12" cy="12" r="9"/><path d="M4 8c7 1 11 5 12 12M8 4c1 7 5 11 12 12"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    "scale": '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7 8a6 6 0 0 1 10 0l-5 5Zm5 5 2-6"/>',
    "spark": '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/>',
    "scissors": '<circle cx="5" cy="6" r="3"/><circle cx="5" cy="18" r="3"/><path d="m7 8 14 13M7 16 21 3"/>',
    "clean": '<path d="m17 3-7 10M5 12l8 6-3 4-8-6Zm3 4-3 4M10 18l-3 4"/>',
    "pill": '<path d="M8 3a5 5 0 0 1 7 0l6 6a5 5 0 0 1-7 7l-6-6a5 5 0 0 1 0-7ZM10 12l7-7"/>',
    "eye": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    "cross": '<path d="M9 3h6v6h6v6h-6v6H9v-6H3V9h6Z"/>',
    "moon": '<path d="M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z"/>',
    "thermometer": '<path d="M9 14V5a3 3 0 0 1 6 0v9a5 5 0 1 1-6 0ZM12 7v11"/>',
    "egg": '<path d="M19 14c0 5-3 7-7 7s-7-2-7-7S9 3 12 3s7 6 7 11Z"/>',
    "feather": '<path d="M3 21 17 7M7 17c-1-5 0-10 5-13s10 2 7 7-8 6-12 6ZM10 14h7M13 11V6"/>',
    "box": '<path d="m3 7 9-4 9 4v10l-9 4-9-4Zm0 0 9 4 9-4M12 11v10M7 5l10 4"/>',
    "wallet": '<rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 8V5l14-3v3M15 11h6v5h-6Z"/>',
}

@register.simple_tag
def icon(name):
    path = PATHS.get(name, PATHS["grid"])
    return format_html('<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>', mark_safe(path))

@register.filter
def number(value):
    try:
        return compact(value)
    except (ValueError, TypeError, ArithmeticError):
        return value
