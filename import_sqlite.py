import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv

from postgres import postgres_engine


TABLES = {
    "groups": ("chat_id", "title", "chat_type", "bot_status"),
    "link_filter": ("chat_id",),
    "reports": ("chat_id", "message_id", "reporter_id", "excerpt"),
    "profanity_settings": ("chat_id", "mode"),
    "autoban": ("chat_id",),
    "profanity_strikes": ("chat_id", "user_id", "message_id", "recorded_at"),
    "feature_options": ("chat_id", "name", "value"),
    "allowlist": ("chat_id", "kind", "value"),
    "moderation_log": ("id", "chat_id", "user_id", "actor_id", "action", "detail", "created_at"),
    "reputation_votes": ("chat_id", "voter_id", "user_id", "day"),
    "announcements": ("chat_id", "body", "interval_seconds", "next_at"),
    "moderation_recovery": ("chat_id", "message_id", "payload", "sent_at", "updated_at", "state"),
}


def import_database(source_path: Path, engine) -> dict[str, int]:
    source_uri = f"{source_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source:
        source.execute("BEGIN")
        source_tables = {
            row[0]
            for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if not source_tables.intersection(TABLES):
            raise ValueError("В исходной базе нет таблиц Katana Moderator.")

        counts = dict.fromkeys(TABLES, 0)
        with engine.begin() as target:
            target.exec_driver_sql(f"LOCK TABLE {', '.join(TABLES)} IN SHARE ROW EXCLUSIVE MODE")
            for table in TABLES:
                if target.exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first():
                    raise ValueError("Импорт возможен только в пустую базу PostgreSQL.")

            for table, columns in TABLES.items():
                if table not in source_tables:
                    continue
                names = ", ".join(columns)
                placeholders = ", ".join("%s" for _ in columns)
                order = " ORDER BY rowid" if table == "reports" else ""
                cursor = source.execute(f"SELECT {names} FROM {table}{order}")
                while rows := cursor.fetchmany(1000):
                    target.exec_driver_sql(
                        f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
                        rows,
                    )
                    counts[table] += len(rows)
            if counts["moderation_log"]:
                target.exec_driver_sql(
                    "SELECT setval(pg_get_serial_sequence('moderation_log', 'id'), MAX(id)) "
                    "FROM moderation_log"
                )

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт данных Katana Moderator в PostgreSQL.")
    parser.add_argument("source", type=Path, help="Путь к исходной базе SQLite")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parent / ".env")
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        parser.error("Укажите DATABASE_URL в .env.")

    engine = postgres_engine(database_url)
    try:
        counts = import_database(args.source, engine)
    except (ValueError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")
    finally:
        engine.dispose()

    for table, count in counts.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
