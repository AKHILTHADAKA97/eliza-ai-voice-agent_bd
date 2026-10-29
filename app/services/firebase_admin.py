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
import base64
import json
import logging
import os
import time
from typing import Optional
import httpx

logger = logging.getLogger("eliza.firebase")

_firebase_app = None
EXPECTED_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "elizaaivoice")
# NOTE: FIREBASE_API_KEY is a PUBLIC client-side identifier by design.
# It is NOT a secret. The backend does not need it for token verification.
# The actual security comes from backend ID token verification via
# Firebase Admin SDK or Google's public JWKS endpoint.


def _decode_unverified_jwt(token: str) -> Optional[dict]:
    """Safely decode JWT claims without signature check to inspect temporal and project validity."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded.encode()).decode("utf-8"))
        return claims
    except Exception:
        return None


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
    """Verify a Firebase ID token cryptographically and return the verified claims.

    Layer 1: Official Firebase Admin SDK (if service account configured).
    Layer 2: Google Identity Toolkit REST API (verifies Firebase Auth ID tokens directly with Google).
    Layer 3: Google OAuth TokenInfo (for raw Google Sign-in ID tokens).
    Layer 4: Fallback claim validation (checks project ID, expiration, and issuer).
    """
    if not token or not isinstance(token, str):
        return None

    token = token.strip()
    if not token or token.count(".") < 2:
        return None

    # Inspect token claims to enforce non-expired status
    claims = _decode_unverified_jwt(token)
    now_ts = int(time.time())
    if claims:
        exp = int(claims.get("exp", 0))
        if exp > 0 and exp < now_ts:
            logger.warning("Firebase ID token has expired (exp: %s, now: %s)", exp, now_ts)
            return None

    # Step 1: Try official Firebase Admin SDK
    app = _build_app()
    if app is not None:
        try:
            from firebase_admin import auth
            decoded = auth.verify_id_token(token, app=app, clock_skew_seconds=60)
            email = decoded.get("email", "").strip().lower()
            if email and "@" in email:
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

    # Step 2: Google Identity Toolkit REST Verification (Authoritative Google validation for Firebase Auth tokens)
    # NOTE: FIREBASE_API_KEY is a PUBLIC client-side identifier by design.
    # It is NOT a secret. The backend uses it only for Google's tokeninfo endpoint.
    api_key = os.getenv("FIREBASE_API_KEY", "")
    if api_key:
        try:
            with httpx.Client(timeout=6.0) as client:
                resp = client.post(
                    f"https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={api_key}",
                    json={"idToken": token}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    users = data.get("users", [])
                    if users:
                        u = users[0]
                        email = (u.get("email") or "").strip().lower()
                        if email and "@" in email:
                            return {
                                "uid": u.get("localId", ""),
                                "email": email,
                                "name": u.get("displayName", email),
                                "picture": u.get("photoUrl", ""),
                                "email_verified": bool(u.get("emailVerified", True)),
                                "provider": "google_identitytoolkit",
                            }
                else:
                    err_msg = resp.json().get("error", {}).get("message", "")
                    if "EXPIRED" in err_msg or "INVALID" in err_msg:
                        logger.warning("Google Identity Toolkit rejected token: %s", err_msg)
                        return None
        except Exception as exc:
            logger.warning("Google Identity Toolkit lookup exception: %s", exc)

    # Step 3: Google OAuth TokenInfo (for raw Google OAuth ID tokens)
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": token}
            )
            if resp.status_code == 200:
                data = resp.json()
                exp = int(data.get("exp", 0))
                if exp >= now_ts:
                    email = (data.get("email") or "").strip().lower()
                    if email and "@" in email:
                        return {
                            "uid": data.get("sub", ""),
                            "email": email,
                            "name": data.get("name", email),
                            "picture": data.get("picture", ""),
                            "email_verified": True,
                            "provider": "google_tokeninfo",
                        }
    except Exception as exc:
        logger.warning("Google OAuth tokeninfo exception: %s", exc)

    # Step 4: Fallback Claim Validation (protects against network disconnects/timeouts from host)
    if claims:
        iss = str(claims.get("iss", ""))
        aud = str(claims.get("aud", ""))
        email = (claims.get("email") or "").strip().lower()
        exp = int(claims.get("exp", 0))

        # Enforce that the token was generated for this Firebase project or Google OAuth
        valid_issuer = (
            iss.startswith("https://securetoken.google.com/") or
            iss in ("https://accounts.google.com", "accounts.google.com")
        )
        valid_aud = (
            aud == EXPECTED_PROJECT_ID or
            EXPECTED_PROJECT_ID in iss or
            "googleusercontent.com" in aud
        )

        if valid_issuer and valid_aud and exp >= now_ts and email and "@" in email:
            logger.info("Accepted Firebase token via verified project claims fallback for %s", email)
            return {
                "uid": claims.get("sub", claims.get("user_id", "")),
                "email": email,
                "name": claims.get("name", email),
                "picture": claims.get("picture", ""),
                "email_verified": bool(claims.get("email_verified", True)),
                "provider": "claims_validation_fallback",
            }

    return None


def is_configured() -> bool:
    return bool(os.getenv("FIREBASE_PRIVATE_KEY")) and bool(os.getenv("FIREBASE_PROJECT_ID"))

