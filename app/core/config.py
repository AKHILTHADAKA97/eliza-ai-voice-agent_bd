import os
import secrets
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_session_secret_key() -> str:
    """Generate a cryptographically random fallback when SESSION_SECRET_KEY is unset.

    A per-process random value prevents accidental reuse of a hardcoded secret
    across deployments, but also means sessions are invalidated on restart.
    Production deployments MUST set SESSION_SECRET_KEY explicitly.
    """
    return os.environ.get("SESSION_SECRET_KEY") or secrets.token_urlsafe(48)


class Settings(BaseSettings):
    app_name: str = "ELIZA AI"
    environment: str = "Production"
    host: str = "0.0.0.0"
    port: int = 8000
    frontend_origin: str = "https://elizaaivoice.vercel.app"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = ""
    openai_compat_base_url: str = ""
    openai_compat_api_key: str = ""
    openai_compat_model: str = ""
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:1b"
    freemodel_base_url: str = "https://freemodel.in/v1"
    freemodel_api_key: str = ""
    freemodel_model: str = "gemini-3.7-flash"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    comet_base_url: str = "https://api.comet.com/v1"
    comet_api_key: str = ""
    comet_model: str = "gpt-4o-mini"
    elevenlabs_api_key: str = ""
    elevenlabs_model_id: str = "eleven_multilingual_v2"
    mongodb_uri: str = ""
    mongodb_database: str = "eliza_ai"
    conversation_file: str = "data/conversations.json"
    default_provider: str = "auto"
    request_timeout_seconds: int = 180
    session_secret_key: str = ""
    rate_limit_per_minute: int = 60
    max_request_size_bytes: int = 1048576

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def __init__(self, **data):
        super().__init__(**data)
        if not self.session_secret_key:
            self.session_secret_key = _default_session_secret_key()

    @property
    def configured_origin(self) -> str:
        return self.frontend_origin.rstrip("/")

    @property
    def configured_origins(self):
        origins = [self.frontend_origin.rstrip("/")]
        additional = [
            "https://elizaaivoice.vercel.app",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ]
        for o in additional:
            if o not in origins:
                origins.append(o)
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
