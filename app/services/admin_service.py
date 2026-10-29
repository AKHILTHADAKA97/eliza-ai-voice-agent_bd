"""Admin authentication and analytics service for Eliza AI."""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple
from jose import JWTError, jwt
import bcrypt
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, asc, distinct, and_, or_

from app.core.config import get_settings
from app.models import Admin, Customer, AccessPeriod, QuestionUsage, Feedback, Visit


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    pw_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pw_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password using bcrypt."""
    try:
        pw_bytes = plain_password.encode("utf-8")[:72]
        hash_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(pw_bytes, hash_bytes)
    except Exception:
        return False


def seed_default_admin(db: Session) -> Admin:
    """Create default initial administrator credentials if no admins exist."""
    admin_count = db.query(Admin).count()
    if admin_count > 0:
        return db.query(Admin).first()

    default_username = os.environ.get("ADMIN_USERNAME", "admin").strip()
    default_password = os.environ.get("ADMIN_PASSWORD", "ElizaAdmin2026!").strip()
    default_email = os.environ.get("ADMIN_EMAIL", "admin@eliza.ai").strip()

    admin = Admin(
        username=default_username,
        email=default_email,
        password_hash=hash_password(default_password),
        is_active=True,
        created_at=now_utc()
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def authenticate_admin(db: Session, username: str, password: str) -> Optional[Admin]:
    """Verify admin login credentials."""
    admin = db.query(Admin).filter(
        or_(Admin.username == username.strip(), Admin.email == username.strip().lower())
    ).first()
    if not admin or not admin.is_active:
        return None
    if not verify_password(password, admin.password_hash):
        return None

    admin.last_login_at = now_utc()
    db.commit()
    return admin


def create_admin_token(admin: Admin) -> str:
    """Generate admin authentication JWT valid for 8 hours."""
    settings = get_settings()
    expire = now_utc() + timedelta(hours=8)
    payload = {
        "sub": admin.id,
        "username": admin.username,
        "email": admin.email,
        "role": "admin",
        "exp": expire,
        "iat": now_utc(),
    }
    return jwt.encode(payload, settings.session_secret_key, algorithm="HS256")


def verify_admin_token(token: str) -> Optional[dict]:
    """Validate admin JWT."""
    if not token:
        return None
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.session_secret_key, algorithms=["HS256"])
        if payload.get("role") != "admin":
            return None
        return payload
    except JWTError:
        return None


# --- Analytics & Overview Helpers ---

def get_date_bounds(preset: str, custom_start: Optional[str] = None, custom_end: Optional[str] = None) -> Tuple[datetime, datetime]:
    """Calculate UTC start and end bounds for given preset or custom filter."""
    now = now_utc()
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    today_end = today_start + timedelta(days=1) - timedelta(microseconds=1)

    if preset == "today":
        return today_start, today_end
    elif preset == "yesterday":
        yest_start = today_start - timedelta(days=1)
        yest_end = today_start - timedelta(microseconds=1)
        return yest_start, yest_end
    elif preset == "last7days":
        start = today_start - timedelta(days=6)
        return start, today_end
    elif preset == "last30days":
        start = today_start - timedelta(days=29)
        return start, today_end
    elif preset == "thismonth":
        start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        return start, today_end
    elif preset == "custom" and custom_start and custom_end:
        try:
            start = datetime.fromisoformat(custom_start).replace(tzinfo=timezone.utc)
            end = datetime.fromisoformat(custom_end).replace(tzinfo=timezone.utc)
            return start, end
        except Exception:
            pass

    # Default to last 30 days
    return today_start - timedelta(days=29), today_end


def get_dashboard_overview(
    db: Session,
    preset: str = "last30days",
    custom_start: Optional[str] = None,
    custom_end: Optional[str] = None
) -> Dict[str, Any]:
    """Compute high-level summary cards and time-series analytics."""
    start_dt, end_dt = get_date_bounds(preset, custom_start, custom_end)
    now = now_utc()
    today_str = now.strftime("%Y-%m-%d")

    # 1. High-level Summary Cards
    total_emails = db.query(Customer).count()

    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    new_emails_today = db.query(Customer).filter(Customer.created_at >= today_start).count()

    unique_visitors_today = db.query(func.count(distinct(Visit.customer_id))).filter(
        Visit.visit_date == today_str
    ).scalar() or 0

    # Total access sessions today (count of visits records today)
    total_sessions_today = db.query(Visit).filter(Visit.visit_date == today_str).count()

    total_questions_today = db.query(QuestionUsage).filter(
        QuestionUsage.created_at >= today_start
    ).count()

    # Blocked emails (active periods currently blocked)
    currently_blocked = db.query(AccessPeriod).filter(
        AccessPeriod.status == "blocked",
        AccessPeriod.period_expires_at > now
    ).count()

    eligible_emails = db.query(AccessPeriod).filter(
        AccessPeriod.status == "active",
        AccessPeriod.questions_used < 3,
        AccessPeriod.period_expires_at > now
    ).count()

    total_feedback = db.query(Feedback).count()
    avg_rating_res = db.query(func.avg(Feedback.rating)).scalar()
    avg_rating = round(float(avg_rating_res), 1) if avg_rating_res is not None else 0.0

    # 2. Time-Series Data over selected range (Daily buckets)
    # Generate continuous list of dates
    cur = start_dt
    days_list = []
    while cur <= end_dt:
        days_list.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)

    # Daily unique visitors from Visit table
    visitor_counts = dict(
        db.query(Visit.visit_date, func.count(distinct(Visit.customer_id)))
        .filter(Visit.visit_date.in_(days_list))
        .group_by(Visit.visit_date)
        .all()
    )

    # Daily new email registrations
    reg_rows = (
        db.query(
            func.strftime("%Y-%m-%d", Customer.created_at).label("day"),
            func.count(Customer.id)
        )
        .filter(Customer.created_at >= start_dt, Customer.created_at <= end_dt)
        .group_by("day")
        .all()
    )
    reg_counts = {r[0]: r[1] for r in reg_rows}

    # Daily question usage
    q_rows = (
        db.query(
            func.strftime("%Y-%m-%d", QuestionUsage.created_at).label("day"),
            func.count(QuestionUsage.id)
        )
        .filter(QuestionUsage.created_at >= start_dt, QuestionUsage.created_at <= end_dt)
        .group_by("day")
        .all()
    )
    q_counts = {r[0]: r[1] for r in q_rows}

    daily_visitors_series = [{"date": d, "count": visitor_counts.get(d, 0)} for d in days_list]
    daily_registrations_series = [{"date": d, "count": reg_counts.get(d, 0)} for d in days_list]
    daily_questions_series = [{"date": d, "count": q_counts.get(d, 0)} for d in days_list]

    # 3. Feedback Rating Distribution (1 to 5 stars)
    rating_counts_raw = dict(
        db.query(Feedback.rating, func.count(Feedback.id))
        .group_by(Feedback.rating)
        .all()
    )
    rating_distribution = {star: rating_counts_raw.get(star, 0) for star in range(1, 6)}

    # 4. Device Distribution (from Visit table)
    device_counts_raw = dict(
        db.query(Visit.device_category, func.count(Visit.id))
        .filter(Visit.created_at >= start_dt, Visit.created_at <= end_dt)
        .group_by(Visit.device_category)
        .all()
    )
    device_distribution = {
        "desktop": device_counts_raw.get("desktop", 0),
        "mobile": device_counts_raw.get("mobile", 0),
        "tablet": device_counts_raw.get("tablet", 0),
        "unknown": device_counts_raw.get("unknown", 0),
    }

    return {
        "metrics": {
            "total_emails": total_emails,
            "new_emails_today": new_emails_today,
            "unique_visitors_today": unique_visitors_today,
            "total_sessions_today": total_sessions_today,
            "total_questions_today": total_questions_today,
            "currently_blocked": currently_blocked,
            "eligible_emails": eligible_emails,
            "total_feedback": total_feedback,
            "average_rating": avg_rating,
        },
        "charts": {
            "daily_visitors": daily_visitors_series,
            "daily_registrations": daily_registrations_series,
            "daily_questions": daily_questions_series,
            "rating_distribution": rating_distribution,
            "device_distribution": device_distribution,
        },
        "range": {
            "preset": preset,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
        }
    }


# --- Email Management & Analytics ---

def get_emails_list(
    db: Session,
    page: int = 1,
    per_page: int = 15,
    search: str = "",
    status_filter: str = "",
    sort_by: str = "newest",
    question_usage_filter: Optional[int] = None,
    device_filter: str = "",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> Dict[str, Any]:
    """Retrieve searchable, filterable, and paginated customer records."""
    query = db.query(Customer)

    if search.strip():
        query = query.filter(Customer.normalized_email.ilike(f"%{search.strip().lower()}%"))

    if device_filter.strip():
        query = query.filter(Customer.last_device_type == device_filter.strip().lower())

    if start_date:
        try:
            s_dt = datetime.fromisoformat(start_date)
            query = query.filter(Customer.first_seen_at >= s_dt)
        except Exception:
            pass

    if end_date:
        try:
            e_dt = datetime.fromisoformat(end_date)
            query = query.filter(Customer.first_seen_at <= e_dt)
        except Exception:
            pass

    if sort_by == "oldest":
        query = query.order_by(asc(Customer.created_at))
    elif sort_by == "most_active":
        query = query.order_by(desc(Customer.last_activity_at))
    else:  # newest
        query = query.order_by(desc(Customer.created_at))

    total_records = query.count()
    offset = (page - 1) * per_page
    customers = query.offset(offset).limit(per_page).all()

    now = now_utc()
    items = []
    for c in customers:
        # Latest access period
        latest_period = (
            db.query(AccessPeriod)
            .filter(AccessPeriod.customer_id == c.id)
            .order_by(desc(AccessPeriod.created_at))
            .first()
        )

        questions_used = latest_period.questions_used if latest_period else 0
        remaining_questions = max(0, 3 - questions_used)

        is_blocked = False
        blocked_until = None
        period_expires = None
        current_status = "eligible"

        if latest_period:
            period_expires = latest_period.period_expires_at.isoformat() if latest_period.period_expires_at else None
            if ensure_utc(latest_period.period_expires_at) <= now:
                current_status = "expired"
            elif latest_period.status == "blocked" or questions_used >= 3:
                is_blocked = True
                current_status = "blocked"
                blocked_until = (latest_period.blocked_until or latest_period.period_expires_at).isoformat()
            else:
                current_status = "active"

        # Apply in-memory status or question filters if set
        if status_filter:
            if status_filter == "blocked" and not is_blocked:
                continue
            if status_filter == "eligible" and (is_blocked or remaining_questions <= 0):
                continue
            if status_filter == "active" and current_status != "active":
                continue
            if status_filter == "expired" and current_status != "expired":
                continue

        if question_usage_filter is not None and questions_used != question_usage_filter:
            continue

        # Visit count & session count
        visits_count = db.query(Visit).filter(Visit.customer_id == c.id).count()
        question_count = db.query(QuestionUsage).filter(QuestionUsage.customer_id == c.id).count()

        items.append({
            "id": c.id,
            "email": c.normalized_email,
            "first_seen_at": c.first_seen_at.isoformat() if c.first_seen_at else None,
            "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
            "last_activity_at": c.last_activity_at.isoformat() if c.last_activity_at else None,
            "first_question_at": latest_period.first_question_at.isoformat() if latest_period and latest_period.first_question_at else None,
            "questions_used": f"{questions_used}/3",
            "questions_used_number": questions_used,
            "remaining_questions": remaining_questions,
            "visits_count": visits_count,
            "total_questions": question_count,
            "device_category": c.last_device_type or "unknown",
            "access_status": current_status,
            "blocked": is_blocked,
            "blocked_until": blocked_until,
            "current_period_expires_at": period_expires
        })

    return {
        "items": items,
        "total": total_records,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total_records + per_page - 1) // per_page)
    }


def get_customer_detail(db: Session, customer_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve full customer history, access periods, question records, and feedback."""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        return None

    periods = (
        db.query(AccessPeriod)
        .filter(AccessPeriod.customer_id == customer_id)
        .order_by(desc(AccessPeriod.created_at))
        .all()
    )

    questions = (
        db.query(QuestionUsage)
        .filter(QuestionUsage.customer_id == customer_id)
        .order_by(desc(QuestionUsage.created_at))
        .all()
    )

    feedbacks = (
        db.query(Feedback)
        .filter(Feedback.customer_id == customer_id)
        .order_by(desc(Feedback.created_at))
        .all()
    )

    visits = (
        db.query(Visit)
        .filter(Visit.customer_id == customer_id)
        .order_by(desc(Visit.created_at))
        .all()
    )

    return {
        "customer": {
            "id": customer.id,
            "email": customer.normalized_email,
            "first_seen_at": customer.first_seen_at.isoformat() if customer.first_seen_at else None,
            "last_seen_at": customer.last_seen_at.isoformat() if customer.last_seen_at else None,
            "last_activity_at": customer.last_activity_at.isoformat() if customer.last_activity_at else None,
            "device_type": customer.last_device_type or "unknown",
            "status": customer.status,
        },
        "periods": [
            {
                "id": p.id,
                "period_started_at": p.period_started_at.isoformat() if p.period_started_at else None,
                "period_expires_at": p.period_expires_at.isoformat() if p.period_expires_at else None,
                "first_question_at": p.first_question_at.isoformat() if p.first_question_at else None,
                "blocked_at": p.blocked_at.isoformat() if p.blocked_at else None,
                "blocked_until": p.blocked_until.isoformat() if p.blocked_until else None,
                "questions_used": p.questions_used,
                "status": p.status,
            }
            for p in periods
        ],
        "questions": [
            {
                "id": q.id,
                "access_period_id": q.access_period_id,
                "status": q.status,
                "device_type": q.device_type,
                "created_at": q.created_at.isoformat() if q.created_at else None,
            }
            for q in questions
        ],
        "feedbacks": [
            {
                "id": f.id,
                "rating": f.rating,
                "category": f.category,
                "message": f.message,
                "device_type": f.device_type,
                "browser_name": f.browser_name,
                "status": f.status,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in feedbacks
        ],
        "visits": [
            {
                "id": v.id,
                "visit_date": v.visit_date,
                "device_category": v.device_category,
                "browser_family": v.browser_family,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in visits
        ]
    }


# --- Feedback Management ---

def get_feedback_list(
    db: Session,
    page: int = 1,
    per_page: int = 15,
    search: str = "",
    rating_filter: Optional[int] = None,
    category_filter: str = "",
    status_filter: str = "",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sort_order: str = "newest"
) -> Dict[str, Any]:
    """Retrieve searchable, filterable, paginated feedback list."""
    query = db.query(Feedback, Customer.normalized_email).join(
        Customer, Feedback.customer_id == Customer.id
    )

    if search.strip():
        search_term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Customer.normalized_email.ilike(search_term),
                Feedback.message.ilike(search_term)
            )
        )

    if rating_filter:
        query = query.filter(Feedback.rating == rating_filter)

    if category_filter.strip():
        query = query.filter(Feedback.category == category_filter.strip())

    if status_filter.strip():
        query = query.filter(Feedback.status == status_filter.strip())

    if start_date:
        try:
            s_dt = datetime.fromisoformat(start_date)
            query = query.filter(Feedback.created_at >= s_dt)
        except Exception:
            pass

    if end_date:
        try:
            e_dt = datetime.fromisoformat(end_date)
            query = query.filter(Feedback.created_at <= e_dt)
        except Exception:
            pass

    if sort_order == "oldest":
        query = query.order_by(asc(Feedback.created_at))
    else:
        query = query.order_by(desc(Feedback.created_at))

    total_records = query.count()
    offset = (page - 1) * per_page
    rows = query.offset(offset).limit(per_page).all()

    items = []
    for f, email in rows:
        items.append({
            "id": f.id,
            "customer_id": f.customer_id,
            "email": email,
            "rating": f.rating,
            "category": f.category,
            "message": f.message,
            "device_type": f.device_type or "unknown",
            "browser_name": f.browser_name or "unknown",
            "status": f.status or "new",
            "admin_notes": getattr(f, "admin_notes", ""),
            "created_at": f.created_at.isoformat() if f.created_at else None,
        })

    # Summary cards
    now = now_utc()
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    total_feedback = db.query(Feedback).count()
    feedback_today = db.query(Feedback).filter(Feedback.created_at >= today_start).count()
    avg_rating_val = db.query(func.avg(Feedback.rating)).scalar()
    avg_rating = round(float(avg_rating_val), 1) if avg_rating_val is not None else 0.0

    return {
        "items": items,
        "total": total_records,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total_records + per_page - 1) // per_page),
        "summary": {
            "total_feedback": total_feedback,
            "feedback_today": feedback_today,
            "average_rating": avg_rating
        }
    }


def update_feedback_status(
    db: Session,
    feedback_id: str,
    status: Optional[str] = None,
    admin_notes: Optional[str] = None
) -> Optional[Feedback]:
    """Update feedback status and administrative notes."""
    feedback = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not feedback:
        return None

    if status in ("new", "reviewed", "resolved"):
        feedback.status = status
    if admin_notes is not None:
        feedback.admin_notes = admin_notes

    db.commit()
    db.refresh(feedback)
    return feedback
