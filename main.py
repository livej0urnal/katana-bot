import asyncio
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from aiohttp import ClientSession
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from ai_moderation import AIAnalysis
from gatekeeper import router as gatekeeper_router
from handlers import announcement_worker, router
from storage import ChatSettings


PROJECT_DIR = Path(__file__).resolve().parent


@contextmanager
def open_settings(database_url: str):
    if not database_url:
        database = sqlite3.connect(PROJECT_DIR / "katana.sqlite3")
        try:
            yield ChatSettings(database)
        finally:
            database.close()
        return

    from postgres import PostgresConnection, postgres_engine

    engine = postgres_engine(database_url)
    try:
        database = PostgresConnection(engine)
        with database:
            database.execute("SELECT 1 FROM groups LIMIT 1")
        yield ChatSettings(database, initialize=False)
    finally:
        engine.dispose()


async def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Укажите BOT_TOKEN в файле .env.")

    dispatcher = Dispatcher()
    dispatcher.include_router(gatekeeper_router)
    dispatcher.include_router(router)
    with open_settings(os.getenv("DATABASE_URL", "").strip()) as settings:
        async with Bot(token) as bot, ClientSession() as session:
            text_model = os.getenv("OLLAMA_TEXT_MODEL", "").strip()
            vision_model = os.getenv("OLLAMA_VISION_MODEL", "").strip()
            ai_analysis = None
            if text_model or vision_model:
                ai_analysis = AIAnalysis(
                    session, os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
                    text_model, vision_model,
                )
            redis_traffic = None
            redis_url = os.getenv("REDIS_URL", "").strip()
            if redis_url:
                from redis_traffic import RedisTraffic

                redis_traffic = RedisTraffic(redis_url)
            worker = asyncio.create_task(announcement_worker(bot, settings))
            try:
                await dispatcher.start_polling(bot, settings=settings, ai_analysis=ai_analysis,
                    redis_traffic=redis_traffic, close_bot_session=False)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                if redis_traffic is not None:
                    await redis_traffic.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
