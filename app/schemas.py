from typing import Literal, Optional, List, Any
from pydantic import BaseModel, Field, ConfigDict

UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


# --- Core Chat & Settings Schemas ---

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
    voice_profile: str = "eliza"
    voice_lang: str = "en-IN"
    speech_recognition_lang: str = "en-IN"
    voice_engine: str = "auto"
    speech_rate: float = 1.0
    speech_pitch: float = 1.0
    response_style: str = "balanced"
    custom_system_prompt: str = ""
    theme: str = "green"
    detailed_responses: bool = False


class SettingsIn(BaseModel):
    default_provider: str | None = Field(default=None, max_length=64, pattern=r"^[a-zA-Z0-9_\-]{1,64}$")
    default_model: str | None = Field(default=None, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.\:\/]{1,128}$")
    auto_speak: bool | None = None
    voice_name: str | None = Field(default=None, max_length=128)
    voice_profile: str | None = Field(default=None, max_length=64)
    voice_lang: str | None = Field(default=None, max_length=32)
    speech_recognition_lang: str | None = Field(default=None, max_length=32)
    voice_engine: str | None = Field(default=None, max_length=32)
    speech_rate: float | None = Field(default=None, ge=0.5, le=2.0)
    speech_pitch: float | None = Field(default=None, ge=0.5, le=2.0)
    response_style: Literal["balanced", "tutor", "expert", "concise", "creative", "coding"] | None = None
    custom_system_prompt: str | None = Field(default=None, max_length=4000)
    theme: str | None = Field(default=None, max_length=32, pattern=r"^[a-zA-Z0-9_\-]{1,32}$")
    detailed_responses: bool | None = None


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=3000)
    voice_id: str | None = Field(default=None, max_length=128)
    voice_profile: str | None = Field(default=None, max_length=64)
    voice_lang: str | None = Field(default=None, max_length=32)


# --- Access Control & Usage Schemas ---

class EmailVerifyRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    device_type: str = Field(default="unknown", max_length=32)
    browser_name: str = Field(default="unknown", max_length=64)


class GoogleLoginRequest(BaseModel):
    email: str | None = Field(default=None, max_length=254)
    credential: str | None = Field(default=None, max_length=4096)
    name: str | None = Field(default=None, max_length=128)
    picture: str | None = Field(default=None, max_length=512)
    device_type: str = Field(default="unknown", max_length=32)
    browser_name: str = Field(default="unknown", max_length=64)


class AccessStatusResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    allowed: bool
    remaining_questions: int = Field(default=0, alias="remainingQuestions")
    blocked: bool = False
    blocked_until: str | None = Field(default=None, alias="blockedUntil")
    period_expires_at: str | None = Field(default=None, alias="periodExpiresAt")
    message: str
    session_token: str | None = Field(default=None, alias="sessionToken")
    email: str | None = None
    access_period_id: str | None = Field(default=None, alias="accessPeriodId")


class QuestionRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    request_id: str | None = Field(default=None, max_length=64)
    conversation_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    provider: str = Field(default="auto", max_length=64, pattern=r"^[a-zA-Z0-9_\-]{1,64}$")
    model: str | None = Field(default=None, max_length=128)
    routing_mode: Literal["auto", "manual"] = "auto"
    system_prompt: str | None = Field(default=None, max_length=4000)
    history: list[MessageIn] = Field(default_factory=list, max_length=30)
    device_type: str = Field(default="unknown", max_length=32)
    browser_name: str = Field(default="unknown", max_length=64)


class QuestionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str
    message_id: str
    answer: str
    provider: str
    model: str
    routing_mode: str
    created_at: str
    usage: dict | None = None
    remaining_questions: int = Field(..., alias="remainingQuestions")
    blocked: bool = False
    blocked_until: str | None = Field(default=None, alias="blockedUntil")


# --- Feedback Schemas ---

class FeedbackRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    category: str = Field(default="general", max_length=64)
    message: str = Field(min_length=10, max_length=5000)
    device_type: str = Field(default="unknown", max_length=32)
    browser_name: str = Field(default="unknown", max_length=64)


class FeedbackResponse(BaseModel):
    id: str
    status: str
    message: str


class FeedbackItem(BaseModel):
    id: str
    rating: int
    category: str
    created_at: str | None = None


class FeedbackStatusResponse(BaseModel):
    submitted: bool
    feedback: FeedbackItem | None = None


# --- Admin Schemas ---

class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class AdminUserOut(BaseModel):
    id: str
    username: str
    email: str


class AdminLoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    admin: AdminUserOut


class AdminFeedbackUpdateRequest(BaseModel):
    status: str = Field(pattern=r"^(new|reviewed|flagged|resolved)$")
    admin_notes: Optional[str] = Field(default=None, max_length=2000)


class BulkDeleteCustomersRequest(BaseModel):
    customer_ids: List[str] = Field(min_length=1, max_length=1000)


class BulkDeleteFeedbackRequest(BaseModel):
    feedback_ids: List[str] = Field(min_length=1, max_length=1000)


class BulkDeleteResponse(BaseModel):
    success: bool = True
    deleted_count: int
    message: str


class AdminCustomerUnblockResponse(BaseModel):
    success: bool = True
    message: str
    customer_id: str
    access_status: str = "eligible"
    remaining_questions: int = 3


# --- Aliases for Backward / Test Compatibility ---

VerifyEmailIn = EmailVerifyRequest
VerifyEmailOut = AccessStatusResponse
StartSessionIn = EmailVerifyRequest
StartSessionOut = AccessStatusResponse
QuestionIn = QuestionRequest
QuestionOut = QuestionResponse
FeedbackIn = FeedbackRequest
FeedbackOut = FeedbackResponse
AccessStatusOut = AccessStatusResponse
