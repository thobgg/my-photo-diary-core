"""
routers/users_admin.py
Admin UI: user lifecycle in the modal.

GET    /api/admin/users             — all users without pw_hash, plus self=caller
POST   /api/admin/users              — create new user
PATCH  /api/admin/users/{username}   — display_name | role | password
DELETE /api/admin/users/{username}   — delete user (guards: self, last admin)

Password rules:
  - Min length 12 (matching CLI, except for is_demo=true which uses 6;
    not settable here)
  - Hash via Argon2id (userdb.hash_password)
  - Never return in the response

Session consequences:
  - PW reset invalidates ALL sessions of the user
  - Delete invalidates ALL sessions of the user
"""

import re

from fastapi import APIRouter, HTTPException, Request

from config import default_personal_path
from core import userdb, session as session_mod
from core.i18n import t as _t
from routers.deps import require_session

router = APIRouter()

MIN_PW_LEN  = 12
_USER_RE    = re.compile(r"^[a-z][a-z0-9._-]{1,31}$")


def _require_admin(request: Request):
    session = require_session(request)
    if session.role != "admin":
        raise HTTPException(403, _t("users.admin_only", request))
    return session


def _public_entry(username: str, entry: dict) -> dict:
    """User entry without pw_hash — display data only."""
    return {
        "username"      : username,
        "role"          : entry.get("role", "viewer"),
        "personal_path" : entry.get("personal_path", ""),
        "is_demo"       : bool(entry.get("is_demo", False)),
        "display_name"  : entry.get("display_name") or "",
        "has_password"  : bool(entry.get("pw_hash")),
    }


def _admin_count(users: dict) -> int:
    return sum(1 for e in users.values() if e.get("role") == "admin")


@router.get("/api/admin/users")
async def list_users(request: Request):
    session = _require_admin(request)
    data = userdb.load_raw()
    users = data.get("users") or {}
    return {
        "self" : session.user,
        "users": [_public_entry(name, entry) for name, entry in sorted(users.items())],
    }


@router.post("/api/admin/users")
async def create_user(request: Request, body: dict):
    _require_admin(request)
    if not isinstance(body, dict):
        raise HTTPException(400, _t("users.body_must_be_object", request))

    username = (body.get("username") or "").strip().lower()
    password = body.get("password") or ""
    role     = (body.get("role") or "").strip().lower()
    personal_path = (body.get("personal_path") or "").strip()

    if not _USER_RE.match(username):
        raise HTTPException(400, _t("users.invalid_username", request))
    if role not in userdb.ALLOWED_ROLES:
        raise HTTPException(400, _t("users.invalid_role", request, {"roles": sorted(userdb.ALLOWED_ROLES)}))
    if not isinstance(password, str) or len(password) < MIN_PW_LEN:
        raise HTTPException(400, _t("users.password_min", request, {"n": MIN_PW_LEN}))
    if not personal_path:
        personal_path = default_personal_path(username)

    data = userdb.load_raw()
    users = data.setdefault("users", {})
    if username in users:
        raise HTTPException(409, _t("users.user_exists", request, {"user": username}))

    users[username] = {
        "pw_hash"       : userdb.hash_password(password),
        "role"          : role,
        "personal_path" : personal_path,
        "is_demo"       : False,
    }
    display_name = (body.get("display_name") or "").strip()
    if display_name:
        users[username]["display_name"] = display_name

    data.setdefault("version", userdb.SCHEMA_VERSION)
    userdb.save_raw(data)
    return _public_entry(username, users[username])


@router.patch("/api/admin/users/{username}")
async def patch_user(username: str, request: Request, body: dict):
    session = _require_admin(request)
    if not isinstance(body, dict):
        raise HTTPException(400, _t("users.body_must_be_object", request))

    data = userdb.load_raw()
    users = data.setdefault("users", {})
    if username not in users:
        raise HTTPException(404, _t("users.user_unknown", request))

    password_changed = False

    # ── display_name ────────────────────────────────────────────────
    if "display_name" in body:
        value = body["display_name"]
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise HTTPException(400, _t("users.display_name_must_be_string", request))
        value = value.strip()
        if value:
            users[username]["display_name"] = value
        else:
            users[username].pop("display_name", None)

    # ── role ────────────────────────────────────────────────────────
    if "role" in body:
        new_role = body["role"]
        if not isinstance(new_role, str) or new_role not in userdb.ALLOWED_ROLES:
            raise HTTPException(400, _t("users.invalid_role", request, {"roles": sorted(userdb.ALLOWED_ROLES)}))

        old_role = users[username].get("role", "viewer")
        if new_role != old_role:
            if username == session.user and new_role != "admin":
                raise HTTPException(400, _t("users.self_admin_role_locked", request))
            if old_role == "admin" and new_role != "admin" and _admin_count(users) <= 1:
                raise HTTPException(400, _t("users.last_admin_role_locked", request))
            users[username]["role"] = new_role

    # ── password (admin reset) ──────────────────────────────────────
    if "password" in body:
        password = body["password"] or ""
        if not isinstance(password, str) or len(password) < MIN_PW_LEN:
            raise HTTPException(400, _t("users.password_min", request, {"n": MIN_PW_LEN}))
        users[username]["pw_hash"] = userdb.hash_password(password)
        password_changed = True

    data.setdefault("version", userdb.SCHEMA_VERSION)
    userdb.save_raw(data)

    if password_changed:
        session_mod.destroy_sessions_for_user(username)

    return _public_entry(username, users[username])


@router.delete("/api/admin/users/{username}")
async def delete_user(username: str, request: Request):
    session = _require_admin(request)

    if username == session.user:
        raise HTTPException(400, _t("users.self_delete_denied", request))

    data = userdb.load_raw()
    users = data.setdefault("users", {})
    if username not in users:
        raise HTTPException(404, _t("users.user_unknown", request))

    if users[username].get("role") == "admin" and _admin_count(users) <= 1:
        raise HTTPException(400, _t("users.last_admin_delete_denied", request))

    del users[username]
    data.setdefault("version", userdb.SCHEMA_VERSION)
    userdb.save_raw(data)

    killed = session_mod.destroy_sessions_for_user(username)
    return {"deleted": username, "sessions_killed": killed}
