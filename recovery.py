import json
import logging
from datetime import datetime
from time import time

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import MessageEntity, ReplyParameters


logger = logging.getLogger(__name__)
DELETE_WINDOW = 48 * 60 * 60
RETENTION = 7 * 24 * 60 * 60
MEDIA_FIELDS = ("animation", "audio", "document", "photo", "video", "video_note", "voice", "sticker")


def snapshot(message):
    text = message.text
    if text:
        fields = {"text": text, "entities": message.entities}
        method = "message"
    else:
        for method in MEDIA_FIELDS:
            media = getattr(message, method, None)
            if media:
                media = media[-1] if method == "photo" else media
                fields = {method: media.file_id}
                if method not in {"sticker", "video_note"}:
                    fields.update(caption=message.caption, caption_entities=message.caption_entities)
                break
        else:
            return None

    thread = getattr(message, "message_thread_id", None)
    is_topic = getattr(message, "is_topic_message", False)
    parent = getattr(message, "reply_to_message", None)
    return json.dumps({
        "method": method,
        "fields": {
            key: [item.model_dump(mode="json", exclude_none=True) for item in value]
            if isinstance(value, list) else value
            for key, value in fields.items() if value is not None
        },
        "thread": thread if is_topic else None,
        "reply": None if is_topic else thread or getattr(parent, "message_id", None),
    }, ensure_ascii=False)


def sent_at(message) -> int:
    date = getattr(message, "date", None)
    return int(date.timestamp()) if isinstance(date, datetime) else int(time())


async def remove_saved(bot, settings, chat_id, message_id, payload, date, *, flagged_only=False):
    if payload is None or date <= int(time()) - DELETE_WINDOW:
        return False
    if not settings.stage_removal(chat_id, message_id, payload, date, flagged_only=flagged_only):
        return False
    try:
        deleted = await bot.delete_message(chat_id, message_id)
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.info("Deletion rejected for chat %s, message %s: %s", chat_id, message_id, error)
        deleted = False
    settings.finish_removal(chat_id, message_id, deleted)
    return deleted


async def repost(bot, chat_id: int, payload: str):
    saved = json.loads(payload)
    fields = saved["fields"]
    for key in ("entities", "caption_entities"):
        if key in fields:
            fields[key] = [MessageEntity.model_validate(entity) for entity in fields[key]]
    if saved["method"] not in {"sticker", "video_note"}:
        fields["parse_mode"] = None
    if saved["thread"] is not None:
        fields["message_thread_id"] = saved["thread"]
    if saved.get("reply"):
        fields["reply_parameters"] = ReplyParameters(
            message_id=saved["reply"], allow_sending_without_reply=True
        )
    send = getattr(bot, f"send_{saved['method']}")
    return await send(chat_id=chat_id, **fields)
