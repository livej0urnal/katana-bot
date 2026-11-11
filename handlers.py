import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message

from moderation import contains_link, contains_profanity
from storage import ChatSettings


logger = logging.getLogger(__name__)
AUTOBAN_THRESHOLD = 3
router = Router()
group_router = Router()
router.include_router(group_router)
pending_autobans: set[tuple[int, int]] = set()
group = F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP})
group_router.message.filter(group)
group_router.edited_message.filter(group)


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


@group_router.message(F.migrate_to_chat_id.is_not(None) | F.migrate_from_chat_id.is_not(None))
async def migrate_settings(message: Message, settings: ChatSettings) -> None:
    if message.migrate_to_chat_id is not None:
        settings.migrate(message.chat.id, message.migrate_to_chat_id)
    elif message.migrate_from_chat_id is not None:
        settings.migrate(message.migrate_from_chat_id, message.chat.id)


@group_router.message(Command("katana"))
async def show_help(message: Message, settings: ChatSettings) -> None:
    status = "включён" if settings.links_blocked(message.chat.id) else "выключен"
    await message.reply(
        f"Katana Bot. Фильтр ссылок {status}.\n"
        f"Фильтр мата: {settings.profanity_mode(message.chat.id)}.\n"
        "/links on — включить фильтр ссылок\n"
        "/links off — выключить фильтр ссылок\n"
        "/del — удалить сообщение, ответив на него этой командой\n"
        "/report — пожаловаться на сообщение ответом\n"
        "/reports — получить последние жалобы в личку (для администраторов)\n"
        "/profanity log|delete|off — режим фильтра мата\n"
        "/ban — забанить автора сообщения ответом\n"
        "/unban ID — разрешить пользователю снова вступить\n"
        f"/autoban on|off — автобан ({'включён' if settings.autoban_enabled(message.chat.id) else 'выключен'})"
    )


async def can_delete_messages(message: Message, bot: Bot) -> bool:
    member = await bot.get_chat_member(message.chat.id, bot.id)
    return member.status == ChatMemberStatus.CREATOR or (
        member.status == ChatMemberStatus.ADMINISTRATOR
        and (
            message.chat.type == ChatType.GROUP
            or getattr(member, "can_delete_messages", False)
        )
    )


@group_router.message(Command("profanity"))
async def change_profanity_mode(
    message: Message, command: CommandObject, bot: Bot, settings: ChatSettings
) -> None:
    if not await is_admin(message, bot):
        return

    mode = (command.args or "").strip().lower()
    if mode not in {"log", "delete", "off"}:
        await message.reply("Используйте /profanity log, /profanity delete или /profanity off.")
        return
    if mode == "delete" and not await can_delete_messages(message, bot):
        await message.reply("Дайте боту право удалять сообщения в этой группе.")
        return

    settings.set_profanity_mode(message.chat.id, mode)
    await message.reply(f"Режим фильтра мата: {mode}.")


@group_router.message(Command("links"))
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
    if enabled and not await can_delete_messages(message, bot):
        await message.reply("Дайте боту право удалять сообщения в этой группе.")
        return

    settings.set_links_blocked(message.chat.id, enabled)
    await message.reply("Фильтр ссылок включён." if enabled else "Фильтр ссылок выключен.")


@group_router.message(Command("del"))
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


async def ban_user(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        return await bot.ban_chat_member(chat_id, user_id)
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning("Ban rejected in chat %s for user %s: %s", chat_id, user_id, error)
        return False


@group_router.message(Command("ban"))
async def ban_by_reply(message: Message, bot: Bot, settings: ChatSettings) -> None:
    if not await is_admin(message, bot):
        return
    if message.chat.type != ChatType.SUPERGROUP:
        await message.reply("Баны доступны в супергруппах.")
        return

    target = message.reply_to_message
    if target is None or target.sender_chat is not None or target.from_user is None:
        await message.reply("Ответьте командой /ban на сообщение пользователя.")
        return
    if await is_admin(target, bot) or target.from_user.id == bot.id:
        await message.reply("Этот участник защищён от бана.")
        return

    user_id = target.from_user.id
    if await ban_user(bot, message.chat.id, user_id):
        settings.clear_profanity_strikes(message.chat.id, user_id)
        await message.reply(f"Пользователь {user_id} забанен. Для разбана используйте /unban {user_id}.")
    else:
        await message.reply("Telegram отклонил бан. Проверьте право блокировать участников.")


@group_router.message(Command("unban"))
async def unban_member(
    message: Message, command: CommandObject, bot: Bot, settings: ChatSettings
) -> None:
    if not await is_admin(message, bot):
        return
    if message.chat.type != ChatType.SUPERGROUP:
        await message.reply("Разбан доступен в супергруппах.")
        return

    user_id_text = (command.args or "").strip()
    if not user_id_text.isascii() or not user_id_text.isdecimal() or len(user_id_text) > 16:
        await message.reply("Используйте /unban ID, где ID указан в уведомлении о бане.")
        return
    user_id = int(user_id_text)
    try:
        if not await bot.unban_chat_member(message.chat.id, user_id, only_if_banned=True):
            await message.reply("Telegram не подтвердил разбан.")
            return
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.reply("Telegram отклонил разбан. Проверьте ID и права бота.")
        return

    settings.clear_profanity_strikes(message.chat.id, user_id)
    await message.reply(f"Пользователь {user_id} может снова вступить в группу.")


@group_router.message(Command("autoban"))
async def change_autoban(
    message: Message, command: CommandObject, bot: Bot, settings: ChatSettings
) -> None:
    if not await is_admin(message, bot):
        return
    option = (command.args or "").strip().lower()
    if option not in {"on", "off"}:
        await message.reply("Используйте /autoban on или /autoban off.")
        return

    chat_id = message.chat.id
    enabled = option == "on"
    if enabled:
        if message.chat.type != ChatType.SUPERGROUP or settings.profanity_mode(chat_id) != "delete":
            await message.reply("Автобан требует супергруппу и режим /profanity delete.")
            return
        member = await bot.get_chat_member(chat_id, bot.id)
        if not getattr(member, "can_restrict_members", False):
            await message.reply("Дайте боту право блокировать участников.")
            return

    settings.set_autoban(chat_id, enabled)
    await message.reply(
        "Автобан включён: три удаления за мат за 24 часа."
        if enabled else "Автобан выключен, счётчики очищены."
    )


@group_router.message(Command("report"))
async def report_message(message: Message, settings: ChatSettings) -> None:
    reporter = message.from_user
    if reporter is None or reporter.is_bot or message.sender_chat is not None:
        await message.reply("Отправьте жалобу от своего аккаунта.")
        return

    target = message.reply_to_message
    if target is None:
        await message.reply("Ответьте командой /report на сообщение или комментарий.")
        return

    excerpt = " ".join((target.text or target.caption or "[вложение]").split())[:120]
    created = settings.report(message.chat.id, target.message_id, reporter.id, excerpt)
    await message.reply("Жалоба принята." if created else "Вы уже пожаловались на это сообщение.")


@group_router.message(Command("reports"))
async def show_reports(message: Message, bot: Bot, settings: ChatSettings) -> None:
    if message.sender_chat is not None or message.from_user is None:
        await message.reply("Чтобы получить жалобы в личку, отправьте /reports от своего аккаунта.")
        return
    if not await is_admin(message, bot):
        return

    lines = ["Последние жалобы:"]
    for message_id, count, excerpt in settings.recent_reports(message.chat.id):
        reference = f"Сообщение {message_id}"
        if message.chat.type == ChatType.SUPERGROUP:
            internal_id = str(message.chat.id).removeprefix("-100")
            reference = f"https://t.me/c/{internal_id}/{message_id}"
        lines.append(f"{reference} · жалоб: {count}\n{excerpt}")

    try:
        await bot.send_message(
            message.from_user.id,
            "\n\n".join(lines) if len(lines) > 1 else "В этой группе жалоб пока нет.",
            link_preview_options={"is_disabled": True},
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        await message.reply("Откройте личный чат с ботом, нажмите Start и повторите /reports.")
    else:
        await message.reply("Отправил список жалоб в личку.")


@group_router.message(F.text.is_not(None) | F.caption.is_not(None))
@group_router.edited_message(F.text.is_not(None) | F.caption.is_not(None))
async def moderate_message(message: Message, bot: Bot, settings: ChatSettings) -> None:
    if message.is_automatic_forward:
        return

    chat_id = message.chat.id
    mode = settings.profanity_mode(chat_id)
    has_profanity = mode != "off" and contains_profanity(message.text or message.caption or "")
    entities = (message.entities or []) + (message.caption_entities or [])
    has_blocked_link = settings.links_blocked(chat_id) and contains_link(
        entity.type for entity in entities
    )
    if not (has_profanity or has_blocked_link):
        return
    if await is_admin(message, bot):
        return
    if has_profanity:
        logger.info("Profanity detected in chat %s, message %s (%s)", chat_id, message.message_id, mode)
    if has_blocked_link or (has_profanity and mode == "delete"):
        deleted = await delete_message(message, bot)
        if deleted and has_profanity and mode == "delete" and settings.autoban_enabled(chat_id):
            await enforce_autoban(message, bot, settings)


async def enforce_autoban(message: Message, bot: Bot, settings: ChatSettings) -> None:
    user = message.from_user
    if message.sender_chat is not None or user is None or user.is_bot:
        return

    chat_id = message.chat.id
    key = (chat_id, user.id)
    count = settings.add_profanity_strike(chat_id, user.id, message.message_id)
    if count < AUTOBAN_THRESHOLD or key in pending_autobans:
        return

    pending_autobans.add(key)
    try:
        if not await ban_user(bot, chat_id, user.id):
            return
        settings.clear_profanity_strikes(chat_id, user.id)
        await bot.send_message(
            chat_id,
            f"Пользователь {user.id} забанен за повторные нарушения. Разбан: /unban {user.id}.",
        )
    finally:
        pending_autobans.discard(key)
