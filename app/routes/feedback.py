"""Feedback API endpoints for visitor reviews and ratings."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Feedback, Customer
from app.schemas import FeedbackRequest, FeedbackResponse, FeedbackStatusResponse
from app.routes.access import get_current_session
from app.services.prompt_guard import redact_secrets
from app.core.security import general_limiter, get_client_identifier

router = APIRouter(prefix="/api/v1", tags=["feedback"])


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    body: FeedbackRequest,
    request: Request,
    session_data: dict = Depends(get_current_session),
    db: Session = Depends(get_db)
):
    """
    Save visitor feedback and star rating in the database.
    Tied to the server-verified customer session and access period.
    Does NOT count as a question submission and does NOT unlock a blocked user.
    """
    client_id = get_client_identifier(request)
    allowed, retry_after = general_limiter.is_allowed(f"fb:{client_id}", max_requests=10)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many feedback submissions. Please wait.",
            headers={"Retry-After": str(retry_after)},
        )

    customer_id = session_data.get("sub")
    access_period_id = session_data.get("access_period_id")

    # Sanitize message
    cleaned_message = redact_secrets(body.message.strip())
    if len(cleaned_message) < 10:
        raise HTTPException(status_code=400, detail="Feedback message must be at least 10 characters.")

    feedback_record = Feedback(
        customer_id=customer_id,
        access_period_id=access_period_id,
        rating=body.rating,
        category=body.category,
        message=cleaned_message,
        device_type=body.device_type,
        browser_name=body.browser_name,
        created_at=datetime.now(timezone.utc),
        status="new"
    )

    db.add(feedback_record)

    # Update customer activity timestamp
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer:
        customer.last_activity_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(feedback_record)

    return {
        "id": feedback_record.id,
        "status": "received",
        "message": "Thank you for your review and feedback! Your response has been recorded."
    }


@router.get("/feedback/status", response_model=FeedbackStatusResponse)
async def get_feedback_status(
    session_data: dict = Depends(get_current_session),
    db: Session = Depends(get_db)
):
    """Check if feedback has already been submitted for the current access period."""
    customer_id = session_data.get("sub")
    access_period_id = session_data.get("access_period_id")

    existing = (
        db.query(Feedback)
        .filter(Feedback.customer_id == customer_id, Feedback.access_period_id == access_period_id)
        .order_by(Feedback.created_at.desc())
        .first()
    )

    if not existing:
        return {"submitted": False, "feedback": None}

    return {
        "submitted": True,
        "feedback": {
            "id": existing.id,
            "rating": existing.rating,
            "category": existing.category,
            "created_at": existing.created_at.isoformat() if existing.created_at else None,
        }
    }

