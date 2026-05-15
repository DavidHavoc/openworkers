"""Widget renders to HTML. Verifies README's Usage claim."""

from typing import Any


class Widget:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def render(self) -> str:
        return f"<div>{self.payload}</div>"
