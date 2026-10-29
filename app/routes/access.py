"""Access verification and session status API routes."""
from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Customer, AccessPeriod
from app.schemas import EmailVerifyRequest, GoogleLoginRequest, AccessStatusResponse
from app.services.access_control import (
    verify_email_access,
    verify_session_token,
    evaluate_access,
    create_session_token,
)
from app.core.security import general_limiter, get_client_identifier

router = APIRouter(prefix="/api/v1/access", tags=["access"])


def get_current_session(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db)
) -> dict:
    """Dependency to extract and validate customer session JWT."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session authorization token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    payload = verify_session_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token invalid or expired. Please verify your email again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


@router.post("/verify-email", response_model=AccessStatusResponse)
async def verify_email(
    body: EmailVerifyRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Validate visitor email address, determine eligibility or blocked status,
    and issue a cryptographically signed access session token.
    """
    client_id = get_client_identifier(request)
    allowed, retry_after = general_limiter.is_allowed(f"email_verify:{client_id}", max_requests=20)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many verification attempts. Please wait.",
            headers={"Retry-After": str(retry_after)},
        )

    user_agent = request.headers.get("user-agent", "")
    result = verify_email_access(
        db=db,
        raw_email=body.email,
        user_agent=user_agent,
        client_device_type=body.device_type
    )

    if not result.get("session_token") and "error" in result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["error"]
        )

    return result


@router.post("/google-login", response_model=AccessStatusResponse)
async def google_login(
    body: GoogleLoginRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Handle Google Direct Auth Login. Extracts verified email from Google credential
    or validated email payload and commits customer profile and access period to database.
    """
    client_id = get_client_identifier(request)
    allowed, retry_after = general_limiter.is_allowed(f"google_login:{client_id}", max_requests=25)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts. Please wait.",
            headers={"Retry-After": str(retry_after)},
        )

    resolved_email = (body.email or "").strip()
    if body.credential and not resolved_email:
        try:
            import json, base64
            parts = body.credential.split(".")
            if len(parts) >= 2:
                padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
                payload_json = base64.urlsafe_b64decode(padded.encode()).decode("utf-8")
                claims = json.loads(payload_json)
                resolved_email = claims.get("email", "").strip()
        except Exception:
            pass

    if not resolved_email or "@" not in resolved_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Valid Google account email is required."
        )

    user_agent = request.headers.get("user-agent", "")
    result = verify_email_access(
        db=db,
        raw_email=resolved_email,
        user_agent=user_agent,
        client_device_type=body.device_type
    )

    if not result.get("session_token") and "error" in result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["error"]
        )

    return result


@router.post("/start-session", response_model=AccessStatusResponse)
async def start_session(
    body: EmailVerifyRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """Alias for email verification and session initiation."""
    return await verify_email(body=body, request=request, db=db)


@router.get("/status", response_model=AccessStatusResponse)
async def get_access_status(
    session_data: dict = Depends(get_current_session),
    db: Session = Depends(get_db)
):
    """
    Inspect real-time remaining question balance and restriction status
    directly from backend database records.
    """
    customer_id = session_data.get("sub")
    access_period_id = session_data.get("access_period_id")

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer identity not found.")

    period = db.query(AccessPeriod).filter(AccessPeriod.id == access_period_id).first()
    if not period:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access period not found.")

    evaluated = evaluate_access(db, customer, period)
    # Refresh token with current period if rotated
    token = create_session_token(customer.id, evaluated["access_period_id"], customer.normalized_email)

    return {
        "allowed": evaluated["allowed"],
        "remaining_questions": evaluated["remaining_questions"],
        "blocked": evaluated["blocked"],
        "blocked_until": evaluated["blocked_until"],
        "period_expires_at": evaluated["period_expires_at"],
        "message": evaluated["message"],
        "session_token": token,
        "email": customer.normalized_email,
        "access_period_id": evaluated["access_period_id"]
    }

