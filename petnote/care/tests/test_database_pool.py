"""Connection reuse and failure recovery against an isolated PostgreSQL test DB."""
from copy import deepcopy
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import connection, connections
from django.db.backends.postgresql.base import DatabaseWrapper
from django.test import RequestFactory, SimpleTestCase
from psycopg.pq import TransactionStatus
from psycopg_pool import PoolTimeout

from care.views import health
from config.database import vercel_database


POOL_TEMPLATE = vercel_database(
    "postgresql://pool-test:fixture-only@db.example.invalid/petnote_pool_test"
)


def pooled_wrapper(database_settings, **pool_overrides):
    """Keep the test runner's DB/TLS settings; use the production pool policy."""
    config = deepcopy(database_settings)
    config["CONN_MAX_AGE"] = POOL_TEMPLATE["CONN_MAX_AGE"]
    config["CONN_HEALTH_CHECKS"] = POOL_TEMPLATE["CONN_HEALTH_CHECKS"]
    config["DISABLE_SERVER_SIDE_CURSORS"] = POOL_TEMPLATE["DISABLE_SERVER_SIDE_CURSORS"]
    config["OPTIONS"] = deepcopy(config.get("OPTIONS", {}))
    config["OPTIONS"]["prepare_threshold"] = None
    config["OPTIONS"]["pool"] = {
        **deepcopy(POOL_TEMPLATE["OPTIONS"]["pool"]),
        **pool_overrides,
    }
    return DatabaseWrapper(config, alias=f"petnote_pool_test_{uuid4().hex}")


def close_wrapper(wrapper):
    try:
        wrapper.close()
    finally:
        wrapper.close_pool()


class PooledHealthFailureTests(SimpleTestCase):
    def test_real_pool_timeout_is_wrapped_as_database_error_and_returns_503(self):
        config = {
            **deepcopy(connections["default"].settings_dict),
            **deepcopy(POOL_TEMPLATE),
        }
        wrapper = pooled_wrapper(config)
        self.addCleanup(close_wrapper, wrapper)
        with patch("care.views.connection", wrapper), patch.object(
            wrapper,
            "get_new_connection",
            side_effect=PoolTimeout("Pooled connection checkout timed out."),
        ):
            response = health(RequestFactory().get("/health/"))
        self.assertEqual(response.status_code, 503)
        self.assertJSONEqual(response.content, {"status": "unavailable"})


@skipUnless(connection.vendor == "postgresql", "Requires isolated PostgreSQL test DB.")
class PostgreSQLPoolTests(SimpleTestCase):
    databases = {"default"}

    def make_wrapper(self, **pool_overrides):
        wrapper = pooled_wrapper(connections["default"].settings_dict, **pool_overrides)
        self.addCleanup(close_wrapper, wrapper)
        return wrapper

    def select_one(self, wrapper):
        with wrapper.cursor() as cursor:
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone(), (1,))
        return wrapper.connection

    def test_request_end_returns_client_and_next_request_reuses_connection(self):
        wrapper = self.make_wrapper()
        first = self.select_one(wrapper)
        pool = wrapper.pool
        opened = pool.get_stats()["connections_num"]

        # This is the same cleanup path used by Django request_finished.
        wrapper.close_if_unusable_or_obsolete()
        self.assertIsNone(wrapper.connection)
        self.assertFalse(first.closed)

        second = self.select_one(wrapper)
        self.assertIs(second, first)
        self.assertEqual(pool.get_stats()["connections_num"], opened)

    def test_closed_idle_socket_is_replaced_before_next_query(self):
        wrapper = self.make_wrapper()
        first = self.select_one(wrapper)
        pool = wrapper.pool
        wrapper.close_if_unusable_or_obsolete()

        # A disconnected idle client must never escape the checkout health check.
        first.close()
        second = self.select_one(wrapper)
        self.assertIsNot(second, first)
        self.assertFalse(second.closed)
        self.assertGreaterEqual(pool.get_stats()["connections_num"], 2)

    def test_failed_transaction_rolls_back_before_connection_is_reused(self):
        wrapper = self.make_wrapper()
        first = self.select_one(wrapper)
        with wrapper.cursor() as cursor:
            cursor.execute(
                "CREATE TEMPORARY TABLE petnote_pool_rollback (value integer) "
                "ON COMMIT PRESERVE ROWS"
            )
        with self.assertRaisesMessage(RuntimeError, "fixture transaction failure"):
            with first.transaction():
                with wrapper.cursor() as cursor:
                    cursor.execute("INSERT INTO petnote_pool_rollback VALUES (1)")
                raise RuntimeError("fixture transaction failure")

        wrapper.close_if_unusable_or_obsolete()
        self.assertIs(self.select_one(wrapper), first)
        with wrapper.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM petnote_pool_rollback")
            self.assertEqual(cursor.fetchone(), (0,))
            cursor.execute("DROP TABLE petnote_pool_rollback")
        self.assertEqual(first.info.transaction_status, TransactionStatus.IDLE)

    def test_four_clients_bound_connections_and_fifth_checkout_times_out(self):
        wrapper = self.make_wrapper()
        pool = wrapper.pool
        pool.open()
        clients = []
        try:
            for _ in range(4):
                clients.append(pool.getconn())
            self.assertEqual(len({id(client) for client in clients}), 4)
            self.assertEqual(pool.get_stats()["pool_size"], 4)
            started = monotonic()
            with self.assertRaises(PoolTimeout):
                pool.getconn(timeout=0.1)
            self.assertLess(monotonic() - started, 2)
            self.assertEqual(pool.get_stats()["pool_size"], 4)
        finally:
            for client in clients:
                pool.putconn(client)
        self.select_one(wrapper)

    def test_idle_pool_drains_to_zero_and_later_request_reconnects(self):
        wrapper = self.make_wrapper(max_idle=0.05)
        first = self.select_one(wrapper)
        pool = wrapper.pool
        wrapper.close_if_unusable_or_obsolete()

        # A short test-only timeout exercises the real maintenance worker.
        deadline = monotonic() + 3
        while pool.get_stats()["pool_size"] and monotonic() < deadline:
            sleep(0.01)
        self.assertEqual(pool.get_stats()["pool_size"], 0)
        self.assertTrue(first.closed)
        second = self.select_one(wrapper)
        self.assertIsNot(second, first)
        self.assertGreaterEqual(pool.get_stats()["connections_num"], 2)
