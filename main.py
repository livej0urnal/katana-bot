import asyncio
import logging
import os
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from handlers import router
from storage import ChatSettings


PROJECT_DIR = Path(__file__).resolve().parent


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
