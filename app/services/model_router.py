from app.core.config import get_settings
from app.providers.openai_provider import OpenAIProvider
from app.providers.compatible_provider import CompatibleProvider
from app.providers.ollama_provider import OllamaProvider


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

    def choose(self, provider: str, model: str | None, routing_mode: str):
        catalog = {p["id"]: p for p in self.catalog()}
        if routing_mode == "manual" and provider == "auto":
            raise ValueError("Manual mode requires a specific provider.")
        if provider == "auto":
            configured_default = self.settings.default_provider
            order = [configured_default] if configured_default in self.providers else []
            order += ["ollama", "freemodel", "groq", "openai", "openai-compatible"]
            for candidate in dict.fromkeys(order):
                if candidate in catalog and catalog[candidate]["available"]:
                    cand_models = catalog[candidate].get("models", [])
                    cand_default = catalog[candidate]["default_model"]
                    if model and (model == cand_default or model in cand_models):
                        return candidate, model
                    return candidate, cand_default
            raise RuntimeError("No AI provider is configured. Configure FreeModel, Groq, OpenAI, an OpenAI-compatible endpoint, or Ollama.")
        if provider not in self.providers:
            raise ValueError(f"Unsupported provider: {provider}")
        if not catalog[provider]["available"]:
            raise RuntimeError(f"{provider} is not configured or available. Check backend/.env.")
        
        prov_models = catalog[provider].get("models", [])
        prov_default = catalog[provider]["default_model"]
        if model:
            if provider == "ollama" and model.lower().startswith("gpt-"):
                return provider, prov_default
            if prov_models and model not in prov_models and not any(m.startswith(model) for m in prov_models):
                return provider, prov_default
            return provider, model
        return provider, prov_default

    async def chat(self, messages, provider: str, model: str | None, routing_mode: str):
        chosen, selected_model = self.choose(provider, model, routing_mode)
        answer, actual_model, usage = await self.providers[chosen].chat(messages, selected_model)
        return answer, chosen, actual_model, usage
