"""
routers/notify.py
My Photo Diary v2 — Benachrichtigungs-API für die native App (additiv 09/2026).

Konzept: docs/API_CHANGES.md. Kein FCM, kein ntfy — die App pollt
(WorkManager, ~15 min) bzw. long-pollt im Vordergrund (`wait`-Parameter).
Auth wie überall: Bearer-Token oder mpd_session-Cookie (require_session).

Demo-Konten: die Middleware blockt für Demo alles außer GET — Registrierung
und Ack laufen damit automatisch ins 403; Benachrichtigungen sind für Demo
nicht vorgesehen.

Fehlertexte hier bewusst ohne i18n-Schicht: einziger Konsument ist die
native App, die die Texte nicht anzeigt, sondern auf Statuscodes reagiert.
"""

import asyncio
import logging
import secrets

from fastapi import APIRouter, HTTPException, Query, Request

import config
from core import notifydb
from routers.deps import require_session

logger = logging.getLogger(__name__)
router = APIRouter()

_LONGPOLL_MAX_WAIT = 25   # Sekunden — unter typischen Client/Proxy-Timeouts
_LONGPOLL_INTERVAL = 5.0  # Fallback-Prüfabstand (Weckung kommt per Event)

# Sofort-Weckung (09/2026): notifydb ruft nach jedem Insert den
# registrierten Hook — wartende Long-Polls werden per Event geweckt
# statt im Sekundentakt die DB zu befragen. Registrierung lazy beim
# ersten Pull (dann läuft die Event-Loop sicher).
_wake_event = asyncio.Event()
_wake_registered = False


def _register_wake() -> None:
    global _wake_registered
    if _wake_registered:
        return
    loop = asyncio.get_running_loop()

    def _hook():
        loop.call_soon_threadsafe(_pulse)

    def _pulse():
        _wake_event.set()
        _wake_event.clear()  # set() weckt alle aktuellen Warter trotzdem

    notifydb.on_notify = _hook
    _wake_registered = True


def _notif_json(n: notifydb.Notification) -> dict:
    return {
        "id"        : n.id,
        "created_at": n.created_at,
        "type"      : n.type,
        "title"     : n.title,
        "body"      : n.body,
        "payload"   : n.payload,
    }


def _device_json(d: notifydb.DeviceRow) -> dict:
    return {
        "device_id"    : d.device_id,
        "name"         : d.name,
        "platform"     : d.platform,
        "transport"    : d.transport,
        "created_at"   : d.created_at,
        "last_seen"    : d.last_seen,
        "last_acked_id": d.last_acked_id,
    }


@router.post("/api/notify/publish")
async def publish(request: Request):
    """Fremd-Einlieferung (03.09.2026): NAS-Dienste (db-kies, blank …)
    liefern Warnungen hier ein statt an ntfy — MPD wird die eine
    Benachrichtigungszentrale. Auth ueber statischen Publish-Token aus
    mpd.env (die Absender sind Shell-Skripte, keine Sessions); ohne
    konfigurierten Token existiert der Endpunkt praktisch nicht (404),
    Kundeninstallationen bleiben unberuehrt. Ereignis wird Broadcast an
    alle Nutzer, gleiche Pull/Ack-Mechanik wie memories."""
    token = config.NOTIFY_PUBLISH_TOKEN
    if not token:
        raise HTTPException(404, "Publish nicht konfiguriert")
    # N5 (Audit): Raten des Publish-Tokens drosseln wie beim Share-Token
    from core import ratelimit
    ip = request.client.host if request.client else "unknown"
    if ratelimit.check(ip) > 0:
        raise HTTPException(429, "Zu viele Fehlversuche — kurz warten")
    provided = request.headers.get("X-Publish-Token", "")
    if not secrets.compare_digest(provided, token):
        ratelimit.record_failure(ip)
        raise HTTPException(401, "Publish-Token fehlt oder ist falsch")

    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except Exception:
        raise HTTPException(400, "JSON-Objekt erwartet")

    type_ = str(body.get("type") or "alert")[:40]
    title = str(body.get("title") or "").strip()[:200]
    text = str(body.get("body") or "").strip()[:2000]
    if not title and not text:
        raise HTTPException(400, "title oder body erforderlich")
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}

    # Adressierung (03.09.2026, angefordert von der App): Wächter-Warnungen
    # gehen NUR an Admins — nicht als Broadcast an die ganze Familie.
    from core.userdb import list_users
    admins = [u for u, role in list_users().items() if role == "admin"]
    if admins:
        ids = notifydb.notify_many(
            recipients=admins, type=type_,
            title=title or "NAS", body=text, payload=payload,
        )
    else:  # theoretischer Fallback: keine Admins → Broadcast statt Verlust
        ids = [notifydb.notify(recipient="*", type=type_,
                               title=title or "NAS", body=text, payload=payload)]
    logger.info("Notify publish: type=%s title=%r ids=%s (Admins: %d)",
                type_, title, ids, len(admins))
    return {"ok": True, "id": ids[0], "ids": ids}


@router.put("/api/notify/devices/{device_id}")
async def register_device(request: Request, device_id: str):
    """Gerät anlegen/aktualisieren. Body (alles optional):
    {"name": …, "platform": …, "transport": "poll"}"""
    session = require_session(request)
    if not (1 <= len(device_id) <= 128):
        raise HTTPException(400, "device_id ungueltig")
    try:
        body = await request.json()
    except Exception:
        body = {}
    device = notifydb.register_device(
        device_id = device_id,
        user      = session.user,
        name      = (body.get("name") or None),
        platform  = (body.get("platform") or None),
        transport = (body.get("transport") or "poll"),
    )
    if device is None:
        raise HTTPException(403, "device_id gehoert einem anderen Benutzer")
    return _device_json(device)


@router.get("/api/notify/devices")
async def my_devices(request: Request):
    session = require_session(request)
    return {"devices": [_device_json(d) for d in notifydb.list_devices(session.user)]}


@router.delete("/api/notify/devices/{device_id}")
async def unregister_device(request: Request, device_id: str):
    session = require_session(request)
    if not notifydb.delete_device(device_id=device_id, user=session.user):
        raise HTTPException(404, "Geraet nicht gefunden")
    return {"ok": True}


@router.get("/api/notify/pull")
async def pull(
    request  : Request,
    device_id: str,
    since    : int = Query(-1, ge=-1),
    wait     : int = Query(0, ge=0, le=_LONGPOLL_MAX_WAIT),
):
    """Ereignisse nach `since` (Default: der Ack-Cursor des Geräts).
    `wait` > 0 hält die Antwort bis zu N Sekunden offen (Long-Poll) und
    liefert sofort, sobald etwas eintrifft."""
    session = require_session(request)
    device = notifydb.get_device(device_id)
    if device is None or device.user != session.user:
        raise HTTPException(404, "Geraet nicht registriert")

    _register_wake()
    cursor = since if since >= 0 else device.last_acked_id
    notifydb.touch_device(device_id)

    events = notifydb.pull(user=session.user, since=cursor)
    if not events and wait > 0:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while loop.time() < deadline:
            remaining = deadline - loop.time()
            try:
                # Event weckt sofort beim Eintreffen; Intervall nur als Netz
                await asyncio.wait_for(_wake_event.wait(),
                                       timeout=min(remaining, _LONGPOLL_INTERVAL))
            except asyncio.TimeoutError:
                pass
            events = notifydb.pull(user=session.user, since=cursor)
            if events:
                break

    return {
        "notifications": [_notif_json(n) for n in events],
        "cursor"       : events[-1].id if events else cursor,
    }


@router.post("/api/notify/ack")
async def ack(request: Request):
    """Body: {"device_id": …, "last_id": <hoechste verarbeitete id>}"""
    session = require_session(request)
    try:
        body = await request.json()
        device_id = body["device_id"]
        last_id   = int(body["last_id"])
    except Exception:
        raise HTTPException(400, "device_id und last_id erforderlich")
    if not notifydb.ack_device(device_id=device_id, user=session.user, last_id=last_id):
        raise HTTPException(404, "Geraet nicht registriert")
    return {"ok": True}
