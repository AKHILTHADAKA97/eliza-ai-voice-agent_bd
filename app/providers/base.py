from abc import ABC, abstractmethod


class BaseProvider(ABC):
    name = "base"

    @abstractmethod
    async def chat(self, messages: list[dict], model: str | None = None) -> tuple[str, str, dict | None]:
        raise NotImplementedError
