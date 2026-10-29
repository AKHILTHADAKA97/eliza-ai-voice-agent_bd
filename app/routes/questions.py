"""Question submission endpoint protected by atomic usage limit and 20-day block."""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import QuestionRequest, QuestionResponse
from app.routes.access import get_current_session
from app.services.access_control import record_question_deduction_atomic
from app.services.model_router import ModelRouter
from app.services import conversations, prompt_guard
from app.core.security import chat_limiter, get_client_identifier

router = APIRouter(prefix="/api/v1", tags=["questions"])

# Singleton model router shared with app
_ai_router = ModelRouter()

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


@router.post("/questions", response_model=QuestionResponse)
async def submit_question(
    body: QuestionRequest,
    request: Request,
    session_data: dict = Depends(get_current_session),
    db: Session = Depends(get_db)
):
    """
    Submit a question through Eliza AI.
    Strictly verifies email access, enforces the 3-question quota atomically in the database,
    and returns remaining question balance.
    """
    client_id = get_client_identifier(request)
    allowed, retry_after = chat_limiter.is_allowed(client_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please wait before submitting more questions.",
            headers={"Retry-After": str(retry_after)},
        )

    customer_id = session_data.get("sub")
    access_period_id = session_data.get("access_period_id")

    # 1. Atomic DB Question Limit Verification & Deduction
    allowed_quota, remaining, blocked_until, err_msg = record_question_deduction_atomic(
        db=db,
        customer_id=customer_id,
        access_period_id=access_period_id,
        request_id=body.request_id,
        device_type=body.device_type,
        session_id=request.headers.get("X-Session-ID", "")
    )

    if not allowed_quota:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "allowed": False,
                "remainingQuestions": 0,
                "blocked": True,
                "blockedUntil": blocked_until,
                "message": err_msg or "Your limit is over."
            }
        )

    # 2. Defense-in-depth sanitization and prompt safety
    sanitized_message = prompt_guard.sanitize_input(body.message)
    if not sanitized_message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    if body.model and not prompt_guard.is_valid_model_name(body.model):
        raise HTTPException(status_code=400, detail="Invalid model identifier.")

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

    # 3. Model execution
    try:
        answer, provider, model, usage = await _ai_router.chat(
            messages, body.provider, body.model, body.routing_mode
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="AI provider request failed.") from exc

    safe_answer = prompt_guard.clean_assistant_output(answer)
    session_id = request.headers.get("X-Session-ID") or "anonymous"
    saved = conversations.save_exchange(
        body.conversation_id, session_id, sanitized_message, safe_answer, provider, model
    )

    is_now_blocked = (remaining <= 0)

    return {
        "conversation_id": saved["id"],
        "message_id": str(uuid.uuid4()),
        "answer": safe_answer,
        "provider": provider,
        "model": model,
        "routing_mode": body.routing_mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "usage": usage,
        "remainingQuestions": remaining,
        "blocked": is_now_blocked,
        "blockedUntil": blocked_until if is_now_blocked else None
    }

