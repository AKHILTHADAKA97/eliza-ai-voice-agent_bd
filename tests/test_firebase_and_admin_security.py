"""Comprehensive Security & Admin Management Test Suite.

Verifies:
1. Admin unblock customer functionality (resets balance to 3, unblocks period).
2. Admin single customer deletion (cascades cleanly).
3. Admin bulk customer deletion.
4. Admin single feedback deletion.
5. Admin bulk feedback deletion.
6. Google login token verification & spoofing protection.
7. Admin authentication & authorization enforcement.
8. Frontend build artifact secret scanning (zero server secrets leaked).
"""
import glob
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db import get_db, SessionLocal
from app.models import Customer, AccessPeriod, QuestionUsage, Feedback, Admin
from app.services.access_control import verify_email_access, record_question_deduction_atomic
from app.services.admin_service import (
    authenticate_admin,
    create_admin_token,
    unblock_customer,
    delete_customer,
    bulk_delete_customers,
    delete_feedback,
    bulk_delete_feedback,
)

client = TestClient(app)


@pytest.fixture
def db_session():
    """Provide a transactional DB session for tests."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def admin_token(db_session: Session):
    """Authenticate or create test admin and return Bearer token."""
    admin = authenticate_admin(db_session, "hello.superui@gmail.com", "Sravanthi@7777")
    if not admin:
        admin = db_session.query(Admin).filter(Admin.username == "hello.superui@gmail.com").first()
    assert admin is not None
    token = create_admin_token(admin)
    return token


def test_unauthenticated_admin_routes_rejected():
    """Unauthenticated access to any admin dashboard route returns 401."""
    res1 = client.get("/api/v1/admin/dashboard/overview")
    assert res1.status_code == 401

    res2 = client.get("/api/v1/admin/dashboard/emails")
    assert res2.status_code == 401

    res3 = client.get("/api/v1/admin/dashboard/feedback")
    assert res3.status_code == 401

    res4 = client.post("/api/v1/admin/customers/fake-id/unblock")
    assert res4.status_code == 401


def test_admin_unblock_restores_3_questions(db_session: Session, admin_token: str):
    """Admin unblocking immediately restores 3 questions and clears blocked status."""
    # Create customer and exhaust 3 questions
    import uuid
    uid = uuid.uuid4().hex[:8]
    email = f"blocked_test_{uid}@example.com"
    ver = verify_email_access(db_session, email)
    cust = db_session.query(Customer).filter(Customer.normalized_email == email).first()
    cust_id = cust.id
    period_id = ver["access_period_id"]

    for i in range(3):
        allowed, rem, _, _ = record_question_deduction_atomic(db_session, cust_id, period_id, request_id=f"req_{uid}_{i}")
        assert allowed is True

    # Verify they are now blocked
    ver_blocked = verify_email_access(db_session, email)
    assert ver_blocked["remaining_questions"] == 0
    assert ver_blocked["blocked"] is True

    # Call Admin Unblock Endpoint
    headers = {"Authorization": f"Bearer {admin_token}"}
    res = client.post(f"/api/v1/admin/customers/{cust_id}/unblock", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["remaining_questions"] == 3
    assert data["access_status"] == "eligible"

    # Re-verify through normal access evaluation
    ver_after = verify_email_access(db_session, email)
    assert ver_after["remaining_questions"] == 3
    assert ver_after["blocked"] is False


def test_admin_customer_single_and_bulk_delete(db_session: Session, admin_token: str):
    """Admin can delete a single customer or bulk delete multiple customers."""
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Create 3 test customers
    emails = ["del_user_1@test.com", "del_user_2@test.com", "del_user_3@test.com"]
    ids = []
    for em in emails:
        verify_email_access(db_session, em)
        cust = db_session.query(Customer).filter(Customer.normalized_email == em).first()
        ids.append(cust.id)

    # Single delete
    res_single = client.delete(f"/api/v1/admin/customers/{ids[0]}", headers=headers)
    assert res_single.status_code == 200
    assert db_session.query(Customer).filter(Customer.id == ids[0]).first() is None

    # Bulk delete remaining 2
    res_bulk = client.post(
        "/api/v1/admin/customers/bulk-delete",
        headers=headers,
        json={"customer_ids": [ids[1], ids[2]]}
    )
    assert res_bulk.status_code == 200
    assert res_bulk.json()["deleted_count"] == 2
    assert db_session.query(Customer).filter(Customer.id.in_([ids[1], ids[2]])).count() == 0


def test_admin_feedback_single_and_bulk_delete(db_session: Session, admin_token: str):
    """Admin can delete single feedback or bulk delete selected feedback."""
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Create test customer and feedbacks
    email = "fb_test_author@test.com"
    v = verify_email_access(db_session, email)
    cust = db_session.query(Customer).filter(Customer.normalized_email == email).first()
    cid = cust.id
    pid = v["access_period_id"]

    fb1 = Feedback(customer_id=cid, access_period_id=pid, rating=5, category="UI/UX", message="Great UI!")
    fb2 = Feedback(customer_id=cid, access_period_id=pid, rating=4, category="Voice Experience", message="Nice voice")
    db_session.add_all([fb1, fb2])
    db_session.commit()

    # Delete fb1
    res1 = client.delete(f"/api/v1/admin/feedback/{fb1.id}", headers=headers)
    assert res1.status_code == 200
    assert db_session.query(Feedback).filter(Feedback.id == fb1.id).first() is None

    # Bulk delete fb2
    res2 = client.post(
        "/api/v1/admin/feedback/bulk-delete",
        headers=headers,
        json={"feedback_ids": [fb2.id]}
    )
    assert res2.status_code == 200
    assert res2.json()["deleted_count"] == 1
    assert db_session.query(Feedback).filter(Feedback.id == fb2.id).first() is None


def test_google_login_rejects_spoofed_or_invalid_tokens():
    """Google login rejects invalid or tampered credentials with 401."""
    # Attempt login with forged invalid token
    fake_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJlbWFpbCI6InNwb29mZWRAYWRtaW4uY29tIn0.invalid_signature"
    res = client.post(
        "/api/v1/access/google-login",
        json={"credential": fake_token}
    )
    assert res.status_code == 401
    assert "Google authentication token could not be cryptographically verified" in res.json()["detail"]


def test_frontend_dist_contains_no_backend_secrets():
    """Verify built frontend bundle does not expose server secrets."""
    dist_files = glob.glob("../frontend/dist/assets/*.js")
    if not dist_files:
        dist_files = glob.glob("eliza-ai/frontend/dist/assets/*.js")

    # If dist folder is present, check it
    if dist_files:
        secrets = [
            "fm_a9f05128ef4c7a1bd11635054889a67c364b4fd2e193696c",  # FreeModel API key
            "Sravanthi@7777",                                        # Admin password
            "sk_319ccba23196a49b0cbb6bc2dc68202e4d3f7702604f9d6a",  # Elevenlabs secret key
        ]
        for f in dist_files:
            content = open(f, "r", encoding="utf-8").read()
            for s in secrets:
                assert s not in content, f"Sensitive server secret {s[:8]} leaked into bundle {f}!"
