from __future__ import annotations

from abc import ABC, abstractmethod


class ChatBackend(ABC):
    model_name: str

    @property
    def cache_identity(self) -> str:
        """Identity of the actual inference backend used for decision caching."""
        return self.model_name

    @abstractmethod
    def complete(self, system: str, user: str, temperature: float = 0.0) -> str:
        raise NotImplementedError
