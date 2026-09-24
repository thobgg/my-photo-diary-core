"""
My Photo Diary v2 — FastAPI Backend
Session-based, multi-user
Port 8089 (V1 runs in parallel on 8088)
"""

import asyncio
from datetime import datetime, timedelta
import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

import config
from config import (
    PHOTO_PATH_SHARED, MEMORIES_HOUR, MEMORIES_MINUTE, STATE_DIR,
)
from core.library_warmer import warm_library, discover_personal_spaces
from core.session import (
    login, logout, get_session, request_token, COOKIE_NAME, SESSION_MAX_AGE,
)
from core import ratelimit
from core import userdb
from core.i18n import t as _t
# Der Scheduler gehoert dem Kern (19.09.2026, core/scheduler.py): Aufraeumen,
# Rettungskopie und Benachrichtigungen liegen im Kern-Scheduler; vorher lag
# die ganze Instanz in diary_memories/ — fehlte das Modul, fielen die
# Kern-Jobs still mit weg. Memories selbst gehoert seit 20.09.2026 ebenfalls
# zum Kern und wird fest geladen.
from core.scheduler import start_scheduler, stop_scheduler
from diary_memories.scheduler import register_jobs as _register_memories_jobs

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler(
            STATE_DIR / "my-photo-diary.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)


def _read_product_version() -> str:
    """Die Produktversion — die Aussage ueber das Produkt, von Hand gepflegt
    in backend/product_version.txt. Sie steht vorn in jeder Versionsnummer
    und ist das, was Kunden und SPK-Pakete sehen."""
    f = Path(__file__).parent / "product_version.txt"
    if f.is_file():
        try:
            v = f.read_text(encoding="utf-8").strip()
            if v:
                return v
        except Exception:
            pass
    return "3.0"


PRODUCT_VERSION = _read_product_version()


def _read_version() -> str:
    """Volle Version <Produkt>.<Build> aus version.txt (post-commit-Hook).
    Rueckfall: Produktversion + Commit-Anzahl direkt aus git, dann '<Produkt>.0'.

    Die dritte Stelle ist eine BUILD-Nummer, keine Patch-Nummer: sie zaehlt
    Commits und laeuft deshalb weit hoch (246 bei der Umstellung 09/2026).
    Wer eine neue Produktversion will, aendert product_version.txt — nicht
    diese Funktion und nicht den Hook."""
    version_file = Path(__file__).parent / "version.txt"
    if version_file.is_file():
        try:
            return version_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    try:
        import subprocess
        count = subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=Path(__file__).parent,
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return f"{PRODUCT_VERSION}.{count}"
    except Exception:
        pass
    return f"{PRODUCT_VERSION}.0"


VERSION = _read_version()
# Build = alles hinter der Produktversion; leer, falls das Format mal abweicht.
BUILD = VERSION[len(PRODUCT_VERSION) + 1:] if VERSION.startswith(PRODUCT_VERSION + ".") else ""
logger.info("My Photo Diary %s (Produkt %s, Build %s)", VERSION, PRODUCT_VERSION, BUILD)

# Ohne gesetzte Basisadresse werden Links aus dem Request abgeleitet. Das
# traegt im laufenden Betrieb, aber nicht fuer Links aus Hintergrundjobs —
# und es ist eine stille Annahme. Deshalb eine Zeile im Log statt Schweigen.
if not config.MPD_BASE_URL_CONFIGURED:
    logger.warning(
        "MPD_BASE_URL ist nicht gesetzt. Links (Share, Mail, OG, QR) werden "
        "aus dem jeweiligen Request abgeleitet. Fuer verlaessliche Links den "
        "Wert in mpd.env eintragen."
    )

# Demo-Zugang: laut sagen, wenn er an ist — eine oeffentliche Anmeldung
# ohne Passwort soll niemanden ueberraschen. Und laut sagen, wenn der
# Schalter an ist, das Passwort aber fehlt: dann ist er wirkungslos.
if config.DEMO_ENABLED and config.DEMO_PASSWORD:
    logger.warning("Demo-Zugang ist AKTIV: /demo-login meldet oeffentlich "
                   "als '%s' an. Nur fuer die Schau-Installation gedacht.",
                   config.DEMO_USER)
elif config.DEMO_ENABLED:
    logger.warning("MPD_DEMO=1, aber MPD_DEMO_PASSWORD ist leer — der "
                   "Demo-Zugang bleibt aus.")

# ---------------------------------------------------------------------------
# Login HTML (read once)
# ---------------------------------------------------------------------------

_LOGIN_HTML = (Path(__file__).parent.parent / "frontend" / "login.html").read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Kern-Jobs immer; der Memories-Job, wenn das Modul da ist (ntfy 09/2026
    # ausgebaut — der Push geht über den In-App-Notify-Store).
    start_scheduler()
    _register_memories_jobs(
        photo_base = PHOTO_PATH_SHARED,
        hour       = MEMORIES_HOUR,
        minute     = MEMORIES_MINUTE,
    )

    # Background warmer: migrates album.json meta and generates thumb
    # caches. Shared + all personal spaces in parallel; within an
    # album parallel photo tasks, across albums sequential per space.
    # The default thread pool (~8 slots) caps Pillow load.
    async def _run_all_warmers():
        tasks = [warm_library(PHOTO_PATH_SHARED, "shared")]
        for user, path in discover_personal_spaces():
            tasks.append(warm_library(path, f"personal:{user}"))
        await asyncio.gather(*tasks, return_exceptions=True)

    warmer_task = asyncio.create_task(_run_all_warmers())

    # Memories-Tages-Scan vorwärmen (03.09.2026): der 12-s-Kaltscan soll
    # nie einen Nutzer treffen — Start hier, Tageswechsel im Scheduler.
    async def _prewarm_memories():
        from routers.memories import prewarm_memories
        try:
            await prewarm_memories("startup")
        except Exception as e:
            logger.warning("Memories-Prewarm fehlgeschlagen: %s", e)

    prewarm_task = asyncio.create_task(_prewarm_memories())

    # MPD Trails (09/2026): nächtlicher Monatsexport der Standorthistorie
    # als Zeilen-JSON — „Dateien bleiben die Wahrheit", der Store ist
    # daraus neu aufbaubar. Läuft in einem Thread, blockiert die Loop nicht.
    async def _trails_export_loop():
        from config import TRAILS_EXPORT_HOUR
        try:
            from core import trails_store
        except ImportError:
            return            # Modul nicht mitgeliefert — kein Nachtlauf noetig
        while True:
            now = datetime.now()
            nxt = now.replace(hour=TRAILS_EXPORT_HOUR, minute=0, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            await asyncio.sleep((nxt - now).total_seconds())
            try:
                done = await asyncio.to_thread(trails_store.export_all_dirty)
                if done:
                    logger.info("Trails export: %s", done)
            except Exception as e:
                logger.warning("Trails export fehlgeschlagen: %s", e)
            # Phase 4: Besuche/Wege aus den eigenen Rohpunkten ableiten
            try:
                from core import trails_derive
                derived = await asyncio.to_thread(trails_derive.derive_all_pending)
                if derived:
                    logger.info("Trails derive: %s", derived)
            except Exception as e:
                logger.warning("Trails derive fehlgeschlagen: %s", e)

            # Ortsnamen nachtragen. Bewusst zuletzt und gedrosselt: der
            # oeffentliche Dienst erlaubt fuer Stapellaeufe 4 Anfragen je
            # Minute. Wichtigste Orte zuerst.
            await _backfill_places("nacht")

    # Ortsnamen: nicht bis 04:00 warten, wenn schon Orte offen sind
    # (Entscheidung 08.09.2026). Der Dienst ist ohnehin
    # per Takt gedrosselt, tagsueber laeuft der Server fast leer, und ein
    # Restart kostet nichts: jede Antwort wird einzeln gespeichert, der
    # naechste Start macht dort weiter, wo der Thread abgebrochen wurde.
    # Ist nichts offen, ist der Aufruf in Sekunden durch. Das Lock haelt
    # den 04:00-Lauf davon ab, einen noch laufenden Tageslauf zu doppeln.
    _places_lock = asyncio.Lock()

    async def _backfill_places(anlass: str):
        if _places_lock.locked():
            logger.info("Trails Ortsnamen (%s): laeuft schon, uebersprungen", anlass)
            return
        async with _places_lock:
            try:
                try:
                    from core import trails_places
                except ImportError:
                    return    # Modul nicht mitgeliefert — nichts zu benennen
                benannt = await asyncio.to_thread(trails_places.backfill_all)
                if benannt:
                    logger.info("Trails Ortsnamen (%s): %s", anlass, benannt)
            except Exception as e:
                logger.warning("Trails Ortsnamen (%s) fehlgeschlagen: %s", anlass, e)

    trails_export_task = asyncio.create_task(_trails_export_loop())
    places_task = asyncio.create_task(_backfill_places("start"))

    try:
        yield
    finally:
        warmer_task.cancel()
        prewarm_task.cancel()
        trails_export_task.cancel()
        places_task.cancel()
        stop_scheduler()

# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

# Routes reachable without a valid session. Anything not listed here
# requires `require_session` to succeed. Keep this list short — every
# entry is an attack-surface decision, not a convenience.
# /api/notify/publish traegt seine eigene Auth (statischer Publish-Token,
# routers/notify.py) — die Absender sind NAS-Skripte ohne Session.
# /demo-login steht bewusst NICHT hier — es ist nur oeffentlich, wo es den
# Demo-Zugang gibt (siehe _demo_available unten); die Pruefung steht in der
# Middleware.
_PUBLIC_PATHS = frozenset({"/api/login", "/login", "/favicon.ico", "/apple-touch-icon.png", "/manifest.webmanifest", "/api/version", "/api/notify/publish", "/api/trails/ingest"})
# Public path *prefixes*. /share/ is the magic-link viewer (its own auth via
# token). /static, /js, /css, /fonts, /img/brand are static assets.
_PUBLIC_PREFIXES = ("/static/", "/share/", "/contrib/", "/js/", "/css/", "/fonts/", "/img/brand/")

# Pfade, die trotz gesetztem must_change_password-Flag erreichbar bleiben
# (die Seite selbst, die Änderungs-API, Abmelden und die Statusabfrage,
# über die Web-Frontend und App den Zustand erkennen).
_MUST_CHANGE_ALLOWED = frozenset({
    "/change-password", "/api/account/change-password",
    "/api/logout", "/api/whoami",
})


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in _PUBLIC_PATHS or (path == "/demo-login" and _demo_available()):
            return await call_next(request)
        for prefix in _PUBLIC_PREFIXES:
            if path.startswith(prefix):
                # /share/*: rate-limit lock before every token lookup
                # (log-flood protection against scanning bots).
                if path.startswith(("/share/", "/contrib/")):
                    ip = request.client.host if request.client else "unknown"
                    remaining = ratelimit.check(ip)
                    if remaining > 0:
                        return JSONResponse(
                            {"detail": _t("auth_be.rate_limit_share", request, {"seconds": int(remaining) + 1})},
                            status_code=429,
                        )
                return await call_next(request)

        # (Der MEMORIES_TOKEN-Bypass für die alte Memories-Extra-APK wurde
        # am 03.09.2026 mit dem ntfy-Ausbau entfernt — /api/memories und
        # /api/memories-thumb laufen jetzt ausschließlich über die normale
        # Session-Auth der App bzw. des Webs.)

        # request_token: Bearer-Header (native App) oder Cookie (Web/Wrapper)
        token = request_token(request)
        session = get_session(token) if token else None
        if session is None:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Nicht eingeloggt"}, status_code=401)
            return RedirectResponse("/login", status_code=302)

        # Demo session: strictly read-only (except /api/logout)
        if session.is_demo and request.method not in ("GET", "HEAD"):
            if path != "/api/logout":
                return JSONResponse(
                    {"detail": "Demo: nur Leseoperationen erlaubt"},
                    status_code=403,
                )

        # Erstanmelde-Zwangswechsel (04.09.2026): solange das Flag steht,
        # sind nur Passwort-Änderung, Abmelden und whoami erreichbar.
        # users.json ist mtime-gecached — der lookup() kostet praktisch nichts.
        if not session.is_demo and path not in _MUST_CHANGE_ALLOWED:
            record = userdb.lookup(session.user)
            if record is not None and record.must_change_password:
                if path.startswith("/api/"):
                    return JSONResponse(
                        {"detail": _t("auth_be.must_change_password", request),
                         "code": "must_change_password"},
                        status_code=403,
                    )
                return RedirectResponse("/change-password", status_code=302)

        return await call_next(request)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="My Photo Diary", version=VERSION, lifespan=lifespan)
app.add_middleware(AuthMiddleware)


# ---------------------------------------------------------------------------
# Security headers (lightweight middleware, no new dep)
# ---------------------------------------------------------------------------

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    # 05.09.2026: war "same-origin" — damit schickte der Browser an FREMDE
    # Server gar keinen Referer, und die OSM-Kachelserver antworten darauf
    # mit „Access blocked" (ihre Nutzungsbedingung verlangt einen Referer).
    # "strict-origin-when-cross-origin" ist der heutige Browser-Standard:
    # nach außen nur die Herkunft (https://…), niemals der Pfad, und beim
    # Wechsel https→http gar nichts. Share-Seiten bleiben auf no-referrer.
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for k, v in _SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    # Share pages stricter: no referrer to outside (overrides the
    # global same-origin default — header beats meta tag and applies
    # to 404/error responses too).
    if request.url.path.startswith("/share/"):
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


# ---------------------------------------------------------------------------
# Login rate limit — seit 03.09.2026 über core/ratelimit (gleiche Kennlinie:
# 5 Fehlversuche in 5 min → 60 s Sperre je IP). Der Schlüssel "login:<ip>"
# hält die Zählung getrennt von Share-/Publish-Fehlversuchen.
# ---------------------------------------------------------------------------

def _login_check_rate_limit(ip: str) -> float:
    return ratelimit.check(f"login:{ip}")


def _login_record_failure(ip: str):
    ratelimit.record_failure(f"login:{ip}")


def _login_clear_failures(ip: str):
    ratelimit.clear(f"login:{ip}")

# ---------------------------------------------------------------------------
# Login / Logout / Whoami
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(_LOGIN_HTML, headers={"Cache-Control": "no-store"})


@app.post("/api/login")
async def api_login(request: Request):
    ip = request.client.host if request.client else "unknown"

    remaining = _login_check_rate_limit(ip)
    if remaining > 0:
        return JSONResponse(
            {"detail": _t("auth_be.rate_limit_login", request, {"seconds": int(remaining) + 1})},
            status_code=429,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": _t("auth_be.invalid_request", request)}, status_code=400)

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or not password:
        return JSONResponse({"detail": _t("auth_be.credentials_required", request)}, status_code=400)

    # Kill-Switch fuer das Demo-Konto (Einstellung, admin-gesteuert).
    # Nur wirksam, wo es einen Demo-Zugang gibt — sonst wuerde ein Kunde,
    # dessen Konto zufaellig so heisst, durch eine fremde Einstellung
    # ausgesperrt.
    from core import settings as _settings
    if (_demo_available() and username == config.DEMO_USER
            and not _settings.get("demo_enabled")):
        return JSONResponse({"detail": _t("auth_be.demo_disabled", request)}, status_code=403)

    user_agent = request.headers.get("user-agent", "")[:300]
    try:
        token = login(username, password, ip=ip, user_agent=user_agent)
    except ValueError as e:
        _login_record_failure(ip)
        return JSONResponse({"detail": str(e)}, status_code=401)

    _login_clear_failures(ip)

    session = get_session(token)
    payload = {"ok": True, "user": session.user, "role": session.role}
    # Additiv für die native App: {"want_token": true} im Login-Body liefert
    # den Session-Token auch im JSON (für Authorization: Bearer …). Das
    # Cookie wird unverändert zusätzlich gesetzt.
    if body.get("want_token"):
        payload["token"]      = token
        payload["expires_in"] = SESSION_MAX_AGE
    resp = JSONResponse(payload)
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=(request.url.scheme == "https"),
        samesite="lax",
        path="/",
    )
    return resp


@app.post("/api/logout")
async def api_logout(request: Request):
    token = request_token(request)
    if token:
        logout(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(key=COOKIE_NAME, path="/")
    return resp


def _demo_available() -> bool:
    """Gibt es den Demo-Zugang in dieser Installation ueberhaupt?

    Zwei Bedingungen, beide aus der Installationsebene: der Schalter
    MPD_DEMO und ein gesetztes Passwort. Der Kill-Switch demo_enabled aus
    den Einstellungen kommt erst danach — er kann abschalten, was es gibt,
    aber nichts einschalten, was es nicht gibt."""
    return bool(config.DEMO_ENABLED and config.DEMO_PASSWORD)


@app.post("/api/account/change-password")
async def api_change_password(request: Request):
    """Eigenes Passwort ändern (04.09.2026). Prüft das alte Passwort,
    setzt das neue (Argon2id) und löscht ein gesetztes
    must_change_password-Flag. Falsche Alt-Passwörter zählen auf das
    Login-Ratelimit derselben IP."""
    token = request_token(request)
    session = get_session(token)
    if session.is_demo:
        return JSONResponse({"detail": "Demo: nicht verfügbar"}, status_code=403)

    ip = request.client.host if request.client else "unknown"
    remaining = _login_check_rate_limit(ip)
    if remaining > 0:
        return JSONResponse(
            {"detail": _t("auth_be.rate_limit_login", request, {"seconds": int(remaining) + 1})},
            status_code=429,
        )

    try:
        body = await request.json()
        old_pw = str(body["old_password"])
        new_pw = str(body["new_password"])
    except Exception:
        return JSONResponse({"detail": "old_password und new_password erforderlich"}, status_code=400)

    if userdb.authenticate(session.user, old_pw) is None:
        _login_record_failure(ip)
        return JSONResponse({"detail": _t("auth_be.old_password_wrong", request)}, status_code=401)

    if len(new_pw) < 12:
        return JSONResponse({"detail": _t("auth_be.password_too_short", request, {"n": 12})}, status_code=400)
    if new_pw == old_pw:
        return JSONResponse({"detail": _t("auth_be.password_unchanged", request)}, status_code=400)

    if not userdb.change_password(session.user, new_pw):
        return JSONResponse({"detail": "users.json inkonsistent"}, status_code=500)
    _login_clear_failures(ip)
    logger.info("Password changed: %s (must_change cleared)", session.user)
    return {"ok": True}


@app.get("/change-password", response_class=HTMLResponse)
async def change_password_page():
    """Seite für den (erzwungenen) Passwortwechsel. Bewusst per
    FileResponse-Muster statt Modul-Level-read_text wie login.html —
    Änderungen brauchen so keinen Restart."""
    page = Path(__file__).parent.parent / "frontend" / "change-password.html"
    return HTMLResponse(page.read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store"})


@app.get("/demo-login")
async def demo_login(request: Request):
    """
    Convenience endpoint for demo access from the landing page.
    Invalidates existing session, logs in as mpd-demo, redirects to /.
    Admin can disable demo access globally via settings.
    """
    if not _demo_available():
        raise HTTPException(404)
    from core import settings as _settings
    if not _settings.get("demo_enabled"):
        return RedirectResponse("/login", status_code=302)

    # Sicherheits-Audit N1 (03.09.2026): Ein untergeschobener
    # /demo-login-Link darf keine ECHTE Session zerstören — wer
    # eingeloggt ist, wird unangetastet zur Startseite geleitet.
    old_token = request.cookies.get(COOKIE_NAME)
    if old_token:
        existing = get_session(old_token)
        if existing and not existing.is_demo:
            return RedirectResponse("/", status_code=302)
        logout(old_token)

    ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")[:300]
    try:
        token = login(config.DEMO_USER, config.DEMO_PASSWORD, ip=ip, user_agent=user_agent)
    except ValueError:
        return RedirectResponse("/login", status_code=302)

    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=(request.url.scheme == "https"),
        samesite="lax",
        path="/",
    )
    return resp


@app.get("/api/version")
async def api_version():
    """
    Build info for the version badge and the client-side stale-tab watcher.
    build_date = most recent mtime of backend .py files.
    build_token = monotonic mtime across backend + frontend assets, so
    version-watch.js can detect frontend-only deploys (HTML/CSS/JS edits).
    """
    from datetime import datetime
    backend_dir  = Path(__file__).parent
    frontend_dir = backend_dir.parent / "frontend"

    try:
        backend_mtime = max(p.stat().st_mtime for p in backend_dir.rglob("*.py"))
    except ValueError:
        backend_mtime = 0

    frontend_mtime = 0
    if frontend_dir.is_dir():
        for ext in ("*.html", "*.css", "*.js"):
            try:
                frontend_mtime = max(frontend_mtime, max(
                    (p.stat().st_mtime for p in frontend_dir.rglob(ext)), default=0
                ))
            except ValueError:
                pass

    build_token = max(backend_mtime, frontend_mtime)
    return {
        # version = <Produkt>.<Build>, unveraendert im Format — Clients, die
        # nur dieses Feld kennen, funktionieren weiter. product/build sind
        # additiv (09/2026) fuer alle, die die zwei Dinge trennen wollen.
        "version": VERSION,
        "product": PRODUCT_VERSION,
        "build": BUILD,
        "build_date": datetime.fromtimestamp(backend_mtime).strftime("%Y-%m-%d") if backend_mtime else "",
        "build_token": int(build_token),
        # 19.09.2026: "core" = keine Plus-Datei installiert (oeffentlicher
        # Kern), sonst "plus". Aus dem laufenden Code, nicht aus dem Bau —
        # so kann die Angabe nicht falsch sein, auch nach einem Umbau.
        "edition": _edition(),
    }


def _edition() -> str:
    from routers.deps import installed_modules
    return "plus" if installed_modules() else "core"


@app.get("/api/whoami")
async def api_whoami(request: Request):
    """Session data + freigeschaltete Module (fürs Frontend-Gating)."""
    from routers.deps import user_modules, user_rights
    token = request_token(request)
    session = get_session(token)
    from core.license import license_info
    from config import MPD_BASE_URL, MPD_BASE_URL_CONFIGURED
    return {
        "user": session.user,
        "role": session.role,
        "personal_path": str(session.personal_path),
        "is_demo": session.is_demo,
        "modules": sorted(user_modules(session)),
        # Additiv 06.09.2026: Was dieses Konto darf — damit Web und App die
        # Regeln nicht nachbauen muessen. Nur fuer die Anzeige; durchgesetzt
        # wird weiterhin an jedem Endpunkt (siehe deps.user_rights).
        "rights": user_rights(session),
        # Additiv 09/2026: Lizenzstatus (Basis frei, Module kosten)
        "license": license_info(),
        # Additiv 04.09.2026: Erstanmelde-Zwangswechsel — Web und App
        # leiten damit auf die Passwort-Änderung.
        "must_change_password": bool(
            not session.is_demo
            and (_r := userdb.lookup(session.user)) is not None
            and _r.must_change_password
        ),
        # Additiv 05.09.2026 fuer die App: oeffentliche Basisadresse, damit
        # QR-Codes und geteilte Links nicht mit einer lokalen IP gebaut
        # werden, die ausserhalb des WLAN ins Leere laeuft.
        #
        # **Nur wenn wirklich konfiguriert.** Waere hier ein geratener Wert,
        # bekaeme ein Kunde die Adresse einer fremden Installation
        # ausgeliefert — schlimmer als gar kein Feld. Fehlt es, faellt der
        # Client auf sein bisheriges Verhalten zurueck.
        **({"base_url": MPD_BASE_URL} if MPD_BASE_URL_CONFIGURED else {}),
    }


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

# Kern — muss da sein.
from routers import static, albums, album_files, media, companion, photo_edit, share, contacts as contacts_router, settings as settings_router, users_admin as users_admin_router, geo as geo_router
from routers import notify as notify_router
from routers import timeline as timeline_router
from routers import photos_geo as photos_geo_router
from routers import memories as memories_router   # Kern seit 20.09.2026

app.include_router(static.router)
app.include_router(albums.router)
app.include_router(album_files.router)
app.include_router(media.router)
app.include_router(companion.router)
app.include_router(photo_edit.router)
app.include_router(share.router)
app.include_router(contacts_router.router)
app.include_router(settings_router.router)
app.include_router(users_admin_router.router)
app.include_router(geo_router.router)
app.include_router(notify_router.router)
app.include_router(timeline_router.router)
app.include_router(photos_geo_router.router)
app.include_router(memories_router.router)

# Module — duerfen fehlen (Block A, 09.09.2026). Der Kern importiert sie
# nicht mehr (core/events); fehlt die Datei, fehlt der Router, und
# require_module() liefert fuer den Schluessel ohnehin 403. Welche Module
# geladen sind, steht beim Start im Log.
import importlib as _importlib
_LOADED_MODULES = []
for _mod in ("stats", "search", "tours", "trails"):
    try:
        _m = _importlib.import_module(f"routers.{_mod}")
        app.include_router(_m.router)
        _LOADED_MODULES.append(_mod)
    except ImportError as _e:
        # Fehlt die Datei, ist das der Normalfall des oeffentlichen Kerns
        # (19.09.2026) — eine Zeile reicht. Laut wird es nur, wenn die Datei
        # DA ist und trotzdem nicht laedt: dann fehlt ihr etwas.
        if getattr(_e, "name", None) == f"routers.{_mod}":
            logger.info("Modul %s nicht mitgeliefert", _mod)
        else:
            logger.warning("Modul %s nicht geladen: %s", _mod, _e)
logger.info("Module geladen: %s", ", ".join(_LOADED_MODULES) or "keine")
