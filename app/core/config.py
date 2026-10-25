from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ELIZA AI"
    environment: str = "Production"
    host: str = "127.0.0.1"
    port: int = 0.0.0.0
    frontend_origin: str = "https://elizaai-five.vercel.app/"
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
    elevenlabs_api_key: str = ""
    elevenlabs_model_id: str = "eleven_multilingual_v2"
    mongodb_uri: str = ""
    mongodb_database: str = "eliza_ai"
    conversation_file: str = "data/conversations.json"
    default_provider: str = "auto"
    request_timeout_seconds: int = 180
    session_secret_key: str = "change-in-production-secret-key-32chars"
    rate_limit_per_minute: int = 60
    max_request_size_bytes: int = 1048576

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def configured_origin(self) -> str:
        return self.frontend_origin.rstrip("/") 

    @property
    def configured_origins(self):
        origins = [self.frontend_origin.rstrip("/")]
        if self.environment == "development":
            # Allow local development origins only in development mode
            for o in ["https://elizaai-five.vercel.app/"]:
                if o not in origins:
                    origins.append(o)
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
