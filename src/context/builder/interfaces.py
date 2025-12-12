from abc import ABC, abstractmethod
from pathlib import Path


class ContextBuilderNamespace(ABC):
    @abstractmethod
    def as_path(self) -> str | Path:
        pass

    @abstractmethod
    def as_list(self) -> list[str]:
        pass


class ContextBuilder(ABC):
    @abstractmethod
    @property
    def namespace(self) -> str | None:
        pass

    @abstractmethod
    @property
    def contribution(
        self,
    ) -> str | list[str] | "ContextBuilder" | list["ContextBuilder"]:
        pass
