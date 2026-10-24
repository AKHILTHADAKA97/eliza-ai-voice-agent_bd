from openai import AsyncOpenAI
from app.core.config import get_settings
from app.providers.base import BaseProvider


class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self):
        self.settings = get_settings()
        self.client = None
        if self.settings.openai_api_key:
            kwargs = {"api_key": self.settings.openai_api_key}
            if self.settings.openai_base_url:
                kwargs["base_url"] = self.settings.openai_base_url
            self.client = AsyncOpenAI(**kwargs)

    def available(self) -> bool:
        return self.client is not None

    async def chat(self, messages: list[dict], model: str | None = None):
        if not self.client:
            raise RuntimeError("OpenAI is not configured. Add OPENAI_API_KEY to backend/.env.")
        selected = model or self.settings.openai_model
        result = await self.client.chat.completions.create(
            model=selected,
            messages=messages,
            timeout=self.settings.request_timeout_seconds,
        )
        answer = result.choices[0].message.content or ""
        usage = None
        if result.usage:
            usage = {
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
                "total_tokens": result.usage.total_tokens,
            }
        return answer, selected, usage
