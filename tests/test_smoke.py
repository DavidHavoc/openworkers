import os

os.environ["DRY_RUN"] = "true"

from fastapi.testclient import TestClient

from apps.api.main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["tier"] == "api-gateway"
    assert "pending_tasks" in body


def test_task_submission():
    response = client.post(
        "/tasks/",
        json={"query": "What is the capital of France?", "discipline": "general"},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued"
    assert "task_id" in data
    assert "created_at" in data
