import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    webhook_token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connections (
    user_id TEXT PRIMARY KEY REFERENCES users (id),
    n8n_url TEXT NOT NULL,
    encrypted_api_key TEXT NOT NULL,
    telegram_chat_id TEXT
);
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users (id),
    workflow_id TEXT NOT NULL,
    workflow_name TEXT NOT NULL,
    failed_node TEXT NOT NULL,
    root_cause TEXT NOT NULL,
    category TEXT NOT NULL,
    fix_status TEXT NOT NULL,
    signature TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users (id),
    incident_id TEXT NOT NULL REFERENCES incidents (id),
    workflow_id TEXT NOT NULL,
    workflow_name TEXT NOT NULL,
    node_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    signature TEXT NOT NULL,
    parameters TEXT,
    backup_workflow TEXT NOT NULL,
    affected_execution_ids TEXT NOT NULL,
    messages TEXT NOT NULL,
    created_at TEXT NOT NULL,
    thread_id TEXT REFERENCES threads (id)
);
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users (id),
    workflow_id TEXT,
    workflow_name TEXT,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_thread_per_workflow
    ON threads (user_id, workflow_id) WHERE workflow_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS thread_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL REFERENCES threads (id),
    role TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

JSON_COLUMNS = ("parameters", "backup_workflow", "affected_execution_ids", "messages")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(get_settings().database_path)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db() -> None:
    path = Path(get_settings().database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as connection:
        connection.executescript(SCHEMA)
        # Databases from before accounts existed already have these tables without user_id.
        for table in ("incidents", "proposals"):
            columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            if "user_id" not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT REFERENCES users (id)")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(proposals)")}
        if "thread_id" not in columns:
            connection.execute("ALTER TABLE proposals ADD COLUMN thread_id TEXT REFERENCES threads (id)")


def create_user(email: str, password_hash: str) -> dict[str, Any]:
    """Raises sqlite3.IntegrityError if the email is already registered."""
    user = {
        "id": f"usr_{uuid.uuid4().hex[:12]}",
        "email": email,
        "password_hash": password_hash,
        "webhook_token": secrets.token_urlsafe(24),
        "created_at": now_iso(),
    }
    with _connect() as connection:
        connection.execute(
            "INSERT INTO users (id, email, password_hash, webhook_token, created_at) "
            "VALUES (:id, :email, :password_hash, :webhook_token, :created_at)",
            user,
        )
    return user


def _get_user(column: str, value: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(f"SELECT * FROM users WHERE {column} = ?", (value,)).fetchone()
    return dict(row) if row else None


def get_user(user_id: str) -> dict[str, Any] | None:
    return _get_user("id", user_id)


def get_user_by_email(email: str) -> dict[str, Any] | None:
    return _get_user("email", email)


def get_user_by_webhook_token(token: str) -> dict[str, Any] | None:
    return _get_user("webhook_token", token)


def get_connection(user_id: str) -> dict[str, Any] | None:
    """The stored connection with its API key still encrypted."""
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM connections WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def save_connection(
    user_id: str, n8n_url: str, encrypted_api_key: str, telegram_chat_id: str | None
) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO connections "
            "(user_id, n8n_url, encrypted_api_key, telegram_chat_id) VALUES (?, ?, ?, ?)",
            (user_id, n8n_url, encrypted_api_key, telegram_chat_id),
        )


def get_legacy_settings() -> dict[str, Any] | None:
    """The single, unencrypted connection stored before accounts existed."""
    with _connect() as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'settings'"
        ).fetchone()
        row = connection.execute("SELECT * FROM settings WHERE id = 1").fetchone() if exists else None
    return dict(row) if row else None


def has_unowned_data() -> bool:
    with _connect() as connection:
        return any(
            connection.execute(f"SELECT 1 FROM {table} WHERE user_id IS NULL LIMIT 1").fetchone()
            for table in ("incidents", "proposals")
        )


def assign_unowned_data(user_id: str) -> None:
    """Moves pre-accounts data to this user and removes the old plaintext API key."""
    with _connect() as connection:
        for table in ("incidents", "proposals"):
            connection.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (user_id,))
        connection.execute("DROP TABLE IF EXISTS settings")


def find_open_incident(user_id: str, workflow_id: str, signature: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM incidents WHERE user_id = ? AND workflow_id = ? AND signature = ? "
            "AND fix_status = 'open' ORDER BY created_at DESC LIMIT 1",
            (user_id, workflow_id, signature),
        ).fetchone()
    return dict(row) if row else None


def create_incident(
    user_id: str,
    workflow_id: str,
    workflow_name: str,
    failed_node: str,
    root_cause: str,
    category: str,
    signature: str,
) -> str:
    incident_id = f"inc_{uuid.uuid4().hex[:12]}"
    with _connect() as connection:
        connection.execute(
            "INSERT INTO incidents (id, user_id, workflow_id, workflow_name, failed_node, "
            "root_cause, category, fix_status, signature, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (
                incident_id,
                user_id,
                workflow_id,
                workflow_name,
                failed_node,
                root_cause,
                category,
                signature,
                now_iso(),
            ),
        )
    return incident_id


def update_incident_diagnosis(incident_id: str, root_cause: str, category: str) -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE incidents SET root_cause = ?, category = ? WHERE id = ?",
            (root_cause, category, incident_id),
        )


def resolve_incident(incident_id: str, fix_status: str) -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE incidents SET fix_status = ?, resolved_at = ? WHERE id = ?",
            (fix_status, now_iso(), incident_id),
        )


def list_incidents(user_id: str) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM incidents WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def create_proposal(
    user_id: str,
    proposal_id: str,
    incident_id: str,
    workflow_id: str,
    workflow_name: str,
    node_name: str,
    kind: str,
    signature: str,
    parameters: dict[str, Any] | None,
    backup_workflow: dict[str, Any],
    affected_execution_ids: list[str],
    messages: list[dict[str, Any]],
    thread_id: str | None = None,
) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO proposals (id, user_id, incident_id, workflow_id, workflow_name, "
            "node_name, kind, status, signature, parameters, backup_workflow, "
            "affected_execution_ids, messages, created_at, thread_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)",
            (
                proposal_id,
                user_id,
                incident_id,
                workflow_id,
                workflow_name,
                node_name,
                kind,
                signature,
                json.dumps(parameters),
                json.dumps(backup_workflow),
                json.dumps(affected_execution_ids),
                json.dumps(messages),
                now_iso(),
                thread_id,
            ),
        )


def _decode_proposal(row: sqlite3.Row) -> dict[str, Any]:
    proposal = dict(row)
    for column in JSON_COLUMNS:
        proposal[column] = json.loads(proposal[column])
    return proposal


def get_proposal(proposal_id: str, user_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM proposals WHERE id = ? AND user_id = ?", (proposal_id, user_id)
        ).fetchone()
    return _decode_proposal(row) if row else None


def list_open_proposals(
    user_id: str, workflow_id: str | None = None, thread_id: str | None = None
) -> list[dict[str, Any]]:
    query = "SELECT * FROM proposals WHERE status = 'open' AND user_id = ?"
    params: tuple[str, ...] = (user_id,)
    if workflow_id:
        query += " AND workflow_id = ?"
        params += (workflow_id,)
    if thread_id:
        query += " AND thread_id = ?"
        params += (thread_id,)
    with _connect() as connection:
        rows = connection.execute(f"{query} ORDER BY created_at DESC", params).fetchall()
    return [_decode_proposal(row) for row in rows]


def open_proposal_workflow_ids(user_id: str) -> set[str]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT DISTINCT workflow_id FROM proposals "
            "WHERE status IN ('open', 'applying') AND user_id = ?",
            (user_id,),
        ).fetchall()
    return {row["workflow_id"] for row in rows}


def claim_proposal(proposal_id: str) -> bool:
    """Atomically moves an open proposal to applying, so a double approve can't apply twice."""
    with _connect() as connection:
        cursor = connection.execute(
            "UPDATE proposals SET status = 'applying' WHERE id = ? AND status = 'open'",
            (proposal_id,),
        )
    return cursor.rowcount == 1


def set_proposal_status(proposal_id: str, status: str) -> None:
    with _connect() as connection:
        connection.execute("UPDATE proposals SET status = ? WHERE id = ?", (status, proposal_id))


def set_proposal_affected(proposal_id: str, execution_ids: list[str]) -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE proposals SET affected_execution_ids = ? WHERE id = ?",
            (json.dumps(execution_ids), proposal_id),
        )


def _new_thread(
    connection: sqlite3.Connection,
    user_id: str,
    workflow_id: str | None,
    workflow_name: str | None,
    title: str,
    timestamp: str | None = None,
) -> str:
    thread_id = f"thr_{uuid.uuid4().hex[:12]}"
    timestamp = timestamp or now_iso()
    connection.execute(
        "INSERT INTO threads (id, user_id, workflow_id, workflow_name, title, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (thread_id, user_id, workflow_id, workflow_name, title, timestamp, timestamp),
    )
    return thread_id


def get_thread(thread_id: str, user_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM threads WHERE id = ? AND user_id = ?", (thread_id, user_id)
        ).fetchone()
    return dict(row) if row else None


def get_or_create_workflow_thread(
    user_id: str, workflow_id: str, workflow_name: str | None = None
) -> dict[str, Any]:
    """Each workflow has exactly one thread per user. Keeps its name current if one is given."""
    with _connect() as connection:
        row = connection.execute(
            "SELECT id FROM threads WHERE user_id = ? AND workflow_id = ?", (user_id, workflow_id)
        ).fetchone()
        if row:
            thread_id = row["id"]
            if workflow_name:
                connection.execute(
                    "UPDATE threads SET workflow_name = ?, title = ? WHERE id = ?",
                    (workflow_name, workflow_name, thread_id),
                )
        else:
            title = workflow_name or "Workflow"
            thread_id = _new_thread(connection, user_id, workflow_id, workflow_name, title)
    return get_thread(thread_id, user_id)


def get_or_create_blank_thread(user_id: str) -> dict[str, Any]:
    """A chat with no workflow. Reuses an unused one, so empty chats don't pile up."""
    with _connect() as connection:
        row = connection.execute(
            "SELECT id FROM threads t WHERE user_id = ? AND workflow_id IS NULL AND NOT EXISTS "
            "(SELECT 1 FROM thread_messages m WHERE m.thread_id = t.id) LIMIT 1",
            (user_id,),
        ).fetchone()
        thread_id = row["id"] if row else _new_thread(connection, user_id, None, None, "New chat")
    return get_thread(thread_id, user_id)


def list_threads(user_id: str) -> list[dict[str, Any]]:
    """Chats with at least one message, newest first. Unused threads stay out of the list."""
    with _connect() as connection:
        rows = connection.execute(
            "SELECT t.*, EXISTS (SELECT 1 FROM proposals p WHERE p.thread_id = t.id "
            "AND p.user_id = t.user_id AND p.status = 'open') AS pending "
            "FROM threads t WHERE t.user_id = ? AND EXISTS "
            "(SELECT 1 FROM thread_messages m WHERE m.thread_id = t.id) "
            "ORDER BY t.updated_at DESC",
            (user_id,),
        ).fetchall()
    return [{**dict(row), "pending": bool(row["pending"])} for row in rows]


def append_thread_messages(thread_id: str, messages: list[tuple[str, dict[str, Any]]]) -> None:
    """Adds (role, payload) pairs in order. A blank chat takes its title from the first question."""
    timestamp = now_iso()
    with _connect() as connection:
        for role, payload in messages:
            connection.execute(
                "INSERT INTO thread_messages (thread_id, role, payload, created_at) VALUES (?, ?, ?, ?)",
                (thread_id, role, json.dumps(payload), timestamp),
            )
            if role == "user":
                connection.execute(
                    "UPDATE threads SET title = ? WHERE id = ? AND workflow_id IS NULL AND title = 'New chat'",
                    (payload["content"][:60], thread_id),
                )
        connection.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (timestamp, thread_id))


def list_thread_messages(thread_id: str) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM thread_messages WHERE thread_id = ? ORDER BY id", (thread_id,)
        ).fetchall()
    return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]


def thread_proposal_states(thread_id: str, user_id: str) -> dict[str, str]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT id, status FROM proposals WHERE thread_id = ? AND user_id = ?",
            (thread_id, user_id),
        ).fetchall()
    return {row["id"]: row["status"] for row in rows}


def backfill_threads() -> None:
    """Files proposals from before threads existed into their workflow's thread. Safe to repeat."""
    with _connect() as connection:
        proposals = connection.execute(
            "SELECT id, user_id, workflow_id, workflow_name, messages, created_at FROM proposals "
            "WHERE thread_id IS NULL AND user_id IS NOT NULL ORDER BY created_at"
        ).fetchall()
        for proposal in proposals:
            row = connection.execute(
                "SELECT id FROM threads WHERE user_id = ? AND workflow_id = ?",
                (proposal["user_id"], proposal["workflow_id"]),
            ).fetchone()
            thread_id = row["id"] if row else _new_thread(
                connection,
                proposal["user_id"],
                proposal["workflow_id"],
                proposal["workflow_name"],
                proposal["workflow_name"],
                proposal["created_at"],
            )
            for message in json.loads(proposal["messages"]):
                connection.execute(
                    "INSERT INTO thread_messages (thread_id, role, payload, created_at) "
                    "VALUES (?, 'agent', ?, ?)",
                    (thread_id, json.dumps(message), proposal["created_at"]),
                )
            connection.execute(
                "UPDATE proposals SET thread_id = ? WHERE id = ?", (thread_id, proposal["id"])
            )
            connection.execute(
                "UPDATE threads SET updated_at = ? WHERE id = ?", (proposal["created_at"], thread_id)
            )
