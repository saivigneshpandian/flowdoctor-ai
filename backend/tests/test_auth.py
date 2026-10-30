import sqlite3
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from app import auth, db
from app.api import routes
from app.config import get_settings
from app.main import app
from app.n8n.client import N8nClient
from tests.conftest import TEST_USER
from tests.fake_n8n import FakeN8n

EMAIL = "new.user@example.com"
PASSWORD = "correct horse battery"
PROTECTED = [
    ("POST", "/connect"),
    ("GET", "/workflows"),
    ("GET", "/workflows/abc/error"),
    ("POST", "/chat"),
    ("POST", "/fix/abc/approve"),
    ("POST", "/fix/abc/reject"),
    ("POST", "/fix/abc/done"),
    ("GET", "/incidents"),
    ("GET", "/auth/me"),
]


@pytest.fixture
def api():
    with TestClient(app) as client:
        yield client


def signup(client, email=EMAIL, password=PASSWORD, invite_code="test-invite"):
    return client.post(
        "/auth/signup", json={"email": email, "password": password, "invite_code": invite_code}
    )


def test_health_needs_no_login(api):
    assert api.get("/health").json() == {"ok": True}


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_every_protected_endpoint_requires_login(api, method, path):
    assert api.request(method, path, json={}).status_code == 401


def test_signup_logs_in_and_stores_only_a_hash(api):
    response = signup(api)

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == EMAIL
    assert body["has_connection"] is False
    assert "password" not in response.text
    assert api.get("/auth/me").json()["email"] == EMAIL
    stored = db.get_user_by_email(EMAIL)["password_hash"]
    assert stored.startswith("$argon2")
    assert PASSWORD not in stored


def test_email_is_case_insensitive_and_unique(api):
    assert signup(api, email="Mixed@Example.com").json()["email"] == "mixed@example.com"
    assert signup(api, email="MIXED@example.com").status_code == 409


@pytest.mark.parametrize(
    ("email", "password"), [("not-an-email", PASSWORD), (EMAIL, "short")]
)
def test_signup_validates_input(api, email, password):
    assert signup(api, email=email, password=password).status_code == 422
    assert "set-cookie" not in signup(api, email=email, password=password).headers


def test_login_and_logout(api):
    signup(api)
    api.post("/auth/logout")
    assert api.get("/auth/me").status_code == 401

    assert api.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 200
    assert api.get("/auth/me").status_code == 200


@pytest.mark.parametrize(
    "credentials",
    [{"email": EMAIL, "password": "wrong password"}, {"email": "nobody@example.com", "password": PASSWORD}],
)
def test_bad_login_gives_the_same_answer(api, credentials):
    signup(api)
    api.post("/auth/logout")

    response = api.post("/auth/login", json=credentials)

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password."
    assert "set-cookie" not in response.headers


def cookie_header(response):
    return response.headers["set-cookie"].lower()


def test_local_cookie_is_httponly_lax_and_lasts_seven_days(api):
    header = cookie_header(signup(api))

    assert "httponly" in header
    assert "samesite=lax" in header
    assert "secure" not in header
    assert "max-age=604800" in header
    assert "path=/" in header


def test_production_cookie_is_secure_and_still_lax(api, monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "true")
    get_settings.cache_clear()

    header = cookie_header(signup(api))
    assert "samesite=lax" in header
    assert "secure" in header
    assert "httponly" in header

    cleared = cookie_header(api.post("/auth/logout"))
    assert "samesite=lax" in cleared and "secure" in cleared and "max-age=0" in cleared


@pytest.mark.parametrize("invite_code", ["wrong-code", ""])
def test_signup_needs_the_invite_code(api, invite_code):
    response = signup(api, invite_code=invite_code)

    assert response.status_code == 403
    assert "set-cookie" not in response.headers
    assert db.get_user_by_email(EMAIL) is None


def test_signup_is_closed_without_a_configured_code(api, monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "")
    get_settings.cache_clear()

    for code in ("", "anything"):
        response = signup(api, invite_code=code)
        assert response.status_code == 403
        assert response.json()["detail"] == "Sign-up is closed."
    assert db.get_user_by_email(EMAIL) is None


def test_login_and_demo_user_need_no_invite_code(api, monkeypatch):
    set_demo(monkeypatch)
    monkeypatch.setenv("INVITE_CODE", "")
    get_settings.cache_clear()

    auth.bootstrap()

    response = api.post("/auth/login", json={"email": "demo@example.com", "password": "demo-password"})
    assert response.status_code == 200


def test_api_sends_no_cors_headers_so_other_sites_cant_read_it(api):
    signup(api)
    headers = {"Origin": "https://evil.example"}

    assert "access-control-allow-origin" not in api.get("/auth/me", headers=headers).headers
    preflight = api.options(
        "/auth/login", headers={**headers, "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in preflight.headers


@pytest.mark.parametrize("path", ["/auth/me", "/incidents", "/health"])
def test_responses_must_not_be_cached_by_a_cdn(api, path):
    signup(api)

    assert api.get(path).headers["cache-control"] == "no-store"


def test_token_expires_after_seven_days(api):
    token = signup(api).cookies[auth.COOKIE_NAME]

    claims = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])

    assert claims["exp"] - claims["iat"] == 7 * 24 * 60 * 60


def forged(secret=None, **claims):
    secret = secret or get_settings().secret_key
    payload = {"sub": TEST_USER, "exp": datetime.now(timezone.utc) + timedelta(hours=1), **claims}
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.mark.parametrize(
    "make_token",
    [
        lambda: "garbage",
        lambda: forged(secret="an-attacker-secret-that-is-long-enough-for-hs256"),
        lambda: forged(exp=datetime.now(timezone.utc) - timedelta(seconds=1)),
        lambda: forged(sub="usr_does_not_exist"),
        lambda: jwt.encode({"sub": TEST_USER}, get_settings().secret_key, algorithm="HS256"),
    ],
    ids=["garbage", "wrong-signature", "expired", "unknown-user", "no-expiry"],
)
def test_bad_tokens_are_rejected(api, make_token):
    api.cookies.set(auth.COOKIE_NAME, make_token())

    assert api.get("/incidents").status_code == 401


def test_me_returns_the_webhook_url(api, monkeypatch):
    body = signup(api).json()
    token = db.get_user_by_email(EMAIL)["webhook_token"]
    assert body["webhook_url"] == f"http://testserver/webhook/n8n-error/{token}"

    monkeypatch.setenv("PUBLIC_API_URL", "https://api.example.com/")
    get_settings.cache_clear()
    assert (
        api.get("/auth/me").json()["webhook_url"]
        == f"https://api.example.com/webhook/n8n-error/{token}"
    )


def test_each_user_gets_a_different_webhook_token():
    first = db.create_user("a@example.com", "x")
    second = db.create_user("b@example.com", "x")

    assert first["webhook_token"] != second["webhook_token"]
    assert len(first["webhook_token"]) >= 32


@pytest.mark.parametrize(
    ("name", "value"), [("SECRET_KEY", "too-short"), ("ENCRYPTION_KEY", "not-a-fernet-key"), ("ENCRYPTION_KEY", "")]
)
def test_startup_refuses_bad_keys(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match=name):
        auth.bootstrap()


# n8n API keys


def fake_connection(monkeypatch, execution, workflow):
    n8n = FakeN8n(execution, workflow)
    monkeypatch.setattr(
        routes, "build_client", lambda url, key: N8nClient(url, key, transport=n8n.transport)
    )
    return n8n


def test_api_key_is_encrypted_at_rest_and_never_returned(api, monkeypatch, execution, workflow):
    fake_connection(monkeypatch, execution, workflow)
    signup(api)
    user = db.get_user_by_email(EMAIL)

    response = api.post("/connect", json={"n8n_url": "http://n8n:5678", "api_key": "plain-n8n-key"})

    assert response.json() == {"ok": True}
    assert "plain-n8n-key" not in api.get("/auth/me").text
    with open(get_settings().database_path, "rb") as file:
        assert b"plain-n8n-key" not in file.read()
    assert auth.load_connection(user["id"])["api_key"] == "plain-n8n-key"
    assert api.get("/auth/me").json()["has_connection"] is True


def test_changed_encryption_key_means_reconnect_not_a_crash(api, monkeypatch, execution, workflow):
    from cryptography.fernet import Fernet

    fake_connection(monkeypatch, execution, workflow)
    signup(api)
    api.post("/connect", json={"n8n_url": "http://n8n:5678", "api_key": "plain-n8n-key"})

    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()

    assert api.get("/auth/me").json()["has_connection"] is False
    assert api.get("/workflows").status_code == 400


# Demo account and migration


def set_demo(monkeypatch, email="Demo@Example.com", password="demo-password", url="http://n8n:5678", key="env-key"):
    monkeypatch.setenv("DEMO_EMAIL", email)
    monkeypatch.setenv("DEMO_PASSWORD", password)
    monkeypatch.setenv("N8N_URL", url)
    monkeypatch.setenv("N8N_API_KEY", key)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "555")
    get_settings.cache_clear()


def test_demo_user_is_created_and_linked_to_env_connection(api, monkeypatch):
    set_demo(monkeypatch)

    auth.bootstrap()

    demo = db.get_user_by_email("demo@example.com")
    connection = auth.load_connection(demo["id"])
    assert connection == {"n8n_url": "http://n8n:5678", "api_key": "env-key", "telegram_chat_id": "555"}
    response = api.post("/auth/login", json={"email": "demo@example.com", "password": "demo-password"})
    assert response.status_code == 200
    assert response.json()["has_connection"] is True


def test_restart_keeps_the_demo_user_and_picks_up_a_rotated_key(monkeypatch):
    set_demo(monkeypatch)
    auth.bootstrap()
    first = db.get_user_by_email("demo@example.com")

    set_demo(monkeypatch, key="rotated-key")
    auth.bootstrap()

    second = db.get_user_by_email("demo@example.com")
    assert second["id"] == first["id"]
    assert second["webhook_token"] == first["webhook_token"]
    assert auth.load_connection(first["id"])["api_key"] == "rotated-key"


def test_no_demo_settings_means_no_demo_user():
    auth.bootstrap()

    with db._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


OLD_SCHEMA = """
CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK (id = 1), n8n_url TEXT NOT NULL,
    api_key TEXT NOT NULL, telegram_chat_id TEXT);
CREATE TABLE incidents (id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, workflow_name TEXT NOT NULL,
    failed_node TEXT NOT NULL, root_cause TEXT NOT NULL, category TEXT NOT NULL,
    fix_status TEXT NOT NULL, signature TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT);
CREATE TABLE proposals (id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, workflow_id TEXT NOT NULL,
    workflow_name TEXT NOT NULL, node_name TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
    signature TEXT NOT NULL, parameters TEXT, backup_workflow TEXT NOT NULL,
    affected_execution_ids TEXT NOT NULL, messages TEXT NOT NULL, created_at TEXT NOT NULL);
INSERT INTO settings VALUES (1, 'http://old:5678', 'old-plaintext-key', '999');
INSERT INTO incidents VALUES ('inc_old', 'wf1', 'Lead Capture', 'Node', 'cause', 'auto_fixable',
    'open', 'sig', '2026-10-30T00:00:00Z', NULL);
INSERT INTO proposals VALUES ('prop_old', 'inc_old', 'wf1', 'Lead Capture', 'Node', 'patch', 'open',
    'sig', '{}', '{"nodes": []}', '["4"]', '[]', '2026-10-30T00:00:00Z');
"""


@pytest.fixture
def old_database(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.executescript(OLD_SCHEMA)
    connection.close()
    monkeypatch.setenv("DATABASE_PATH", str(path))
    get_settings.cache_clear()
    return path


def test_old_database_moves_to_the_demo_user(old_database, monkeypatch):
    set_demo(monkeypatch, url="", key="")

    auth.bootstrap()

    demo = db.get_user_by_email("demo@example.com")
    assert [i["id"] for i in db.list_incidents(demo["id"])] == ["inc_old"]
    assert db.get_proposal("prop_old", demo["id"])["status"] == "open"
    assert auth.load_connection(demo["id"]) == {
        "n8n_url": "http://old:5678",
        "api_key": "old-plaintext-key",
        "telegram_chat_id": "999",
    }
    # The plaintext key is gone from the table.
    assert db.get_legacy_settings() is None


def test_env_connection_wins_over_the_old_one_and_migration_is_repeatable(old_database, monkeypatch):
    set_demo(monkeypatch)

    auth.bootstrap()
    auth.bootstrap()

    demo = db.get_user_by_email("demo@example.com")
    assert auth.load_connection(demo["id"])["api_key"] == "env-key"
    assert len(db.list_incidents(demo["id"])) == 1


def test_old_data_stays_hidden_and_intact_without_a_demo_user(old_database, caplog):
    auth.bootstrap()

    assert db.has_unowned_data()
    assert db.get_legacy_settings() is not None
    assert "no owner" in caplog.text
