import sqlite3
import time


AUTOBAN_WINDOW_SECONDS = 24 * 60 * 60


class ChatSettings:
    def __init__(self, database: sqlite3.Connection) -> None:
        self.database = database
        with database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS link_filter (chat_id INTEGER PRIMARY KEY)"
            )
            database.execute(
                "CREATE TABLE IF NOT EXISTS reports ("
                "chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, "
                "reporter_id INTEGER NOT NULL, excerpt TEXT NOT NULL, "
                "PRIMARY KEY (chat_id, message_id, reporter_id))"
            )
            database.execute(
                "CREATE TABLE IF NOT EXISTS profanity_settings ("
                "chat_id INTEGER PRIMARY KEY, mode TEXT NOT NULL "
                "CHECK (mode IN ('log', 'delete')))"
            )
            database.execute(
                "CREATE TABLE IF NOT EXISTS autoban (chat_id INTEGER PRIMARY KEY)"
            )
            database.execute(
                "CREATE TABLE IF NOT EXISTS profanity_strikes ("
                "chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
                "message_id INTEGER NOT NULL, recorded_at INTEGER NOT NULL, "
                "PRIMARY KEY (chat_id, user_id, message_id))"
            )

    def autoban_enabled(self, chat_id: int) -> bool:
        return self.database.execute(
            "SELECT 1 FROM autoban WHERE chat_id = ?", (chat_id,)
        ).fetchone() is not None

    def set_autoban(self, chat_id: int, enabled: bool) -> None:
        with self.database:
            if enabled:
                self.database.execute("INSERT OR IGNORE INTO autoban VALUES (?)", (chat_id,))
            else:
                self.database.execute("DELETE FROM autoban WHERE chat_id = ?", (chat_id,))
                self.database.execute("DELETE FROM profanity_strikes WHERE chat_id = ?", (chat_id,))

    def add_profanity_strike(self, chat_id: int, user_id: int, message_id: int) -> int:
        now = int(time.time())
        with self.database:
            self.database.execute(
                "DELETE FROM profanity_strikes WHERE recorded_at <= ?",
                (now - AUTOBAN_WINDOW_SECONDS,),
            )
            result = self.database.execute(
                "INSERT OR IGNORE INTO profanity_strikes VALUES (?, ?, ?, ?)",
                (chat_id, user_id, message_id, now),
            )
            if result.rowcount == 0:
                return 0
            return self.database.execute(
                "SELECT COUNT(*) FROM profanity_strikes WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()[0]

    def clear_profanity_strikes(self, chat_id: int, user_id: int) -> None:
        with self.database:
            self.database.execute(
                "DELETE FROM profanity_strikes WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )

    def profanity_mode(self, chat_id: int) -> str:
        row = self.database.execute(
            "SELECT mode FROM profanity_settings WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row[0] if row else "off"

    def set_profanity_mode(self, chat_id: int, mode: str) -> None:
        with self.database:
            if mode == "off":
                self.database.execute(
                    "DELETE FROM profanity_settings WHERE chat_id = ?", (chat_id,)
                )
            else:
                self.database.execute(
                    "INSERT INTO profanity_settings (chat_id, mode) VALUES (?, ?) "
                    "ON CONFLICT (chat_id) DO UPDATE SET mode = excluded.mode",
                    (chat_id, mode),
                )

    def report(self, chat_id: int, message_id: int, reporter_id: int, excerpt: str) -> bool:
        with self.database:
            result = self.database.execute(
                "INSERT OR IGNORE INTO reports VALUES (?, ?, ?, ?)",
                (chat_id, message_id, reporter_id, excerpt),
            )
        return result.rowcount == 1

    def recent_reports(self, chat_id: int) -> list[tuple[int, int, str]]:
        return self.database.execute(
            "SELECT message_id, COUNT(*), MAX(excerpt) FROM reports "
            "WHERE chat_id = ? GROUP BY message_id ORDER BY MAX(rowid) DESC LIMIT 10",
            (chat_id,),
        ).fetchall()

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
            self.database.execute(
                "UPDATE OR IGNORE reports SET chat_id = ? WHERE chat_id = ?",
                (new_id, old_id),
            )
            self.database.execute("DELETE FROM reports WHERE chat_id = ?", (old_id,))
            self.database.execute(
                "UPDATE OR IGNORE profanity_settings SET chat_id = ? WHERE chat_id = ?",
                (new_id, old_id),
            )
            self.database.execute(
                "DELETE FROM profanity_settings WHERE chat_id = ?", (old_id,)
            )
