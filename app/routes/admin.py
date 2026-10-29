"""Administrative endpoints for authentication and dashboard analytics."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Admin
from app.schemas import (
    AdminLoginRequest,
    AdminLoginResponse,
    AdminUserOut,
    AdminFeedbackUpdateRequest,
    BulkDeleteCustomersRequest,
    BulkDeleteFeedbackRequest,
    BulkDeleteResponse,
    AdminCustomerUnblockResponse,
)
from app.services.admin_service import (
    authenticate_admin,
    create_admin_token,
    verify_admin_token,
    get_dashboard_overview,
    get_emails_list,
    get_customer_detail,
    get_feedback_list,
    update_feedback_status,
    unblock_customer,
    delete_customer,
    bulk_delete_customers,
    delete_feedback,
    bulk_delete_feedback,
)
from app.core.security import general_limiter, get_client_identifier

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def get_current_admin(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db)
) -> Admin:
    """Dependency protecting admin routes; validates JWT and active admin account."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin credentials required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    payload = verify_admin_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin session token is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    admin = db.query(Admin).filter(Admin.id == payload.get("sub")).first()
    if not admin or not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin account inactive or not found.",
        )
    return admin


@router.post("/login", response_model=AdminLoginResponse)
async def admin_login(
    body: AdminLoginRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """Admin login endpoint using bcrypt verification."""
    client_id = get_client_identifier(request)
    allowed, retry_after = general_limiter.is_allowed(f"admin_login:{client_id}", max_requests=10)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many admin login attempts. Please wait.",
            headers={"Retry-After": str(retry_after)},
        )

    admin = authenticate_admin(db, body.username, body.password)
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    token = create_admin_token(admin)
    return {
        "token": token,
        "token_type": "bearer",
        "admin": {
            "id": admin.id,
            "username": admin.username,
            "email": admin.email,
        }
    }


@router.get("/me", response_model=AdminUserOut)
async def admin_me(admin: Admin = Depends(get_current_admin)):
    """Return profile for currently authenticated administrator."""
    return {
        "id": admin.id,
        "username": admin.username,
        "email": admin.email,
    }


@router.post("/logout")
async def admin_logout(admin: Admin = Depends(get_current_admin)):
    """Stateless logout confirmation."""
    return {"message": "Logged out successfully"}


# --- Page 1: Overview Analytics ---

@router.get("/dashboard/overview")
async def dashboard_overview(
    preset: str = Query(default="last30days"),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """
    Overview summary metrics, KPIs, and time-series charts calculated
    from live database tables.
    """
    return get_dashboard_overview(
        db=db,
        preset=preset,
        custom_start=start_date,
        custom_end=end_date
    )


# --- Page 2: Email IDs and Access Analytics ---

@router.get("/dashboard/emails")
async def dashboard_emails(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=15, ge=1, le=100),
    search: str = Query(default=""),
    status: str = Query(default=""),
    sort_by: str = Query(default="newest"),
    question_usage: Optional[int] = Query(default=None),
    device: str = Query(default=""),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """
    Searchable, filterable, and paginated table of customer email records
    and real-time question usage.
    """
    return get_emails_list(
        db=db,
        page=page,
        per_page=per_page,
        search=search,
        status_filter=status,
        sort_by=sort_by,
        question_usage_filter=question_usage,
        device_filter=device,
        start_date=start_date,
        end_date=end_date
    )


@router.get("/dashboard/emails/{customer_id}")
async def customer_detail(
    customer_id: str,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Full historical profile, access periods, questions, and feedback for a customer."""
    data = get_customer_detail(db, customer_id)
    if not data:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return data


# --- Page 3: Feedback Management ---

@router.get("/dashboard/feedback")
async def dashboard_feedback(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=15, ge=1, le=100),
    search: str = Query(default=""),
    rating: Optional[int] = Query(default=None),
    category: str = Query(default=""),
    status: str = Query(default=""),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    sort_order: str = Query(default="newest"),
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Paginated feedback management list with search and categorical filters."""
    return get_feedback_list(
        db=db,
        page=page,
        per_page=per_page,
        search=search,
        rating_filter=rating,
        category_filter=category,
        status_filter=status,
        start_date=start_date,
        end_date=end_date,
        sort_order=sort_order
    )


@router.patch("/dashboard/feedback/{feedback_id}")
async def update_feedback(
    feedback_id: str,
    body: AdminFeedbackUpdateRequest,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update feedback status and admin internal notes."""
    updated = update_feedback_status(
        db=db,
        feedback_id=feedback_id,
        status=body.status,
        admin_notes=body.admin_notes
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Feedback entry not found.")
    return {
        "id": updated.id,
        "status": updated.status,
        "admin_notes": getattr(updated, "admin_notes", ""),
        "message": "Feedback updated successfully."
    }


# --- Customer Management Actions (Unblock & Deletion) ---

@router.post("/customers/{customer_id}/unblock", response_model=AdminCustomerUnblockResponse)
async def unblock_customer_endpoint(
    customer_id: str,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Admin access: immediately unblock a customer and restore their 3 questions access."""
    customer = unblock_customer(db, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return {
        "success": True,
        "message": f"Successfully unblocked {customer.normalized_email}. Trial balance restored.",
        "customer_id": customer.id,
        "access_status": "eligible",
        "remaining_questions": 3
    }


@router.delete("/customers/{customer_id}")
async def delete_customer_endpoint(
    customer_id: str,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Admin access: permanently delete a customer and all their access and usage records."""
    success = delete_customer(db, customer_id)
    if not success:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return {"success": True, "message": "Customer record deleted successfully."}


@router.post("/customers/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete_customers_endpoint(
    body: BulkDeleteCustomersRequest,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Admin access: bulk delete selected customer records."""
    count = bulk_delete_customers(db, body.customer_ids)
    return {
        "success": True,
        "deleted_count": count,
        "message": f"Successfully deleted {count} customer record(s)."
    }


# --- Feedback Management Actions (Deletion) ---

@router.delete("/feedback/{feedback_id}")
async def delete_feedback_endpoint(
    feedback_id: str,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Admin access: permanently delete a single feedback review."""
    success = delete_feedback(db, feedback_id)
    if not success:
        raise HTTPException(status_code=404, detail="Feedback entry not found.")
    return {"success": True, "message": "Feedback review deleted successfully."}


@router.post("/feedback/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete_feedback_endpoint(
    body: BulkDeleteFeedbackRequest,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Admin access: bulk delete selected feedback reviews."""
    count = bulk_delete_feedback(db, body.feedback_ids)
    return {
        "success": True,
        "deleted_count": count,
        "message": f"Successfully deleted {count} feedback review(s)."
    }

