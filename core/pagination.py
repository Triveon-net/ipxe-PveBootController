"""Small offset-pagination helper shared by the web panel and the JSON API."""

from __future__ import annotations

PER_PAGE = 50
MAX_PER_PAGE = 50


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def page_args(request, per_page: int = PER_PAGE) -> tuple[int, int, int]:
    """Return ``(page, per_page, offset)`` from the request query string."""
    page = max(1, _int(request.args.get("page"), 1))
    size = _int(request.args.get("per_page"), per_page)
    size = min(MAX_PER_PAGE, max(1, size))
    return page, size, (page - 1) * size


class Pager:
    def __init__(self, page: int, per_page: int, total: int) -> None:
        self.page = page
        self.per_page = per_page
        self.total = total

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.per_page - 1) // self.per_page)

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def prev_page(self) -> int:
        return max(1, self.page - 1)

    @property
    def next_page(self) -> int:
        return min(self.pages, self.page + 1)

    @property
    def start(self) -> int:
        return 0 if self.total == 0 else (self.page - 1) * self.per_page + 1

    @property
    def end(self) -> int:
        return min(self.total, self.page * self.per_page)

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "per_page": self.per_page,
            "total": self.total,
            "pages": self.pages,
        }
