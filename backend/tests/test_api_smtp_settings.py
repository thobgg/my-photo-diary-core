"""SMTP gehoert in die Einstellungen, nicht nur in mpd.env.

Ohne SMTP gibt es kein Teilen per Mail und keine Einladungen. Bisher stand
die Konfiguration ausschliesslich in mpd.env — wer keine Shell im Container
hat (SPK auf fremder NAS), konnte Mailversand also gar nicht einrichten.

Regel: **die Einstellung gewinnt, mpd.env ist der Rueckfall.** Leer bzw.
Port 0 heisst "nicht gesetzt".
"""
import pytest

from core import mailer, settings
from tests.conftest import VIEWER


@pytest.fixture(autouse=True)
def _clean_smtp():
    """Nach jedem Test die SMTP-Schluessel wieder leeren."""
    yield
    settings.set_many({
        "smtp_host": "", "smtp_port": 0, "smtp_user": "",
        "smtp_pass": "", "smtp_from_addr": "", "smtp_from_name": "",
    })


# --- Vorrang -------------------------------------------------------------

def test_settings_win_over_env(monkeypatch):
    import config
    monkeypatch.setattr(config, "SMTP_HOST", "env.example.org", raising=False)
    assert mailer._val("smtp_host") == "env.example.org", "leer → Rueckfall auf env"

    settings.set_many({"smtp_host": "settings.example.org"})
    assert mailer._val("smtp_host") == "settings.example.org"


def test_empty_setting_falls_back_to_env(monkeypatch):
    """Bestandsinstallationen, die nur mpd.env nutzen, aendern ihr Verhalten nicht."""
    import config
    monkeypatch.setattr(config, "SMTP_USER", "env-user", raising=False)
    settings.set_many({"smtp_user": ""})
    assert mailer._val("smtp_user") == "env-user"


def test_is_configured_uses_settings(monkeypatch):
    import config
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_FROM_ADDR"):
        monkeypatch.setattr(config, k, "", raising=False)
    assert mailer.is_configured() is False

    settings.set_many({
        "smtp_host": "h", "smtp_user": "u",
        "smtp_pass": "p", "smtp_from_addr": "a@b.c",
    })
    assert mailer.is_configured() is True
    assert mailer.config_source() == "settings"


# --- Was nach aussen geht ------------------------------------------------

def test_smtp_block_hidden_from_non_admins(client, login):
    """smtp_user ist eine Mailadresse — die geht keinen Viewer etwas an."""
    settings.set_many({"smtp_host": "smtp.example.org", "smtp_user": "geheim@example.org"})
    login(VIEWER[0], VIEWER[1])
    d = client.get("/api/settings").json()
    for k in ("smtp_host", "smtp_user", "smtp_pass", "smtp_pass_hint",
              "smtp_from_addr", "smtp_port", "smtp_from_name"):
        assert k not in d, f"{k} darf nicht an Nicht-Admins gehen"


def test_admin_sees_block_but_password_only_masked(client, login):
    settings.set_many({
        "smtp_host": "smtp.example.org", "smtp_user": "u@example.org",
        "smtp_pass": "superlanges-geheimnis-123", "smtp_from_addr": "a@b.c",
    })
    login()
    d = client.get("/api/settings").json()
    assert d["smtp_host"] == "smtp.example.org"
    assert d["smtp_user"] == "u@example.org"
    assert "smtp_pass" not in d, "das Passwort selbst darf nie hinausgehen"
    assert d["smtp_pass_hint"] and "geheimnis" not in d["smtp_pass_hint"]
    assert d["smtp_source"] == "settings"
    assert d["smtp_configured"] is True


# --- Die Falle: Speichern darf nicht loeschen ----------------------------

def test_empty_password_means_unchanged_not_deleted(client, login):
    """Das Passwort geht nur maskiert heraus. Kaeme es leer zurueck und
    wuerde gespeichert, schaltete ein harmloses "Speichern" den
    Mailversand ab."""
    login()
    settings.set_many({"smtp_pass": "behalte-mich"})

    r = client.post("/api/settings", json={"smtp_host": "neu.example.org", "smtp_pass": "   "})
    assert r.status_code == 200, r.text

    assert settings.get("smtp_pass") == "behalte-mich"
    assert settings.get("smtp_host") == "neu.example.org"


def test_password_can_still_be_changed(client, login):
    login()
    settings.set_many({"smtp_pass": "alt"})
    r = client.post("/api/settings", json={"smtp_pass": "neu"})
    assert r.status_code == 200
    assert settings.get("smtp_pass") == "neu"


def test_non_admin_cannot_write_smtp(client, login):
    login(VIEWER[0], VIEWER[1])
    r = client.post("/api/settings", json={"smtp_host": "boese.example.org"})
    assert r.status_code == 403
