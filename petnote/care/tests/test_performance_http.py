import json
from unittest.mock import patch

from django.db import connection
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.test import RequestFactory, SimpleTestCase, TestCase

from care.middleware import PrivateResponseMiddleware, ServerTimingMiddleware


class RedirectPerformanceTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def request(self, *, method="post", opt_in=True, secure=True):
        headers = {"HTTP_X_REQUESTED_WITH": "fetch"}
        if opt_in:
            headers["HTTP_X_PETNOTE_RESPONSE"] = "redirect-json"
        return getattr(self.factory, method)("/rooms/1/settings/", secure=secure, **headers)

    def dispatch(self, request, response):
        return PrivateResponseMiddleware(lambda unused: response)(request)

    def test_opted_in_redirect_preserves_cookie_headers_and_private_policy(self):
        original = HttpResponseRedirect("/rooms/1/?saved=1")
        original.set_cookie("saved", "yes", httponly=True, secure=True)
        original["X-Content-Type-Options"] = "nosniff"
        response = self.dispatch(self.request(), original)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {"ok": True, "redirect_url": "/rooms/1/?saved=1"})
        self.assertNotIn("Location", response)
        self.assertEqual(response.cookies["saved"]["httponly"], True)
        self.assertEqual(response.cookies["saved"]["secure"], True)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Cache-Control"], "no-store, private")
        self.assertIn("X-Petnote-Response", response["Vary"])
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])

    def test_plain_forms_and_get_requests_keep_normal_redirects(self):
        for request in (self.request(opt_in=False), self.request(method="get"),
                        self.factory.post("/rooms/1/settings/", secure=True,
                                          HTTP_X_PETNOTE_RESPONSE="redirect-json")):
            with self.subTest(request=request):
                original = HttpResponseRedirect("/rooms/1/")
                self.assertIs(self.dispatch(request, original), original)
                self.assertEqual(original.status_code, 302)

    def test_external_oauth_cross_port_and_downgrade_redirects_are_not_converted(self):
        for destination in ("https://accounts.google.com/o/oauth2/v2/auth?state=example",
                            "https://example.com/", "//example.com/", "http://testserver/",
                            "https://testserver:444/"):
            with self.subTest(destination=destination):
                original = HttpResponseRedirect(destination)
                self.assertIs(self.dispatch(self.request(), original), original)

    def test_same_origin_absolute_redirect_and_303_are_supported(self):
        original = HttpResponseRedirect("https://testserver/rooms/1/")
        original.status_code = 303
        response = self.dispatch(self.request(), original)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["redirect_url"], "https://testserver/rooms/1/")

    def test_validation_json_and_method_preserving_redirects_remain_unchanged(self):
        for original in (HttpResponse("validation", status=200), HttpResponse("invalid", status=400),
                         HttpResponse("csrf rejected", status=403), JsonResponse({"ok": True}),
                         HttpResponse(status=307, headers={"Location": "/rooms/1/"}),
                         HttpResponse(status=308, headers={"Location": "/rooms/1/"})):
            with self.subTest(status=original.status_code):
                self.assertIs(self.dispatch(self.request(), original), original)

    def test_static_responses_do_not_install_a_database_probe(self):
        with patch("care.middleware.connection.execute_wrapper") as probe:
            response = ServerTimingMiddleware(lambda request: HttpResponse("asset"))(
                self.factory.get("/static/care/petnote.css"))
        probe.assert_not_called()
        self.assertNotIn("Server-Timing", response)


class TimingPrivacyTests(TestCase):
    def test_timings_report_only_duration_and_count(self):
        def view(request):
            with connection.cursor() as cursor:
                cursor.execute("SELECT %s", ["private-marker-not-for-headers"])
                cursor.fetchone()
            return HttpResponse("ok")
        response = ServerTimingMiddleware(view)(RequestFactory().get("/health/"))
        header = response["Server-Timing"]
        self.assertRegex(header, r'app;dur=[0-9.]+, db;dur=[0-9.]+;desc="1 queries"')
        self.assertNotIn("SELECT", header)
        self.assertNotIn("private-marker", header)

    def test_probe_is_removed_when_view_raises(self):
        count = len(connection.execute_wrappers)
        def view(request):
            raise RuntimeError("example")
        with self.assertRaises(RuntimeError):
            ServerTimingMiddleware(view)(RequestFactory().get("/health/"))
        self.assertEqual(len(connection.execute_wrappers), count)
