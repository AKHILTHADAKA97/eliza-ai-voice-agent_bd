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
    response = client.patch("/api/v1/settings", json={"theme": "amber", "auto_speak": False})
    assert response.status_code == 200
    assert response.json()["theme"] == "amber"
    assert response.json()["auto_speak"] is False
