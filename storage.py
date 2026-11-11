import time

from feature_storage import FeatureStorage, SQLITE_TABLES


AUTOBAN_WINDOW_SECONDS = 24 * 60 * 60


class ChatSettings(FeatureStorage):
    def __init__(self, database, *, initialize: bool = True) -> None:
        self.database = database
        if not initialize:
            return
        with database:
            for statement in SQLITE_TABLES:
                database.execute(statement)
            database.execute(
                "CREATE TABLE IF NOT EXISTS groups ("
                "chat_id INTEGER PRIMARY KEY, title TEXT NOT NULL, "
                "chat_type TEXT NOT NULL, bot_status TEXT NOT NULL)"
            )
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

    def register_group(self, chat_id: int, title: str, chat_type: str, bot_status: str) -> None:
        with self.database:
            self.database.execute(
                "INSERT INTO groups (chat_id, title, chat_type, bot_status) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (chat_id) DO UPDATE SET title = excluded.title, "
                "chat_type = excluded.chat_type, bot_status = excluded.bot_status",
                (chat_id, title, chat_type, bot_status),
            )

    def autoban_enabled(self, chat_id: int) -> bool:
        with self.database:
            return self.database.execute(
                "SELECT 1 FROM autoban WHERE chat_id = ?", (chat_id,)
            ).fetchone() is not None

    def set_autoban(self, chat_id: int, enabled: bool) -> None:
        with self.database:
            if enabled:
                self.database.execute("INSERT INTO autoban (chat_id) VALUES (?) ON CONFLICT DO NOTHING", (chat_id,))
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
                "INSERT INTO profanity_strikes VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
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
        with self.database:
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
                "INSERT INTO reports (chat_id, message_id, reporter_id, excerpt) "
                "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (chat_id, message_id, reporter_id, excerpt),
            )
        return result.rowcount == 1

    def recent_reports(self, chat_id: int) -> list[tuple[int, int, str]]:
        with self.database:
            return self.database.execute(
                "SELECT message_id, COUNT(*), MAX(excerpt) FROM reports "
                "WHERE chat_id = ? GROUP BY message_id ORDER BY MAX(rowid) DESC LIMIT 10",
                (chat_id,),
            ).fetchall()

    def links_blocked(self, chat_id: int) -> bool:
        with self.database:
            row = self.database.execute(
                "SELECT 1 FROM link_filter WHERE chat_id = ?", (chat_id,)
            ).fetchone()
            return row is not None

    def set_links_blocked(self, chat_id: int, enabled: bool) -> None:
        with self.database:
            if enabled:
                self.database.execute(
                    "INSERT INTO link_filter (chat_id) VALUES (?) ON CONFLICT DO NOTHING",
                    (chat_id,),
                )
            else:
                self.database.execute(
                    "DELETE FROM link_filter WHERE chat_id = ?", (chat_id,)
                )

    def migrate(self, old_id: int, new_id: int) -> None:
        if old_id == new_id:
            return
        with self.database:
            self.database.execute("DELETE FROM groups WHERE chat_id = ?", (old_id,))
            self.database.execute(
                "INSERT INTO link_filter (chat_id) "
                "SELECT ? FROM link_filter WHERE chat_id = ? ON CONFLICT DO NOTHING",
                (new_id, old_id),
            )
            self.database.execute(
                "DELETE FROM link_filter WHERE chat_id = ?", (old_id,)
            )
            self.database.execute(
                "UPDATE reports SET chat_id = ? WHERE chat_id = ? AND NOT EXISTS ("
                "SELECT 1 FROM reports AS destination WHERE destination.chat_id = ? "
                "AND destination.message_id = reports.message_id "
                "AND destination.reporter_id = reports.reporter_id)",
                (new_id, old_id, new_id),
            )
            self.database.execute("DELETE FROM reports WHERE chat_id = ?", (old_id,))
            self.database.execute(
                "INSERT INTO profanity_settings (chat_id, mode) "
                "SELECT ?, mode FROM profanity_settings WHERE chat_id = ? ON CONFLICT DO NOTHING",
                (new_id, old_id),
            )
            self.database.execute(
                "DELETE FROM profanity_settings WHERE chat_id = ?", (old_id,)
            )
            self.database.execute(
                "INSERT INTO feature_options (chat_id, name, value) "
                "SELECT ?, name, value FROM feature_options WHERE chat_id = ? "
                "AND name NOT LIKE ? ON CONFLICT DO NOTHING",
                (new_id, old_id, "discussion_thread:%"),
            )
            self.database.execute(
                "INSERT INTO allowlist (chat_id, kind, value) "
                "SELECT ?, kind, value FROM allowlist WHERE chat_id = ? ON CONFLICT DO NOTHING",
                (new_id, old_id),
            )
            self.database.execute(
                "INSERT INTO announcements (chat_id, body, interval_seconds, next_at) "
                "SELECT ?, body, interval_seconds, next_at FROM announcements "
                "WHERE chat_id = ? ON CONFLICT DO NOTHING",
                (new_id, old_id),
            )
            self.database.execute(
                "INSERT INTO reputation_votes (chat_id, voter_id, user_id, day) "
                "SELECT ?, voter_id, user_id, day FROM reputation_votes WHERE chat_id = ? "
                "ON CONFLICT DO NOTHING",
                (new_id, old_id),
            )
            self.database.execute(
                "INSERT INTO autoban (chat_id) "
                "SELECT ? FROM autoban WHERE chat_id = ? ON CONFLICT DO NOTHING",
                (new_id, old_id),
            )
            self.database.execute(
                "UPDATE moderation_log SET chat_id = ? WHERE chat_id = ?", (new_id, old_id)
            )
            self.database.execute(
                "INSERT INTO profanity_strikes (chat_id, user_id, message_id, recorded_at) "
                "SELECT ?, user_id, message_id, recorded_at FROM profanity_strikes "
                "WHERE chat_id = ? ON CONFLICT DO NOTHING",
                (new_id, old_id),
            )
            for table in (
                "feature_options", "allowlist", "announcements", "reputation_votes",
                "autoban", "profanity_strikes",
            ):
                self.database.execute(f"DELETE FROM {table} WHERE chat_id = ?", (old_id,))
