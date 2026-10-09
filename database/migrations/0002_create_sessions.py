"""Create the DB-backed session table.

Sessions are stored server side so they can be revoked instantly. Only the
SHA-256 hash of the session token is persisted, never the token itself.
"""

from __future__ import annotations


def up(conn) -> None:
    conn.execute(
        """
        CREATE TABLE sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash    TEXT NOT NULL UNIQUE,
            user_id       INTEGER,
            ip_address    TEXT,
            user_agent    TEXT,
            csrf_token    TEXT NOT NULL,
            payload       TEXT NOT NULL DEFAULT '{}',
            last_activity INTEGER NOT NULL,
            created_at    TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX idx_sessions_user ON sessions (user_id)")
    conn.execute("CREATE INDEX idx_sessions_activity ON sessions (last_activity)")


def down(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS sessions")
