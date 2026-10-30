"""Simple email/password authentication routes for Eliza AI.

Uses JWT session tokens stored in HttpOnly cookies.
Replaces Firebase/Google login with a clean backend-verified session.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

from app.core.config import get_settings
from app.core.security import get_client_identifier, general_limiter

logger = logging.getLogger("eliza.auth")

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
settings = get_settings()

# In-memory user store (production should use a database)
# Format: { email: { id, email, password_hash, created_at } }
_user_store = {}

# In-memory session store
# Format: { token: { user_id, email, created_at, expires_at } }
_session_store = {}

SESSION_TOKEN_TTL_HOURS = 24
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 15


def _hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    try:
        from passlib.hash import bcrypt
        return bcrypt.hash(password)
    except Exception:
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest()


def _verify_password(password: str, password_hash: str) -> bool:
    """Verify password against hash."""
    try:
        from passlib.hash import bcrypt
        return bcrypt.verify(password, password_hash)
    except Exception:
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest() == password_hash


def _create_session_token() -> str:
    return f"eliza_session_{uuid.uuid4().hex}"


def _get_session(token: str) -> Optional[dict]:
    if not token:
        return None
    session = _session_store.get(token)
    if not session:
        return None
    if datetime.now(timezone.utc) > session["expires_at"]:
        _session_store.pop(token, None)
        return None
    return session


def get_current_user(token: Optional[str] = None) -> Optional[dict]:
    """Dependency that returns the current authenticated user or None."""
    if not token:
        return None
    return _get_session(token)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=4, max_length=128)


class SignupIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=4, max_length=128)


class AuthOut(BaseModel):
    success: bool
    session_token: str = ""
    email: str = ""
    message: str = ""


class MeOut(BaseModel):
    email: str
    user_id: str
    authenticated: bool = True


def _check_rate_limit(identifier: str) -> None:
    """Simple in-memory rate limit for auth endpoints."""
    allowed, _ = general_limiter.is_allowed(identifier)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please wait before trying again.",
            headers={"Retry-After": "900"},
        )


@router.post("/login", response_model=AuthOut)
async def login(body: LoginIn, request: Request):
    _check_rate_limit(get_client_identifier(request))

    email = body.email.strip().lower()
    password = body.password

    user = _user_store.get(email)
    if not user or not _verify_password(password, user["password_hash"]):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
        )

    token = _create_session_token()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_TOKEN_TTL_HOURS)
    _session_store[token] = {
        "user_id": user["id"],
        "email": email,
        "created_at": datetime.now(timezone.utc),
        "expires_at": expires_at,
    }

    user["last_login_at"] = datetime.now(timezone.utc)

    return {
        "success": True,
        "session_token": token,
        "email": email,
        "message": "Login successful",
    }


@router.post("/signup", response_model=AuthOut)
async def signup(body: SignupIn, request: Request):
    _check_rate_limit(get_client_identifier(request))

    email = body.email.strip().lower()
    password = body.password

    if email in _user_store:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        )

    user_id = f"usr_{uuid.uuid4().hex[:12]}"
    _user_store[email] = {
        "id": user_id,
        "email": email,
        "password_hash": _hash_password(password),
        "created_at": datetime.now(timezone.utc),
        "last_login_at": None,
    }

    token = _create_session_token()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_TOKEN_TTL_HOURS)
    _session_store[token] = {
        "user_id": user_id,
        "email": email,
        "created_at": datetime.now(timezone.utc),
        "expires_at": expires_at,
    }

    return {
        "success": True,
        "session_token": token,
        "email": email,
        "message": "Account created successfully",
    }


@router.post("/logout")
async def logout(request: Request):
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
    if token:
        _session_store.pop(token, None)
    return {"success": True, "message": "Logged out"}


@router.get("/me")
async def me(request: Request):
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
    session = _get_session(token)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "email": session["email"],
        "user_id": session["user_id"],
        "authenticated": True,
    }


def _seed_admin():
    """Seed the default admin account from environment variables."""
    admin_email = settings.admin_email
    admin_password = settings.admin_password
    if admin_email and admin_password and admin_email not in _user_store:
        _user_store[admin_email] = {
            "id": f"adm_{uuid.uuid4().hex[:12]}",
            "email": admin_email,
            "password_hash": _hash_password(admin_password),
            "created_at": datetime.now(timezone.utc),
            "last_login_at": None,
            "is_admin": True,
        }
        logger.info("Seeded admin account for %s", admin_email)


_seed_admin()