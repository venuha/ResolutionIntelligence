import pytest
from fastapi.testclient import TestClient
from resolution.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, testing=True)) as client:
        yield client


def login(client, identity):
    assert client.post("/api/login", json={"identity": identity}).status_code == 200


def test_login_required(client):
    assert client.get("/api/cases").status_code == 401


def test_customer_case_isolation_and_no_agent_state(client):
    login(client, "customer_riley")
    assert client.get("/api/cases/CASE-2001").status_code == 403
    body = client.get("/api/cases/CASE-1007").json()
    assert "runs" not in body and "documents" not in body
    assert client.get("/api/runs").status_code == 403
    assert client.post("/api/runs", json={"case_id": "CASE-1007"}).status_code == 403


def test_specialist_cannot_approve_telecom_or_bank(client):
    login(client, "telecom_specialist")
    r = client.post("/api/runs", json={"case_id": "CASE-1007"}).json()
    run = client.get("/api/runs/"+r["id"]).json()
    assert run["status"] == "awaiting_approval"
    assert client.post(f"/api/runs/{r['id']}/decision", json={"revision": run["revision"], "decision": "approve"}).status_code == 403
    assert client.get("/api/cases/CASE-1008").status_code == 403


def test_telecom_cannot_approve_bank_credit(client):
    login(client, "telecom_supervisor")
    rid = client.post("/api/runs", json={"case_id": "CASE-1007"}).json()["id"]
    r = client.get("/api/runs/"+rid).json()
    r = client.post(f"/api/runs/{rid}/decision", json={"revision": r["revision"], "decision": "approve"}).json()
    task = r["state"]["bank_task"]
    assert client.post(f"/api/bank/{task['id']}/decision", json={"revision": 1, "decision": "approve"}).status_code == 403
    login(client, "bank_reviewer")
    assert client.post(f"/api/bank/{task['id']}/decision", json={"revision": 1, "decision": "approve"}).status_code == 200
    login(client, "customer_riley")
    body = client.get("/api/cases/CASE-1007").json()
    assert len(body["messages"]) == 2
    assert any(x["kind"] == "merchant_refund" for x in body["ledger"])


def test_cross_origin_writes_rejected(client):
    assert client.post("/api/login", json={"identity": "telecom_supervisor"}, headers={"Origin": "https://example.com"}).status_code == 403


def test_keys_are_never_returned(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-test")
    monkeypatch.setenv("KEENABLE_API_KEY", "keen-secret-test")
    login(client, "telecom_supervisor")
    text = client.get("/api/me").text
    assert "sk-secret-test" not in text
    assert "keen-secret-test" not in text
    assert client.get("/api/me").json()["configuration"]["keenable"]["configured"] is True
    assert client.get("/.env").status_code == 404
