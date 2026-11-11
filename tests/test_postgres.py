import os
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from storage import ChatSettings


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "Requires an empty PostgreSQL test database")
class PostgresTests(unittest.TestCase):
    def test_import_and_storage(self):
        from import_sqlite import import_database
        from postgres import PostgresConnection, postgres_engine

        engine = postgres_engine(os.environ["TEST_DATABASE_URL"])
        self.addCleanup(engine.dispose)
        chat_id = -1001234567890
        user_id = 9_000_000_001

        with TemporaryDirectory() as directory:
            source = Path(directory) / "katana.sqlite3"
            with closing(sqlite3.connect(source)) as database:
                settings = ChatSettings(database)
                settings.register_group(chat_id, "Test", "supergroup", "administrator")
                settings.set_links_blocked(chat_id, True)
                settings.set_profanity_mode(chat_id, "delete")
                settings.set_autoban(chat_id, True)
                settings.report(chat_id, 20, user_id, "First")
                settings.report(chat_id, 10, user_id, "Second")
                settings.add_profanity_strike(chat_id, user_id, 10)

            self.assertEqual(
                import_database(source, engine),
                {
                    "groups": 1, "link_filter": 1, "reports": 2,
                    "profanity_settings": 1, "autoban": 1, "profanity_strikes": 1,
                    "feature_options": 0, "allowlist": 0, "moderation_log": 0,
                    "reputation_votes": 0, "announcements": 0,
                },
            )
            with self.assertRaisesRegex(ValueError, "пустую"):
                import_database(source, engine)

        settings = ChatSettings(PostgresConnection(engine), initialize=False)
        self.assertTrue(settings.links_blocked(chat_id))
        self.assertTrue(settings.autoban_enabled(chat_id))
        self.assertEqual(settings.profanity_mode(chat_id), "delete")
        self.assertEqual(settings.recent_reports(chat_id), [(10, 1, "Second"), (20, 1, "First")])
        self.assertFalse(settings.report(chat_id, 10, user_id, "Duplicate"))
        self.assertTrue(settings.report(chat_id, 30, user_id, "New"))
        self.assertEqual(settings.add_profanity_strike(chat_id, user_id, 30), 2)

        settings.migrate(chat_id, chat_id - 1)
        self.assertFalse(settings.links_blocked(chat_id))
        self.assertTrue(settings.links_blocked(chat_id - 1))
        self.assertEqual(settings.recent_reports(chat_id - 1)[0], (30, 1, "New"))
