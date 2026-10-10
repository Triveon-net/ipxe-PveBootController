"""Application configuration.

Loads values from `.env` (project root) into the process environment and exposes
a small, immutable-ish config object. No third-party dotenv dependency is used
on purpose so the framework bootstraps with the minimum possible surface.
"""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """Absolute path to the project root (parent of the `config` package)."""
    return Path(__file__).resolve().parent.parent


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip matching surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def load_dotenv(path: Path | None = None) -> None:
    """Populate `os.environ` from a .env file without overriding real env vars."""
    env_path = path or (project_root() / ".env")
    for key, value in _parse_env_file(env_path).items():
        os.environ.setdefault(key, value)


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class Config:
    """Typed accessor over the process environment."""

    def __init__(self) -> None:
        load_dotenv()
        root = project_root()

        self.root = root
        self.name = os.environ.get("APP_NAME", "iPXE PVE Controller")
        self.env = os.environ.get("APP_ENV", "production")
        self.debug = _as_bool(os.environ.get("APP_DEBUG"), default=False)
        self.key = os.environ.get("APP_KEY", "")
        self.url = os.environ.get("APP_URL", "http://127.0.0.1:8000").rstrip("/")

        self.host = os.environ.get("APP_HOST", "127.0.0.1")
        self.port = int(os.environ.get("APP_PORT", "8000"))
        # Agent WebSocket runs on the same host/port as HTTP; only the path is
        # configurable. See core/websocket.py.
        self.ws_path = os.environ.get("WS_PATH", "/agent")

        self.session_lifetime = int(os.environ.get("SESSION_LIFETIME", "120"))
        self.session_cookie = os.environ.get("SESSION_COOKIE", "pve_ctrl_session")
        self.session_secure = _as_bool(os.environ.get("SESSION_SECURE"), default=False)
        self.trusted_proxies = [
            p.strip() for p in os.environ.get("TRUSTED_PROXIES", "").split(",") if p.strip()
        ]

        db_path = os.environ.get("DB_PATH", "database/sql.db")
        db_file = Path(db_path)
        self.database_path = db_file if db_file.is_absolute() else (root / db_file)

        self.login_max_attempts = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5"))
        self.login_decay_seconds = int(os.environ.get("LOGIN_DECAY_SECONDS", "300"))

        self.pxe_next_server = os.environ.get("PXE_NEXT_SERVER", "127.0.0.1")
        self.pxe_boot_file = os.environ.get("PXE_BOOT_FILE", "boot.ipxe")

        # Filesystem locations used by the framework.
        self.resources_path = root / "resources"
        self.views_path = self.resources_path / "views"
        self.static_path = self.resources_path / "static"
        self.controllers_path = self.resources_path / "controllers"
        self.migrations_path = root / "database" / "migrations"

        # Uploaded boot files (vmlinuz, initrd.img, root.squashfs, ...).
        self.storage_path = root / "storage"
        self.bootfiles_path = self.storage_path / "bootfiles"
        self.bootfile_max_bytes = int(os.environ.get("BOOTFILE_MAX_MB", "8192")) * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.env.lower() in ("production", "prod")

    def validate(self) -> None:
        """Raise if the configuration is unsafe to run with."""
        if not self.key:
            raise RuntimeError(
                "APP_KEY is not set. Run `python main.py key:generate` to create one."
            )


# Module-level singleton, replaced in tests via `set_config`.
_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(config: Config) -> None:
    global _config
    _config = config
