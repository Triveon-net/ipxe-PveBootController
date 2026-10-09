"""A small ActiveRecord-style model layer.

Design goals:
* Convenience similar to Laravel's Eloquent for the common cases.
* Safety: values are always bound parameters; column/table identifiers are
  validated through `quote_identifier` before they touch SQL.
"""

from __future__ import annotations

import json
from typing import Any, Generic, Iterable, Mapping, TypeVar

from database import get_db
from database.database import quote_identifier

M = TypeVar("M", bound="Model")

_ALLOWED_OPERATORS = {
    "=", "!=", "<>", "<", "<=", ">", ">=", "LIKE", "NOT LIKE", "IS", "IS NOT",
}


class QueryBuilder(Generic[M]):
    def __init__(self, model_cls: type[M]) -> None:
        self.model_cls = model_cls
        self.db = model_cls.db()
        self._wheres: list[tuple[str, list[Any]]] = []
        self._order: list[str] = []
        self._limit: int | None = None
        self._offset: int | None = None

    # -- clause builders ----------------------------------------------------
    def where(self, column: str, value: Any = None, operator: str = "=") -> "QueryBuilder":
        op = operator.upper()
        if op not in _ALLOWED_OPERATORS:
            raise ValueError(f"Unsupported operator: {operator!r}")
        col = quote_identifier(column)
        if value is None and op in ("=", "IS"):
            self._wheres.append((f"{col} IS NULL", []))
        elif value is None and op in ("!=", "<>", "IS NOT"):
            self._wheres.append((f"{col} IS NOT NULL", []))
        else:
            self._wheres.append((f"{col} {op} ?", [value]))
        return self

    def where_in(self, column: str, values: Iterable[Any]) -> "QueryBuilder":
        values = list(values)
        if not values:
            self._wheres.append(("0 = 1", []))
            return self
        col = quote_identifier(column)
        placeholders = ", ".join("?" for _ in values)
        self._wheres.append((f"{col} IN ({placeholders})", list(values)))
        return self

    def order_by(self, column: str, direction: str = "asc") -> "QueryBuilder":
        direction = direction.lower()
        if direction not in ("asc", "desc"):
            raise ValueError("Order direction must be 'asc' or 'desc'.")
        self._order.append(f"{quote_identifier(column)} {direction.upper()}")
        return self

    def limit(self, value: int) -> "QueryBuilder":
        self._limit = int(value)
        return self

    def offset(self, value: int) -> "QueryBuilder":
        self._offset = int(value)
        return self

    # -- SQL assembly -------------------------------------------------------
    def _where_sql(self) -> tuple[str, list[Any]]:
        if not self._wheres:
            return "", []
        clauses, params = [], []
        for clause, values in self._wheres:
            clauses.append(clause)
            params.extend(values)
        return " WHERE " + " AND ".join(clauses), params

    def _select_sql(self) -> tuple[str, list[Any]]:
        table = quote_identifier(self.model_cls.table)
        where_sql, params = self._where_sql()
        sql = f"SELECT * FROM {table}{where_sql}"
        if self._order:
            sql += " ORDER BY " + ", ".join(self._order)
        if self._limit is not None:
            sql += " LIMIT ?"
            params.append(self._limit)
        if self._offset is not None:
            sql += " OFFSET ?"
            params.append(self._offset)
        return sql, params

    # -- read ---------------------------------------------------------------
    def get(self) -> list[M]:
        sql, params = self._select_sql()
        return [self.model_cls.hydrate(row) for row in self.db.query(sql, params)]

    def first(self) -> M | None:
        sql, params = self._select_sql()
        row = self.db.first(sql, params)
        return self.model_cls.hydrate(row) if row else None

    def count(self) -> int:
        table = quote_identifier(self.model_cls.table)
        where_sql, params = self._where_sql()
        return int(self.db.scalar(f"SELECT COUNT(*) FROM {table}{where_sql}", params, 0))

    def exists(self) -> bool:
        return self.count() > 0

    def value(self, column: str) -> Any:
        row = self.first()
        return row[column] if row else None

    # -- write --------------------------------------------------------------
    def insert(self, data: Mapping[str, Any]) -> int:
        table = quote_identifier(self.model_cls.table)
        columns = [quote_identifier(c) for c in data]
        placeholders = ", ".join("?" for _ in data)
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        cursor = self.db.run(sql, list(data.values()))
        return int(cursor.lastrowid or 0)

    def update(self, data: Mapping[str, Any]) -> int:
        if not data:
            return 0
        table = quote_identifier(self.model_cls.table)
        assignments = ", ".join(f"{quote_identifier(c)} = ?" for c in data)
        where_sql, where_params = self._where_sql()
        sql = f"UPDATE {table} SET {assignments}{where_sql}"
        cursor = self.db.run(sql, list(data.values()) + where_params)
        return cursor.rowcount

    def delete(self) -> int:
        table = quote_identifier(self.model_cls.table)
        where_sql, params = self._where_sql()
        cursor = self.db.run(f"DELETE FROM {table}{where_sql}", params)
        return cursor.rowcount


class Model:
    """Base model. Subclasses set `table` and (optionally) `casts`/`fillable`."""

    table: str = ""
    primary_key: str = "id"
    fillable: tuple[str, ...] = ()
    hidden: tuple[str, ...] = ()
    casts: dict[str, str] = {}

    def __init__(self, attributes: Mapping[str, Any] | None = None, *, exists: bool = False):
        object.__setattr__(self, "_attributes", {})
        object.__setattr__(self, "_exists", exists)
        object.__setattr__(self, "_dirty", set())
        for key, value in (attributes or {}).items():
            self._attributes[key] = self._cast_set(key, value)

    # -- framework hooks ----------------------------------------------------
    @classmethod
    def db(cls):
        return get_db()

    @classmethod
    def hydrate(cls: type[M], row: Mapping[str, Any]) -> M:
        instance = cls(dict(row), exists=True)
        object.__setattr__(instance, "_dirty", set())
        return instance

    # -- query entry points -------------------------------------------------
    @classmethod
    def query(cls: type[M]) -> QueryBuilder[M]:
        return QueryBuilder(cls)

    @classmethod
    def all(cls: type[M]) -> list[M]:
        return cls.query().get()

    @classmethod
    def find(cls: type[M], identifier: Any) -> M | None:
        return cls.query().where(cls.primary_key, identifier).first()

    @classmethod
    def where(cls: type[M], column: str, value: Any = None, operator: str = "=") -> QueryBuilder[M]:
        return cls.query().where(column, value, operator)

    @classmethod
    def create(cls: type[M], data: Mapping[str, Any]) -> M:
        from core.support import now_iso

        payload = dict(data)
        if cls._timestamps() and "created_at" not in payload:
            payload["created_at"] = now_iso()
            payload["updated_at"] = now_iso()
        instance = cls(payload)
        instance.save()
        return instance

    @classmethod
    def _timestamps(cls) -> bool:
        return True

    # -- persistence --------------------------------------------------------
    @property
    def exists(self) -> bool:
        return self._exists

    def save(self) -> "Model":
        from core.support import now_iso

        if not self._exists:
            new_id = self.query().insert(self._attributes)
            object.__setattr__(self, "_attributes", {**self._attributes, self.primary_key: new_id})
            object.__setattr__(self, "_exists", True)
            object.__setattr__(self, "_dirty", set())
            return self
        if not self._dirty:
            return self
        data = {c: self._attributes[c] for c in self._dirty if c in self._attributes}
        if self._timestamps():
            data["updated_at"] = now_iso()
        self.query().where(self.primary_key, self._attributes.get(self.primary_key)).update(data)
        object.__setattr__(self, "_dirty", set())
        return self

    def update(self, data: Mapping[str, Any]) -> "Model":
        for key, value in data.items():
            self[key] = value
        return self.save()

    def delete(self) -> int:
        if not self._exists:
            return 0
        deleted = self.query().where(self.primary_key, self._attributes.get(self.primary_key)).delete()
        object.__setattr__(self, "_exists", False)
        return deleted

    # -- attribute access ---------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        attributes = object.__getattribute__(self, "_attributes")
        if name in attributes:
            return self._cast_get(name, attributes[name])
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        self._attributes[name] = self._cast_set(name, value)
        self._dirty.add(name)

    def __getitem__(self, key: str) -> Any:
        value = self._attributes.get(key)
        return self._cast_get(key, value)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def __contains__(self, key: str) -> bool:
        return key in self._attributes

    def get(self, key: str, default: Any = None) -> Any:
        if key not in self._attributes:
            return default
        return self._cast_get(key, self._attributes[key])

    # -- casting ------------------------------------------------------------
    def _cast_get(self, key: str, value: Any) -> Any:
        cast = self.casts.get(key)
        if cast == "json" and isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        if cast == "bool" and value is not None:
            return bool(value)
        if cast == "int" and value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return value
        return value

    def _cast_set(self, key: str, value: Any) -> Any:
        cast = self.casts.get(key)
        if cast == "json" and not isinstance(value, str):
            return json.dumps(value)
        if cast == "bool":
            return 1 if value else 0
        return value

    def to_dict(self) -> dict[str, Any]:
        return {k: self[k] for k in self._attributes if k not in self.hidden}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        pk = self._attributes.get(self.primary_key)
        return f"<{type(self).__name__} {self.primary_key}={pk}>"
