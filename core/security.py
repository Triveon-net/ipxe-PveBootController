"""Framework-wide security primitives.

Three classes of attacks are neutralised *by the framework* so application code
does not have to remember them:

* **XSS** – `Sanitizer.escape` / `Sanitizer.strip_tags` plus Jinja2 autoescaping
  (see `core/view.py`) which HTML-escapes every interpolated value by default.
* **SQL injection** – all values are bound parameters (see `core/database` and
  `core/model`); raw identifiers must pass `quote_identifier`.
* **Command injection** – `Shell.run` never uses a shell, requires an argument
  list, validates every argument and only executes allow-listed binaries.
"""

from __future__ import annotations

import html
import os
import re
import subprocess
from typing import Sequence


# --------------------------------------------------------------------------
# XSS helpers
# --------------------------------------------------------------------------
class Sanitizer:
    _TAG_RE = re.compile(r"<[^>]*>")

    @staticmethod
    def escape(value: object) -> str:
        """HTML-escape a value for safe embedding in markup."""
        if value is None:
            return ""
        return html.escape(str(value), quote=True)

    @classmethod
    def strip_tags(cls, value: object) -> str:
        """Remove anything that looks like an HTML tag, then escape the rest."""
        if value is None:
            return ""
        without_tags = cls._TAG_RE.sub("", str(value))
        return html.escape(without_tags, quote=True)

    @classmethod
    def clean_text(cls, value: object, *, max_length: int | None = None) -> str:
        """Normalise free-form text that must never contain markup."""
        if value is None:
            return ""
        text = cls._TAG_RE.sub("", str(value))
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).strip()
        if max_length is not None:
            text = text[:max_length]
        return text


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------
class ValidationError(ValueError):
    """Raised when input fails validation."""


class Validator:
    """Strict validators for the primitive types this app deals with."""

    _MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
    _HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
    _CLUSTER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,62}$")
    _USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,64}$")

    @staticmethod
    def mac(value: object) -> str:
        """Normalise and validate a MAC address -> 'aa:bb:cc:dd:ee:ff'."""
        if value is None:
            raise ValidationError("MAC address is required.")
        normalised = str(value).strip().lower().replace("-", ":").replace(".", ":")
        # Accept dotted notation like aabb.ccdd.eeff -> convert above mishandles
        # the 4-char groups, so rebuild from hex digits as a fallback.
        compact = re.sub(r"[^0-9a-f]", "", str(value).lower())
        if len(compact) == 12:
            normalised = ":".join(compact[i : i + 2] for i in range(0, 12, 2))
        if not Validator._MAC_RE.match(normalised):
            raise ValidationError(f"Invalid MAC address: {value!r}")
        return normalised

    @staticmethod
    def ipv4(value: object) -> str:
        import ipaddress

        if value is None:
            raise ValidationError("IP address is required.")
        try:
            return str(ipaddress.IPv4Address(str(value).strip()))
        except (ipaddress.AddressValueError, ValueError):
            raise ValidationError(f"Invalid IPv4 address: {value!r}")

    @staticmethod
    def ipv4_netmask(value: object) -> str:
        import ipaddress

        try:
            net = ipaddress.IPv4Network(f"0.0.0.0/{value}")
        except (ipaddress.NetmaskValueError, ValueError):
            raise ValidationError(f"Invalid netmask: {value!r}")
        return str(net.netmask)

    @staticmethod
    def hostname(value: object) -> str:
        if value is None:
            raise ValidationError("Hostname is required.")
        value = str(value).strip().lower()
        if not Validator._HOSTNAME_RE.match(value):
            raise ValidationError(f"Invalid hostname: {value!r}")
        return value

    @staticmethod
    def cluster_name(value: object) -> str:
        """Cluster name, case-preserving (e.g. 'AGC-M1')."""
        if value is None:
            raise ValidationError("Cluster name is required.")
        value = str(value).strip()
        if not Validator._CLUSTER_RE.match(value):
            raise ValidationError(
                "Cluster name must start with a letter and contain only "
                "letters, digits, dot, dash, underscore."
            )
        return value

    @staticmethod
    def username(value: object) -> str:
        if value is None:
            raise ValidationError("Username is required.")
        value = str(value).strip()
        if not Validator._USERNAME_RE.match(value):
            raise ValidationError(
                "Username must be 3-64 chars: letters, digits, dot, dash, underscore."
            )
        return value

    @staticmethod
    def integer(value: object, *, minimum: int | None = None,
                maximum: int | None = None) -> int:
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            raise ValidationError(f"Invalid integer: {value!r}")
        if minimum is not None and number < minimum:
            raise ValidationError("Value is below the allowed minimum.")
        if maximum is not None and number > maximum:
            raise ValidationError("Value is above the allowed maximum.")
        return number

    @staticmethod
    def vlan(value: object) -> int:
        return Validator.integer(value, minimum=1, maximum=4094)

    @staticmethod
    def password(value: object, *, min_length: int = 10) -> str:
        """Enforce a basic password policy (length + mixed character classes)."""
        if value is None:
            raise ValidationError("Password is required.")
        value = str(value)
        if len(value) < min_length:
            raise ValidationError(f"Password must be at least {min_length} characters.")
        if len(value) > 1024:
            raise ValidationError("Password is too long.")
        if not any(c.isalpha() for c in value):
            raise ValidationError("Password must contain at least one letter.")
        if not any(c.isdigit() for c in value):
            raise ValidationError("Password must contain at least one digit.")
        return value


# --------------------------------------------------------------------------
# Safe command execution
# --------------------------------------------------------------------------
class CommandNotAllowed(RuntimeError):
    pass


class Shell:
    """Runs external commands safely (no shell, allow-listed binaries).

    Even though `subprocess` with a list and ``shell=False`` already prevents
    most command injection, we additionally restrict *which* binaries may run
    and reject arguments containing NUL bytes.
    """

    DEFAULT_ALLOWED = frozenset(
        {"ip", "ipmitool", "ssh", "scp", "mount", "umount", "showmount", "pvesh", "pvesm"}
    )

    @classmethod
    def run(
        cls,
        args: Sequence[str],
        *,
        allowed: frozenset[str] | None = None,
        timeout: int = 30,
        check: bool = False,
        input_data: bytes | None = None,
    ) -> subprocess.CompletedProcess:
        if not args:
            raise ValueError("Command arguments must not be empty.")
        if isinstance(args, (str, bytes)):
            raise TypeError(
                "Shell.run expects a list of arguments, not a string "
                "(strings invite shell-injection mistakes)."
            )
        argv = [str(a) for a in args]
        for arg in argv:
            if "\x00" in arg:
                raise ValueError("Command arguments must not contain NUL bytes.")
        allow = allowed or cls.DEFAULT_ALLOWED
        binary = os.path.basename(argv[0])
        if binary not in allow:
            raise CommandNotAllowed(f"Command not allow-listed: {binary}")
        return subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            check=check,
            shell=False,
            input=input_data,
        )
