"""Create the login_attempts table used for brute-force throttling."""

from __future__ import annotations


def up(conn) -> None:
    conn.execute(
        """
        CREATE TABLE login_attempts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            successful INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_login_attempts_lookup "
        "ON login_attempts (username, ip_address, created_at)"
    )


def down(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS login_attempts")
