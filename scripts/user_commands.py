"""User management commands."""

from __future__ import annotations

from core.cli import io
from core.cli.kernel import command
from core.hashing import hash_password
from core.security import ValidationError
from core.security import Validator
from resources.app.models.user import User


def _unique_username(value: str) -> str:
    value = Validator.username(value)
    if User.where("username", value).exists():
        raise ValidationError("That username is already taken.")
    return value


def _existing_username(value: str) -> str:
    value = Validator.username(value)
    if not User.where("username", value).exists():
        raise ValidationError("No user with that username exists.")
    return value


@command("app:user:make", "Interactively create a new panel user")
def make_user(app, args) -> int:
    app.boot()
    io.title("Create a new user")

    username = io.ask("Username", validator=_unique_username)
    first_name = io.ask("First name", default="")
    last_name = io.ask("Last name", default="")

    while True:
        password = io.secret("Password (min 10 chars)", validator=Validator.password)
        if io.secret("Confirm password") != password:
            io.error("Passwords do not match. Please try again.")
            continue
        break

    User.create(
        {
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "password_hash": hash_password(password),
            "is_active": 1,
        }
    )
    io.success(f"User '{username}' created successfully.")
    io.info("You can now sign in at the login screen.")
    return 0


@command("app:user:list", "List existing panel users")
def list_users(app, args) -> int:
    app.boot()
    io.title("Panel users")
    io.table(
        ["ID", "Username", "Name", "Active", "Last login"],
        [
            (
                user.id,
                user.username,
                user.full_name,
                "yes" if user.is_active else "no",
                user.last_login_at or "—",
            )
            for user in User.query().order_by("id").get()
        ],
    )
    return 0


@command("app:user:password", "Reset a user's password")
def reset_password(app, args) -> int:
    app.boot()
    io.title("Reset password")
    username = io.ask("Username", validator=_existing_username)
    user = User.where("username", username).first()
    assert user is not None
    while True:
        password = io.secret("New password (min 10 chars)", validator=Validator.password)
        if io.secret("Confirm password") != password:
            io.error("Passwords do not match. Please try again.")
            continue
        break
    user.set_password(password)
    io.success(f"Password for '{username}' has been updated.")
    return 0
