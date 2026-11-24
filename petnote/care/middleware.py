from django.utils import timezone


class PrivateResponseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            response = self.get_response(request)
        finally:
            timezone.deactivate()
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
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        return response
