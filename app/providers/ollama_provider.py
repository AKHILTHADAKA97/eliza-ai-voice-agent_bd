import time
import httpx
from app.core.config import get_settings
from app.providers.base import BaseProvider


class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(self):
        self.settings = get_settings()
        self._cache_time = 0.0
        self._cached_models = []

    def get_installed_models(self) -> list[str]:
        if not self.settings.ollama_base_url:
            return []
        now = time.time()
        if now - self._cache_time < 30.0 and self._cached_models:
            return self._cached_models
        try:
            with httpx.Client(timeout=2.0) as client:
                res = client.get(self.settings.ollama_base_url.rstrip("/") + "/api/tags")
                if res.status_code == 200:
                    data = res.json()
                    models = []
                    for item in data.get("models", []):
                        name = item.get("name")
                        caps = item.get("details", {}).get("capabilities", item.get("capabilities", []))
                        # If capabilities are specified, prefer completion/chat models
                        if caps and "embedding" in caps and "completion" not in caps:
                            continue
                        if name:
                            models.append(name)
                    self._cached_models = models
                    self._cache_time = now
                    return models
        except Exception:
            pass
        return self._cached_models

    def available(self) -> bool:
        if not self.settings.ollama_base_url:
            return False
        installed = self.get_installed_models()
        return bool(installed)

    def resolve_model(self, model: str | None = None) -> str:
        installed = self.get_installed_models()
        # If user passed a model, ignore if it is clearly an OpenAI model (e.g. gpt-4o-mini)
        if model and not model.lower().startswith("gpt-"):
            if model in installed:
                return model
            # Try prefix match (e.g. 'llama3.2' matching 'llama3.2:1b')
            prefix_match = next((m for m in installed if m.startswith(model) or m.split(":")[0] == model), None)
            if prefix_match:
                return prefix_match
            if not installed:
                return model

        # Next check configured default in settings
        configured = self.settings.ollama_model
        if configured:
            if configured in installed:
                return configured
            prefix_match = next((m for m in installed if m.startswith(configured) or m.split(":")[0] == configured), None)
            if prefix_match:
                return prefix_match

        # Fallback to first installed model or configured model
        if installed:
            return installed[0]
        return configured or "llama3.2:1b"

    async def chat(self, messages: list[dict], model: str | None = None):
        selected = self.resolve_model(model)
        url = self.settings.ollama_base_url.rstrip("/") + "/api/chat"
        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                response = await client.post(url, json={
                    "model": selected,
                    "messages": messages,
                    "stream": False,
                })
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError as exc:
            raise RuntimeError("Cannot connect to Ollama. Start Ollama and confirm OLLAMA_BASE_URL.") from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Ollama returned HTTP {exc.response.status_code}: {exc.response.text[:300]}") from exc
        return data.get("message", {}).get("content", ""), selected, None
