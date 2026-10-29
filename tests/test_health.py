from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_providers_endpoint():
    response = client.get("/api/v1/providers")
    assert response.status_code == 200
    assert "providers" in response.json()


def test_settings_roundtrip():
    response = client.patch("/api/v1/settings", json={"theme": "amber", "auto_speak": False, "voice_profile": "eliza", "voice_lang": "en-IN", "speech_recognition_lang": "en-IN"})
    assert response.status_code == 200
    assert response.json()["theme"] == "amber"
    assert response.json()["auto_speak"] is False
    assert response.json()["voice_profile"] == "eliza"
    assert response.json()["voice_lang"] == "en-IN"


def test_voice_catalog_endpoint():
    response = client.get("/api/v1/audio/voices")
    assert response.status_code == 200
    data = response.json()
    assert "profiles" in data
    assert any(p["id"] == "eliza" for p in data["profiles"])
    assert any(p["id"] == "arjun" for p in data["profiles"])
    assert any(p["id"] == "kavya" for p in data["profiles"])


def test_tts_fallback_when_unconfigured():
    response = client.post("/api/v1/audio/tts", json={"text": "Hello, I am Eliza AI.", "voice_profile": "eliza"})
    assert response.status_code == 200
    data = response.json()
    assert "available" in data

