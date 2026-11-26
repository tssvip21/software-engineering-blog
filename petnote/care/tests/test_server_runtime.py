"""Server startup, health probes, and simultaneous first-login regressions."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, OperationalError, connection, connections
from django.db.models.query import QuerySet
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from care.auth import account_for_claims
from care.models import GoogleAccount
from config.database import vercel_database


class VercelDatabaseTests(SimpleTestCase):
    url = "postgresql://pool.user:example-password@db.example.test:6543/postgres"

    def test_transaction_pooler_options_and_encoded_password(self):
        db = vercel_database(self.url.replace("example-password", "secret%40value%2Fvalue"))
        self.assertEqual(db["PASSWORD"], "secret@value/value")
        self.assertEqual(db["PORT"], "6543")
        self.assertEqual(db["CONN_MAX_AGE"], 0)
        self.assertTrue(db["DISABLE_SERVER_SIDE_CURSORS"])
        self.assertIsNone(db["OPTIONS"]["prepare_threshold"])
        self.assertEqual(db["OPTIONS"]["sslmode"], "require")
        self.assertEqual(db["OPTIONS"]["connect_timeout"], 10)

    def test_explicit_certificate_verification_is_preserved(self):
        for mode in ("verify-ca", "verify-full"):
            with self.subTest(mode=mode):
                options = vercel_database(
                    self.url + f"?sslmode={mode}&sslrootcert=%2Frun%2Fsecrets%2Fdb-ca.pem&channel_binding=require"
                )["OPTIONS"]
                self.assertEqual(options["sslmode"], mode)
                self.assertEqual(options["sslrootcert"], "/run/secrets/db-ca.pem")
                self.assertEqual(options["channel_binding"], "require")

    def test_insecure_explicit_ssl_modes_are_rejected(self):
        for mode in ("disable", "allow", "prefer"):
            with self.subTest(mode=mode), self.assertRaises(ImproperlyConfigured):
                vercel_database(self.url + "?sslmode=" + mode)

    def test_missing_database_credentials_are_rejected(self):
        for url in ("", "sqlite:///example.db", "postgresql://user@db.example.test/db",
                    "postgresql://user:secret@db.example.test/"):
            with self.subTest(url=url), self.assertRaises(ImproperlyConfigured):
                vercel_database(url)

    def test_parser_errors_do_not_expose_secret_fragments(self):
        for url in (self.url.replace("example-password", "private-value%oops"),
                    self.url + "?private-value=1"):
            with self.subTest(url=url), self.assertRaises(ImproperlyConfigured) as caught:
                vercel_database(url)
            self.assertNotIn("private-value", str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)


class VercelStartupTests(SimpleTestCase):
    def load_settings(self, origin, db_url=VercelDatabaseTests.url):
        with TemporaryDirectory(prefix="petnote-runtime-") as directory:
            env = os.environ.copy()
            env.update({
                "DJANGO_SETTINGS_MODULE": "config.settings", "DJANGO_DEBUG": "1",
                "DJANGO_SECRET_KEY": secrets.token_urlsafe(64), "VERCEL": "1",
                "APP_ORIGIN": origin, "DJANGO_ALLOWED_HOSTS": "petnote.example.test",
                "VERCEL_URL": "petnote-preview.vercel.app", "VERCEL_PROJECT_PRODUCTION_URL": "",
                "DATABASE_URL": db_url, "POSTGRES_HOST": "", "CARE_DATA_DIR": directory,
                "CARE_DEMO_MODE": "0", "CARE_PUBLIC_DEMO": "0", "CARE_PUBLIC_DEMO_ENTRY": "0",
            })
            return subprocess.run(
                [sys.executable, "-X", "utf8", "-c",
                 "from config import settings; assert not settings.DEBUG; "
                 "assert 'petnote-preview.vercel.app' in settings.ALLOWED_HOSTS"],
                cwd=Path(settings.BASE_DIR), env=env, capture_output=True,
                text=True, encoding="utf-8", timeout=20,
            )

    def test_public_https_origin_and_preview_host_are_supported(self):
        result = self.load_settings("https://petnote.example.test")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_public_origins_fail_at_startup(self):
        for origin in ("http://petnote.example.test", "https://other.example.test",
                       "https://user:private-value@petnote.example.test",
                       "https://petnote.example.test:8443", "https://petnote.example.test/path",
                       "https://petnote.example.test?secret=private-value", "https://[invalid"):
            with self.subTest(origin=origin):
                result = self.load_settings(origin)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ImproperlyConfigured", result.stderr)
                self.assertNotIn("private-value", result.stderr)

    def test_malformed_password_is_not_in_startup_traceback(self):
        result = self.load_settings(
            "https://petnote.example.test",
            VercelDatabaseTests.url.replace("example-password", "private-value%oops"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ImproperlyConfigured", result.stderr)
        self.assertNotIn("private-value", result.stderr)


@override_settings(SECURE_SSL_REDIRECT=False)
class HealthProbeTests(TestCase):
    def test_anonymous_home_accepts_head_without_starting_login(self):
        response = self.client.head(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)

    def test_get_and_head_check_database_without_creating_sessions(self):
        for method in ("get", "head"):
            with self.subTest(method=method), self.assertNumQueries(1):
                response = getattr(self.client, method)(reverse("health"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Cache-Control"], "no-store, private")
            self.assertFalse(response.cookies)
            if method == "head":
                self.assertEqual(response.content, b"")

    def test_head_keeps_database_failure_status_without_details(self):
        with patch("care.views.connection.cursor", side_effect=OperationalError("private-value")):
            response = self.client.head(reverse("health"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"")

    def test_mutation_methods_do_not_run_database_probe(self):
        with patch("care.views.connection.cursor") as cursor:
            response = self.client.post(reverse("health"))
        self.assertEqual(response.status_code, 405)
        cursor.assert_not_called()


class FirstGoogleLoginTests(TestCase):
    claims = {"sub": "concurrent-example-subject", "email_verified": True, "name": "Example"}

    def setUp(self):
        self.username = "g_" + hashlib.sha256(self.claims["sub"].encode()).hexdigest()

    def existing_account(self, active=True):
        user = get_user_model().objects.create_user(
            username=self.username, password=None, is_active=active,
        )
        return GoogleAccount.objects.create(user=user, subject=self.claims["sub"])

    def test_creation_race_reuses_the_already_linked_account(self):
        winner = self.existing_account()
        with patch.object(GoogleAccount.objects, "select_related") as query:
            query.return_value.filter.return_value.first.side_effect = [None, winner]
            actual = account_for_claims(self.claims)
        self.assertEqual(actual.pk, winner.user_id)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(GoogleAccount.objects.count(), 1)
        self.assertFalse(actual.has_usable_password())

    def test_inactive_account_is_not_accepted_after_a_race(self):
        winner = self.existing_account(active=False)
        with patch.object(GoogleAccount.objects, "select_related") as query:
            query.return_value.filter.return_value.first.side_effect = [None, winner]
            with self.assertRaises(ValueError):
                account_for_claims(self.claims)

    def test_unrelated_username_collision_is_not_claimed(self):
        get_user_model().objects.create_user(username=self.username, password=None)
        with self.assertRaises(IntegrityError):
            account_for_claims(self.claims)
        self.assertFalse(GoogleAccount.objects.exists())


class ConcurrentGoogleLoginTests(TransactionTestCase):
    def test_two_database_connections_create_only_one_account(self):
        if connection.vendor != "postgresql":
            self.skipTest("Concurrent unique-key contention is checked on PostgreSQL.")
        claims = {"sub": "parallel-example-subject", "email_verified": True}
        initial_reads = Barrier(2)
        original_first = QuerySet.first

        def first_with_barrier(query):
            result = original_first(query)
            if query.model is GoogleAccount and result is None:
                initial_reads.wait(timeout=10)
            return result

        def connect():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                return account_for_claims(claims).pk
            finally:
                connections.close_all()

        with patch.object(QuerySet, "first", first_with_barrier), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(connect) for _ in range(2)]
            users = [future.result(timeout=20) for future in futures]
        self.assertEqual(users[0], users[1])
        self.assertEqual(GoogleAccount.objects.count(), 1)
        self.assertEqual(get_user_model().objects.count(), 1)
