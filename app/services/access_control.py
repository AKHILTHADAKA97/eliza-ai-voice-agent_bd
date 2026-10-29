"""Access control and usage limit service for Eliza AI."""
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.core.config import get_settings
from app.models import Customer, AccessPeriod, QuestionUsage, Visit

MAX_QUESTIONS_PER_PERIOD = 3
PERIOD_DURATION_DAYS = 20
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure datetime has UTC timezone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_email(email: str) -> str:
    """Normalize email: trim whitespace and lowercase."""
    if not email:
        return ""
    return email.strip().lower()


def validate_email_format(email: str) -> bool:
    """Validate RFC-compliant email structure."""
    if not email or len(email) > 254:
        return False
    return bool(EMAIL_REGEX.match(email.strip()))


def create_session_token(customer_id: str, access_period_id: str, email: str) -> str:
    """Generate a signed JWT session token for client requests."""
    settings = get_settings()
    expire = now_utc() + timedelta(hours=24)
    payload = {
        "sub": customer_id,
        "access_period_id": access_period_id,
        "email": email,
        "type": "customer_session",
        "exp": expire,
        "iat": now_utc(),
    }
    return jwt.encode(payload, settings.session_secret_key, algorithm="HS256")


def verify_session_token(token: str) -> Optional[dict]:
    """Verify and decode customer session JWT."""
    if not token:
        return None
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.session_secret_key, algorithms=["HS256"])
        if payload.get("type") != "customer_session":
            return None
        return payload
    except JWTError:
        return None


def parse_device_info(user_agent_str: str, client_device_type: str = "") -> Tuple[str, str]:
    """Extract lightweight device category and browser family."""
    device_type = "desktop"
    browser = "unknown"

    if client_device_type in ("mobile", "tablet", "desktop"):
        device_type = client_device_type

    if user_agent_str:
        ua_lower = user_agent_str.lower()
        if "ipad" in ua_lower or "tablet" in ua_lower:
            device_type = "tablet"
        elif "mobile" in ua_lower or "android" in ua_lower or "iphone" in ua_lower:
            device_type = "mobile"
        elif not client_device_type:
            device_type = "desktop"

        if "edg" in ua_lower:
            browser = "Edge"
        elif "chrome" in ua_lower:
            browser = "Chrome"
        elif "safari" in ua_lower and "chrome" not in ua_lower:
            browser = "Safari"
        elif "firefox" in ua_lower:
            browser = "Firefox"
        elif "opera" in ua_lower or "opr" in ua_lower:
            browser = "Opera"

    return device_type, browser


def record_visit(
    db: Session,
    customer_id: str,
    access_period_id: str,
    device_category: str = "unknown",
    browser_family: str = "unknown"
) -> None:
    """Record unique daily visit without inflating on refresh."""
    today = now_utc().strftime("%Y-%m-%d")
    existing = db.query(Visit).filter(
        Visit.customer_id == customer_id,
        Visit.visit_date == today,
        Visit.access_period_id == access_period_id
    ).first()

    if not existing:
        visit = Visit(
            customer_id=customer_id,
            access_period_id=access_period_id,
            visit_date=today,
            device_category=device_category,
            browser_family=browser_family,
            created_at=now_utc()
        )
        db.add(visit)
        db.commit()


def get_or_create_customer(
    db: Session,
    normalized_email: str,
    device_type: str = "",
    browser_name: str = ""
) -> Customer:
    """Retrieve existing customer or initialize a new customer profile."""
    customer = db.query(Customer).filter(Customer.normalized_email == normalized_email).first()
    now = now_utc()
    if customer:
        customer.last_seen_at = now
        customer.last_activity_at = now
        if device_type:
            customer.last_device_type = device_type
        db.commit()
        db.refresh(customer)
        return customer

    customer = Customer(
        normalized_email=normalized_email,
        created_at=now,
        first_seen_at=now,
        last_seen_at=now,
        last_activity_at=now,
        last_device_type=device_type,
        status="active"
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def get_or_rotate_access_period(db: Session, customer: Customer) -> AccessPeriod:
    """
    Ensure customer has an active 20-day period.
    If the latest period has expired, start a new 20-day access period with 3 fresh questions,
    preserving all historical periods for reporting.
    """
    now = now_utc()
    latest_period = db.query(AccessPeriod).filter(
        AccessPeriod.customer_id == customer.id
    ).order_by(AccessPeriod.created_at.desc()).first()

    if latest_period:
        # Check if period is still active (period_expires_at in future)
        if ensure_utc(latest_period.period_expires_at) > now:
            return latest_period
        else:
            # Mark old period as expired if not already
            if latest_period.status != "expired":
                latest_period.status = "expired"
                db.commit()

    # Create new 20-day access period
    period_start = now
    period_expires = now + timedelta(days=PERIOD_DURATION_DAYS)
    new_period = AccessPeriod(
        customer_id=customer.id,
        period_started_at=period_start,
        period_expires_at=period_expires,
        questions_used=0,
        status="active",
        created_at=now,
        updated_at=now
    )
    db.add(new_period)
    db.commit()
    db.refresh(new_period)
    return new_period


def evaluate_access(db: Session, customer: Customer, period: AccessPeriod) -> dict:
    """Calculate whether customer is currently allowed or blocked."""
    now = now_utc()

    # If period has expired, rotate immediately
    if ensure_utc(period.period_expires_at) <= now:
        period = get_or_rotate_access_period(db, customer)

    remaining = max(0, MAX_QUESTIONS_PER_PERIOD - period.questions_used)
    is_blocked = (period.questions_used >= MAX_QUESTIONS_PER_PERIOD) or (period.status == "blocked")

    blocked_until_iso = None
    if is_blocked:
        # Blocked until period expires (20 days from start)
        blocked_until = period.blocked_until or period.period_expires_at
        blocked_until_iso = blocked_until.isoformat()

    return {
        "allowed": not is_blocked and remaining > 0,
        "remaining_questions": remaining,
        "blocked": is_blocked,
        "blocked_until": blocked_until_iso,
        "period_expires_at": period.period_expires_at.isoformat(),
        "customer_id": customer.id,
        "access_period_id": period.id,
        "email": customer.normalized_email,
        "message": "Your limit is over." if is_blocked else ("Access granted" if remaining > 0 else "Limit reached")
    }


def verify_email_access(
    db: Session,
    raw_email: str,
    user_agent: str = "",
    client_device_type: str = ""
) -> dict:
    """Handle visitor email verification entrypoint."""
    normalized = normalize_email(raw_email)
    if not validate_email_format(normalized):
        return {
            "allowed": False,
            "error": "Invalid email address format.",
            "remaining_questions": 0,
            "blocked": False,
        }

    device_type, browser = parse_device_info(user_agent, client_device_type)
    customer = get_or_create_customer(db, normalized, device_type, browser)
    period = get_or_rotate_access_period(db, customer)

    record_visit(db, customer.id, period.id, device_type, browser)

    status = evaluate_access(db, customer, period)
    token = create_session_token(customer.id, period.id, customer.normalized_email)

    return {
        "allowed": status["allowed"],
        "remaining_questions": status["remaining_questions"],
        "blocked": status["blocked"],
        "blocked_until": status["blocked_until"],
        "period_expires_at": status["period_expires_at"],
        "message": status["message"],
        "session_token": token,
        "email": customer.normalized_email,
        "access_period_id": period.id
    }


def record_question_deduction_atomic(
    db: Session,
    customer_id: str,
    access_period_id: str,
    request_id: Optional[str] = None,
    device_type: str = "unknown",
    session_id: str = ""
) -> Tuple[bool, int, Optional[str], Optional[str]]:
    """
    Atomically verify quota and increment question usage.
    Returns: (is_allowed, remaining_questions, blocked_until, error_message)
    """
    now = now_utc()

    # If request_id given, check if already recorded for idempotency
    if request_id:
        existing_usage = db.query(QuestionUsage).filter(
            QuestionUsage.request_id == request_id
        ).first()
        if existing_usage:
            # Already accepted under this request_id
            period = db.query(AccessPeriod).filter(AccessPeriod.id == access_period_id).first()
            remaining = max(0, MAX_QUESTIONS_PER_PERIOD - (period.questions_used if period else 0))
            return True, remaining, (period.blocked_until.isoformat() if period and period.blocked_until else None), None

    try:
        # Query and lock access period for concurrency safety
        period = (
            db.query(AccessPeriod)
            .filter(AccessPeriod.id == access_period_id, AccessPeriod.customer_id == customer_id)
            .with_for_update()
            .first()
        )
    except Exception:
        # SQLite doesn't support SELECT ... FOR UPDATE, fallback to standard query inside transaction
        period = (
            db.query(AccessPeriod)
            .filter(AccessPeriod.id == access_period_id, AccessPeriod.customer_id == customer_id)
            .first()
        )

    if not period:
        return False, 0, None, "Active access period not found."

    # If period has expired, rotation needed
    if ensure_utc(period.period_expires_at) <= now:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if customer:
            period = get_or_rotate_access_period(db, customer)

    if period.questions_used >= MAX_QUESTIONS_PER_PERIOD or period.status == "blocked":
        blocked_until = period.blocked_until or period.period_expires_at
        return False, 0, blocked_until.isoformat(), "Your limit is over."

    # First accepted question timestamp
    if period.questions_used == 0 or period.first_question_at is None:
        period.first_question_at = now

    period.questions_used += 1
    period.updated_at = now

    remaining = MAX_QUESTIONS_PER_PERIOD - period.questions_used

    # If reached maximum of 3, block for remainder of the 20-day period
    blocked_until_iso = None
    if remaining <= 0:
        period.status = "blocked"
        period.blocked_at = now
        period.blocked_until = period.period_expires_at
        blocked_until_iso = period.blocked_until.isoformat()

    # Record question usage
    usage = QuestionUsage(
        customer_id=customer_id,
        access_period_id=period.id,
        request_id=request_id,
        status="accepted",
        device_type=device_type,
        session_id=session_id,
        created_at=now
    )
    db.add(usage)

    # Update customer activity
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer:
        customer.last_activity_at = now

    db.commit()
    return True, remaining, blocked_until_iso, None
