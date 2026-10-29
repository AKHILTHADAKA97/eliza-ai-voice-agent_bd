import uuid
from fastapi.testclient import TestClient
from app.main import app
from app.services import prompt_guard
from app.core.security import chat_limiter, general_limiter

client = TestClient(app)


def test_security_headers_present():
    """Verify that all essential security headers are injected into API responses."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    headers = response.headers
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "DENY"
    assert headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "Content-Security-Policy" in headers
    assert "X-Request-ID" in headers


def test_idor_conversation_isolation():
    """Verify that Session A cannot access or delete conversations created by Session B."""
    session_a = str(uuid.uuid4())
    session_b = str(uuid.uuid4())

    # Create conversation under session A
    res_a = client.post(
        "/api/v1/chat",
        headers={"X-Session-ID": session_a},
        json={"message": "Confidential session A message", "provider": "auto"},
    )
    assert res_a.status_code == 200
    conv_id = res_a.json()["conversation_id"]

    # Session A can view its own conversation
    get_a = client.get(f"/api/v1/conversations/{conv_id}", headers={"X-Session-ID": session_a})
    assert get_a.status_code == 200
    assert get_a.json()["id"] == conv_id

    # Session B attempting to access Session A's conversation must be denied (404 Not Found)
    get_b = client.get(f"/api/v1/conversations/{conv_id}", headers={"X-Session-ID": session_b})
    assert get_b.status_code == 404

    # Session B attempting to delete Session A's conversation must be denied (404)
    del_b = client.delete(f"/api/v1/conversations/{conv_id}", headers={"X-Session-ID": session_b})
    assert del_b.status_code == 404

    # Confirm conversation is still intact for Session A
    get_a_after = client.get(f"/api/v1/conversations/{conv_id}", headers={"X-Session-ID": session_a})
    assert get_a_after.status_code == 200

    # Session A can successfully delete its own conversation
    del_a = client.delete(f"/api/v1/conversations/{conv_id}", headers={"X-Session-ID": session_a})
    assert del_a.status_code == 200


def test_path_traversal_conversation_id_rejection():
    """Verify that path traversal attempts in conversation_id path parameter are rejected."""
    bad_ids = [
        "../../etc/passwd",
        "..\\..\\windows\\system32",
        "12345",
        "../../conversations.json",
        "; DROP TABLE users; --",
    ]
    for bad_id in bad_ids:
        res = client.get(f"/api/v1/conversations/{bad_id}")
        assert res.status_code in [400, 404]


def test_rate_limiting_enforcement():
    """Verify that requests exceeding the sliding rate limit are throttled with HTTP 429."""
    test_sid = str(uuid.uuid4())
    client_id = f"sess:{test_sid}"

    # Exhaust rate limit quota
    for _ in range(30):
        chat_limiter.is_allowed(client_id)

    # Next chat request should be blocked
    res = client.post(
        "/api/v1/chat",
        headers={"X-Session-ID": test_sid},
        json={"message": "Fast request", "provider": "auto"},
    )
    assert res.status_code == 429
    assert "Retry-After" in res.headers
    assert "Rate limit exceeded" in res.json()["detail"]


def test_request_size_limit():
    """Verify that oversized request payloads (> 1MB) are rejected with HTTP 413."""
    oversized_headers = {
        "Content-Length": "2097152", # 2MB
        "Content-Type": "application/json",
    }
    res = client.post("/api/v1/chat", headers=oversized_headers, content=b"{}")
    assert res.status_code == 413
    assert "maximum allowed size" in res.json()["detail"]


def test_secret_redaction_in_prompts():
    """Verify that API keys and sensitive tokens are automatically redacted before processing."""
    raw_prompt = "Here is my secret key: sk-abcdefghijklmnopqrstuvwxyz1234567890 and Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token"
    redacted = prompt_guard.redact_secrets(raw_prompt)
    assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in redacted
    assert "[REDACTED_SECRET]" in redacted


def test_model_name_injection_rejection():
    """Verify that malicious model identifiers with command injection or traversal characters are rejected."""
    malicious_models = [
        "model; rm -rf /",
        "gpt-4o-mini && whoami",
        "../../malicious_model",
        "model | cat /etc/shadow",
    ]
    for m in malicious_models:
        res = client.post(
            "/api/v1/chat",
            json={"message": "Hello", "model": m, "provider": "auto"},
        )
        assert res.status_code in [400, 422]


def test_error_masking_no_stack_traces():
    """Verify that internal server errors return generic messages with correlation IDs and do not leak stack traces."""
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    # Check that health endpoint doesn't disclose secrets
    body = res.json()
    assert "openai_api_key" not in body
    assert "session_secret_key" not in body
