"""Accounts: password hashing, session cookies, key encryption and the demo user."""

import hmac
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request, Response

from app import db
from app.config import get_settings

logger = logging.getLogger(__name__)

COOKIE_NAME = "flowdoctor_session"
SESSION_DAYS = 7
JWT_ALGORITHM = "HS256"
MIN_SECRET_LENGTH = 32
MIN_PASSWORD_LENGTH = 8
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
KEY_HELP = (
    "Generate SECRET_KEY with: python -c \"import secrets; print(secrets.token_urlsafe(48))\" "
    "and ENCRYPTION_KEY with: python -c \"from cryptography.fernet import Fernet; "
    'print(Fernet.generate_key().decode())"'
)

_hasher = PasswordHasher()
# Verified when an email is unknown, so login takes the same time either way.
_dummy_hash = _hasher.hash("flowdoctor-unknown-user")


def check_keys() -> None:
    """Refuses to start without usable keys, rather than failing on the first login."""
    settings = get_settings()
    if len(settings.secret_key) < MIN_SECRET_LENGTH:
        raise RuntimeError(f"SECRET_KEY must be at least {MIN_SECRET_LENGTH} characters. {KEY_HELP}")
    try:
        Fernet(settings.encryption_key.encode())
    except ValueError as exc:
        raise RuntimeError(f"ENCRYPTION_KEY is missing or invalid. {KEY_HELP}") from exc


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_demo_user(user: dict[str, Any]) -> bool:
    demo_email = get_settings().demo_email
    return bool(demo_email) and user["email"] == normalize_email(demo_email)


def check_invite_code(invite_code: str) -> None:
    expected = get_settings().invite_code
    if not expected:
        raise HTTPException(403, "Sign-up is closed.")
    if not hmac.compare_digest(invite_code.strip().encode(), expected.encode()):
        raise HTTPException(403, "That invite code isn't valid.")


def register(email: str, password: str) -> dict[str, Any]:
    email = normalize_email(email)
    if not EMAIL.match(email):
        raise HTTPException(422, "Enter a valid email address.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(422, f"Use a password of at least {MIN_PASSWORD_LENGTH} characters.")
    try:
        return db.create_user(email, _hasher.hash(password))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "An account with this email already exists.") from exc


def authenticate(email: str, password: str) -> dict[str, Any] | None:
    user = db.get_user_by_email(normalize_email(email))
    try:
        _hasher.verify(user["password_hash"] if user else _dummy_hash, password)
    except (Argon2Error, InvalidHashError):
        return None
    return user


def set_session_cookie(response: Response, user_id: str) -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": user_id, "iat": now, "exp": now + timedelta(days=SESSION_DAYS)},
        settings.secret_key,
        algorithm=JWT_ALGORITHM,
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        path="/",
        httponly=True,
        **_cookie_flags(),
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, **_cookie_flags())


def _cookie_flags() -> dict[str, Any]:
    # First-party: the browser only ever talks to the frontend's domain, which proxies to this API.
    return {"secure": get_settings().cookie_secure, "samesite": "lax"}


def current_user(request: Request) -> dict[str, Any]:
    """FastAPI dependency for every endpoint that requires login."""
    token = request.cookies.get(COOKIE_NAME)
    user_id = None
    if token:
        try:
            claims = jwt.decode(
                token,
                get_settings().secret_key,
                algorithms=[JWT_ALGORITHM],
                options={"require": ["exp", "sub"]},
            )
            user_id = claims["sub"]
        except jwt.PyJWTError:
            user_id = None
    user = db.get_user(user_id) if user_id else None
    if user is None:
        raise HTTPException(401, "Please log in.")
    return user


def _fernet() -> Fernet:
    return Fernet(get_settings().encryption_key.encode())


def encrypt_api_key(api_key: str) -> str:
    return _fernet().encrypt(api_key.encode()).decode()


def load_connection(user_id: str) -> dict[str, Any] | None:
    """The user's connection with its API key decrypted, for server-side use only."""
    stored = db.get_connection(user_id)
    if stored is None:
        return None
    try:
        api_key = _fernet().decrypt(stored["encrypted_api_key"].encode()).decode()
    except InvalidToken:
        logger.error("The n8n API key for user %s can't be decrypted. Did ENCRYPTION_KEY change?", user_id)
        return None
    return {
        "n8n_url": stored["n8n_url"],
        "api_key": api_key,
        "telegram_chat_id": stored["telegram_chat_id"],
    }


def save_connection(user_id: str, n8n_url: str, api_key: str, telegram_chat_id: str | None) -> None:
    db.save_connection(user_id, n8n_url, encrypt_api_key(api_key), telegram_chat_id)


def bootstrap() -> None:
    """Runs at startup: checks keys, updates the schema and sets up the demo user."""
    check_keys()
    db.init_db()
    settings = get_settings()

    if not settings.demo_email or not settings.demo_password:
        if db.get_legacy_settings() or db.has_unowned_data():
            logger.warning(
                "Existing data has no owner and stays hidden until DEMO_EMAIL and DEMO_PASSWORD are set."
            )
        db.backfill_threads()
        return

    email = normalize_email(settings.demo_email)
    user = db.get_user_by_email(email) or db.create_user(email, _hasher.hash(settings.demo_password))

    # Env wins on every start, so rotating N8N_API_KEY in .env reaches the demo account.
    legacy = db.get_legacy_settings() or {}
    current = load_connection(user["id"]) or {}
    n8n_url = settings.n8n_url or legacy.get("n8n_url")
    api_key = settings.n8n_api_key or legacy.get("api_key")
    chat_id = (
        legacy.get("telegram_chat_id") or settings.telegram_chat_id or current.get("telegram_chat_id")
    )
    if n8n_url and api_key:
        save_connection(user["id"], n8n_url, api_key, chat_id)

    db.assign_unowned_data(user["id"])
    db.backfill_threads()
