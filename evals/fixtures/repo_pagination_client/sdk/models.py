from dataclasses import dataclass

@dataclass(frozen=True)
class Page:
    items: list
    next_cursor: str | None = None
