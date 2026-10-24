import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from app.core.config import get_settings
from app.core.security import is_valid_uuid
from app.services.prompt_guard import redact_secrets

_lock = Lock()


def _base_data_dir() -> Path:
    """Returns the canonical root directory where conversation data is permitted to reside."""
    return Path(__file__).resolve().parents[2] / "data"


def _path() -> Path:
    """Resolves and bounds the conversation storage path within the designated data directory."""
    settings = get_settings()
    base_dir = _base_data_dir().resolve()
    base_dir.mkdir(parents=True, exist_ok=True)

    target_path = Path(settings.conversation_file)
    if not target_path.is_absolute():
        target_path = Path(__file__).resolve().parents[2] / target_path
    target_resolved = target_path.resolve()

    # Prevent path traversal attacks attempting to write outside backend/data
    try:
        target_resolved.relative_to(base_dir)
    except ValueError:
        # Fall back to safe default inside base_dir if settings path escapes
        target_resolved = base_dir / "conversations.json"

    target_resolved.parent.mkdir(parents=True, exist_ok=True)
    return target_resolved


def _read() -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _write(items: list[dict]) -> None:
    """Performs atomic write via tempfile rename to prevent corrupted writes or race collisions."""
    target_path = _path()
    dir_path = target_path.parent
    serialized = json.dumps(items, ensure_ascii=False, indent=2)

    with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, encoding="utf-8") as tf:
        tf.write(serialized)
        temp_name = tf.name

    os.replace(temp_name, target_path)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_conversations(session_id: str | None = None) -> list[dict]:
    """Lists conversations isolated to the authenticated session to prevent IDOR/BOLA data leakage."""
    with _lock:
        items = _read()
        if session_id:
            # If session is provided, only return conversations belonging to that session
            filtered = [c for c in items if c.get("session_id") == session_id]
        else:
            filtered = items
        return sorted(filtered, key=lambda x: x.get("updated_at", ""), reverse=True)


def get_conversation(conversation_id: str, session_id: str | None = None) -> dict | None:
    """Retrieves a conversation only if the caller owns it."""
    if not is_valid_uuid(conversation_id):
        return None
    with _lock:
        found = next((c for c in _read() if c.get("id") == conversation_id), None)
        if not found:
            return None
        # Enforce session isolation if conversation has an owner session
        if session_id and found.get("session_id") and found.get("session_id") != session_id:
            return None
        return found


def save_exchange(
    conversation_id: str | None,
    session_id: str | None,
    user_message: str,
    assistant_message: str,
    provider: str,
    model: str,
) -> dict:
    """Saves a conversation exchange, validating ownership and redacting sensitive data."""
    if conversation_id and not is_valid_uuid(conversation_id):
        conversation_id = None

    safe_user = redact_secrets(user_message)
    safe_assistant = redact_secrets(assistant_message)

    with _lock:
        items = _read()
        existing = next((c for c in items if conversation_id and c.get("id") == conversation_id), None)

        # Enforce IDOR protection: if user tries to save into an existing conversation they don't own, reject/branch
        if existing and session_id and existing.get("session_id") and existing.get("session_id") != session_id:
            existing = None
            conversation_id = None

        if existing is None:
            new_id = str(uuid.uuid4())
            existing = {
                "id": new_id,
                "session_id": session_id or "anonymous",
                "title": safe_user[:72],
                "messages": [],
                "created_at": now_iso(),
            }
            items.append(existing)

        existing["messages"].extend([
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": safe_user,
                "created_at": now_iso(),
            },
            {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": safe_assistant,
                "provider": provider,
                "model": model,
                "created_at": now_iso(),
            },
        ])
        existing["updated_at"] = now_iso()
        _write(items)
        return existing


def delete_conversation(conversation_id: str, session_id: str | None = None) -> bool:
    """Deletes a conversation only if the caller owns it."""
    if not is_valid_uuid(conversation_id):
        return False
    with _lock:
        items = _read()
        target = next((c for c in items if c.get("id") == conversation_id), None)
        if not target:
            return False
        # Prevent IDOR: reject delete if owned by a different session
        if session_id and target.get("session_id") and target.get("session_id") != session_id:
            return False

        filtered = [c for c in items if c.get("id") != conversation_id]
        _write(filtered)
        return True
