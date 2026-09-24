"""pytest-Config: backend/ auf sys.path, damit Imports wie `from routers...` funktionieren.

Für die API-Tests wird `config` VOR jedem App-Import auf temporäre Pfade
umgebogen — alle Module binden Config-Werte bei ihrem Import (`from config
import X`), daher muss das hier auf Modulebene passieren, nicht in Fixtures.
"""
import json
import sys
import tempfile
from pathlib import Path

_BACKEND = Path(__file__).parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# ── Test-Umgebung: config auf tmp-Pfade, bevor irgendein App-Modul lädt ──
_TMP = Path(tempfile.mkdtemp(prefix="mpd-tests-"))

import config  # noqa: E402

config.PHOTO_PATH_SHARED = _TMP / "shared"
config.THUMB_CACHE_DIR   = _TMP / "thumb-cache"
config.USERS_JSON_PATH   = _TMP / "users.json"
config.SESSION_DB_PATH   = _TMP / "mpd.sqlite"
config.NOTIFY_DB_PATH    = _TMP / "mpd-notify.sqlite"
config.LICENSE_FILE_PATH = _TMP / "mpd-license.json"
config.TRAILS_DIR        = _TMP / "trails"
config.GEOCODE_URL       = ""   # Startlauf der Ortsnamen darf nie ins Netz (test_trails_places setzt es selbst)
config.IMPORT_DIR        = _TMP / "import"
# Der Demo-Zugang haengt an der ECHTEN mpd.env des Deploys (config liest sie
# beim Import). Tests sollen davon nicht abhaengen: hier aus, wer ihn braucht,
# schaltet ihn per monkeypatch an.
config.DEMO_ENABLED      = False
config.DEMO_PASSWORD     = ""

config.PHOTO_PATH_SHARED.mkdir(parents=True, exist_ok=True)
config.IMPORT_DIR.mkdir(parents=True, exist_ok=True)
config.THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

import pytest  # noqa: E402

@pytest.fixture(autouse=True)
def _reset_ratelimit():
    """Der Share-/Contrib-Rate-Limiter ist In-Memory und testweit geteilt —
    provozierte 404er (Token-Raten) würden sonst spätere Tests in die
    429-Sperre laufen lassen."""
    from core import ratelimit
    for attr in ("_FAILS", "_fails", "_failures"):
        d = getattr(ratelimit, attr, None)
        if isinstance(d, dict):
            d.clear()
    yield


# Testnutzer: (username, passwort, rolle)
ADMIN  = ("tester", "geheim123", "admin")
VIEWER = ("vera", "viewer123", "viewer")


def make_jpeg(path: Path, size=(4, 4), color=(200, 150, 40)):
    """Winziges, aber echtes JPEG — die Thumb-Pipeline liest es mit PIL."""
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG")
    return path


@pytest.fixture(scope="session")
def users_file():
    """users.json mit Admin + Viewer anlegen (Argon2-Hashes wie in Produktion)."""
    from core.userdb import hash_password

    users = {}
    for name, pw, role in (ADMIN, VIEWER):
        personal = _TMP / f"personal-{name}"
        personal.mkdir(exist_ok=True)
        users[name] = {
            "pw_hash": hash_password(pw),
            "role": role,
            "personal_path": str(personal),
            "is_demo": False,
        }
    config.USERS_JSON_PATH.write_text(
        json.dumps({"version": 1, "users": users}), encoding="utf-8"
    )
    return users


@pytest.fixture(scope="session")
def _app(users_file):
    """App einmal pro Testlauf hochfahren (Lifespan: Scheduler, Warmer)."""
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app):
        yield app


@pytest.fixture
def client(_app):
    """Frischer Client pro Test — eigene Cookie-Jar, kein Session-Bleed."""
    from fastapi.testclient import TestClient

    return TestClient(_app)


@pytest.fixture
def login(client):
    """client einloggen; default: Admin. Gibt die Login-Response zurück."""
    def _login(user=ADMIN[0], pw=ADMIN[1]):
        r = client.post("/api/login", json={"username": user, "password": pw})
        assert r.status_code == 200, f"Login fehlgeschlagen: {r.status_code} {r.text}"
        return r
    return _login


@pytest.fixture
def personal_album(client, login):
    """Album-Ordner mit 2 echten Mini-JPEGs im Personal-Space des Admins,
    per API in ein Album (album.json) verwandelt. Name pro Test eindeutig."""
    import uuid

    folder = f"2020 - Test {uuid.uuid4().hex[:8]}"
    base = _TMP / f"personal-{ADMIN[0]}" / folder
    make_jpeg(base / "p1.jpg")
    make_jpeg(base / "p2.jpg", color=(40, 90, 160))

    login()
    r = client.post("/api/album/create", json={
        "space": "personal", "folder_name": folder, "thumbnail": "p1.jpg",
    })
    assert r.status_code == 200, f"Album-Anlage fehlgeschlagen: {r.status_code} {r.text}"
    return folder


@pytest.fixture
def alle_module_installiert(monkeypatch):
    """Freischalt- und Lizenzlogik unabhaengig davon pruefen, welche
    Plus-Dateien mitgeliefert sind (19.09.2026: der oeffentliche Kern
    kommt ohne). user_modules() schneidet seither mit installed_modules();
    diese Tests pruefen die Logik DARUEBER und tun so, als laege alles da."""
    from routers import deps
    monkeypatch.setattr(deps, "_INSTALLED", deps.MODULE_KEYS)


def modul_da(name: str) -> bool:
    """Liegt das Plus-Modul (Router-Datei) in dieser Installation?"""
    import importlib.util
    return importlib.util.find_spec(f"routers.{name}") is not None
