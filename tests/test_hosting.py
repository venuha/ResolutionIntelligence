import pytest
from fastapi.testclient import TestClient
from resolution.main import create_app
from resolution.hosting import validate_settings
from resolution.database import PostgresConnection


@pytest.fixture
def hosted_client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFUND_HOSTED", "true")
    monkeypatch.setenv("REFUND_PUBLIC_URL", "https://demo.example")
    monkeypatch.setenv("REFUND_ACCESS_PASSWORD", "private-test-password-123")
    with TestClient(create_app(tmp_path, testing=True), base_url="https://demo.example") as client:
        yield client


def test_hosted_login_requires_password_and_preserves_role_checks(hosted_client):
    c=hosted_client
    assert c.get('/api/bootstrap').json()['password_required']
    assert c.post('/api/login',json={'identity':'telecom_supervisor'}).status_code==401
    assert c.post('/api/perspective',json={'identity':'bank_reviewer'}).status_code==401
    r=c.post('/api/login',json={'identity':'customer_riley','password':'private-test-password-123'})
    assert r.status_code==200
    assert all(x in r.headers['set-cookie'].lower() for x in ('secure','httponly','samesite=strict'))
    assert c.get('/api/cases/CASE-2001').status_code==403
    assert c.post('/api/perspective',json={'identity':'bank_reviewer'}).status_code==200
    assert c.get('/api/me').json()['role']=='bank'
    assert c.post('/api/logout').status_code==200
    assert c.get('/api/me').status_code==401


def test_hosted_password_attempts_are_bounded(hosted_client):
    for _ in range(10):
        assert hosted_client.post('/api/login',json={'identity':'telecom_supervisor','password':'wrong'}).status_code==401
    assert hosted_client.post('/api/login',json={'identity':'telecom_supervisor','password':'private-test-password-123'}).status_code==429


def test_hosted_origin_and_host_are_checked(hosted_client):
    assert hosted_client.post('/api/login',json={'identity':'telecom_supervisor'},headers={'Origin':'https://evil.example'}).status_code==403
    assert hosted_client.get('/health',headers={'Host':'evil.example'}).status_code==400


def test_hosted_startup_rejects_missing_durability_and_weak_access(monkeypatch):
    monkeypatch.setenv('REFUND_HOSTED','true')
    monkeypatch.setenv('REFUND_PUBLIC_URL','https://demo.example')
    monkeypatch.delenv('REFUND_ACCESS_PASSWORD',raising=False)
    with pytest.raises(RuntimeError,match='ACCESS_PASSWORD'):
        validate_settings()
    monkeypatch.setenv('REFUND_ACCESS_PASSWORD','private-test-password-123')
    monkeypatch.delenv('DATABASE_URL',raising=False)
    with pytest.raises(RuntimeError,match='durable'):
        validate_settings()


def test_hosted_checkpoints_reject_transaction_pooler(monkeypatch):
    monkeypatch.setenv('REFUND_HOSTED','true')
    monkeypatch.setenv('REFUND_ACCESS_PASSWORD','private-test-password-123')
    monkeypatch.setenv('DATABASE_URL','postgresql://host:6543/postgres')
    monkeypatch.setenv('CHECKPOINT_DATABASE_URL','postgresql://host:6543/postgres')
    with pytest.raises(RuntimeError,match='session pooler'):
        validate_settings()


def test_postgres_bank_transaction_and_duplicate_translation():
    calls=[]
    class Connection:
        def execute(self,sql,params=()):
            calls.append((sql,params))
    c=PostgresConnection(Connection())
    c.execute('BEGIN IMMEDIATE')
    c.execute('INSERT OR IGNORE INTO messages VALUES (?,?)',('one','two'))
    assert 'pg_advisory_xact_lock' in calls[0][0]
    assert calls[1]==('INSERT INTO messages VALUES (%s,%s) ON CONFLICT DO NOTHING',('one','two'))
