import os
import secrets
from pathlib import Path
from functools import lru_cache
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Ensure environment variables are loaded directly from backend .env regardless of working directory
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _BACKEND_DIR / ".env"
if _ENV_FILE.exists():
    load_dotenv(dotenv_path=_ENV_FILE, override=False)
load_dotenv(override=False)


def _get_port_from_env() -> int:
    """Import port strictly from .env / environment variable."""
    env_port = os.getenv("PORT")
    if env_port and env_port.strip():
        try:
            return int(env_port.strip())
        except ValueError:
            pass
    return 8000


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
    port: int = _get_port_from_env()
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
    cors_origin_regex: str = r"^(https:\/\/(.*\.)?(vercel\.app|onrender\.com)|http:\/\/(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+))(:\d+)?$"

    model_config = SettingsConfigDict(env_file=str(_ENV_FILE) if _ENV_FILE.exists() else ".env", env_file_encoding="utf-8", extra="ignore")

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
            "http://localhost:5175",
            "http://127.0.0.1:5175",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
        for o in additional:
            if o not in origins:
                origins.append(o)
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
