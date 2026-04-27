from fastapi.testclient import TestClient
from apps.api.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "tier": "api-gateway"}

def test_task_submission():
    response = client.post("/tasks/?query=What+is+the+capital+of+France")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert "task_id" in data
