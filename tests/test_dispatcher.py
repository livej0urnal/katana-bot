import re
import sqlite3
import time
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import Update

from gatekeeper import router as gatekeeper_router
from handlers import router
from storage import ChatSettings


ADMIN = {"id": 91, "is_bot": False, "first_name": "Admin"}
BOT = {"id": 123456, "is_bot": True, "first_name": "Katana", "username": "test_katana_bot"}
MEMBER = {"id": 92, "is_bot": False, "first_name": "Member"}
CHAT = {"id": -1009876543210, "type": "supergroup", "title": "Integration test"}


class TelegramSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.restricted = {}

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b"test image"

    async def make_request(self, bot, method, timeout=None):
        name = method.__api_method__
        self.calls.append(method)
        if name == "getMe":
            return SimpleNamespace(**BOT)
        if name == "getChat":
            return SimpleNamespace(pinned_message=None)
        if name == "getChatMember":
            if method.user_id in {ADMIN["id"], BOT["id"]}:
                return SimpleNamespace(
                    status="administrator", can_delete_messages=True, can_restrict_members=True
                )
            if method.user_id in self.restricted:
                return SimpleNamespace(
                    status="restricted", is_member=True, until_date=self.restricted[method.user_id]
                )
            return SimpleNamespace(status="member")
        if name == "restrictChatMember":
            if method.until_date:
                from datetime import datetime, timezone
                self.restricted[method.user_id] = datetime.fromtimestamp(method.until_date, timezone.utc)
            else:
                self.restricted.pop(method.user_id, None)
        if name == "getUserProfilePhotos":
            return SimpleNamespace(photos=[[SimpleNamespace(file_id="test-profile")]])
        return True

    def methods(self, name):
        return [method for method in self.calls if method.__api_method__ == name]


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.dispatcher = Dispatcher()
        cls.dispatcher.include_router(gatekeeper_router)
        cls.dispatcher.include_router(router)

    async def asyncSetUp(self):
        database = sqlite3.connect(":memory:")
        self.addCleanup(database.close)
        self.settings = ChatSettings(database)
        self.session = TelegramSession()
        self.bot = Bot("123456:TEST_ONLY", session=self.session)
        self.sequence = 0
        self.ai = SimpleNamespace(
            text_model="test-text", vision_model="test-vision",
            text_label=AsyncMock(return_value="ok"),
            image_label=AsyncMock(return_value="ok"),
            record_message=lambda *_: None,
            recent_ids=lambda *_: [],
        )

    def message(self, text=None, author=MEMBER, **extra):
        self.sequence += 1
        value = {
            "message_id": self.sequence, "date": int(time.time()),
            "chat": CHAT, "from": author, **extra,
        }
        if text is not None:
            value["text"] = text
        return value

    async def update(self, kind, value):
        self.sequence += 1
        await self.dispatcher.feed_update(
            self.bot, Update.model_validate({"update_id": self.sequence, kind: value}),
            settings=self.settings, ai_analysis=self.ai, redis_traffic=None,
        )

    async def send(self, text, author=MEMBER, **extra):
        value = self.message(text, author, **extra)
        await self.update("message", value)
        return value

    async def test_profanity_is_moderated_without_setup_command(self):
        await self.send("This is bullshit.", ADMIN)
        removed = await self.send("This is bullshit.")
        await self.send("This is ordinary text.")
        self.assertEqual(
            [call.message_id for call in self.session.methods("deleteMessage")],
            [removed["message_id"]],
        )
        await self.send("/profanity off", ADMIN)
        self.settings = ChatSettings(self.settings.database)
        await self.send("This is bullshit.")
        self.assertEqual(len(self.session.methods("deleteMessage")), 1)

    async def test_cleanup_and_restore_are_admin_only_and_repost_once(self):
        await self.send("/profanity log", ADMIN)
        kept = await self.send("This is bullshit.")
        await self.update("edited_message", kept | {"text": "Edited to remove profanity"})
        removed = await self.send("This is bullshit.")

        await self.send("/cleanup 30m")
        self.assertFalse(self.session.methods("deleteMessage"))
        await self.send("/cleanup 30m", ADMIN)
        self.assertEqual(
            [call.message_id for call in self.session.methods("deleteMessage")],
            [removed["message_id"]],
        )

        command = f"/restore {removed['message_id']}"
        await self.send(command)
        self.assertNotIn(removed["text"], [call.text for call in self.session.methods("sendMessage")])
        await self.send(command, ADMIN)
        await self.send(command, ADMIN)
        self.assertEqual(
            [call.text for call in self.session.methods("sendMessage")].count(removed["text"]), 1
        )

    async def test_routed_filters_allowlists_comments_and_edited_messages(self):
        await self.send("/links on", ADMIN)
        await self.send("/allow add domain example.com", ADMIN)
        allowed = "https://example.com/help"
        forbidden = "https://example.net"
        await self.send(allowed, entities=[{"type": "url", "offset": 0, "length": len(allowed)}])
        item = await self.send("A message without links")
        item.update(text=forbidden, entities=[{"type": "url", "offset": 0, "length": len(forbidden)}])
        await self.update("edited_message", item)
        self.assertEqual([call.message_id for call in self.session.methods("deleteMessage")], [item["message_id"]])

        await self.send("/links off", ADMIN)
        await self.send("/protect comments strict", ADMIN)
        root = await self.send(forbidden, ADMIN, is_automatic_forward=True)
        link_entities = [{"type": "url", "offset": 0, "length": len(forbidden)}]
        comment = await self.send(forbidden, message_thread_id=root["message_id"], entities=link_entities)
        await self.send(forbidden, message_thread_id=root["message_id"] + 900, entities=link_entities)
        self.assertEqual(
            [call.message_id for call in self.session.methods("deleteMessage")],
            [item["message_id"], comment["message_id"]],
        )

        await self.send("/profanity delete", ADMIN)
        await self.send("/allow add user 92", ADMIN)
        await self.send("This is bullshit.")
        self.assertEqual(len(self.session.methods("deleteMessage")), 2)
        await self.send("/allow remove user 92", ADMIN)
        await self.send("This is bullshit.")
        self.assertEqual(len(self.session.methods("deleteMessage")), 3)

    async def test_routed_reports_sanctions_appeals_and_announcements(self):
        target = await self.send("Useful fixture")
        await self.send("/report", reply_to_message=target)
        await self.send("/report", reply_to_message=target)
        self.assertEqual(self.settings.recent_reports(CHAT["id"]), [(target["message_id"], 1, "Useful fixture")])
        await self.send("/reports", ADMIN)
        await self.send("/warn test", ADMIN, reply_to_message=target)
        case = self.settings.recent_actions(CHAT["id"])[0][0]
        await self.update("message", self.message(
            f"/appeal {CHAT['id']} {case} Recheck this fixture",
            chat={"id": MEMBER["id"], "type": "private"},
        ))
        await self.send("/mute 5", ADMIN, reply_to_message=target)
        await self.send("/unmute 92", ADMIN)
        await self.send("/tempban 10", ADMIN, reply_to_message=target)
        await self.send("/ban", ADMIN, reply_to_message=target)
        await self.send("/unban 92", ADMIN)
        await self.send("/thanks", ADMIN, reply_to_message=target)
        await self.send("/rep")
        await self.send("/audit", ADMIN)
        await self.send("/stats", ADMIN)
        await self.send("/announce 1 Test reminder", ADMIN)
        self.assertEqual(self.settings.due_announcements(int(time.time()) + 65)[0][1], "Test reminder")
        await self.send("/announce off", ADMIN)
        self.assertFalse(self.settings.due_announcements(int(time.time()) + 65))
        self.assertEqual(self.settings.reputation(CHAT["id"], MEMBER["id"]), 1)
        self.assertEqual(len(self.session.methods("banChatMember")), 2)
        self.assertTrue(self.session.methods("unbanChatMember")[0].only_if_banned)
        self.assertEqual(len(self.session.methods("restrictChatMember")), 2)
        self.assertGreaterEqual(sum(call.chat_id == ADMIN["id"] for call in self.session.methods("sendMessage")), 3)
        self.assertIn(f"appeal:{case}", dict(self.settings.action_totals(CHAT["id"])))

    async def test_routed_captcha_raid_and_ai_signals(self):
        await self.send("/protect raid on", ADMIN)
        await self.send("/protect newspam log", ADMIN)
        await self.send("/protect profile log", ADMIN)
        for index in range(6):
            newcomer = MEMBER | {"id": MEMBER["id"] + index}
            await self.update("chat_member", {
                "chat": CHAT, "from": ADMIN, "date": int(time.time()),
                "old_chat_member": {"status": "left", "user": newcomer},
                "new_chat_member": {"status": "member", "user": newcomer},
            })
        self.assertGreater(int(self.settings.option(CHAT["id"], "raid_until")), int(time.time()))
        captcha = self.session.methods("sendMessage")[-1]
        operands = re.search(r"(\d+) \+ (\d+)\?", captcha.text)
        answer = str(sum(map(int, operands.groups())))
        correct = next(
            button.callback_data for button in captcha.reply_markup.inline_keyboard[0] if button.text == answer
        )
        callback_message = self.message(captcha.text, BOT)
        for author in (MEMBER, newcomer):
            await self.update("callback_query", {
                "id": str(self.sequence), "chat_instance": "test", "from": author,
                "data": correct, "message": callback_message,
            })
        self.assertEqual(len(self.session.methods("restrictChatMember")), 2)
        self.assertEqual(len(self.session.methods("getUserProfilePhotos")), 6)
        self.assertEqual(
            [call.message_id for call in self.session.methods("deleteMessage")],
            [callback_message["message_id"]],
        )

        await self.send("Claim your free reward click now", newcomer)
        await self.send("/protect ai log", ADMIN)
        await self.send("/protect nsfw delete", ADMIN)
        self.ai.text_label.return_value = "scam"
        self.ai.image_label.return_value = "nsfw"
        await self.send("AI fixture")
        photo = await self.send(None, photo=[{
            "file_id": "photo", "file_unique_id": "photo", "width": 20, "height": 20, "file_size": 50,
        }])
        self.assertEqual(
            [call.message_id for call in self.session.methods("deleteMessage")],
            [callback_message["message_id"], photo["message_id"]],
        )
        self.assertEqual(
            Counter(dict(self.settings.action_totals(CHAT["id"]))),
            Counter(captcha=1, captcha_passed=1, spam_signal=1, ai_signal=1, nsfw_signal=1, delete=1),
        )

    async def test_routed_flood_autoban_and_shadow_mode(self):
        sender = MEMBER | {"id": 199}
        await self.send("/protect flood on", sender)
        self.assertEqual(self.settings.option(CHAT["id"], "flood"), "off")
        await self.send("/protect flood on", ADMIN)
        await self.send("Repeated fixture", sender)
        await self.send("Repeated fixture", sender)
        third = await self.send("Repeated fixture", sender)
        self.assertEqual(
            [call.message_id for call in self.session.methods("deleteMessage")],
            [third["message_id"]],
        )

        await self.send("/protect flood off", ADMIN)
        await self.send("/profanity delete", ADMIN)
        await self.send("/protect shadow on", ADMIN)
        await self.send("/autoban on", ADMIN)
        for _ in range(3):
            await self.send("This is bullshit.", sender)
        bans = self.session.methods("banChatMember")
        self.assertEqual([call.user_id for call in bans], [sender["id"]])
        self.assertNotIn("забанен", self.session.methods("sendMessage")[-1].text)
        self.assertIn("autoban", dict(self.settings.action_totals(CHAT["id"])))
