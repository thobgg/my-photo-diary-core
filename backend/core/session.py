"""
session.py
My Photo Diary v2 — session facade (M3: SQLite-persistent)

Login:   Argon2id against users.json (core/userdb.py).
Storage: SQLite (core/sessiondb.py) — survives container restart.

Session is a slim DTO for main.py + routers/deps.py.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core import sessiondb
from core.userdb import authenticate

logger = logging.getLogger(__name__)

SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days
COOKIE_NAME = "mpd_session"


@dataclass(frozen=True)
class Session:
    user: str
    role: str
    personal_path: Path
    is_demo: bool = False


def _to_session(row: sessiondb.SessionRow) -> Session:
    return Session(
        user=row.user,
        role=row.role,
        personal_path=row.personal_path,
        is_demo=row.is_demo,
    )


def get_session(token: Optional[str]) -> Optional[Session]:
    row = sessiondb.get(token)
    return _to_session(row) if row else None


def request_token(request) -> Optional[str]:
    """Session-Token aus dem Request lesen (additiv 09/2026).

    `Authorization: Bearer <token>` (native App) hat Vorrang, sonst wie
    bisher das mpd_session-Cookie (Web/Wrapper). Der Bearer-Wert ist
    derselbe Session-Token, den /api/login als Cookie setzt — eine
    Session-Tabelle, zwei Transportwege.
    """
    auth = request.headers.get("authorization")
    if auth:
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    return request.cookies.get(COOKIE_NAME)


def login(
    username: str,
    password: str,
    *,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> str:
    """
    Login via users.json (Argon2id). Session lands in SQLite.
    Returns: plain session token (cookie).
    Raises:  ValueError on unknown user or wrong password.
    """
    record = authenticate(username, password)
    if record is None:
        logger.warning("Login failed: %s", username)
        raise ValueError("Login fehlgeschlagen")

    token = sessiondb.create(
        user=record.username,
        role=record.role,
        personal_path=record.personal_path,
        is_demo=record.is_demo,
        ttl_seconds=SESSION_MAX_AGE,
        ip=ip,
        user_agent=user_agent,
    )
    tag = " [demo]" if record.is_demo else ""
    logger.info("Session created: %s → %s%s", record.username, record.role, tag)
    return token


def logout(token: str) -> None:
    """Invalidate session."""
    if sessiondb.destroy(token):
        logger.info("Logout: %s", token[:8])


def destroy_sessions_for_user(user: str) -> int:
    """Kill all sessions for a user (admin PW reset or user delete)."""
    n = sessiondb.destroy_all_for_user(user)
    if n:
        logger.info("Sessions invalidated: %s (%d)", user, n)
    return n
