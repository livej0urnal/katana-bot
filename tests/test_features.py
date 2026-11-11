import sqlite3
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.enums import ChatMemberStatus

from features import TrafficWindow, domain_of, has_untrusted_link
from gatekeeper import is_member, router as gatekeeper_router, signature
from handlers import apply_strike, router
from panel_auth import sign, verify
from spam_model import spam_probability
from storage import ChatSettings


class RulesTests(unittest.TestCase):
    def test_domain_allowlist_only_matches_the_host(self):
        allowed = {"example.com"}
        entity = SimpleNamespace(type="text_link", url="https://www.example.com/docs")
        self.assertFalse(has_untrusted_link("", [entity], allowed))
        entity.url = "https://example.com@attacker.example"
        self.assertTrue(has_untrusted_link("", [entity], allowed))
        entity.url = "https://example.com.attacker.example"
        self.assertTrue(has_untrusted_link("", [entity], allowed))
        self.assertIsNone(domain_of("mailto:user@example.com"))

    def test_flood_deduplicates_updates_and_expires_events(self):
        window = TrafficWindow(10, 6)
        with patch("features.time.monotonic", return_value=100):
            self.assertFalse(window.record((1, 2), 1, "Repeated"))
            self.assertFalse(window.record((1, 2), 1, "Repeated"))
            self.assertFalse(window.record((1, 2), 2, " repeated "))
            self.assertTrue(window.record((1, 2), 3, "REPEATED"))
        with patch("features.time.monotonic", return_value=110):
            self.assertFalse(window.record((1, 2), 4, "Repeated"))

    def test_bootstrap_model_separates_known_examples_and_unknown_text(self):
        self.assertGreater(spam_probability("Claim your free reward click now"), 0.9)
        self.assertLess(spam_probability("Thanks for helping me fix the problem"), 0.1)
        self.assertEqual(spam_probability("абракадабра"), 0.5)

    def test_verification_is_bound_to_chat_user_deadline_and_answer(self):
        bot = SimpleNamespace(token="test")
        digest = signature(bot, 1, 2, 3, 4)
        for args in ((2, 2, 3, 4), (1, 3, 3, 4), (1, 2, 4, 4), (1, 2, 3, 5)):
            self.assertNotEqual(digest, signature(bot, *args))
        self.assertTrue(is_member(SimpleNamespace(status=ChatMemberStatus.RESTRICTED, is_member=True)))
        self.assertFalse(is_member(SimpleNamespace(status=ChatMemberStatus.RESTRICTED, is_member=False)))

    def test_dispatcher_subscribes_to_newcomer_and_callback_updates(self):
        events = set(router.resolve_used_update_types()) | set(gatekeeper_router.resolve_used_update_types())
        self.assertTrue({"message", "edited_message", "chat_member", "callback_query"}.issubset(events))
        self.assertIn("my_chat_member", events)

    def test_dashboard_token_has_a_purpose_group_and_expiration(self):
        with patch("panel_auth.time.time", return_value=100):
            token = sign("test", "login", 2, -1001, 300)
            self.assertEqual(verify("test", "login", token)[:2], (2, -1001))
            self.assertIsNone(verify("test", "session", token))
            self.assertIsNone(verify("another-secret", "login", token))
        with patch("panel_auth.time.time", return_value=400):
            self.assertIsNone(verify("test", "login", token))


class FeatureStorageTests(unittest.TestCase):
    def test_reputation_appeals_and_announcements_are_group_scoped(self):
        with closing(sqlite3.connect(":memory:")) as database:
            settings = ChatSettings(database)
            self.assertTrue(settings.vote(1, 2, 3))
            self.assertFalse(settings.vote(1, 2, 3))
            self.assertEqual(settings.reputation(1, 3), 1)
            self.assertEqual(settings.reputation(2, 3), 0)

            case = settings.audit(1, "warn", 3, 4, "reason")
            self.assertFalse(settings.submit_appeal(2, 3, case, "appeal"))
            self.assertFalse(settings.submit_appeal(1, 2, case, "appeal"))
            self.assertTrue(settings.submit_appeal(1, 3, case, "appeal"))
            self.assertFalse(settings.submit_appeal(1, 3, case, "appeal"))
            self.assertEqual(settings.strike_count(1, 3), 1)

            with patch("feature_storage.time.time", return_value=100):
                settings.set_announcement(1, "Hello", 60)
            self.assertEqual(settings.due_announcements(159), [])
            self.assertEqual(settings.due_announcements(160), [(1, "Hello", 60, 160)])
            self.assertTrue(settings.advance_announcement(1, 160, 220))
            self.assertFalse(settings.advance_announcement(1, 160, 220))


class StrikeTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_points_mute_member_and_log_sanction(self):
        with closing(sqlite3.connect(":memory:")) as database:
            settings = ChatSettings(database)
            for _ in range(3):
                settings.audit(1, "warn", 3)
            bot = SimpleNamespace(
                get_chat_member=AsyncMock(return_value=SimpleNamespace(status=ChatMemberStatus.MEMBER)),
                restrict_chat_member=AsyncMock(return_value=True),
                send_message=AsyncMock(),
            )
            with patch("handlers.time.time", return_value=100), patch.object(settings, "strike_count", return_value=3):
                await apply_strike(bot, settings, 1, 3)
            self.assertEqual(bot.restrict_chat_member.await_args.kwargs["until_date"], 3700)
            self.assertEqual(settings.recent_actions(1)[0][1], "strike_mute")
