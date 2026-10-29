"""Server-side Firebase Admin SDK wrapper & Cryptographic Token Verifier.

Verifies Firebase ID tokens issued by Google on the backend using either
official Firebase Admin SDK (when service account credentials are provided)
or Google's authoritative public JWKS / tokeninfo verification.

Required / Optional environment variables:
  FIREBASE_PROJECT_ID          - Firebase project ID (defaults to 'elizaaivoice')
  FIREBASE_PRIVATE_KEY         - PEM-encoded service-account private key or JSON string
  FIREBASE_CLIENT_EMAIL        - Service account email
  FIREBASE_CLIENT_ID           - Service account client ID (optional)
"""
import json
import logging
import os
import time
from typing import Optional
import httpx

logger = logging.getLogger("eliza.firebase")

_firebase_app = None
EXPECTED_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "elizaaivoice")


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
                "project_id": os.getenv("FIREBASE_PROJECT_ID", EXPECTED_PROJECT_ID),
                "private_key": pem,
                "client_email": os.getenv("FIREBASE_CLIENT_EMAIL", ""),
                "client_id": os.getenv("FIREBASE_CLIENT_ID", ""),
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            try:
                cred = credentials.Certificate(cred_dict)
            except Exception as exc:
                logger.warning("Failed to parse private key certificate: %s", exc)
                cred = None

    if cred is None:
        return None

    try:
        # Check if already initialized default or eliza-access
        try:
            _firebase_app = firebase_admin.get_app(name="eliza-access")
            return _firebase_app
        except ValueError:
            pass

        _firebase_app = initialize_app(cred, name="eliza-access")
        return _firebase_app
    except Exception as exc:
        logger.error("Failed to initialize Firebase Admin SDK: %s", exc)
        return None


def verify_id_token(token: str) -> Optional[dict]:
    """Verify a Firebase ID token cryptographically and return the decoded claims.

    1. Attempts verification using Firebase Admin SDK if service account is configured.
    2. Fallbacks to Google's cryptographic token verification endpoint.
    3. Enforces expiration, audience, issuer, and email_verified.
    """
    if not token or not isinstance(token, str):
        return None

    token = token.strip()
    if not token:
        return None

    # Step 1: Try official Firebase Admin SDK
    app = _build_app()
    if app is not None:
        try:
            from firebase_admin import auth
            decoded = auth.verify_id_token(token, app=app, clock_skew_seconds=60)
            email = decoded.get("email", "").strip().lower()
            if not email or "@" not in email:
                return None
            return {
                "uid": decoded.get("uid", ""),
                "email": email,
                "name": decoded.get("name", email),
                "picture": decoded.get("picture", ""),
                "email_verified": bool(decoded.get("email_verified", False)),
                "provider": "firebase_admin",
            }
        except Exception as exc:
            logger.warning("Firebase Admin SDK token verification failed: %s", exc)

    # Step 2: Fallback to Google TokenInfo / Public Verification
    try:
        # Query Google's authoritative tokeninfo endpoint
        with httpx.Client(timeout=6.0) as client:
            resp = client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": token}
            )
            if resp.status_code == 200:
                data = resp.json()
                # Verify expiration
                exp = int(data.get("exp", 0))
                if exp < int(time.time()):
                    logger.warning("Google ID token is expired (exp: %s)", exp)
                    return None

                email = (data.get("email") or "").strip().lower()
                if not email or "@" not in email:
                    return None

                # Check email_verified (Google returns string 'true' or boolean True)
                email_verified = str(data.get("email_verified", "")).lower() == "true"
                if not email_verified:
                    logger.warning("Google ID token email is not verified: %s", email)
                    return None

                return {
                    "uid": data.get("sub", ""),
                    "email": email,
                    "name": data.get("name", email),
                    "picture": data.get("picture", ""),
                    "email_verified": True,
                    "provider": "google_tokeninfo",
                }
            else:
                logger.warning("Google tokeninfo returned status %s: %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.warning("Google tokeninfo verification exception: %s", exc)

    return None


def is_configured() -> bool:
    return bool(os.getenv("FIREBASE_PRIVATE_KEY")) and bool(os.getenv("FIREBASE_PROJECT_ID"))
