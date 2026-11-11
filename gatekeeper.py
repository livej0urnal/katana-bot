import hashlib
import hmac
import logging
import secrets
import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery, ChatMemberUpdated, ChatPermissions,
    InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from features import TrafficWindow
from storage import ChatSettings


router = Router()
logger = logging.getLogger(__name__)
joins = TrafficWindow(60, 6)
GATE_SECONDS = 600


def signature(bot: Bot, chat_id: int, user_id: int, deadline: int, answer: int) -> str:
    data = f"{chat_id}:{user_id}:{deadline}:{answer}".encode()
    return hmac.new(bot.token.encode(), data, hashlib.sha256).hexdigest()[:20]


def is_member(member) -> bool:
    if member.status == ChatMemberStatus.RESTRICTED:
        return member.is_member
    return member.status in {
        ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER
    }


@router.chat_member(F.chat.type == ChatType.SUPERGROUP)
async def on_join(event: ChatMemberUpdated, bot: Bot, settings: ChatSettings, ai_analysis=None):
    if is_member(event.old_chat_member) or event.new_chat_member.status != ChatMemberStatus.MEMBER:
        return
    chat_id = event.chat.id
    user = event.new_chat_member.user
    if user.is_bot or str(user.id) in settings.allowlist(chat_id, "user"):
        return
    now = int(time.time())
    if settings.option(chat_id, "newspam") != "off":
        settings.set_option(chat_id, f"joined:{user.id}", str(now))
    profile_mode = settings.option(chat_id, "profile")
    if ai_analysis is not None and profile_mode != "off":
        await scan_profile(bot, settings, ai_analysis, chat_id, user.id, profile_mode)
    raid_enabled = settings.option(chat_id, "raid") == "on"
    if raid_enabled and joins.record(chat_id, user.id):
        settings.set_option(chat_id, "raid_until", str(now + 300))
    in_raid = raid_enabled and int(settings.option(chat_id, "raid_until", "0")) > now
    if not in_raid and settings.option(chat_id, "gate") != "on":
        return

    deadline = now + GATE_SECONDS
    first, second = (secrets.randbelow(9) + 1 for _ in range(2))
    answer = first + second
    choices = [answer, answer + 1, answer - 1]
    secrets.SystemRandom().shuffle(choices)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=str(choice),
            callback_data=f"gate:{user.id}:{deadline}:{choice}:{signature(bot, chat_id, user.id, deadline, answer)}",
        )
        for choice in choices
    ]])
    try:
        if not await bot.restrict_chat_member(
            chat_id, user.id,
            permissions=ChatPermissions(can_send_messages=False),
            use_independent_chat_permissions=True,
            until_date=deadline,
        ):
            return
        await bot.send_message(
            chat_id, f"{user.first_name}, добро пожаловать! Сколько будет {first} + {second}?",
            reply_markup=keyboard,
        )
        settings.audit(chat_id, "captcha", user.id)
    except TelegramAPIError as error:
        logger.warning("Could not start verification in chat %s: %s", chat_id, error)


@router.callback_query(F.data.startswith("gate:"))
async def verify_join(callback: CallbackQuery, bot: Bot, settings: ChatSettings):
    if not isinstance(callback.message, Message) or not callback.data:
        await callback.answer("Проверка недоступна.")
        return
    try:
        _, user_id, deadline, choice, digest = callback.data.split(":")
        user_id, deadline, choice = int(user_id), int(deadline), int(choice)
    except ValueError:
        await callback.answer("Некорректная проверка.")
        return
    chat_id = callback.message.chat.id
    if callback.from_user.id != user_id or deadline <= time.time():
        await callback.answer("Эта проверка недоступна для вас.")
        return
    if not hmac.compare_digest(signature(bot, chat_id, user_id, deadline, choice), digest):
        await callback.answer("Неверный ответ.", show_alert=True)
        return
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if (
            member.status != ChatMemberStatus.RESTRICTED or not member.is_member
            or member.until_date is None or int(member.until_date.timestamp()) != deadline
        ):
            await callback.answer("Ограничение уже изменилось.")
            return
        permissions = ChatPermissions(**{name: True for name in ChatPermissions.model_fields})
        if not await bot.restrict_chat_member(
            chat_id, user_id, permissions=permissions, use_independent_chat_permissions=True
        ):
            await callback.answer("Telegram не подтвердил проверку.")
            return
    except TelegramAPIError:
        await callback.answer("Проверка временно недоступна.")
        return
    settings.audit(chat_id, "captcha_passed", user_id)
    await callback.answer("Проверка пройдена.")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramAPIError:
        logger.info("Could not remove verification buttons in chat %s", chat_id)


async def scan_profile(bot, settings, ai_analysis, chat_id, user_id, mode):
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if not photos.photos or await ai_analysis.image_label(bot, photos.photos[0][-1]) != "nsfw":
            return
        settings.audit(chat_id, "profile_signal", user_id)
        if mode == "delete":
            member = await bot.get_chat_member(chat_id, user_id)
            if member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}:
                return
            for message_id in ai_analysis.recent_ids(chat_id, user_id):
                try:
                    if await bot.delete_message(chat_id, message_id):
                        settings.audit(chat_id, "delete", user_id, detail=f"profile · {message_id}")
                except TelegramAPIError:
                    logger.info("Could not remove message %s after profile scan", message_id)
    except TelegramAPIError:
        logger.info("Profile photo was unavailable in chat %s", chat_id)
