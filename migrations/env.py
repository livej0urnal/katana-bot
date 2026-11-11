import os
from pathlib import Path

from alembic import context
from dotenv import load_dotenv

from postgres import postgres_engine


load_dotenv(Path(__file__).resolve().parents[1] / ".env")
database_url = os.getenv("DATABASE_URL", "").strip()
if not database_url:
    raise SystemExit("Укажите DATABASE_URL в .env.")

engine = postgres_engine(database_url)
try:
    if context.is_offline_mode():
        context.configure(url=engine.url, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
    else:
        with engine.connect() as connection:
            context.configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
finally:
    engine.dispose()
