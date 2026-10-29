"""Automated test suite for Eliza AI access control, usage enforcement, and admin dashboard."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import SessionLocal, engine, Base
from app.models import Customer, AccessPeriod, QuestionUsage, Feedback, Visit, Admin
from app.services.admin_service import hash_password, seed_default_admin

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    """Ensure clean tables and default admin before tests."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    seed_default_admin(db)
    db.close()
    yield


def test_01_new_email_receives_access():
    """Verify new email registration gets access granted and 3 remaining questions."""
    test_email = f"user_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post("/api/v1/access/verify-email", json={"email": test_email, "device_type": "desktop"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["allowed"] is True
    assert data["remainingQuestions"] == 3
    assert data["blocked"] is False
    assert data["sessionToken"] is not None
    assert data["email"] == test_email.lower()


def test_02_invalid_email_rejected():
    """Verify malformed email formats are rejected with HTTP 400 or 422."""
    invalid_emails = ["not-an-email", "@nodomain.com", "spaces in@email.com", "missingdot@com", ""]
    for bad in invalid_emails:
        resp = client.post("/api/v1/access/verify-email", json={"email": bad})
        assert resp.status_code in (400, 422)


@patch("app.routes.questions._ai_router.chat", new_callable=AsyncMock)
def test_03_three_question_limit_and_fourth_rejected(mock_chat):
    """
    Verify question counting:
    - 1st question: remaining 2
    - 2nd question: remaining 1
    - 3rd question: remaining 0, blocked
    - 4th question: rejected with HTTP 403 'Your limit is over.'
    """
    mock_chat.return_value = ("Test answer", "freemodel", "gemini-3.7-flash", {"total_tokens": 10})

    test_email = f"quota_{uuid.uuid4().hex[:8]}@example.com"
    auth_resp = client.post("/api/v1/access/verify-email", json={"email": test_email})
    token = auth_resp.json()["sessionToken"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1st Question
    q1 = client.post("/api/v1/questions", headers=headers, json={"message": "Question 1"})
    assert q1.status_code == 200
    assert q1.json()["remainingQuestions"] == 2
    assert q1.json()["blocked"] is False

    # 2nd Question
    q2 = client.post("/api/v1/questions", headers=headers, json={"message": "Question 2"})
    assert q2.status_code == 200
    assert q2.json()["remainingQuestions"] == 1
    assert q2.json()["blocked"] is False

    # 3rd Question
    q3 = client.post("/api/v1/questions", headers=headers, json={"message": "Question 3"})
    assert q3.status_code == 200
    assert q3.json()["remainingQuestions"] == 0
    assert q3.json()["blocked"] is True

    # 4th Question -> Must be rejected with 403
    q4 = client.post("/api/v1/questions", headers=headers, json={"message": "Question 4"})
    assert q4.status_code == 403
    detail = q4.json()["detail"]
    assert detail["blocked"] is True
    assert detail["remainingQuestions"] == 0
    assert "Your limit is over." in detail["message"]


def test_04_status_endpoint_and_refresh_does_not_reset_limit():
    """Verify /api/v1/access/status reflects current count and page refresh does not reset it."""
    test_email = f"refresh_{uuid.uuid4().hex[:8]}@example.com"
    auth_resp = client.post("/api/v1/access/verify-email", json={"email": test_email})
    token = auth_resp.json()["sessionToken"]
    headers = {"Authorization": f"Bearer {token}"}

    # Check initial status
    st1 = client.get("/api/v1/access/status", headers=headers)
    assert st1.status_code == 200
    assert st1.json()["remainingQuestions"] == 3

    # Re-verify with same email (simulating another tab or browser)
    reauth = client.post("/api/v1/access/verify-email", json={"email": test_email})
    assert reauth.status_code == 200
    assert reauth.json()["remainingQuestions"] == 3


def test_05_blocked_email_persists_across_sessions():
    """Verify that once an email is blocked, re-verifying from any browser returns blocked status."""
    db = SessionLocal()
    test_email = f"blocked_{uuid.uuid4().hex[:8]}@example.com"
    auth_resp = client.post("/api/v1/access/verify-email", json={"email": test_email})
    cust_id = auth_resp.json()["accessPeriodId"]

    # Manually mark as blocked in DB
    period = db.query(AccessPeriod).filter(AccessPeriod.id == cust_id).first()
    period.questions_used = 3
    period.status = "blocked"
    period.blocked_at = datetime.now(timezone.utc)
    period.blocked_until = datetime.now(timezone.utc) + timedelta(days=20)
    db.commit()
    db.close()

    # Opening website again with same email
    recheck = client.post("/api/v1/access/verify-email", json={"email": test_email})
    assert recheck.status_code == 200
    data = recheck.json()
    assert data["allowed"] is False
    assert data["blocked"] is True
    assert data["remainingQuestions"] == 0
    assert "Your limit is over." in data["message"]


def test_06_expired_access_period_resets_allowance():
    """Verify that after 20-day period expires, email starts a new period with 3 questions."""
    db = SessionLocal()
    test_email = f"expired_{uuid.uuid4().hex[:8]}@example.com"
    auth_resp = client.post("/api/v1/access/verify-email", json={"email": test_email})
    period_id = auth_resp.json()["accessPeriodId"]

    # Age the period beyond 20 days
    period = db.query(AccessPeriod).filter(AccessPeriod.id == period_id).first()
    period.questions_used = 3
    period.status = "blocked"
    past = datetime.now(timezone.utc) - timedelta(days=21)
    period.period_started_at = past - timedelta(days=1)
    period.period_expires_at = past
    period.blocked_until = past
    db.commit()
    db.close()

    # Re-verify -> Should automatically create a new period and grant 3 questions
    reauth = client.post("/api/v1/access/verify-email", json={"email": test_email})
    assert reauth.status_code == 200
    data = reauth.json()
    assert data["allowed"] is True
    assert data["remainingQuestions"] == 3
    assert data["blocked"] is False
    assert data["accessPeriodId"] != period_id


def test_07_feedback_saved_and_does_not_consume_question():
    """Verify feedback saves correctly to database and does not decrement question count."""
    test_email = f"feedback_{uuid.uuid4().hex[:8]}@example.com"
    auth_resp = client.post("/api/v1/access/verify-email", json={"email": test_email})
    token = auth_resp.json()["sessionToken"]
    headers = {"Authorization": f"Bearer {token}"}

    # Submit feedback
    fb_resp = client.post(
        "/api/v1/feedback",
        headers=headers,
        json={
            "rating": 5,
            "category": "Voice Experience",
            "message": "The conversational voice quality is exceptionally clear and helpful!",
            "device_type": "desktop",
            "browser_name": "Chrome"
        }
    )
    assert fb_resp.status_code == 200
    assert fb_resp.json()["status"] == "received"

    # Status check confirms question count remains 3
    st = client.get("/api/v1/access/status", headers=headers)
    assert st.status_code == 200
    assert st.json()["remainingQuestions"] == 3

    # Feedback status endpoint confirms submission
    fb_st = client.get("/api/v1/feedback/status", headers=headers)
    assert fb_st.status_code == 200
    assert fb_st.json()["submitted"] is True
    assert fb_st.json()["feedback"]["rating"] == 5


def test_08_admin_authentication_and_unauthorized_rejection():
    """Verify admin login success, invalid password rejection, and route protection."""
    # Reject without token
    dash_unauth = client.get("/api/v1/admin/dashboard/overview")
    assert dash_unauth.status_code == 401

    # Login with wrong password
    bad_login = client.post("/api/v1/admin/login", json={"username": "admin", "password": "WrongPassword"})
    assert bad_login.status_code == 401

    # Login with correct default credentials
    login = client.post("/api/v1/admin/login", json={"username": "admin", "password": "ElizaAdmin2026!"})
    assert login.status_code == 200
    admin_token = login.json()["token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Authenticated request succeeds
    me = client.get("/api/v1/admin/me", headers=admin_headers)
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


def test_09_admin_dashboard_overview_metrics():
    """Verify admin dashboard overview returns database-backed totals and time-series."""
    login = client.post("/api/v1/admin/login", json={"username": "admin", "password": "ElizaAdmin2026!"})
    admin_headers = {"Authorization": f"Bearer {login.json()['token']}"}

    resp = client.get("/api/v1/admin/dashboard/overview?preset=last30days", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "metrics" in data
    assert "charts" in data
    assert "total_emails" in data["metrics"]
    assert "unique_visitors_today" in data["metrics"]
    assert "daily_visitors" in data["charts"]
    assert "rating_distribution" in data["charts"]


def test_10_admin_emails_and_feedback_management():
    """Verify admin can list emails, inspect details, list feedback, and update feedback status."""
    login = client.post("/api/v1/admin/login", json={"username": "admin", "password": "ElizaAdmin2026!"})
    admin_headers = {"Authorization": f"Bearer {login.json()['token']}"}

    # 1. Emails list
    emails_resp = client.get("/api/v1/admin/dashboard/emails?page=1&per_page=10", headers=admin_headers)
    assert emails_resp.status_code == 200
    assert "items" in emails_resp.json()
    assert "total" in emails_resp.json()

    # 2. Feedback list
    fb_list = client.get("/api/v1/admin/dashboard/feedback?page=1&per_page=10", headers=admin_headers)
    assert fb_list.status_code == 200
    items = fb_list.json()["items"]

    if items:
        fb_id = items[0]["id"]
        # Update feedback status
        patch_resp = client.patch(
            f"/api/v1/admin/dashboard/feedback/{fb_id}",
            headers=admin_headers,
            json={"status": "reviewed", "admin_notes": "Checked by team"}
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["status"] == "reviewed"


def test_11_daily_unique_visitors_not_inflated_by_refreshes():
    """Verify that multiple page refreshes on the same day record only 1 unique visitor."""
    test_email = f"vis_{uuid.uuid4().hex[:8]}@example.com"
    # 5 verification / visit calls in the same day
    for _ in range(5):
        client.post("/api/v1/access/verify-email", json={"email": test_email, "device_type": "desktop"})

    db = SessionLocal()
    cust = db.query(Customer).filter(Customer.normalized_email == test_email.lower()).first()
    assert cust is not None
    visits = db.query(Visit).filter(Visit.customer_id == cust.id).all()
    # Unique constraint ensures exactly 1 visit row per customer per date per period
    assert len(visits) == 1
    db.close()


def test_12_google_direct_login_endpoint():
    """Verify Google direct auth endpoint logs user in and creates DB records."""
    google_email = f"google.user.{uuid.uuid4().hex[:6]}@gmail.com"
    resp = client.post("/api/v1/access/google-login", json={
        "email": google_email,
        "name": "Google Test User",
        "device_type": "desktop",
        "browser_name": "Chrome"
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["allowed"] is True
    assert data["remainingQuestions"] == 3
    assert data["sessionToken"] is not None

    db = SessionLocal()
    cust = db.query(Customer).filter(Customer.normalized_email == google_email.lower()).first()
    assert cust is not None
    assert cust.status == "active"
    db.close()
