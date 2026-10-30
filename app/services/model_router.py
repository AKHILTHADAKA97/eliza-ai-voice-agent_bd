from app.core.config import get_settings
from app.providers.openai_provider import OpenAIProvider
from app.providers.compatible_provider import CompatibleProvider
from app.providers.ollama_provider import OllamaProvider


# Priority order for automatic failover. Cloud API providers first (they have
# keys configured), local Ollama last as a best-effort offline fallback.
AUTO_FALLBACK_ORDER = ["freemodel", "groq", "comet", "openai", "openai-compatible", "ollama"]


class ModelRouter:
    def __init__(self):
        self.settings = get_settings()
        self.providers = {
            "freemodel": CompatibleProvider(
                name="freemodel",
                base_url=self.settings.freemodel_base_url,
                api_key=self.settings.freemodel_api_key,
                default_model=self.settings.freemodel_model,
            ),
            "groq": CompatibleProvider(
                name="groq",
                base_url=self.settings.groq_base_url,
                api_key=self.settings.groq_api_key,
                default_model=self.settings.groq_model,
            ),
            "comet": CompatibleProvider(
                name="comet",
                base_url=self.settings.comet_base_url,
                api_key=self.settings.comet_api_key,
                default_model=self.settings.comet_model,
            ),
            "openai": OpenAIProvider(),
            "openai-compatible": CompatibleProvider(),
            "ollama": OllamaProvider(),
        }

    def catalog(self):
        items = []
        for name, provider in self.providers.items():
            available = provider.available()
            if name == "freemodel":
                model = self.settings.freemodel_model or "gemini-3.7-flash"
                if available:
                    fetched = provider.get_installed_models() if hasattr(provider, "get_installed_models") else []
                    models = fetched or [model, "claude-3-7-sonnet", "deepseek-chat", "gpt-4o"]
                    if model not in models:
                        models = [model] + [m for m in models if m != model]
                else:
                    models = []
            elif name == "groq":
                model = self.settings.groq_model or "llama-3.3-70b-versatile"
                models = [model, "llama-3.1-8b-instant"] if available else []
            elif name == "comet":
                model = self.settings.comet_model or "gpt-4o-mini"
                models = [model] if available and model else []
            elif name == "openai":
                model = self.settings.openai_model
                models = [model] if available and model else []
            elif name == "openai-compatible":
                model = self.settings.openai_compat_model or ""
                models = [model] if available and model else []
            else:
                installed = provider.get_installed_models() if hasattr(provider, "get_installed_models") else []
                resolved = provider.resolve_model() if hasattr(provider, "resolve_model") else self.settings.ollama_model
                model = resolved or self.settings.ollama_model
                models = installed if installed else ([model] if available and model else [])
            items.append({
                "id": name,
                "name": "FreeModel" if name == "freemodel" else ("Groq" if name == "groq" else name.replace("-", " ").title()),
                "available": available,
                "default_model": model,
                "models": models,
            })
        return items

    def _available_catalog(self):
        return {p["id"]: p for p in self.catalog() if p["available"]}

    def choose(self, provider: str, model: str | None, routing_mode: str):
        """Resolve (provider, model). Never raises on transient failures — falls back."""
        available = self._available_catalog()
        if routing_mode == "manual" and provider == "auto":
            raise ValueError("Manual mode requires a specific provider.")

        if provider == "auto":
            configured_default = self.settings.default_provider
            order = [configured_default] if configured_default in self.providers else []
            order += AUTO_FALLBACK_ORDER
            for candidate in dict.fromkeys(order):
                if candidate in available:
                    cand_models = available[candidate].get("models", [])
                    cand_default = available[candidate]["default_model"]
                    if model and (model == cand_default or model in cand_models):
                        return candidate, model
                    return candidate, cand_default
            raise RuntimeError("No AI provider is configured.")

        if provider not in self.providers:
            raise ValueError(f"Unsupported provider: {provider}")
        if provider not in available:
            # Explicit provider unavailable — fall back to next available
            fallback_order = [self.settings.default_provider] + AUTO_FALLBACK_ORDER
            for candidate in dict.fromkeys(fallback_order):
                if candidate in available and candidate != provider:
                    cand_default = available[candidate]["default_model"]
                    return candidate, model if model in available[candidate].get("models", []) else cand_default
            raise RuntimeError(f"{provider} is not configured or available. Check backend/.env.")

        prov_models = available[provider].get("models", [])
        prov_default = available[provider]["default_model"]
        if model:
            if provider == "ollama" and model.lower().startswith("gpt-"):
                return provider, prov_default
            if prov_models and model not in prov_models and not any(m.startswith(model) for m in prov_models):
                return provider, prov_default
            return provider, model
        return provider, prov_default

    async def chat(self, messages, provider: str, model: str | None, routing_mode: str):
        """Chat with automatic failover on any provider error.

        If the chosen provider raises (timeout, rate limit, auth error, etc.),
        the router silently retries with the next available provider so the
        user never sees a broken response.
        """
        available = self._available_catalog()
        configured_default = self.settings.default_provider
        order = [configured_default] if configured_default in self.providers else []
        order += AUTO_FALLBACK_ORDER

        last_error = None
        for candidate in dict.fromkeys(order):
            if candidate not in available:
                continue
            prov_models = available[candidate].get("models", [])
            cand_default = available[candidate]["default_model"]
            selected_model = model if (model and (model == cand_default or model in prov_models)) else cand_default

            candidate_models = [selected_model]
            if candidate == "freemodel":
                for fb_m in ["gemini-3.7-flash", "gemini-3.6-flash", "deepseek-v4.1-flash", "mimo-v2.6-flash", "gemma-2-2b", "claude-haiku-5.5"]:
                    if fb_m not in candidate_models:
                        candidate_models.append(fb_m)

            for try_model in candidate_models:
                try:
                    answer, actual_model, usage = await self.providers[candidate].chat(messages, try_model)
                    return answer, candidate, actual_model, usage
                except Exception as exc:
                    last_error = exc
                    if candidate != "freemodel":
                        break
                    continue
        raise RuntimeError(f"All AI providers failed. Last error: {last_error}")