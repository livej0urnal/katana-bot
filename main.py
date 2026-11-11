import asyncio
import logging
import os
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dotenv import load_dotenv

from moderation import contains_link

PROJECT_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)
router = Router()
group = F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP})
router.message.filter(group)
router.edited_message.filter(group)


class ChatSettings:
    def __init__(self, database: sqlite3.Connection) -> None:
        self.database = database
        with database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS link_filter (chat_id INTEGER PRIMARY KEY)"
            )

    def links_blocked(self, chat_id: int) -> bool:
        row = self.database.execute(
            "SELECT 1 FROM link_filter WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row is not None

    def set_links_blocked(self, chat_id: int, enabled: bool) -> None:
        with self.database:
            if enabled:
                self.database.execute(
                    "INSERT OR IGNORE INTO link_filter (chat_id) VALUES (?)",
                    (chat_id,),
                )
            else:
                self.database.execute(
                    "DELETE FROM link_filter WHERE chat_id = ?", (chat_id,)
                )

    def migrate(self, old_id: int, new_id: int) -> None:
        with self.database:
            self.database.execute(
                "INSERT OR IGNORE INTO link_filter (chat_id) "
                "SELECT ? FROM link_filter WHERE chat_id = ?",
                (new_id, old_id),
            )
            self.database.execute(
                "DELETE FROM link_filter WHERE chat_id = ?", (old_id,)
            )


async def is_admin(message: Message, bot: Bot) -> bool:
    if message.sender_chat is not None:
        return message.sender_chat.id == message.chat.id
    if message.from_user is None:
        return False

    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    return member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}


async def delete_message(message: Message, bot: Bot) -> bool:
    try:
        return await bot.delete_message(message.chat.id, message.message_id)
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning(
            "Deletion rejected in chat %s for message %s: %s",
            message.chat.id,
            message.message_id,
            error,
        )
        return False


@router.message(F.migrate_to_chat_id.is_not(None) | F.migrate_from_chat_id.is_not(None))
async def migrate_settings(message: Message, settings: ChatSettings) -> None:
    if message.migrate_to_chat_id is not None:
        settings.migrate(message.chat.id, message.migrate_to_chat_id)
    elif message.migrate_from_chat_id is not None:
        settings.migrate(message.migrate_from_chat_id, message.chat.id)


@router.message(Command("katana"))
async def show_help(message: Message, settings: ChatSettings) -> None:
    status = "включён" if settings.links_blocked(message.chat.id) else "выключен"
    await message.reply(
        f"Katana Bot. Фильтр ссылок {status}.\n"
        "/links on — включить фильтр ссылок\n"
        "/links off — выключить фильтр ссылок\n"
        "/del — удалить сообщение, ответив на него этой командой"
    )


@router.message(Command("links"))
async def change_link_filter(
    message: Message, command: CommandObject, bot: Bot, settings: ChatSettings
) -> None:
    if not await is_admin(message, bot):
        return

    option = (command.args or "").strip().lower()
    if option not in {"on", "off"}:
        await message.reply("Используйте /links on или /links off.")
        return

    enabled = option == "on"
    if enabled:
        member = await bot.get_chat_member(message.chat.id, bot.id)
        can_delete = member.status == ChatMemberStatus.CREATOR or (
            member.status == ChatMemberStatus.ADMINISTRATOR
            and (
                message.chat.type == ChatType.GROUP
                or getattr(member, "can_delete_messages", False)
            )
        )
        if not can_delete:
            await message.reply("Дайте боту право удалять сообщения в этой группе.")
            return

    settings.set_links_blocked(message.chat.id, enabled)
    await message.reply("Фильтр ссылок включён." if enabled else "Фильтр ссылок выключен.")


@router.message(Command("del"))
async def remove_by_reply(message: Message, bot: Bot) -> None:
    if not await is_admin(message, bot):
        return

    target = message.reply_to_message
    if target is None:
        await message.reply("Ответьте командой /del на сообщение, которое нужно удалить.")
    elif target.is_automatic_forward:
        await message.reply("Пересланный пост открывает ветку комментариев и защищён от удаления.")
    elif not await delete_message(target, bot):
        await message.reply("Telegram отклонил удаление. Проверьте права бота и возраст сообщения.")
    else:
        await delete_message(message, bot)


@router.message(F.text.is_not(None) | F.caption.is_not(None))
@router.edited_message(F.text.is_not(None) | F.caption.is_not(None))
async def filter_links(message: Message, bot: Bot, settings: ChatSettings) -> None:
    if message.is_automatic_forward or not settings.links_blocked(message.chat.id):
        return

    entities = (message.entities or []) + (message.caption_entities or [])
    if contains_link(entity.type for entity in entities) and not await is_admin(message, bot):
        await delete_message(message, bot)


async def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Укажите BOT_TOKEN в файле .env.")

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    database = sqlite3.connect(PROJECT_DIR / "katana.sqlite3")
    try:
        async with Bot(token) as bot:
            await dispatcher.start_polling(bot, settings=ChatSettings(database))
    finally:
        database.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
