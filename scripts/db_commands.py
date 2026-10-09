"""Database commands."""

from __future__ import annotations

from core.cli import io
from core.cli.kernel import command
from database.migrator import Migrator
from resources.app.models.setting import Setting


@command("migrate", "Run pending database migrations")
def migrate(app, args) -> int:
    io.title("Running migrations")
    migrator = Migrator(app.db, app.config.migrations_path)
    applied = migrator.run()
    if not applied:
        io.info("Nothing to migrate. Database is up to date.")
        return 0
    for name in applied:
        io.success(f"Migrated: {name}")
    return 0


@command("db:rollback", "Roll back the last migration batch")
def rollback(app, args) -> int:
    migrator = Migrator(app.db, app.config.migrations_path)
    rolled = migrator.rollback()
    if not rolled:
        io.warn("Nothing to roll back.")
        return 0
    for name in rolled:
        io.success(f"Rolled back: {name}")
    return 0


@command("db:fresh", "Drop all tables and re-run migrations")
def fresh(app, args) -> int:
    if not io.confirm("This will delete ALL data. Continue?", default=False):
        io.warn("Aborted.")
        return 0
    from database.database import quote_identifier

    io.title("Dropping tables")
    rows = app.db.query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    conn = app.db.connection()
    conn.execute("PRAGMA foreign_keys = OFF")
    for row in rows:
        conn.execute(f"DROP TABLE IF EXISTS {quote_identifier(row['name'])}")
    conn.execute("PRAGMA foreign_keys = ON")
    io.success(f"Dropped {len(rows)} table(s).")
    migrate(app, args)
    seed(app, args)
    return 0


@command("db:seed", "Seed default settings")
def seed(app, args) -> int:
    app.boot()
    defaults = {
        "pxe.next_server": app.config.pxe_next_server,
        "pxe.boot_file": app.config.pxe_boot_file,
        "cluster.default_quorum": "1",
    }
    for key, value in defaults.items():
        if Setting.get(key) is None:
            Setting.put(key, value)
            io.success(f"Seeded setting: {key}")
    io.info("Seeding complete.")
    return 0
