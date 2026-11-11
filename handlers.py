import asyncio
import logging
import os
import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import (
    CallbackQuery, ChatMemberUpdated, ChatPermissions,
    InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from features import TrafficWindow, domain_of, has_untrusted_link, record_traffic
from moderation import contains_link, contains_profanity
from panel_auth import sign
from spam_model import spam_probability
from storage import ChatSettings


logger = logging.getLogger(__name__)
AUTOBAN_THRESHOLD = 3
router = Router()
group_router = Router()
router.include_router(group_router)
flood_window = TrafficWindow(10, 6)
spam_burst = TrafficWindow(60, 6)
pending_autobans: set[tuple[int, int]] = set()
group = F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP})
group_router.message.filter(group)
group_router.edited_message.filter(group)


@router.message(F.chat.type == ChatType.PRIVATE, Command("start", "help"))
async def private_help(message: Message) -> None:
    await message.answer(
        "Katana Moderator защищает группы и комментарии к каналам.\n"
        "Добавьте бота администратором в группу и отправьте там /settings.\n"
        "Чтобы получить жалобы в этот чат, отправьте /reports в группе, которой управляете."
    )


@router.my_chat_member(group)
async def register_membership(event: ChatMemberUpdated, settings: ChatSettings) -> None:
    chat = event.chat
    settings.register_group(chat.id, chat.title or str(chat.id), chat.type, event.new_chat_member.status)


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


@group_router.message(Command("katana", "start", "help"))
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
        "\n/settings — настройки группы и права бота"
        "\n/protect, /allow, /warn, /mute, /unmute, /tempban, /audit, /stats, /announce"
        "\n/thanks — поблагодарить автора ответом; /rep — проверить репутацию"
    )


def member_can_delete(member, chat_type: ChatType) -> bool:
    return member.status == ChatMemberStatus.CREATOR or (
        member.status == ChatMemberStatus.ADMINISTRATOR
        and (
            chat_type == ChatType.GROUP
            or getattr(member, "can_delete_messages", False)
        )
    )


async def can_delete_messages(message: Message, bot: Bot) -> bool:
    member = await bot.get_chat_member(message.chat.id, bot.id)
    return member_can_delete(member, message.chat.type)


@group_router.message(Command("settings"))
async def show_settings(message: Message, bot: Bot, settings: ChatSettings) -> None:
    if not await is_admin(message, bot):
        return

    chat = message.chat
    member = await bot.get_chat_member(chat.id, bot.id)
    settings.register_group(chat.id, chat.title or str(chat.id), chat.type, member.status)
    admin = member.status == ChatMemberStatus.ADMINISTRATOR
    can_delete = member_can_delete(member, chat.type)
    can_restrict = admin and getattr(member, "can_restrict_members", False)

    await message.reply(
        f"Настройки группы «{chat.title or chat.id}»\n"
        f"Бот администратор: {'да' if admin else 'нет'}\n"
        f"Удаление сообщений: {'да' if can_delete else 'нет'}\n"
        f"Блокировка участников: {'да' if can_restrict else 'нет'}\n"
        f"Фильтр ссылок: {'включён' if settings.links_blocked(chat.id) else 'выключен'}\n"
        f"Фильтр мата: {settings.profanity_mode(chat.id)}\n"
        f"Антифлуд: {settings.option(chat.id, 'flood')}\n"
        f"Проверка новичков: {settings.option(chat.id, 'gate')}\n"
        f"Антирейд: {settings.option(chat.id, 'raid')}\n"
        f"Комментарии: {settings.option(chat.id, 'comments', 'inherit')}\n"
        f"Автобан: {'включён' if settings.autoban_enabled(chat.id) else 'выключен'}",
        reply_markup=protection_keyboard(chat.id, settings),
    )


def protection_keyboard(chat_id: int, settings: ChatSettings) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"{label} · {'вкл' if settings.option(chat_id, option) == 'on' else 'выкл'}",
            callback_data=f"protect_toggle:{option}",
        )
        for option, label in (("flood", "Антифлуд"), ("shadow", "Shadow Mode"))
    ]])


@router.callback_query(F.data.in_({"protect_toggle:flood", "protect_toggle:shadow"}))
async def toggle_protection(callback: CallbackQuery, bot: Bot, settings: ChatSettings):
    message = callback.message
    if not isinstance(message, Message) or not callback.data:
        await callback.answer("Меню недоступно.")
        return
    chat_id = message.chat.id
    member = await bot.get_chat_member(chat_id, callback.from_user.id)
    if member.status not in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}:
        await callback.answer("Требуются права администратора.", show_alert=True)
        return
    option = callback.data.split(":")[1]
    enabled = settings.option(chat_id, option) != "on"
    if option == "flood" and enabled and not await can_delete_messages(message, bot):
        await callback.answer("Боту нужно право удалять сообщения.", show_alert=True)
        return
    settings.set_option(chat_id, option, "on" if enabled else "off")
    await callback.answer("Настройки обновлены.")
    try:
        await message.edit_reply_markup(reply_markup=protection_keyboard(chat_id, settings))
    except TelegramAPIError:
        logger.info("Could not refresh settings buttons in chat %s", chat_id)


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
async def remove_by_reply(message: Message, bot: Bot, settings: ChatSettings | None = None) -> None:
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
        if settings is not None:
            settings.audit(message.chat.id, "manual_delete", getattr(target.from_user, "id", None), detail=str(target.message_id))
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
        case_id = settings.audit(message.chat.id, "ban", user_id)
        await message.reply(
            f"Пользователь {user_id} забанен. Разбан: /unban {user_id}. "
            f"Обжалование в личке: /appeal {message.chat.id} {case_id} ПРИЧИНА"
        )
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
    settings.audit(message.chat.id, "unban", user_id)
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


async def moderate_message(
    message: Message, bot: Bot, settings: ChatSettings, event_update=None, ai_analysis=None, redis_traffic=None
) -> None:
    chat_id = message.chat.id
    if message.is_automatic_forward:
        settings.set_option(chat_id, f"discussion_thread:{message.message_id}", "on")
        return

    user_id = getattr(message.from_user, "id", None) if message.sender_chat is None else None
    text = message.text or message.caption or ""
    thread_id = getattr(message, "message_thread_id", None)
    parent = getattr(message, "reply_to_message", None)
    direct_comment = bool(getattr(parent, "is_automatic_forward", False))
    is_comment = getattr(message, "is_topic_message", False) is not True and bool(
        direct_comment or (
            thread_id and settings.option(chat_id, f"discussion_thread:{thread_id}") == "on"
        )
    )
    if direct_comment and thread_id:
        settings.set_option(chat_id, f"discussion_thread:{thread_id}", "on")
    comment_mode = settings.option(chat_id, "comments", "inherit") if is_comment else "inherit"
    if comment_mode == "off":
        return

    mode = settings.profanity_mode(chat_id)
    has_profanity = mode != "off" and contains_profanity(text)
    entities = (message.entities or []) + (message.caption_entities or [])
    has_blocked_link = False
    if (settings.links_blocked(chat_id) or comment_mode == "strict") and contains_link(
        entity.type for entity in entities
    ):
        domains = settings.allowlist(chat_id, "domain")
        has_blocked_link = not domains or has_untrusted_link(text, entities, domains)

    flood_enabled = settings.option(chat_id, "flood") == "on"
    check_flood = flood_enabled and user_id is not None and (
        event_update is None or event_update.edited_message is None
    )
    spam_mode = settings.option(chat_id, "newspam")
    recent_join = settings.option(chat_id, f"joined:{user_id}", "0") if spam_mode != "off" and user_id else "0"
    suspected_spam = int(recent_join) + 86400 > int(time.time()) and spam_probability(text) >= 0.9
    ai_mode = settings.option(chat_id, "ai")
    image_mode = settings.option(chat_id, "nsfw")
    check_ai = ai_analysis is not None and ai_mode != "off" and bool(text)
    photos = getattr(message, "photo", None)
    check_image = ai_analysis is not None and image_mode != "off" and bool(photos)
    if ai_analysis is not None and user_id is not None and settings.option(chat_id, "profile") != "off":
        ai_analysis.record_message(chat_id, user_id, message.message_id)
    if not (has_profanity or has_blocked_link or check_flood or suspected_spam or check_ai or check_image):
        return
    if user_id is not None and str(user_id) in settings.allowlist(chat_id, "user"):
        return
    flooding = check_flood and await record_traffic(
        redis_traffic, "flood", flood_window, (chat_id, user_id), message.message_id, text
    )
    if not (has_profanity or has_blocked_link or flooding or suspected_spam or check_ai or check_image) or await is_admin(message, bot):
        return
    if has_profanity:
        logger.info("Profanity detected in chat %s, message %s (%s)", chat_id, message.message_id, mode)
    if suspected_spam:
        settings.audit(chat_id, "spam_signal", user_id, detail=str(message.message_id))

    reason = None
    if has_profanity and mode == "delete":
        reason = "profanity"
    elif has_blocked_link:
        reason = "link"
    elif flooding:
        reason = "flood"
    elif suspected_spam and spam_mode == "delete":
        reason = "new_user_spam"
    if not reason and check_ai:
        label = await ai_analysis.text_label(chat_id, getattr(message, "message_thread_id", None), text)
        if label and label != "ok":
            settings.audit(chat_id, "ai_signal", user_id, detail=f"{label} · {message.message_id}")
            if ai_mode == "delete":
                reason = label
    if not reason and check_image:
        if await ai_analysis.image_label(bot, photos[-1]) == "nsfw":
            settings.audit(chat_id, "nsfw_signal", user_id, detail=str(message.message_id))
            if image_mode == "delete":
                reason = "nsfw"
    if reason and await delete_message(message, bot):
        settings.audit(chat_id, "delete", user_id, detail=f"{reason} · {message.message_id}")
        if (
            reason in {"new_user_spam", "spam", "scam", "advertising"}
            and settings.option(chat_id, "raid") == "on"
            and await record_traffic(redis_traffic, "spam", spam_burst, chat_id, message.message_id)
        ):
            settings.set_option(chat_id, "raid_until", str(int(time.time()) + 300))
        if settings.option(chat_id, "strike") == "on" and user_id is not None:
            await apply_strike(bot, settings, chat_id, user_id)
        elif reason == "profanity" and settings.autoban_enabled(chat_id):
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
        case_id = settings.audit(chat_id, "autoban", user.id)
        if settings.option(chat_id, "shadow") == "on":
            return
        await bot.send_message(
            chat_id,
            f"Пользователь {user.id} забанен. Разбан: /unban {user.id}. "
            f"Обжалование в личке: /appeal {chat_id} {case_id} ПРИЧИНА",
        )
    finally:
        pending_autobans.discard(key)


@group_router.message(Command("protect"))
async def change_protection(
    message: Message, command: CommandObject, bot: Bot, settings: ChatSettings, ai_analysis=None
):
    if not await is_admin(message, bot):
        return
    args = (command.args or "").split()
    modes = {
        "flood": {"on", "off"}, "gate": {"on", "off"}, "raid": {"on", "off"},
        "strike": {"on", "off"}, "shadow": {"on", "off"}, "comments": {"inherit", "strict", "off"},
        **dict.fromkeys(("newspam", "ai", "nsfw", "profile"), {"log", "delete", "off"}),
    }
    if len(args) != 2 or args[1] not in modes.get(args[0], set()):
        await message.reply(
            "/protect flood|gate|raid|shadow|strike on|off\n"
            "/protect comments inherit|strict|off\n"
            "/protect newspam|ai|nsfw|profile log|delete|off"
        )
        return
    name, value = args
    if ((name == "flood" and value == "on") or (name == "comments" and value == "strict")) and not await can_delete_messages(message, bot):
        await message.reply("Дайте боту право удалять сообщения.")
        return
    if value != "off" and name in {"ai", "nsfw", "profile"}:
        model = None if ai_analysis is None else (
            ai_analysis.text_model if name == "ai" else ai_analysis.vision_model
        )
        if not model:
            await message.reply("Сначала настройте соответствующую модель Ollama.")
            return
    if value == "delete" and not await can_delete_messages(message, bot):
        await message.reply("Дайте боту право удалять сообщения.")
        return
    if name in {"gate", "raid", "strike"} and value == "on":
        member = await bot.get_chat_member(message.chat.id, bot.id)
        if message.chat.type != ChatType.SUPERGROUP or not getattr(member, "can_restrict_members", False):
            await message.reply("Для проверки участников нужны супергруппа и право блокировки.")
            return
    settings.set_option(message.chat.id, name, value)
    await message.reply(f"{name}: {value}")


@group_router.message(Command("allow"))
async def edit_allowlist(message: Message, command: CommandObject, bot: Bot, settings: ChatSettings):
    if not await is_admin(message, bot):
        return
    args = (command.args or "").split()
    if len(args) == 3 and args[0] in {"add", "remove"}:
        operation, kind, value = args
        if kind == "domain":
            value = domain_of(value)
        elif kind != "user" or not value.isascii() or not value.isdecimal() or len(value) > 16:
            value = None
        if value:
            settings.set_allowed(message.chat.id, kind, value, operation == "add")
            await message.reply("Белый список обновлён.")
            return
    await message.reply("/allow add|remove domain example.com\n/allow add|remove user ID")


@group_router.message(Command("warn", "mute", "unmute", "tempban"))
async def apply_sanction(message: Message, command: CommandObject, bot: Bot, settings: ChatSettings):
    if not await is_admin(message, bot):
        return
    target = message.reply_to_message
    if target is None or target.sender_chat is not None or target.from_user is None:
        await message.reply("Ответьте на сообщение участника командой наказания.")
        return
    if target.from_user.id == bot.id or await is_admin(target, bot):
        await message.reply("Этот участник защищён от наказания.")
        return
    chat_id = message.chat.id
    user_id = target.from_user.id
    action = command.command
    detail = (command.args or "").strip()
    if action != "warn":
        if message.chat.type != ChatType.SUPERGROUP:
            await message.reply("Ограничения доступны в супергруппах.")
            return
        duration, _, reason = detail.partition(" ")
        if action != "unmute" and (
            not duration.isascii() or not duration.isdecimal() or len(duration) > 6
            or not 1 <= int(duration) <= 525600
        ):
            await message.reply(f"/{action} МИНУТЫ [причина]")
            return
        try:
            if action == "tempban":
                applied = await bot.ban_chat_member(chat_id, user_id, until_date=int(time.time()) + int(duration) * 60)
            else:
                applied = await bot.restrict_chat_member(
                    chat_id, user_id,
                    permissions=unrestricted_permissions() if action == "unmute" else ChatPermissions(can_send_messages=False),
                    use_independent_chat_permissions=True,
                    until_date=None if action == "unmute" else int(time.time()) + int(duration) * 60,
                )
        except (TelegramBadRequest, TelegramForbiddenError):
            applied = False
        if not applied:
            await message.reply("Telegram отклонил действие. Проверьте права бота.")
            return
        if action == "unmute":
            detail = ""
        else:
            detail = f"{duration} мин. {reason}".strip()
    actor_id = message.from_user.id if message.from_user and message.sender_chat is None else None
    number = settings.audit(chat_id, action, user_id, actor_id, detail)
    await message.reply(f"Действие {action} записано под №{number}. Пользователь {user_id}.")
    if action == "warn" and settings.option(chat_id, "strike") == "on":
        await apply_strike(bot, settings, chat_id, user_id)


def unrestricted_permissions() -> ChatPermissions:
    return ChatPermissions(**{name: True for name in ChatPermissions.model_fields})


@group_router.message(Command("audit", "stats"))
async def audit_report(message: Message, command: CommandObject, bot: Bot, settings: ChatSettings):
    if message.from_user is None or message.sender_chat is not None:
        await message.reply("Отправьте команду от своего аккаунта.")
        return
    if not await is_admin(message, bot):
        return
    if command.command == "stats":
        rows = settings.action_totals(message.chat.id)
        body = "\n".join(f"{action}: {count}" for action, count in rows)
    else:
        rows = settings.recent_actions(message.chat.id)
        body = "\n".join(
            f"#{number} · {action} · участник {user or '—'} · модератор {actor or '—'}\n{detail[:150]}"
            for number, action, user, actor, detail in rows
        )
    try:
        await bot.send_message(message.from_user.id, body or "Журнал пока пуст.")
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.reply("Нажмите Start в личном чате с ботом и повторите команду.")
    else:
        await message.reply("Отправил данные в личку.")


@group_router.message(Command("thanks", "rep"))
async def reputation_command(message: Message, command: CommandObject, settings: ChatSettings):
    voter = message.from_user
    if voter is None or voter.is_bot or message.sender_chat is not None:
        return
    target = message.reply_to_message
    if command.command == "rep":
        user = target.from_user if target and target.sender_chat is None else voter
        if user is not None:
            await message.reply(f"Репутация участника: {settings.reputation(message.chat.id, user.id)}.")
        return
    if target is None or target.sender_chat is not None or target.from_user is None:
        await message.reply("Ответьте командой /thanks на полезное сообщение.")
        return
    user = target.from_user
    if user.is_bot or user.id == voter.id:
        return
    if settings.vote(message.chat.id, voter.id, user.id):
        await message.reply("Добавлен 1 балл репутации.")
    else:
        await message.reply("Сегодня вы уже поблагодарили этого участника.")


@group_router.message(Command("announce"))
async def configure_announcement(message: Message, command: CommandObject, bot: Bot, settings: ChatSettings):
    if not await is_admin(message, bot):
        return
    minutes, _, body = (command.args or "").strip().partition(" ")
    if minutes == "off":
        settings.clear_announcement(message.chat.id)
        await message.reply("Объявление выключено.")
    elif minutes.isascii() and minutes.isdecimal() and len(minutes) <= 5 and 1 <= int(minutes) <= 10080 and 1 <= len(body.strip()) <= 2000:
        settings.set_announcement(message.chat.id, body.strip(), int(minutes) * 60)
        await message.reply(f"Объявление будет публиковаться каждые {minutes} мин.")
    else:
        await message.reply("/announce МИНУТЫ ТЕКСТ (1–10080 минут) или /announce off")


async def announcement_worker(bot: Bot, settings: ChatSettings):
    while True:
        now = int(time.time())
        for chat_id, body, interval, next_at in settings.due_announcements(now):
            if not settings.advance_announcement(chat_id, next_at, now + interval):
                continue
            try:
                await bot.send_message(chat_id, body)
            except TelegramAPIError as error:
                logger.warning("Announcement in chat %s failed: %s", chat_id, error)
        await asyncio.sleep(5)


@router.message(F.chat.type == ChatType.PRIVATE, Command("appeal"))
async def appeal_command(message: Message, command: CommandObject, settings: ChatSettings):
    args = (command.args or "").split(maxsplit=2)
    if (
        len(args) != 3 or not args[0].startswith("-") or not args[0][1:].isascii()
        or not args[0][1:].isdecimal() or not args[1].isascii() or not args[1].isdecimal()
        or len(args[0]) > 17 or len(args[1]) > 16 or len(args[2]) > 500
        or message.from_user is None
    ):
        await message.answer("/appeal ID_ГРУППЫ НОМЕР_ДЕЙСТВИЯ ПРИЧИНА")
        return
    received = settings.submit_appeal(int(args[0]), message.from_user.id, int(args[1]), args[2])
    await message.answer("Обжалование записано для администраторов." if received else "Действие не найдено или уже обжаловано.")


async def apply_strike(bot, settings, chat_id, user_id):
    count = settings.strike_count(chat_id, user_id)
    if count not in {3, 5, 10}:
        return
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status not in {ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED}:
            return
        if count == 10:
            applied = await ban_user(bot, chat_id, user_id)
            action = "strike_ban"
        else:
            duration = 3600 if count == 3 else 86400
            until_date = int(time.time()) + duration
            if member.status == ChatMemberStatus.RESTRICTED and (
                member.until_date is None or member.until_date.timestamp() == 0
                or member.until_date.timestamp() >= until_date
            ):
                return
            applied = await bot.restrict_chat_member(
                chat_id, user_id, permissions=ChatPermissions(can_send_messages=False),
                use_independent_chat_permissions=True, until_date=until_date,
            )
            action = "strike_mute"
        if not applied:
            return
        case_id = settings.audit(chat_id, action, user_id, detail=f"{count} points")
        if settings.option(chat_id, "shadow") != "on":
            await bot.send_message(
                chat_id, f"Katana Strike: {user_id} · {count} баллов · действие №{case_id}. "
                f"Обжалование в личке: /appeal {chat_id} {case_id} ПРИЧИНА",
            )
    except TelegramAPIError as error:
        logger.warning("Strike action in chat %s failed: %s", chat_id, error)


@group_router.message(Command("panel"))
async def open_panel(message: Message, bot: Bot):
    secret = os.getenv("DASHBOARD_SESSION_SECRET", "")
    if not secret or not message.from_user or message.sender_chat is not None:
        return
    if not await is_admin(message, bot):
        return
    user_id = message.from_user.id
    token = sign(secret, "login", user_id, message.chat.id, 300)
    try:
        await bot.send_message(user_id, f"Код входа в панель Katana (5 минут):\n{token}")
    except TelegramAPIError:
        await message.reply("Нажмите Start в личном чате с ботом и повторите /panel.")
    else:
        await message.reply("Код входа отправлен в личный чат.")


group_router.message.register(moderate_message)
group_router.edited_message.register(moderate_message)
