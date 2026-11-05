"""Constrained return links keep a record's browsing context within its room."""
from urllib.parse import urlencode, urlsplit
from django.urls import reverse


def record_return(request, room):
    value = request.POST.get("return_to", request.GET.get("return_to", ""))
    fallback = reverse("records", args=[room.pk])
    if not isinstance(value, str) or len(value) > 3000 or any(ord(c) < 32 for c in value) or "\\" in value:
        return fallback
    try:
        parsed = urlsplit(value)
    except ValueError:
        return fallback
    if parsed.scheme or parsed.netloc or parsed.path != fallback:
        return fallback
    return parsed.path + ("?" + parsed.query if parsed.query else "")


def with_return(path, return_to):
    return path + ("&" if "?" in path else "?") + urlencode({"return_to": return_to})
