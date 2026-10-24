from openai import AsyncOpenAI
from app.core.config import get_settings
from app.providers.base import BaseProvider


import time
import httpx
from openai import AsyncOpenAI
from app.core.config import get_settings
from app.providers.base import BaseProvider

FREEMODEL_ALL_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.1-pro",
    "claude-3-7-sonnet",
    "claude-3-5-sonnet",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku",
    "claude-3-opus",
    "claude-sonnet-5",
    "claude-sonnet-5-5",
    "claude-opus-5",
    "claude-opus-5-5",
    "claude-opus-4.8",
    "claude-opus-4.7",
    "claude-opus-4.6",
    "claude-opus-4-8",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-fable-5",
    "claude-fable-5-1",
    "claude-haiku-5.5",
    "claude-mythos-preview",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-5.5",
    "gpt-5.5-pro",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-6-astra",
    "gpt-6-luna",
    "gpt-6.1-sol",
    "deepseek-chat",
    "deepseek-v4-pro-max",
    "deepseek-v4-pro-0813",
    "deepseek-v4.1-flash",
    "deepseek-v4-flash-0731",
    "grok-4.5",
    "grok-4.6",
    "qwen3.7-max",
    "qwen3.8-max",
    "qwen3.8-27b",
    "kimi-k3",
    "kimi-k2.6",
    "seed-2.1-pro",
    "seed-2.1-turbo",
    "glm-5.3",
    "glm-5.3-flash",
    "glm-5.2",
    "muse-spark-1.1",
    "mimo-v2.6-pro",
    "mimo-v2.6-flash",
    "matilda-cerulean-i",
    "matilda-flashflight-i",
    "gemma-2-2b",
    "codex-auto-review",
]


class CompatibleProvider(BaseProvider):
    name = "openai-compatible"

    def __init__(
        self,
        name: str = "openai-compatible",
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
    ):
        self.settings = get_settings()
        self.name = name
        self.base_url = base_url if base_url is not None else self.settings.openai_compat_base_url
        self.api_key = api_key if api_key is not None else self.settings.openai_compat_api_key
        self.default_model = default_model if default_model is not None else self.settings.openai_compat_model
        self.client = None
        self._cached_models = []
        self._cache_time = 0.0

        if self.base_url and self.api_key:
            self.client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )

    def available(self) -> bool:
        return self.client is not None and bool(self.default_model)

    def get_installed_models(self) -> list[str]:
        if not self.base_url or not self.api_key:
            return [self.default_model] if self.default_model else []
        now = time.time()
        if now - self._cache_time < 300.0 and self._cached_models:
            return self._cached_models

        # For freemodel, default to comprehensive known list
        fallback = FREEMODEL_ALL_MODELS if self.name == "freemodel" else ([self.default_model] if self.default_model else [])
        try:
            with httpx.Client(timeout=3.0) as client:
                res = client.get(
                    self.base_url.rstrip("/") + "/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if res.status_code == 200:
                    data = res.json()
                    models = [m.get("id") for m in data.get("data", []) if m.get("id")]
                    if models:
                        self._cached_models = models
                        self._cache_time = now
                        return models
        except Exception:
            pass

        self._cached_models = fallback
        self._cache_time = now
        return self._cached_models

    async def chat(self, messages: list[dict], model: str | None = None):
        if not self.available():
            raise RuntimeError(f"{self.name} provider is not configured. Set base URL, API key, and model.")
        selected = model or self.default_model
        result = await self.client.chat.completions.create(
            model=selected, messages=messages, timeout=self.settings.request_timeout_seconds
        )
        return result.choices[0].message.content or "", selected, None

