"""Server-side Firebase Admin SDK wrapper.

Never store Firebase API keys in the frontend. This module verifies
Firebase ID tokens issued by Google on the backend and returns the
authenticated user's email, name, and picture.

Required environment variables:
  FIREBASE_PROJECT_ID          - Firebase project ID
  FIREBASE_PRIVATE_KEY         - PEM-encoded service-account private key
  FIREBASE_CLIENT_EMAIL        - Service account email
  FIREBASE_CLIENT_ID           - Service account client ID (optional)
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger("eliza.firebase")

_firebase_app = None


def _build_app():
    """Lazily build a Firebase Admin app from environment variables."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    try:
        import firebase_admin
        from firebase_admin import credentials, initialize_app
    except ImportError as exc:
        logger.error("firebase-admin is not installed: %s", exc)
        return None

    cred = None
    raw_key = os.getenv("FIREBASE_PRIVATE_KEY")
    if raw_key:
        if raw_key.strip().startswith("{"):
            try:
                cred_dict = json.loads(raw_key)
                cred = credentials.Certificate(cred_dict)
            except Exception:
                cred = None
        else:
            pem = raw_key.replace("\\n", "\n")
            cred_dict = {
                "type": "service_account",
                "project_id": os.getenv("FIREBASE_PROJECT_ID", ""),
                "private_key": pem,
                "client_email": os.getenv("FIREBASE_CLIENT_EMAIL", ""),
                "client_id": os.getenv("FIREBASE_CLIENT_ID", ""),
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            cred = credentials.Certificate(cred_dict)

    if cred is None:
        logger.error("FIREBASE_PRIVATE_KEY is not configured.")
        return None

    try:
        _firebase_app = initialize_app(cred, name="eliza-access", opt={"httpTimeout": 10})
        return _firebase_app
    except Exception as exc:
        logger.error("Failed to initialize Firebase Admin SDK: %s", exc)
        return None


def verify_id_token(token: str) -> Optional[dict]:
    """Verify a Firebase ID token and return the decoded claims.

    Returns a dict with keys: uid, email, name, picture, email_verified
    or None if the token is invalid/expired.
    """
    app = _build_app()
    if app is None:
        return None

    try:
        from firebase_admin import auth
        decoded = auth.verify_id_token(token, app=app, clock_skew_seconds=30)
        return {
            "uid": decoded.get("uid", ""),
            "email": decoded.get("email", ""),
            "name": decoded.get("name", decoded.get("email", "")),
            "picture": decoded.get("picture", ""),
            "email_verified": bool(decoded.get("email_verified", False)),
        }
    except Exception as exc:
        logger.warning("Firebase token verification failed: %s", exc)
        return None


def is_configured() -> bool:
    return bool(os.getenv("FIREBASE_PRIVATE_KEY")) and bool(os.getenv("FIREBASE_PROJECT_ID"))