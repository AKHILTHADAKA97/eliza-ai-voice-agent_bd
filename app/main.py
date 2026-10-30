import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request, Depends, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from app.core.config import get_settings
from app.core.security import (
    CorrelationIdMiddleware,
    SecurityHeadersMiddleware,
    RequestSizeLimitMiddleware,
    general_limiter,
    chat_limiter,
    get_client_identifier,
    is_valid_uuid,
)
from app.schemas import ChatRequest, ChatResponse, SettingsIn, SettingsOut, TTSRequest
from app.services.model_router import ModelRouter
from app.services import conversations
from app.services import prompt_guard
from app.services.elevenlabs import (
    VOICE_PROFILE_MAP,
    is_elevenlabs_configured,
    synthesize_speech,
)
from app.db import engine, Base, SessionLocal
import app.models # Register all SQLAlchemy models
from app.routes import access, questions, feedback, admin, auth
from app.services.admin_service import seed_default_admin

# Ensure all database tables exist on startup
Base.metadata.create_all(bind=engine)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("eliza.security")

settings = get_settings()
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure database tables and initial administrator account exist."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_default_admin(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    docs_url="/docs" if settings.environment == "development" else None,
    redoc_url="/redoc" if settings.environment == "development" else None,
    lifespan=lifespan,
)

# Multi-layer security middleware pipeline (executed in reverse registration order)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware, max_size_bytes=1048576) # 1MB limit
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.configured_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Register access control and admin dashboard routes
app.include_router(access.router)
app.include_router(questions.router)
app.include_router(feedback.router)
app.include_router(admin.router)
app.include_router(auth.router)


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicons", include_in_schema=False)
async def get_favicons():
    """Favicon endpoint to prevent 404 console errors."""
    return Response(content=b"", media_type="image/x-icon", status_code=204)

router = ModelRouter()


def _default_model_for(provider: str) -> str:
    """Maps a provider id to its configured default model."""
    mapping = {
        "freemodel": settings.freemodel_model,
        "groq": settings.groq_model,
        "comet": settings.comet_model,
        "openai": settings.openai_model,
        "openai-compatible": settings.openai_compat_model,
    }
    return mapping.get(provider, "") or ""


_user_settings = {
    "default_provider": settings.default_provider,
    "default_model": _default_model_for(settings.default_provider),
    "auto_speak": True,
    "voice_name": "",
    "voice_profile": "eliza",
    "voice_lang": "en-IN",
    "speech_recognition_lang": "en-IN",
    "voice_engine": "auto",
    "speech_rate": 1.0,
    "speech_pitch": 1.0,
    "response_style": "balanced",
    "custom_system_prompt": "",
    "theme": "green",
    "detailed_responses": False,
}

DEFAULT_SYSTEM_PROMPT = (
    "You are ELIZA AI, a helpful, accurate, and conversational AI assistant. "
    "Answer the user's actual question directly. "
    "Produce clean, well-structured Markdown with appropriate headings, short paragraphs, "
    "meaningful bullet points, numbered steps, tables, and correctly formatted code blocks whenever useful. "
    "Adapt the structure and length to the question: simple questions deserve concise answers; "
    "complex questions deserve detailed, organized explanations. Use simple English when appropriate. "
    "Avoid repetitive headings, unnecessary lists, excessive bold text, irrelevant content, and generic filler. "
    "Maintain conversation context across follow-up questions. "
    "Never force every response into the same template. "
    "Return clean Markdown that can be rendered correctly by the frontend."
)


def _get_active_provider_and_model() -> tuple[str, str]:
    cat = {p["id"]: p for p in router.catalog()}
    target = _user_settings.get("default_provider", settings.default_provider)
    if target == "auto":
        candidates = [settings.default_provider, "freemodel", "groq", "openai", "openai-compatible", "comet"]
        for cand in dict.fromkeys(candidates):
            if cand in cat and cat[cand]["available"]:
                return cand, cat[cand]["default_model"]
    elif target in cat and cat[target]["available"]:
        return target, _user_settings.get("default_model") or cat[target]["default_model"]
    return "none", "none"


_initial_prov, _initial_m = _get_active_provider_and_model()
if _initial_m != "none":
    _user_settings["default_model"] = _initial_m


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    logger.error(f"[REQ-{req_id}] Unhandled error at {request.url.path}: {exc}", exc_info=True)
    # Mask internal stack traces and server details in production responses
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal server error occurred. Please contact administrator.",
            "request_id": req_id,
        },
    )


def check_rate_limit(request: Request, is_chat: bool = False):
    client_id = get_client_identifier(request)
    limiter = chat_limiter if is_chat else general_limiter
    allowed, retry_after = limiter.is_allowed(client_id)
    if not allowed:
        logger.warning(f"Rate limit exceeded for client {client_id} on {request.url.path}")
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please wait before submitting more requests.",
            headers={"Retry-After": str(retry_after)},
        )


@app.get("/api/v1/health")
async def health(request: Request):
    check_rate_limit(request, is_chat=False)
    active_prov, active_m = _get_active_provider_and_model()
    return {
        "status": "ok",
        "app": settings.app_name,
        "active_provider": active_prov,
        "active_model": active_m,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/providers")
async def providers(request: Request):
    check_rate_limit(request, is_chat=False)
    active_prov, active_m = _get_active_provider_and_model()
    return {
        "providers": router.catalog(),
        "active_provider": active_prov,
        "active_model": active_m,
    }


@app.get("/api/v1/models")
async def models(request: Request):
    check_rate_limit(request, is_chat=False)
    active_prov, active_m = _get_active_provider_and_model()
    return {
        "providers": router.catalog(),
        "active_provider": active_prov,
        "active_model": active_m,
    }


@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request):
    check_rate_limit(request, is_chat=True)

    # 1. Validate model parameter against injection
    if body.model and not prompt_guard.is_valid_model_name(body.model):
        raise HTTPException(status_code=400, detail="Invalid model identifier.")

    session_id = request.headers.get("X-Session-ID") or "anonymous"

    # 2. Defense-in-depth prompt guarding & secret redaction
    sanitized_message = prompt_guard.sanitize_input(body.message)
    if not sanitized_message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    # 3. Detect image inputs and return user-friendly error
    image_indicators = ["image.png", "image.jpg", "image.jpeg", "image.gif", "image.webp",
                        "![", "data:image", "<img", "img src", "photo", "picture"]
    message_lower = sanitized_message.lower()
    for indicator in image_indicators:
        if indicator in message_lower:
            raise HTTPException(
                status_code=400,
                detail="This model does not support image input. Please send text questions only."
            )

    system_parts = [prompt_guard.CORE_SAFETY_INSTRUCTIONS]
    if body.system_prompt and body.system_prompt.strip():
        system_parts.append(f"INSTRUCTIONS:\n{prompt_guard.redact_secrets(body.system_prompt[:3000])}")
    else:
        system_parts.append(f"INSTRUCTIONS:\n{DEFAULT_SYSTEM_PROMPT}")

    system_content = "\n\n".join(system_parts)

    messages = [{"role": "system", "content": system_content}]
    for m in body.history[-20:]:
        safe_hist = prompt_guard.redact_secrets(m.content)
        messages.append({"role": m.role, "content": safe_hist})

    messages.append({
        "role": "user",
        "content": sanitized_message,
    })

    try:
        answer, provider, model, usage = await router.chat(
            messages, body.provider, body.model, body.routing_mode
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Provider chat error: {exc}", exc_info=True)
        raise HTTPException(status_code=502, detail="AI provider request failed.") from exc

    safe_answer = prompt_guard.clean_assistant_output(answer)
    saved = conversations.save_exchange(
        body.conversation_id, session_id, sanitized_message, safe_answer, provider, model
    )

    return {
        "conversation_id": saved["id"],
        "message_id": str(uuid.uuid4()),
        "answer": safe_answer,
        "provider": provider,
        "model": model,
        "routing_mode": body.routing_mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "usage": usage,
    }


@app.post("/api/v1/chat/stream")
async def chat_stream(body: ChatRequest, request: Request):
    check_rate_limit(request, is_chat=True)

    if body.model and not prompt_guard.is_valid_model_name(body.model):
        raise HTTPException(status_code=400, detail="Invalid model identifier.")

    session_id = request.headers.get("X-Session-ID") or "anonymous"
    sanitized_message = prompt_guard.sanitize_input(body.message)

    async def events():
        try:
            system_parts = [prompt_guard.CORE_SAFETY_INSTRUCTIONS]
            if body.system_prompt and body.system_prompt.strip():
                system_parts.append(f"INSTRUCTIONS:\n{prompt_guard.redact_secrets(body.system_prompt[:3000])}")
            else:
                system_parts.append(f"INSTRUCTIONS:\n{DEFAULT_SYSTEM_PROMPT}")

            system_content = "\n\n".join(system_parts)

            messages = [{"role": "system", "content": system_content}]
            for m in body.history[-20:]:
                messages.append({"role": m.role, "content": prompt_guard.redact_secrets(m.content)})
            messages.append({"role": "user", "content": sanitized_message})

            answer, provider, model, usage = await router.chat(
                messages, body.provider, body.model, body.routing_mode
            )
            safe_answer = prompt_guard.clean_assistant_output(answer)
            saved = conversations.save_exchange(
                body.conversation_id, session_id, sanitized_message, safe_answer, provider, model
            )

            for i in range(0, len(safe_answer), 32):
                yield "data: " + json.dumps({"type": "token", "text": safe_answer[i:i+32]}) + "\n\n"
            yield "data: " + json.dumps({
                "type": "done",
                "conversation_id": saved["id"],
                "provider": provider,
                "model": model,
                "usage": usage,
            }) + "\n\n"
        except Exception as exc:
            logger.error(f"Streaming error: {exc}", exc_info=True)
            yield "data: " + json.dumps({"type": "error", "message": "Failed to complete streaming response."}) + "\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/conversations")
async def list_conversations(request: Request):
    check_rate_limit(request, is_chat=False)
    session_id = request.headers.get("X-Session-ID")
    return {"conversations": conversations.list_conversations(session_id)}


@app.get("/api/v1/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request):
    check_rate_limit(request, is_chat=False)
    if not is_valid_uuid(conversation_id):
        raise HTTPException(status_code=400, detail="Invalid conversation ID format.")
    session_id = request.headers.get("X-Session-ID")
    result = conversations.get_conversation(conversation_id, session_id)
    if not result:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return result


@app.delete("/api/v1/conversations/{conversation_id}")
async def remove_conversation(conversation_id: str, request: Request):
    check_rate_limit(request, is_chat=False)
    if not is_valid_uuid(conversation_id):
        raise HTTPException(status_code=400, detail="Invalid conversation ID format.")
    session_id = request.headers.get("X-Session-ID")
    if not conversations.delete_conversation(conversation_id, session_id):
        raise HTTPException(status_code=404, detail="Conversation not found or access denied.")
    return {"deleted": True}


@app.get("/api/v1/settings")
async def get_user_settings(request: Request):
    check_rate_limit(request, is_chat=False)
    resp = dict(_user_settings)
    active_prov, active_m = _get_active_provider_and_model()
    resp["active_provider"] = active_prov
    resp["active_model"] = active_m
    resp["default_model"] = active_m if resp.get("default_provider") == "auto" else resp.get("default_model")
    return resp


@app.patch("/api/v1/settings")
async def update_user_settings(body: SettingsIn, request: Request):
    check_rate_limit(request, is_chat=False)
    patch = body.model_dump(exclude_unset=True)
    for key, value in patch.items():
        if key == "custom_system_prompt" and value:
            _user_settings[key] = prompt_guard.redact_secrets(value[:4000])
        else:
            _user_settings[key] = value
    # When the provider changes, sync the default model to the new provider
    if "default_provider" in patch and "default_model" not in patch:
        _user_settings["default_model"] = _default_model_for(_user_settings["default_provider"])
    active_prov, active_m = _get_active_provider_and_model()
    resp = dict(_user_settings)
    resp["active_provider"] = active_prov
    resp["active_model"] = active_m
    return resp


@app.get("/api/v1/audio/voices")
async def get_voice_catalog(request: Request):
    """Return voice profiles, supported Indian languages, and ElevenLabs configuration status."""
    check_rate_limit(request, is_chat=False)
    return {
        "elevenlabs_configured": is_elevenlabs_configured(),
        "profiles": list(VOICE_PROFILE_MAP.values()),
        "default_profile": "eliza",
    }


@app.post("/api/v1/audio/tts")
async def synthesize_text_to_speech(body: TTSRequest, request: Request):
    """
    Synthesize natural voice audio using ElevenLabs neural voices.
    Falls back gracefully with fallback instructions if ElevenLabs is not configured or fails.
    """
    check_rate_limit(request, is_chat=False)
    if not is_elevenlabs_configured():
        return JSONResponse(
            status_code=200,
            content={
                "available": False,
                "reason": "elevenlabs_not_configured",
                "message": "ElevenLabs API key not configured on backend. Use browser Web Speech API fallback.",
            },
        )

    audio_bytes = await synthesize_speech(
        text=body.text,
        voice_id=body.voice_id,
        voice_profile=body.voice_profile,
    )

    if not audio_bytes:
        return JSONResponse(
            status_code=200,
            content={
                "available": False,
                "reason": "elevenlabs_synthesis_failed",
                "message": "ElevenLabs synthesis failed or rate limited. Fallback to Web Speech API.",
            },
        )

    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Content-Disposition": 'inline; filename="speech.mp3"',
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=(settings.environment.lower() == "development"),
    )
