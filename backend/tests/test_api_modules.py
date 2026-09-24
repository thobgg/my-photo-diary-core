"""API-Tests für die Modul-Entitlement-Schicht (require_module).

Deckt ab: gesperrtes Modul → 403, freigeschaltetes Modul → nicht 403,
Grandfather (kein modules-Feld → alle frei), Admin → alle, und die
modules-Liste in /health. Echte App, echte Argon2-User (kein Mocking).
"""
import pytest

import config
from tests.conftest import ADMIN, VIEWER, modul_da

# Die Freischaltlogik gilt unabhaengig davon, welche Plus-Dateien liegen.
pytestmark = pytest.mark.usefixtures("alle_module_installiert")

ALL_MODULES = ["search", "stats", "tours", "trails"]   # memories: seit 20.09.2026 Kern


def _upsert_user(name, pw, role, modules):
    """User mit optionalem modules-Feld in users.json schreiben.
    save_raw() setzt den userdb-Cache zurück → require_module liest frisch."""
    from core import userdb

    personal = config.USERS_JSON_PATH.parent / f"personal-{name}"
    personal.mkdir(parents=True, exist_ok=True)
    entry = {
        "pw_hash": userdb.hash_password(pw),
        "role": role,
        "personal_path": str(personal),
        "is_demo": False,
    }
    if modules is not None:
        entry["modules"] = modules
    data = userdb.load_raw()
    data.setdefault("users", {})[name] = entry
    userdb.save_raw(data)


@pytest.mark.skipif(not (modul_da("tours") and modul_da("trails") and modul_da("stats")),
                    reason="ruft Plus-Router direkt auf")
def test_locked_module_returns_403(client, users_file):
    _upsert_user("stella", "stella12345", "viewer", ["stats"])
    r = client.post("/api/login", json={"username": "stella", "password": "stella12345"})
    assert r.status_code == 200

    # tours gesperrt → 403
    assert client.get("/api/tours").status_code == 403
    assert client.get("/api/trails").status_code == 403
    # stats freigeschaltet → nicht 403 (200/202 je nach Scan-Status)
    assert client.get("/api/stats?space=personal").status_code != 403


def test_health_lists_entitled_modules(client, users_file):
    _upsert_user("stella2", "stella12345", "viewer", ["stats", "search"])
    client.post("/api/login", json={"username": "stella2", "password": "stella12345"})
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["modules"] == ["search", "stats"]


def test_grandfather_grants_all_modules(client, login):
    # VIEWER aus conftest hat kein modules-Feld → alle Module frei
    login(VIEWER[0], VIEWER[1])
    assert client.get("/api/tours").status_code != 403
    assert client.get("/health").json()["modules"] == ALL_MODULES


def test_admin_has_all_modules_regardless(client, login):
    _upsert_user(ADMIN[0], ADMIN[1], "admin", ["stats"])  # eingeschränktes Feld …
    login()  # … aber Admin bekommt trotzdem alles
    assert client.get("/api/tours").status_code != 403
    assert client.get("/health").json()["modules"] == ALL_MODULES
