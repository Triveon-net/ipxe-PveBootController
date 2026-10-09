"""Response helpers built on top of Werkzeug's Response."""

from __future__ import annotations

import json
from typing import Any, Mapping

from werkzeug.wrappers import Response


def make_response(
    body: str = "",
    status: int = 200,
    headers: Mapping[str, str] | None = None,
    mimetype: str = "text/html",
) -> Response:
    return Response(body, status=status, headers=dict(headers or {}), mimetype=mimetype)


def redirect(url: str, status: int = 302) -> Response:
    return Response("", status=status, headers={"Location": url})


def json_response(data: Any, status: int = 200, headers: Mapping[str, str] | None = None) -> Response:
    body = json.dumps(data, default=str)
    merged = {"Content-Type": "application/json; charset=utf-8"}
    merged.update(headers or {})
    return Response(body, status=status, headers=merged)


def text_response(body: str, status: int = 200) -> Response:
    return Response(body, status=status, mimetype="text/plain")


def no_content(status: int = 204) -> Response:
    return Response("", status=status)
