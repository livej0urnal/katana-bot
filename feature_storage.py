import time

from recovery import RETENTION


SQLITE_TABLES = (
    "CREATE TABLE IF NOT EXISTS moderation_recovery (chat_id INTEGER NOT NULL, "
    "message_id INTEGER NOT NULL, payload TEXT NOT NULL, sent_at INTEGER NOT NULL, "
    "updated_at INTEGER NOT NULL, state TEXT NOT NULL, PRIMARY KEY (chat_id, message_id))",
    "CREATE TABLE IF NOT EXISTS feature_options (chat_id INTEGER NOT NULL, "
    "name TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (chat_id, name))",
    "CREATE TABLE IF NOT EXISTS allowlist (chat_id INTEGER NOT NULL, kind TEXT NOT NULL, "
    "value TEXT NOT NULL, PRIMARY KEY (chat_id, kind, value))",
    "CREATE TABLE IF NOT EXISTS moderation_log (id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, "
    "user_id INTEGER, actor_id INTEGER, action TEXT NOT NULL, detail TEXT NOT NULL, "
    "created_at INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS ix_moderation_log_chat_id ON moderation_log (chat_id, id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_moderation_appeal ON moderation_log (chat_id, user_id, action) "
    "WHERE action LIKE 'appeal:%'",
    "CREATE TABLE IF NOT EXISTS reputation_votes (chat_id INTEGER NOT NULL, "
    "voter_id INTEGER NOT NULL, user_id INTEGER NOT NULL, day INTEGER NOT NULL, "
    "PRIMARY KEY (chat_id, voter_id, user_id, day))",
    "CREATE TABLE IF NOT EXISTS announcements (chat_id INTEGER PRIMARY KEY, "
    "body TEXT NOT NULL, interval_seconds INTEGER NOT NULL, next_at INTEGER NOT NULL)",
)


class FeatureStorage:
    def option(self, chat_id: int, name: str, default: str = "off") -> str:
        with self.database:
            row = self.database.execute(
                "SELECT value FROM feature_options WHERE chat_id = ? AND name = ?",
                (chat_id, name),
            ).fetchone()
            return row[0] if row else default

    def set_option(self, chat_id: int, name: str, value: str) -> None:
        with self.database:
            self.database.execute(
                "INSERT INTO feature_options VALUES (?, ?, ?) "
                "ON CONFLICT (chat_id, name) DO UPDATE SET value = excluded.value",
                (chat_id, name, value),
            )

    def allowlist(self, chat_id: int, kind: str) -> set[str]:
        with self.database:
            return {
                row[0] for row in self.database.execute(
                    "SELECT value FROM allowlist WHERE chat_id = ? AND kind = ?", (chat_id, kind)
                ).fetchall()
            }

    def set_allowed(self, chat_id: int, kind: str, value: str, allowed: bool) -> None:
        with self.database:
            if allowed:
                self.database.execute(
                    "INSERT INTO allowlist VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                    (chat_id, kind, value),
                )
            else:
                self.database.execute(
                    "DELETE FROM allowlist WHERE chat_id = ? AND kind = ? AND value = ?",
                    (chat_id, kind, value),
                )

    def audit(self, chat_id: int, action: str, user_id=None, actor_id=None, detail="") -> int:
        with self.database:
            return self.database.execute(
                "INSERT INTO moderation_log (chat_id, user_id, actor_id, action, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
                (chat_id, user_id, actor_id, action, detail[:500], int(time.time())),
            ).fetchone()[0]

    def recent_actions(self, chat_id: int):
        with self.database:
            return self.database.execute(
                "SELECT id, action, user_id, actor_id, detail FROM moderation_log "
                "WHERE chat_id = ? ORDER BY id DESC LIMIT 10",
                (chat_id,),
            ).fetchall()

    def action_totals(self, chat_id: int):
        with self.database:
            return self.database.execute(
                "SELECT action, COUNT(*) FROM moderation_log WHERE chat_id = ? "
                "GROUP BY action ORDER BY action",
                (chat_id,),
            ).fetchall()

    def vote(self, chat_id: int, voter_id: int, user_id: int) -> bool:
        day = int(time.time()) // 86400
        with self.database:
            return self.database.execute(
                "INSERT INTO reputation_votes VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (chat_id, voter_id, user_id, day),
            ).rowcount == 1

    def reputation(self, chat_id: int, user_id: int) -> int:
        with self.database:
            return self.database.execute(
                "SELECT COUNT(*) FROM reputation_votes WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()[0]

    def set_announcement(self, chat_id: int, body: str, interval: int) -> None:
        with self.database:
            self.database.execute(
                "INSERT INTO announcements VALUES (?, ?, ?, ?) "
                "ON CONFLICT (chat_id) DO UPDATE SET body = excluded.body, "
                "interval_seconds = excluded.interval_seconds, next_at = excluded.next_at",
                (chat_id, body, interval, int(time.time()) + interval),
            )

    def clear_announcement(self, chat_id: int) -> None:
        with self.database:
            self.database.execute("DELETE FROM announcements WHERE chat_id = ?", (chat_id,))

    def due_announcements(self, now: int):
        with self.database:
            return self.database.execute(
                "SELECT chat_id, body, interval_seconds, next_at FROM announcements "
                "WHERE next_at <= ? ORDER BY next_at LIMIT 50",
                (now,),
            ).fetchall()

    def advance_announcement(self, chat_id: int, expected: int, next_at: int) -> bool:
        with self.database:
            return self.database.execute(
                "UPDATE announcements SET next_at = ? WHERE chat_id = ? AND next_at = ?",
                (next_at, chat_id, expected),
            ).rowcount == 1

    def strike_count(self, chat_id: int, user_id: int) -> int:
        with self.database:
            return self.database.execute(
                "SELECT COUNT(*) FROM moderation_log WHERE chat_id = ? AND user_id = ? "
                "AND action IN ('delete', 'warn') AND created_at > ?",
                (chat_id, user_id, int(time.time()) - 86400),
            ).fetchone()[0]

    def submit_appeal(self, chat_id: int, user_id: int, case_id: int, reason: str) -> bool:
        action = f"appeal:{case_id}"
        with self.database:
            case = self.database.execute(
                "SELECT 1 FROM moderation_log WHERE chat_id = ? AND id = ? AND user_id = ? "
                "AND action IN ('warn', 'mute', 'tempban', 'ban', 'autoban', 'strike_mute', 'strike_ban')",
                (chat_id, case_id, user_id),
            ).fetchone()
            if case is None:
                return False
            return self.database.execute(
                "INSERT INTO moderation_log (chat_id, user_id, action, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (chat_id, user_id, action, reason[:500], int(time.time())),
            ).rowcount == 1

    def registered_groups(self):
        with self.database:
            return self.database.execute(
                "SELECT chat_id, title, chat_type, bot_status FROM groups ORDER BY title LIMIT 200"
            ).fetchall()


    def flag_removal(self, chat_id, message_id, payload, sent_at):
        with self.database:
            self.database.execute(
                "INSERT INTO moderation_recovery VALUES (?, ?, ?, ?, ?, 'flagged') "
                "ON CONFLICT (chat_id, message_id) DO UPDATE SET payload = excluded.payload, "
                "updated_at = excluded.updated_at WHERE moderation_recovery.state = 'flagged'",
                (chat_id, message_id, payload, sent_at, int(time.time())),
            )

    def clear_removal_flag(self, chat_id, message_id):
        with self.database:
            self.database.execute(
                "DELETE FROM moderation_recovery WHERE chat_id = ? AND message_id = ? AND state = 'flagged'",
                (chat_id, message_id),
            )

    def flagged_removals(self, chat_id, since):
        with self.database:
            return self.database.execute(
                "SELECT message_id, payload, sent_at FROM moderation_recovery "
                "WHERE chat_id = ? AND sent_at > ? AND state = 'flagged' "
                "ORDER BY sent_at DESC, message_id DESC LIMIT 30",
                (chat_id, since),
            ).fetchall()

    def stage_removal(self, chat_id, message_id, payload, sent_at, *, flagged_only=False):
        with self.database:
            if flagged_only:
                return self.database.execute(
                    "UPDATE moderation_recovery SET state = 'pending', updated_at = ? "
                    "WHERE chat_id = ? AND message_id = ? AND state = 'flagged' AND payload = ?",
                    (int(time.time()), chat_id, message_id, payload),
                ).rowcount == 1
            return self.database.execute(
                "INSERT INTO moderation_recovery VALUES (?, ?, ?, ?, ?, 'pending') "
                "ON CONFLICT (chat_id, message_id) DO UPDATE SET payload = excluded.payload, "
                "updated_at = excluded.updated_at, state = 'pending' "
                "WHERE moderation_recovery.state IN ('flagged', 'failed')",
                (chat_id, message_id, payload, sent_at, int(time.time())),
            ).rowcount == 1

    def finish_removal(self, chat_id, message_id, deleted):
        with self.database:
            self.database.execute(
                "UPDATE moderation_recovery SET state = ?, updated_at = ? "
                "WHERE chat_id = ? AND message_id = ? AND state = 'pending'",
                ("removed" if deleted else "failed", int(time.time()), chat_id, message_id),
            )

    def claim_restore(self, chat_id, message_id):
        with self.database:
            row = self.database.execute(
                "UPDATE moderation_recovery SET state = 'restoring' "
                "WHERE chat_id = ? AND message_id = ? AND state = 'removed' "
                "AND updated_at > ? RETURNING payload",
                (chat_id, message_id, int(time.time()) - RETENTION),
            ).fetchone()
            return row[0] if row else None

    def finish_restore(self, chat_id, message_id, restored):
        with self.database:
            if restored:
                self.database.execute(
                    "DELETE FROM moderation_recovery WHERE chat_id = ? AND message_id = ? AND state = 'restoring'",
                    (chat_id, message_id),
                )
            else:
                self.database.execute(
                    "UPDATE moderation_recovery SET state = 'removed' "
                    "WHERE chat_id = ? AND message_id = ? AND state = 'restoring'",
                    (chat_id, message_id),
                )

    def prune_recovery(self):
        with self.database:
            self.database.execute(
                "DELETE FROM moderation_recovery WHERE updated_at <= ?",
                (int(time.time()) - RETENTION,),
            )
