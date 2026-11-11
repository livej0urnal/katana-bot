import sqlite3
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.enums import ChatMemberStatus, MessageEntityType

from main import ChatSettings, filter_links, remove_by_reply
from moderation import contains_link


class SettingsTests(unittest.TestCase):
    def test_filter_settings_are_independent_for_each_chat(self):
        with closing(sqlite3.connect(":memory:")) as database:
            settings = ChatSettings(database)
            self.assertFalse(settings.links_blocked(-1001))

            settings.set_links_blocked(-1001, True)
            self.assertTrue(ChatSettings(database).links_blocked(-1001))
            self.assertFalse(settings.links_blocked(-1002))

            settings.set_links_blocked(-1001, False)
            self.assertFalse(settings.links_blocked(-1001))

    def test_filter_setting_follows_chat_migration(self):
        with closing(sqlite3.connect(":memory:")) as database:
            settings = ChatSettings(database)
            settings.set_links_blocked(-1001, True)

            settings.migrate(-1001, -1002)
            settings.migrate(-1001, -1002)

            self.assertFalse(settings.links_blocked(-1001))
            self.assertTrue(settings.links_blocked(-1002))


class ModerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        database = sqlite3.connect(":memory:")
        self.addCleanup(database.close)
        self.settings = ChatSettings(database)
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=-1001),
            message_id=42,
            sender_chat=None,
            from_user=SimpleNamespace(id=7),
            is_automatic_forward=False,
            entities=[],
            caption_entities=[SimpleNamespace(type=MessageEntityType.TEXT_LINK)],
            reply=AsyncMock(),
        )
        self.bot = SimpleNamespace(
            get_chat_member=AsyncMock(
                return_value=SimpleNamespace(status=ChatMemberStatus.MEMBER)
            ),
            delete_message=AsyncMock(return_value=True),
        )

    def test_detects_telegram_link_entities(self):
        self.assertTrue(contains_link([MessageEntityType.URL]))
        self.assertTrue(contains_link([MessageEntityType.TEXT_LINK]))
        self.assertFalse(contains_link([MessageEntityType.EMAIL, MessageEntityType.BOLD]))

    async def test_link_in_caption_is_deleted_only_when_filter_is_enabled(self):
        await filter_links(self.message, self.bot, self.settings)
        self.bot.delete_message.assert_not_awaited()

        self.settings.set_links_blocked(self.message.chat.id, True)
        await filter_links(self.message, self.bot, self.settings)
        self.bot.delete_message.assert_awaited_once_with(-1001, 42)

    async def test_admin_link_is_preserved(self):
        self.settings.set_links_blocked(self.message.chat.id, True)
        self.bot.get_chat_member.return_value.status = ChatMemberStatus.ADMINISTRATOR

        await filter_links(self.message, self.bot, self.settings)

        self.bot.delete_message.assert_not_awaited()

    async def test_automatic_forward_is_protected_from_filter_and_manual_deletion(self):
        self.settings.set_links_blocked(self.message.chat.id, True)
        self.message.is_automatic_forward = True
        self.bot.get_chat_member.return_value.status = ChatMemberStatus.ADMINISTRATOR

        await filter_links(self.message, self.bot, self.settings)
        self.bot.get_chat_member.assert_not_awaited()

        command = SimpleNamespace(
            chat=self.message.chat,
            from_user=self.message.from_user,
            sender_chat=None,
            reply_to_message=self.message,
            reply=AsyncMock(),
        )
        await remove_by_reply(command, self.bot)

        self.bot.delete_message.assert_not_awaited()
        command.reply.assert_awaited_once()
