"""
routers/settings.py
Instance-wide feature toggles. GET for all logged-in users (so the
frontend can render conditionally), POST is admin-only.

Also serves the admin-only developer handbook at /admin/handbook —
the file is baked into the Docker image at /app/docs/handbook.html
(see Dockerfile).
"""

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

import config
from core import settings
from core.i18n import t as _t
from routers.deps import require_session

logger = logging.getLogger(__name__)

router = APIRouter()

# Resolves to /app/docs/handbook.html in the container.
_HANDBOOK_PATH = Path(__file__).parent.parent.parent / "docs" / "handbook.html"


def _mask(raw: str) -> str:
    """Genug zum Wiedererkennen, zu wenig zum Benutzen."""
    if not raw:
        return ""
    return raw[:8] + "…" + raw[-4:] if len(raw) > 12 else "•••"


# Admin-sichtbare SMTP-Felder im Klartext. smtp_pass fehlt hier mit
# Absicht — es geht nur maskiert als smtp_pass_hint hinaus.
_SMTP_PLAIN = ("smtp_host", "smtp_port", "smtp_user",
               "smtp_from_addr", "smtp_from_name")

# Felder, bei denen ein leerer Wert "unveraendert" bedeutet, nie "loeschen".
_SECRET_KEYS = frozenset({"smtp_pass", "companion_api_key"})


def _settings_response(is_admin: bool) -> dict:
    """Build the settings response: public keys + masked hints for admin."""
    data = dict(settings.public_settings())
    if is_admin:
        try:
            data["companion_api_key_hint"] = _mask(settings.get("companion_api_key"))
        except Exception:
            data["companion_api_key_hint"] = ""
        # Mailversand: Werte zurueckgeben, Passwort nur maskiert, dazu die
        # Herkunft — damit der Dialog sagen kann, ob gerade die Einstellung
        # oder noch mpd.env gilt.
        for k in _SMTP_PLAIN:
            try:
                data[k] = settings.get(k)
            except Exception:
                pass
        try:
            data["smtp_pass_hint"] = _mask(settings.get("smtp_pass"))
        except Exception:
            data["smtp_pass_hint"] = ""
        try:
            from core import mailer
            data["smtp_source"]     = mailer.config_source()
            data["smtp_configured"] = mailer.is_configured()
        except Exception:
            data["smtp_source"]     = "none"
            data["smtp_configured"] = False
        # MPD Plus: Lizenzstand fuer den Abschnitt im Einstellungsdialog
        try:
            from core import license as _license
            data["license"] = _license.license_info()
        except Exception:
            data["license"] = {"valid": False, "enforced": False, "modules": []}
    return data


# ---------------------------------------------------------------------------
# Pro-Nutzer-Einstellungen (03.09.2026) — Selbstbedienung, streng
# whitelisted. Aktuell nur die Memories-Quelle (users.json:
# memories_scope). Demo kann nicht schreiben (Middleware).
# ---------------------------------------------------------------------------

_ME_FIELDS = {"memories_scope"}
_ME_SCOPES = {"shared", "personal"}


@router.get("/api/settings/me")
async def get_my_settings(request: Request):
    session = require_session(request)
    from core.userdb import lookup
    rec = lookup(session.user)
    return {"memories_scope": rec.memories_scope if rec else "shared"}


@router.put("/api/settings/me")
async def put_my_settings(request: Request):
    session = require_session(request)
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:
        raise HTTPException(400, _t("common.invalid_request", request))
    unknown = set(body) - _ME_FIELDS
    if unknown:
        raise HTTPException(400, _t("common.invalid_request", request))
    scope = body.get("memories_scope")
    if scope not in _ME_SCOPES:
        raise HTTPException(400, _t("common.invalid_request", request))

    from core import userdb
    data = userdb.load_raw()
    users = data.get("users", {})
    if session.user not in users:
        raise HTTPException(404, _t("common.not_logged_in", request))
    users[session.user]["memories_scope"] = scope
    userdb.save_raw(data)
    return {"memories_scope": scope}


@router.get("/api/settings")
async def get_settings(request: Request):
    """Read global settings. Any logged-in user may read (sensitive keys excluded)."""
    session = require_session(request)
    return _settings_response(session.role == "admin")


@router.post("/api/settings")
async def update_settings(request: Request, data: dict):
    """Set values. Admin-only.

    Body stays a dynamic dict on purpose — the allowed-keys whitelist
    lives in core/settings.set_many(), so a Pydantic model here would
    duplicate that list.
    """
    session = require_session(request)
    if session.role != "admin":
        raise HTTPException(403, _t("settings_be.admin_only", request))
    if not isinstance(data, dict):
        raise HTTPException(400, _t("settings_be.body_must_be_object", request))

    # Geheimnisse gehen nur maskiert hinaus. Kaeme ein leeres Feld zurueck,
    # wuerde ein simples "Speichern" die hinterlegten Zugangsdaten loeschen —
    # ein Klick, der aussieht wie nichts und Mailversand abschaltet. Ein
    # leerer Wert heisst deshalb "unveraendert".
    # Zum Loeschen: smtp_host leeren, damit ist der Mailversand aus.
    data = {k: v for k, v in data.items()
            if not (k in _SECRET_KEYS and isinstance(v, str) and not v.strip())}

    try:
        settings.set_many(data)
        return _settings_response(True)
    except Exception as e:
        logger.error("Settings update failed: %s", e, exc_info=True)
        raise HTTPException(500, f"Settings could not be saved: {e}")


@router.get("/api/connect")
async def connect_info(request: Request):
    """Was die App braucht, um sich zu verbinden (11.09.2026).

    Anlass ist der SPK-Fremdnutzer-Durchlauf: Die schwaechste Stelle der
    Einrichtung ist nicht die Installation, sondern das Abtippen der
    Serveradresse auf dem Handy. Der DSM-Assistent kann dabei nicht
    helfen — er kann nur Text, keine Bilder.

    `local` ist die Adresse, ueber die dieser Browser GERADE zugreift —
    sie funktioniert nachweislich, denn sonst waere diese Antwort nicht
    hier. `remote` ist die konfigurierte Basisadresse (MPD_BASE_URL),
    falls sie gesetzt ist und abweicht; sie gilt fuer unterwegs."""
    require_session(request)
    from routers.deps import public_base_url
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    local = f"{scheme}://{host}".rstrip("/") if host else ""
    configured = (config.MPD_BASE_URL or "").rstrip("/")
    return {
        "local": local or public_base_url(request),
        "remote": configured if configured and configured != local else "",
    }


@router.get("/api/connect.svg")
async def connect_qr(request: Request, remote: int = 0):
    """Die Adresse als QR-Code (SVG). `remote=1` nimmt die konfigurierte
    Basisadresse statt der gerade benutzten.

    Selbst gezeichnet (`core/qr_min.py`), weil dieser Code genau dann
    gebraucht wird, wenn noch nichts eingerichtet ist — frische NAS,
    Handy im WLAN, oft ohne Internet. Ein Skript vom CDN waere hier die
    falsche Wahl."""
    require_session(request)
    info = await connect_info(request)
    url = info["remote"] if remote else info["local"]
    if not url:
        raise HTTPException(404, _t("settings_be.connect_no_url", request))
    from core import qr_min
    # Im Code steht der Sprung in die App, nicht die Adresse und nicht der
    # Umweg ueber diese Seite (15.09.2026, mit dem App-Chat zweimal
    # durchgesprochen). Drei Wege standen zur Wahl:
    #
    #   nackte Adresse      Landet im Browser. Hilft beim Einrichten nicht,
    #                       die Adresse muss trotzdem abgetippt werden.
    #   Android App Link    Bindet an EINE verifizierte Domain
    #                       (assetlinks.json). Fuer eine Selbsthoster-
    #                       Software auf fremden Adressen nicht zu haben.
    #   /verbinden?setup=1  Erst erwogen, dann verworfen: Ein neuer Kunde
    #                       hat https://192.168.x.x:5001 mit selbst-
    #                       signiertem Zertifikat. Der Browser warnt dann
    #                       "Verbindung nicht sicher" — beim allerersten
    #                       Schritt, und fuer einen Laien sieht das aus wie
    #                       ein Fehler. Die APP kann solche Zertifikate
    #                       (einmal fragen, Fingerabdruck merken), der
    #                       Browser nicht. Den Weg durch die Instanz zu
    #                       leiten, die es schlechter beherrscht, waere
    #                       verkehrt.
    #
    # Der Einwand "ohne App fuehrt der Code ins Leere" bleibt richtig, er
    # gehoert nur woanders hin: Gescannt wird mit dem Handy, gelesen wird
    # diese Seite am Rechner. Schritt 1 nennt deshalb ausdruecklich, dass
    # die App vorher installiert sein muss. Und der Knopf "In MPD oeffnen"
    # auf der Seite deckt den Fall ab, dass jemand mit dem Handy ohnehin
    # schon hier ist.
    ziel = f"mpd://setup?url={quote(url, safe='')}"
    svg = qr_min.svg(ziel, module=8, quiet=4, title=url)
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@router.post("/api/settings/license")
async def install_license(request: Request, data: dict):
    """MPD Plus (09.09.2026): Lizenztext einspielen — der Weg fuer Kaeufer
    ohne Shell. Admin-only. Body {"text": "<Inhalt der Lizenzdatei>"}.
    Ungueltig → 400 mit Grund, die vorhandene Datei bleibt."""
    session = require_session(request)
    if session.role != "admin":
        raise HTTPException(403, _t("settings_be.admin_only", request))
    text = data.get("text") if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(400, _t("settings_be.license_empty", request))
    from core import license as _license
    try:
        return _license.install(text.strip())
    except ValueError as e:
        raise HTTPException(400, _t("settings_be.license_invalid", request, {"msg": str(e)}))


@router.delete("/api/settings/license")
async def remove_license(request: Request):
    """Lizenz entfernen — Plus faellt weg, Basis bleibt. Admin-only."""
    session = require_session(request)
    if session.role != "admin":
        raise HTTPException(403, _t("settings_be.admin_only", request))
    from core import license as _license
    return _license.remove()


@router.get("/admin/handbook")
async def get_handbook(request: Request):
    """Serve the developer handbook. Admin-only — opens in a new tab from
    the settings modal. The file is baked into the image at build time."""
    session = require_session(request)
    if session.role != "admin":
        raise HTTPException(403, _t("settings_be.admin_only", request))
    if not _HANDBOOK_PATH.is_file():
        raise HTTPException(404, _t("static.not_found", request, {"kind": "Handbook"}))
    return FileResponse(_HANDBOOK_PATH, media_type="text/html")
