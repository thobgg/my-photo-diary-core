"""Modul-Lizenz (core/license.py + Entitlement-Schnitt in deps.py).

Signiert wird mit einem TEST-Schluesselpaar; der eingebettete
Produktiv-Pruefschluessel wird je Test per monkeypatch ersetzt. Voller
API-Roundtrip: Lizenzdatei schreiben → Login → /api/whoami zeigt die
geschnittenen Module und den Lizenzblock.
"""
import json

import pytest

import config
from core import license as license_mod
from core import rsa_min
from routers.deps import MODULE_KEYS

# Die Lizenzlogik gilt unabhaengig davon, welche Plus-Dateien liegen.
pytestmark = pytest.mark.usefixtures("alle_module_installiert")


# Kleines Testpaar (1024 Bit reicht fuers Padding und ist schnell)
_TEST_N, _TEST_D = rsa_min.generate_keypair(1024)


def _write_license(payload: dict, signature: int | None = None):
    sig = signature if signature is not None else rsa_min.sign(
        license_mod.canonical_payload_bytes(payload), _TEST_N, _TEST_D,
    )
    config.LICENSE_FILE_PATH.write_text(
        json.dumps({"payload": payload, "signature": format(sig, "x")}),
        encoding="utf-8",
    )


@pytest.fixture
def license_env(monkeypatch):
    """Testschluessel aktiv, Cache leer, Datei weg — je Test frisch."""
    monkeypatch.setattr(license_mod, "PUBLIC_KEY_N", _TEST_N)
    license_mod._cache = None
    config.LICENSE_FILE_PATH.unlink(missing_ok=True)
    yield monkeypatch
    license_mod._cache = None
    config.LICENSE_FILE_PATH.unlink(missing_ok=True)


def test_rsa_sign_verify_roundtrip():
    sig = rsa_min.sign(b"hallo", _TEST_N, _TEST_D)
    assert rsa_min.verify(b"hallo", sig, _TEST_N)
    assert not rsa_min.verify(b"hallo!", sig, _TEST_N)
    assert not rsa_min.verify(b"hallo", sig + 1, _TEST_N)


def test_ohne_enforce_bleibt_alles_wie_bisher(license_env, client, login):
    # Kein Lizenzfile, ENFORCE aus (Default) → Admin sieht alle Module
    login()
    data = client.get("/api/whoami").json()
    assert set(data["modules"]) == set(MODULE_KEYS)
    assert data["license"]["enforced"] is False
    assert data["license"]["valid"] is False


def test_enforce_ohne_lizenz_sperrt_alles(license_env, client, login):
    license_env.setattr(config, "LICENSE_ENFORCE", True)
    login()
    data = client.get("/api/whoami").json()
    assert data["modules"] == []
    assert data["license"]["enforced"] is True
    assert data["license"]["valid"] is False


def test_gueltige_lizenz_schneidet_auch_den_admin(license_env, client, login):
    license_env.setattr(config, "LICENSE_ENFORCE", True)
    _write_license({
        "customer": "Testkunde", "modules": ["tours", "search"],
        "issued": "2026-09-03", "expires": None,
    })
    login()
    data = client.get("/api/whoami").json()
    assert data["modules"] == ["search", "tours"]
    assert data["license"]["valid"] is True
    assert data["license"]["customer"] == "Testkunde"
    # und ein gesperrtes Modul liefert wirklich 403 (nur pruefbar, wenn der
    # Stats-Router mitgeliefert ist — sonst gibt es den Endpunkt gar nicht)
    from tests.conftest import modul_da
    if modul_da("stats"):
        assert client.get("/api/stats?space=both").status_code == 403


def test_manipulierte_signatur_zaehlt_als_keine_lizenz(license_env, client, login):
    license_env.setattr(config, "LICENSE_ENFORCE", True)
    payload = {"customer": "X", "modules": sorted(MODULE_KEYS),
               "issued": "2026-09-03", "expires": None}
    good = rsa_min.sign(license_mod.canonical_payload_bytes(payload), _TEST_N, _TEST_D)
    _write_license(payload, signature=good + 1)
    login()
    data = client.get("/api/whoami").json()
    assert data["modules"] == []
    assert data["license"]["valid"] is False


def test_manipulierter_payload_zaehlt_als_keine_lizenz(license_env, client, login):
    license_env.setattr(config, "LICENSE_ENFORCE", True)
    payload = {"customer": "X", "modules": ["search"],
               "issued": "2026-09-03", "expires": None}
    sig = rsa_min.sign(license_mod.canonical_payload_bytes(payload), _TEST_N, _TEST_D)
    tampered = dict(payload, modules=sorted(MODULE_KEYS))  # Module aufgebohrt
    _write_license(tampered, signature=sig)
    login()
    assert client.get("/api/whoami").json()["modules"] == []


def test_abgelaufene_lizenz(license_env, client, login):
    license_env.setattr(config, "LICENSE_ENFORCE", True)
    _write_license({
        "customer": "X", "modules": sorted(MODULE_KEYS),
        "issued": "2020-01-01", "expires": "2021-01-01",
    })
    login()
    data = client.get("/api/whoami").json()
    assert data["modules"] == []
    assert data["license"]["valid"] is False
    assert data["license"]["error"] == "abgelaufen"


def test_lizenz_schneidet_users_json_einschraenkung(license_env, client, login):
    """Nutzer-Einschraenkung aus users.json bleibt zusaetzlich wirksam:
    effektiv = users.json ∩ Lizenz."""
    from routers.deps import user_modules

    license_env.setattr(config, "LICENSE_ENFORCE", True)
    _write_license({
        "customer": "X", "modules": ["tours", "search"],
        "issued": "2026-09-03", "expires": None,
    })

    class FakeRec:
        modules = ["search", "stats"]

    class FakeSession:  # user_modules liest nur .role und .user
        user = "egal"
        role = "editor"

    license_env.setattr("core.userdb.lookup", lambda user: FakeRec())
    assert user_modules(FakeSession()) == {"search"}


def _license_text(payload: dict) -> str:
    sig = rsa_min.sign(license_mod.canonical_payload_bytes(payload), _TEST_N, _TEST_D)
    return json.dumps({"payload": payload, "signature": format(sig, "x")})


def test_lizenz_einspielen_ueber_einstellungen(license_env, client, login):
    """09.09.2026: PUT-Weg fuer Kaeufer ohne Shell. Gueltig → Datei liegt,
    Anzeige stimmt; gefaelscht → 400 und alte Datei bleibt; Entfernen → weg."""
    login()
    txt = _license_text({"customer": "Familie Test", "modules": ["stats", "search"],
                         "issued": "2026-09-09", "expires": None})
    r = client.post("/api/settings/license", json={"text": txt})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["valid"] is True and d["customer"] == "Familie Test" and d["modules"] == ["search", "stats"]
    assert config.LICENSE_FILE_PATH.is_file()
    assert oct(config.LICENSE_FILE_PATH.stat().st_mode & 0o777) == "0o600"
    assert client.get("/api/settings").json()["license"]["customer"] == "Familie Test"

    # Gefaelscht: Payload geaendert, Signatur alt → 400, alte Datei unveraendert
    doc = json.loads(txt); doc["payload"]["modules"] = ["stats", "search", "trails"]
    r = client.post("/api/settings/license", json={"text": json.dumps(doc)})
    assert r.status_code == 400 and "Signatur" in r.json()["detail"]
    assert client.get("/api/settings").json()["license"]["modules"] == ["search", "stats"]

    # Kein JSON, leer
    assert client.post("/api/settings/license", json={"text": "kein json"}).status_code == 400
    assert client.post("/api/settings/license", json={"text": "  "}).status_code == 400

    # Entfernen
    r = client.delete("/api/settings/license")
    assert r.status_code == 200 and r.json()["valid"] is False
    assert not config.LICENSE_FILE_PATH.exists()


def test_lizenz_nur_admin(license_env, client, login):
    from tests.conftest import VIEWER
    login(user=VIEWER[0], pw=VIEWER[1])
    assert client.post("/api/settings/license", json={"text": "{}"}).status_code == 403
    assert client.delete("/api/settings/license").status_code == 403
    assert "license" not in client.get("/api/settings").json()


# ── Karenz und Warnung (24.09.2026) ──────────────────────────────────────

def _lizenz_mit_ablauf(tage_ab_heute: int, module=("trails",)):
    """Lizenz, deren Ablaufdatum relativ zu heute liegt."""
    from datetime import date, timedelta
    _write_license({
        "customer": "Testkunde", "modules": list(module),
        "issued": "2026-09-03",
        "expires": (date.today() + timedelta(days=tage_ab_heute)).isoformat(),
    })


def test_karenz_haelt_die_module_offen(license_env):
    """Einen Tag nach Ablauf sind die Module noch da — sonst schaltet eine
    verspaetete Verlaengerung ueber Nacht alles ab."""
    license_env.setattr(config, "LICENSE_ENFORCE", True)
    license_env.setattr(config, "LICENSE_GRACE_DAYS", 30)
    _lizenz_mit_ablauf(-1)
    assert license_mod.licensed_modules() == {"trails"}
    info = license_mod.license_info()
    assert info["in_grace"] is True and info["days_left"] == -1
    assert info["error"] == "Karenz"


def test_nach_der_karenz_ist_schluss(license_env):
    license_env.setattr(config, "LICENSE_ENFORCE", True)
    license_env.setattr(config, "LICENSE_GRACE_DAYS", 30)
    _lizenz_mit_ablauf(-31)
    assert license_mod.licensed_modules() == set()
    info = license_mod.license_info()
    assert info["valid"] is False and info["error"] == "abgelaufen"


def test_karenz_abschaltbar(license_env):
    """MPD_LICENSE_GRACE_DAYS=0 ist das harte Verhalten von vorher."""
    license_env.setattr(config, "LICENSE_ENFORCE", True)
    license_env.setattr(config, "LICENSE_GRACE_DAYS", 0)
    _lizenz_mit_ablauf(-1)
    assert license_mod.licensed_modules() == set()


def test_unbefristete_lizenz_kennt_keinen_ablauf(license_env):
    license_env.setattr(config, "LICENSE_ENFORCE", True)
    _write_license({"customer": "X", "modules": ["trails"],
                    "issued": "2026-09-03", "expires": None})
    info = license_mod.license_info()
    assert info["days_left"] is None and info["in_grace"] is False
    assert license_mod.licensed_modules() == {"trails"}


@pytest.mark.parametrize("tage,erwartet", [
    (14, True), (7, True), (3, True), (1, True), (0, True),   # Vorwarnung
    (10, False), (2, False),                                   # dazwischen: still
    (-1, True), (-15, True), (-30, True),                      # Karenz
    (-2, False),                                               # dazwischen: still
])
def test_warnung_nur_an_den_schwellen(license_env, users_file, tage, erwartet):
    """Taeglicher Lauf, aber Meldung nur an festen Schwellen — wer jeden
    Tag dieselbe Warnung bekaeme, liest sie bald nicht mehr."""
    from core import notify_jobs
    license_env.setattr(config, "LICENSE_ENFORCE", True)
    license_env.setattr(config, "LICENSE_GRACE_DAYS", 30)
    _lizenz_mit_ablauf(tage)
    assert notify_jobs.run_license_check() is erwartet


def test_keine_warnung_ohne_scharfe_pruefung(license_env, users_file):
    from core import notify_jobs
    license_env.setattr(config, "LICENSE_ENFORCE", False)
    _lizenz_mit_ablauf(1)
    assert notify_jobs.run_license_check() is False


def test_standort_ingest_ueberlebt_die_abgelaufene_lizenz(license_env):
    """Ansehen ist Kaufsache, Abliefern nicht: Das Handy darf Punkte auch
    dann loswerden, wenn die Lizenz laengst abgelaufen ist — sonst gehen
    Daten verloren, die niemand zurueckholen kann."""
    from routers.deps import account_modules, user_modules
    from core.session import Session
    license_env.setattr(config, "LICENSE_ENFORCE", True)
    license_env.setattr(config, "LICENSE_GRACE_DAYS", 0)
    _lizenz_mit_ablauf(-5)
    s = Session(user="tester", role="admin", personal_path=None, is_demo=False)
    assert "trails" in account_modules(s)
    assert "trails" not in user_modules(s)
