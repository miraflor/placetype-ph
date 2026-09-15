from __future__ import annotations

from collections.abc import Iterable

from .base import ChatBackend


class MockBackend(ChatBackend):
    def __init__(self, responses: Iterable[str]):
        self.responses = iter(responses)
        self.model_name = "mock"

    def complete(self, system: str, user: str, temperature: float = 0.0) -> str:
        return next(self.responses)
