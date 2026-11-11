import sqlite3
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.enums import ChatMemberStatus, ChatType, MessageEntityType
from aiogram.filters import CommandObject
from aiogram.types import MessageEntity

from handlers import (
    moderate_message,
    register_membership,
    remove_by_reply,
    report_message,
    show_reports,
    show_settings,
    unban_member,
)
from moderation import contains_link, contains_profanity
from storage import ChatSettings


class SettingsTests(unittest.TestCase):
    def test_membership_updates_existing_group(self):
        with closing(sqlite3.connect(":memory:")) as database:
            settings = ChatSettings(database)
            settings.register_group(-1001, "Test", ChatType.SUPERGROUP, ChatMemberStatus.MEMBER)
            settings.register_group(-1001, "Updated", ChatType.SUPERGROUP, ChatMemberStatus.LEFT)
            self.assertEqual(
                database.execute("SELECT chat_id, title, bot_status FROM groups").fetchall(),
                [(-1001, "Updated", "left")],
            )

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

    def test_reports_are_deduplicated_and_migrate_with_the_group(self):
        with closing(sqlite3.connect(":memory:")) as database:
            settings = ChatSettings(database)
            self.assertTrue(settings.report(-1001, 42, 7, "Текст"))
            self.assertFalse(settings.report(-1001, 42, 7, "Текст"))
            self.assertTrue(settings.report(-1001, 42, 8, "Текст"))

            settings.migrate(-1001, -1002)

            self.assertEqual(settings.recent_reports(-1001), [])
            self.assertEqual(settings.recent_reports(-1002), [(42, 2, "Текст")])


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
            text=None,
            caption=None,
            entities=[],
            caption_entities=[MessageEntity(type=MessageEntityType.TEXT_LINK, offset=0, length=4,
                                            url="https://example.net")],
            photo=[SimpleNamespace(file_id="test-photo")],
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

    def test_detects_russian_and_english_profanity_as_whole_words(self):
        self.assertTrue(contains_profanity("Вот БЛЯТЬ!"))
        self.assertTrue(contains_profanity("Это хуйня."))
        self.assertTrue(contains_profanity("This is BULLSHIT."))
        self.assertFalse(contains_profanity("страхуй, хлеб, class, assignment"))

    def test_detects_masked_profanity_and_group_examples(self):
        for text in ("F*uck", "f*ck", "f**k", "F•uck", "От дідько", "Це піздець", "Пиздобол"):
            with self.subTest(text=text):
                self.assertTrue(contains_profanity(text))
        for text in ("class*assignment", "fire*truck", "f*lock", "пі́зня зустріч", "підземний"):
            with self.subTest(text=text):
                self.assertFalse(contains_profanity(text))

    def test_detects_masked_and_elongated_russian_profanity(self):
        for text in ("Бл**дь", "Бл**д", "БЛ•ТЬ", "Пошел нахер", "Еееееебаааать!", "Е*ееееебаааать!"):
            with self.subTest(text=text):
                self.assertTrue(contains_profanity(text))
        for text in ("бл**дный", "бледный", "колебать", "гребааать", "бл**дьё"):
            with self.subTest(text=text):
                self.assertFalse(contains_profanity(text))

    async def test_profanity_is_logged_before_switching_to_deletion(self):
        self.message.caption = "Это, блять, тест."
        self.settings.set_profanity_mode(-1001, "log")

        with self.assertLogs("handlers", level="INFO"):
            await moderate_message(self.message, self.bot, self.settings)
        self.bot.delete_message.assert_not_awaited()

        self.settings.set_profanity_mode(-1001, "delete")
        await moderate_message(self.message, self.bot, self.settings)
        self.bot.delete_message.assert_awaited_once_with(-1001, 42)

    async def test_autoban_counts_distinct_successful_profanity_deletions(self):
        self.message.from_user.is_bot = False
        self.message.caption = "This is bullshit."
        self.settings.set_profanity_mode(-1001, "delete")
        self.settings.set_autoban(-1001, True)
        self.bot.ban_chat_member = AsyncMock(return_value=True)
        self.bot.send_message = AsyncMock()

        for message_id in (42, 42, 43):
            self.message.message_id = message_id
            await moderate_message(self.message, self.bot, self.settings)
        self.bot.ban_chat_member.assert_not_awaited()

        self.message.message_id = 44
        self.bot.delete_message.return_value = False
        await moderate_message(self.message, self.bot, self.settings)
        self.bot.ban_chat_member.assert_not_awaited()

        self.bot.delete_message.return_value = True
        await moderate_message(self.message, self.bot, self.settings)
        self.bot.ban_chat_member.assert_awaited_once_with(-1001, 7)
        self.bot.send_message.assert_awaited_once()

    async def test_only_admin_can_unban_and_active_members_are_untouched(self):
        self.message.chat.type = ChatType.SUPERGROUP
        self.bot.unban_chat_member = AsyncMock(return_value=True)
        command = CommandObject(command="unban", args="8")

        await unban_member(self.message, command, self.bot, self.settings)
        self.bot.unban_chat_member.assert_not_awaited()

        self.bot.get_chat_member.return_value.status = ChatMemberStatus.ADMINISTRATOR
        await unban_member(self.message, command, self.bot, self.settings)
        self.bot.unban_chat_member.assert_awaited_once_with(-1001, 8, only_if_banned=True)

    async def test_link_in_caption_is_deleted_only_when_filter_is_enabled(self):
        await moderate_message(self.message, self.bot, self.settings)
        self.bot.delete_message.assert_not_awaited()

        self.settings.set_links_blocked(self.message.chat.id, True)
        await moderate_message(self.message, self.bot, self.settings)
        self.bot.delete_message.assert_awaited_once_with(-1001, 42)

    async def test_admin_link_is_preserved(self):
        self.settings.set_links_blocked(self.message.chat.id, True)
        self.bot.get_chat_member.return_value.status = ChatMemberStatus.ADMINISTRATOR

        await moderate_message(self.message, self.bot, self.settings)

        self.bot.delete_message.assert_not_awaited()

    async def test_automatic_forward_is_protected_from_filter_and_manual_deletion(self):
        self.settings.set_links_blocked(self.message.chat.id, True)
        self.message.is_automatic_forward = True
        self.bot.get_chat_member.return_value.status = ChatMemberStatus.ADMINISTRATOR

        await moderate_message(self.message, self.bot, self.settings)
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

    async def test_report_uses_replied_message(self):
        self.message.from_user.is_bot = False
        self.message.reply_to_message = SimpleNamespace(
            message_id=71, text=None, caption="Фото с подписью"
        )

        await report_message(self.message, self.settings)

        self.assertEqual(self.settings.recent_reports(-1001), [(71, 1, "Фото с подписью")])

    async def test_reports_are_sent_only_to_admin_in_private(self):
        self.message.chat.type = ChatType.SUPERGROUP
        self.settings.report(-1001, 42, 7, "Тестовая жалоба")
        self.bot.send_message = AsyncMock()

        await show_reports(self.message, self.bot, self.settings)
        self.bot.send_message.assert_not_awaited()

        self.bot.get_chat_member.return_value.status = ChatMemberStatus.ADMINISTRATOR
        await show_reports(self.message, self.bot, self.settings)

        recipient_id, report_text = self.bot.send_message.await_args.args
        self.assertEqual(recipient_id, self.message.from_user.id)
        self.assertIn("Тестовая жалоба", report_text)
        self.message.reply.assert_awaited_once_with("Отправил список жалоб в личку.")

    async def test_settings_require_admin_and_display_current_bot_rights(self):
        self.message.chat.type = ChatType.SUPERGROUP
        self.message.chat.title = "Test"
        self.bot.id = 9
        self.settings.set_profanity_mode(-1001, "log")
        await show_settings(self.message, self.bot, self.settings)
        self.message.reply.assert_not_awaited()

        bot_membership = SimpleNamespace(
            status=ChatMemberStatus.ADMINISTRATOR,
            can_delete_messages=True,
            can_restrict_members=False,
        )
        self.bot.get_chat_member.side_effect = [
            SimpleNamespace(status=ChatMemberStatus.CREATOR),
            bot_membership,
        ]
        await show_settings(self.message, self.bot, self.settings)
        reply = self.message.reply.await_args.args[0]
        self.assertIn("Удаление сообщений: да", reply)
        self.assertIn("Блокировка участников: нет", reply)
        self.assertIn("Фильтр мата: log", reply)

        await register_membership(
            SimpleNamespace(
                chat=self.message.chat,
                new_chat_member=SimpleNamespace(status=ChatMemberStatus.KICKED),
            ),
            self.settings,
        )
        self.assertEqual(
            self.settings.database.execute("SELECT bot_status FROM groups").fetchall(),
            [("kicked",)],
        )
