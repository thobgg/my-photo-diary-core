"""
routers/contacts.py
MPD address book API — per-owner email contacts for Share-per-Mail.
All endpoints behind session auth; every user sees only their own.
"""

import re
from typing import Optional
from fastapi import APIRouter, HTTPException, Request

from core import contacts
from core.i18n import t as _t
from routers.deps import require_session

router = APIRouter()

# Simple sanity check — not RFC-5322-perfect, but covers 99% of sensible
# addresses and rejects obvious junk.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(email: str, request: Optional[Request] = None) -> str:
    email = (email or "").strip().lower()
    if not email or not _EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(400, _t("contacts.invalid_email", request))
    return email


@router.get("/api/contacts")
async def list_contacts(request: Request):
    session = require_session(request)
    entries = contacts.list_for_owner(session.user)
    return {
        "contacts": [
            {
                "email"       : c.email,
                "name"        : c.name,
                "last_used_at": c.last_used_at,
            }
            for c in entries
        ]
    }


@router.delete("/api/contacts/{email}")
async def delete_contact(request: Request, email: str):
    session = require_session(request)
    email_norm = _validate_email(email, request)
    ok = contacts.delete(session.user, email_norm)
    if not ok:
        raise HTTPException(404, _t("contacts.contact_not_found", request))
    return {"deleted": True}
