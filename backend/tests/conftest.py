import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app import auth, db
from app.config import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "execution_error_sample.json"
TEST_USER = "usr_test"
TEST_EMAIL = "owner@example.com"
TEST_WEBHOOK_TOKEN = "test-webhook-token"
ENCRYPTION_KEY = Fernet.generate_key().decode()


@pytest.fixture
def execution():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def workflow(execution):
    return {**execution["workflowData"], "active": True}


def log_in(client, user_id=TEST_USER):
    """Gives a TestClient a real session cookie, without the cost of hashing a password."""
    from starlette.responses import Response

    response = Response()
    auth.set_session_cookie(response, user_id)
    name, value = response.headers["set-cookie"].split(";")[0].split("=", 1)
    client.cookies.set(name, value)


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    names = (
        "N8N_URL",
        "N8N_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "TELEGRAM_WEBHOOK_SECRET",
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "DEMO_EMAIL",
        "DEMO_PASSWORD",
        "PUBLIC_API_URL",
        "GEMINI_API_KEY",
    )
    for name in names:
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("INVITE_CODE", "test-invite")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("ENCRYPTION_KEY", ENCRYPTION_KEY)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    get_settings.cache_clear()
    db.init_db()
    with db._connect() as connection:
        connection.execute(
            "INSERT INTO users (id, email, password_hash, webhook_token, created_at) "
            "VALUES (?, ?, 'not-a-real-hash', ?, ?)",
            (TEST_USER, TEST_EMAIL, TEST_WEBHOOK_TOKEN, db.now_iso()),
        )
    yield
    get_settings.cache_clear()
