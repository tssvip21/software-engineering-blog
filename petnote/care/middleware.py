from time import perf_counter
from urllib.parse import urlsplit

from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme


class ServerTimingMiddleware:
    """Expose durations and counts, never SQL, parameters, or user data."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/static/"):
            return self.get_response(request)
        started = perf_counter()
        database_seconds = 0.0
        queries = 0

        def measure(execute, sql, params, many, context):
            nonlocal database_seconds, queries
            query_started = perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                database_seconds += perf_counter() - query_started
                queries += 1

        with connection.execute_wrapper(measure):
            response = self.get_response(request)
        elapsed = 1000 * (perf_counter() - started)
        response["Server-Timing"] = f'app;dur={elapsed:.1f}, db;dur={1000 * database_seconds:.1f};desc="{queries} queries"'
        return response


def requested_json_redirect(request, response):
    """Avoid fetching the destination twice for explicitly opted-in form saves."""
    if (request.method != "POST" or request.headers.get("X-Requested-With") != "fetch"
            or request.headers.get("X-Petnote-Response") != "redirect-json"
            or response.status_code not in (302, 303)):
        return response
    destination = response.get("Location", "")
    if not url_has_allowed_host_and_scheme(
        destination, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return response
    parts = urlsplit(destination)
    if parts.scheme and parts.scheme != request.scheme:
        return response
    result = JsonResponse({"ok": True, "redirect_url": destination})
    for key, value in response.items():
        if key.lower() not in ("location", "content-type", "content-length"):
            result[key] = value
    result.cookies.update(response.cookies)
    patch_vary_headers(result, ["X-Petnote-Response"])
    return result


class PrivateResponseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            response = self.get_response(request)
        finally:
            timezone.deactivate()
        response = requested_json_redirect(request, response)
        if not request.path.startswith("/static/"):
            if request.method == "GET":
                layout = request.GET.get("layout")
                if layout in ("mobile", "desktop"):
                    response.set_cookie("petnote_layout", layout, httponly=True,
                                        secure=request.is_secure(), samesite="Lax")
                elif layout == "auto":
                    response.delete_cookie("petnote_layout", samesite="Lax")
            response["Cache-Control"] = "no-store, private"
            response["Pragma"] = "no-cache"
            response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        response["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://accounts.google.com; object-src 'none'"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(self), payment=()"
        return response
