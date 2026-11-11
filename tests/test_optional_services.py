import os
import sqlite3
import unittest
import uuid
from contextlib import closing, contextmanager
from importlib.util import find_spec
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from features import TrafficWindow
from panel_auth import sign
from storage import ChatSettings


@unittest.skipUnless(os.getenv("TEST_REDIS_URL"), "Requires a Redis test service")
class RedisTests(unittest.IsolatedAsyncioTestCase):
    async def test_traffic_window_is_shared_and_deduplicates_events(self):
        from redis_traffic import RedisTraffic

        first = RedisTraffic(os.environ["TEST_REDIS_URL"])
        second = RedisTraffic(os.environ["TEST_REDIS_URL"])
        self.addAsyncCleanup(first.close)
        self.addAsyncCleanup(second.close)
        await first.client.ping()
        key = uuid.uuid4().hex
        window = TrafficWindow(10, 6)
        self.assertFalse(await first.record("test", window, key, 1, "same"))
        self.assertFalse(await second.record("test", window, key, 1, "same"))
        self.assertFalse(await second.record("test", window, key, 2, "same"))
        self.assertTrue(await first.record("test", window, key, 3, "same"))


@unittest.skipUnless(
    find_spec("fastapi") and find_spec("httpx"), "Requires dashboard test dependencies"
)
class DashboardTests(unittest.TestCase):
    def test_group_session_requires_origin_csrf_and_current_group_admin(self):
        from fastapi.testclient import TestClient
        import dashboard

        @contextmanager
        def open_test_settings(_):
            yield settings

        secret = "integration-test-secret"
        token = sign(secret, "login", 123, -100456, 300)
        with closing(sqlite3.connect(":memory:", check_same_thread=False)) as database:
            settings = ChatSettings(database)
            with (
                patch.object(dashboard, "open_settings", open_test_settings),
                patch.object(dashboard, "dashboard_secret", return_value=secret),
                patch.object(dashboard, "verify_admin", new_callable=AsyncMock) as check_admin,
                TestClient(dashboard.app) as client,
            ):
                check_admin.return_value = SimpleNamespace(can_delete_messages=True)
                self.assertEqual(client.post("/login", data={"code": token}).status_code, 403)
                response = client.post(
                    "/login", data={"code": token}, headers={"Origin": "http://testserver"},
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 303)
                self.assertEqual(client.post("/group", data={"option": "flood"}).status_code, 403)
                page = client.get("/group")
                self.assertEqual(page.status_code, 200)
                import re
                csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
                response = client.post(
                    "/group", data={"option": "flood", "csrf": csrf}, follow_redirects=False,
                )
                self.assertEqual(response.status_code, 303)
                self.assertEqual(settings.option(-100456, "flood"), "on")
                self.assertEqual(settings.option(-100457, "flood"), "off")
                self.assertEqual(check_admin.await_count, 3)
                check_admin.assert_awaited_with(123, -100456)
