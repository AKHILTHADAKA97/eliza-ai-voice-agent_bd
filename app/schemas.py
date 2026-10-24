from typing import Literal
from pydantic import BaseModel, Field

UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class MessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=15000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    conversation_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    provider: str = Field(default="auto", max_length=64, pattern=r"^[a-zA-Z0-9_\-]{1,64}$")
    model: str | None = Field(default=None, max_length=128)
    routing_mode: Literal["auto", "manual"] = "auto"
    system_prompt: str | None = Field(default=None, max_length=4000)
    history: list[MessageIn] = Field(default_factory=list, max_length=30)


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    answer: str
    provider: str
    model: str
    routing_mode: str
    created_at: str
    usage: dict | None = None


class SettingsOut(BaseModel):
    default_provider: str
    default_model: str
    active_provider: str | None = None
    active_model: str | None = None
    auto_speak: bool = True
    voice_name: str = ""
    voice_profile: str = "natural"
    speech_rate: float = 1.0
    response_style: str = "balanced"
    custom_system_prompt: str = ""
    theme: str = "green"
    detailed_responses: bool = False


class SettingsIn(BaseModel):
    default_provider: str | None = Field(default=None, max_length=64, pattern=r"^[a-zA-Z0-9_\-]{1,64}$")
    default_model: str | None = Field(default=None, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.\:\/]{1,128}$")
    auto_speak: bool | None = None
    voice_name: str | None = Field(default=None, max_length=128)
    voice_profile: Literal["natural", "warm", "energetic", "calm", "narrator", "professional"] | None = None
    speech_rate: float | None = Field(default=None, ge=0.5, le=2.0)
    response_style: Literal["balanced", "tutor", "expert", "concise", "creative", "coding"] | None = None
    custom_system_prompt: str | None = Field(default=None, max_length=4000)
    theme: str | None = Field(default=None, max_length=32, pattern=r"^[a-zA-Z0-9_\-]{1,32}$")
    detailed_responses: bool | None = None
