"""Rule-based input validation.

Usage::

    clean = validate(request.form, {
        "name": "required|hostname",
        "mac_address": "required|mac",
        "vlan": "nullable|vlan",
    })

Returns a dict of cleaned, type-cast values and raises `ValidationException`
(with per-field errors) when any rule fails.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from core.exceptions import ValidationException
from core.security import ValidationError, Validator as Strict

_MISSING = object()


class _Field:
    def __init__(self, name: str, rules: list[str]):
        self.name = name
        self.rules = rules


def _parse_rules(rules: str | list[str]) -> list[str]:
    if isinstance(rules, str):
        return [r for r in rules.split("|") if r]
    return list(rules)


class ValidatorEngine:
    def __init__(self, data: Mapping[str, Any]):
        self.data = data
        self.errors: dict[str, list[str]] = {}
        self.clean: dict[str, Any] = {}

    def validate(self, rules: Mapping[str, str | list[str]]) -> dict[str, Any]:
        for field_name, rule_spec in rules.items():
            field = _Field(field_name, _parse_rules(rule_spec))
            self._apply(field)
        if self.errors:
            raise ValidationException(self.errors)
        return self.clean

    # -- helpers ------------------------------------------------------------
    def _value(self, field: _Field) -> Any:
        return self.data.get(field.name, _MISSING)

    def _fail(self, field: _Field, message: str) -> None:
        self.errors.setdefault(field.name, []).append(message)

    def _apply(self, field: _Field) -> None:
        value = self._value(field)
        is_nullable = "nullable" in field.rules
        is_required = "required" in field.rules

        if value is _MISSING or (isinstance(value, str) and value.strip() == ""):
            if is_required:
                self._fail(field, "This field is required.")
                return
            if is_nullable:
                self.clean[field.name] = None
                return
            # Non-required, absent -> skip entirely.
            return

        if isinstance(value, str):
            value = value.strip()

        for rule in field.rules:
            if rule in ("required", "nullable"):
                continue
            name, _, arg = rule.partition(":")
            try:
                value = self._apply_rule(field, name, arg, value)
            except ValidationError as exc:
                self._fail(field, str(exc))
                return

        self.clean[field.name] = value

    def _apply_rule(self, field: _Field, name: str, arg: str, value: Any) -> Any:
        if name == "string":
            return str(value)
        if name == "integer":
            return Strict.integer(value)
        if name == "boolean":
            return str(value).lower() in ("1", "true", "yes", "on")
        if name == "max":
            limit = int(arg)
            if isinstance(value, (int, float)):
                if value > limit:
                    raise ValidationError(f"Must be at most {limit}.")
                return value
            if len(str(value)) > limit:
                raise ValidationError(f"Must be at most {limit} characters.")
            return value
        if name == "min":
            limit = int(arg)
            if isinstance(value, (int, float)):
                if value < limit:
                    raise ValidationError(f"Must be at least {limit}.")
                return value
            if len(str(value)) < limit:
                raise ValidationError(f"Must be at least {limit} characters.")
            return value
        if name == "in":
            allowed = [a.strip() for a in arg.split(",")]
            if str(value) not in allowed:
                raise ValidationError(f"Must be one of: {', '.join(allowed)}.")
            return value
        if name == "mac":
            return Strict.mac(value)
        if name == "ipv4":
            return Strict.ipv4(value)
        if name == "netmask":
            return Strict.ipv4_netmask(value)
        if name == "hostname":
            return Strict.hostname(value)
        if name == "cluster_name":
            return Strict.cluster_name(value)
        if name == "username":
            return Strict.username(value)
        if name == "vlan":
            return Strict.vlan(value)
        if name == "storage_id":
            if not re.match(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$", str(value)):
                raise ValidationError(
                    "Storage ID must start with a letter and contain only letters, digits, dot, dash, underscore."
                )
            return str(value)
        if name == "host_or_ip":
            try:
                return Strict.ipv4(value)
            except ValidationError:
                return Strict.hostname(value)
        if name == "export_path":
            text = str(value)
            if not text.startswith("/") or ".." in text or not re.match(
                r"^/[A-Za-z0-9._/-]*$", text
            ):
                raise ValidationError("Export must be an absolute path, e.g. /mnt/data.")
            return text
        if name == "json":
            if isinstance(value, (dict, list)):
                return value
            try:
                return json.loads(str(value))
            except json.JSONDecodeError:
                raise ValidationError("Must be valid JSON.")
        if name == "json_list":
            parsed = self._apply_rule(field, "json", arg, value)
            if not isinstance(parsed, list):
                raise ValidationError("Must be a JSON list.")
            return parsed
        raise RuntimeError(f"Unknown validation rule: {name!r}")


def validate(data: Mapping[str, Any], rules: Mapping[str, str | list[str]]) -> dict[str, Any]:
    return ValidatorEngine(data).validate(rules)
