"""`rights` in /api/whoami — was die Oberfläche anbieten darf.

Der Sinn des Feldes ist, dass Web und App die Regeln nicht nachbauen.
Diese Tests halten deshalb fest, dass die Liste die Regeln aus deps.py
und share.py wirklich trifft — inklusive der zwei Stellen, an denen
Teilen und Beisteuern NICHT derselben Stufe folgen.
"""
import pytest

from routers.deps import user_rights


class _Session:
    def __init__(self, role, is_demo=False):
        self.user = "wer"
        self.role = role
        self.is_demo = is_demo
        self.personal_path = None


@pytest.fixture(autouse=True)
def _sharing_an(monkeypatch):
    from core import settings as _settings
    monkeypatch.setattr(_settings, "get", lambda k, d=None: True)
    yield


def test_admin_darf_ueberall_alles():
    r = user_rights(_Session("admin"))
    for space in ("shared", "personal"):
        assert set(r[space]) == {"see", "contribute", "curate", "edit",
                                 "contrib_link", "share"}
    from tests.conftest import modul_da
    erwartet = {"manage_users", "settings"} | ({"foreign_trails"} if modul_da("trails") else set())
    assert set(r["instance"]) == erwartet


def test_editor_traegt_bei_kuratiert_aber_nicht_im_familienbestand():
    r = user_rights(_Session("editor"))
    # edit_unreferenced seit 06.09.: Beitragende duerfen bearbeiten, was sie
    # auch loeschen duerften — Unreferenziertes im Durchlauf.
    assert set(r["shared"]) == {"see", "contribute", "edit_unreferenced", "share"}
    assert set(r["personal"]) == {"see", "contribute", "curate", "edit",
                                  "contrib_link", "share"}
    assert r["instance"] == []


def test_editor_teilt_obwohl_er_nicht_kuratiert():
    """Die Stelle, an der zwei Stufen nicht reichen: teilen ja,
    kuratieren nein, Beisteuern-Link nein."""
    shared = user_rights(_Session("editor"))["shared"]
    assert "share" in shared
    assert "curate" not in shared
    assert "contrib_link" not in shared


def test_viewer_sieht_zu_und_fuehrt_sein_eigenes_tagebuch():
    """Betrachter-Reparatur vom 06.09.2026: Im Familienbestand nur
    zusehen, im EIGENEN Bereich alles. Vorher war der eigene Bereich für
    ihn tot — genau die Rolle „darf mitschauen, führt aber sein eigenes
    Tagebuch" fehlte damit."""
    r = user_rights(_Session("viewer"))
    assert set(r["shared"]) == {"see"}          # kein Teilen im Gemeinsamen
    assert set(r["personal"]) == {"see", "contribute", "curate", "edit",
                                  "contrib_link", "share"}


def test_teilen_laesst_sich_je_konto_abschalten(monkeypatch):
    """Kinderkonto: eigenes Tagebuch ja, veröffentlichen nein."""
    from core import userdb
    class _Rec:
        may_share = False
    monkeypatch.setattr(userdb, "lookup", lambda u: _Rec())
    r = user_rights(_Session("admin"))
    for space in ("shared", "personal"):
        assert "share" not in r[space]
        assert "curate" in r[space]     # alles andere bleibt


def test_demo_sieht_den_familienbestand_gar_nicht():
    r = user_rights(_Session("editor", is_demo=True))
    assert r["shared"] == []            # nicht einmal "see"
    assert set(r["personal"]) == {"see"}
    assert r["instance"] == []


def test_kill_switch_nimmt_teilen_und_beisteuern(monkeypatch):
    from core import settings as _settings
    monkeypatch.setattr(_settings, "get",
                        lambda k, d=None: False if k == "share_enabled" else True)
    r = user_rights(_Session("admin"))
    for space in ("shared", "personal"):
        assert "share" not in r[space]
        assert "contrib_link" not in r[space]
        assert "curate" in r[space]     # der Rest bleibt


def test_whoami_liefert_das_feld(client, login):
    login()
    r = client.get("/api/whoami")
    assert r.status_code == 200
    rights = r.json()["rights"]
    assert set(rights) == {"shared", "personal", "instance"}
    assert "curate" in rights["shared"]      # Testkonto ist Admin


# ── Bearbeiten spiegelt die Löschregel (06.09.2026) ─────────────────────

def test_bearbeiten_kurator_darf_alles(tmp_path):
    from routers.deps import may_edit_photo
    import json
    (tmp_path / "album.json").write_text(json.dumps({
        "meta": {"thumbnail": "cover.jpg"},
        "elements": [{"id": "0010", "type": "photo", "file": "drin.jpg"}],
    }), encoding="utf-8")
    admin = _Session("admin")
    assert may_edit_photo(admin, "shared", tmp_path, "drin.jpg")
    assert may_edit_photo(admin, "shared", tmp_path, "cover.jpg")


def test_bearbeiten_beitragender_nur_unreferenziertes(tmp_path):
    """Spiegelt die Löschregel: Was er löschen dürfte, darf er auch
    geraderücken — sobald es einsortiert ist, ist es Kurator-Sache."""
    from routers.deps import may_edit_photo
    import json
    (tmp_path / "album.json").write_text(json.dumps({
        "meta": {"thumbnail": "cover.jpg"},
        "elements": [{"id": "0010", "type": "photo", "file": "drin.jpg"}],
    }), encoding="utf-8")
    editor = _Session("editor")
    assert not may_edit_photo(editor, "shared", tmp_path, "drin.jpg")
    assert not may_edit_photo(editor, "shared", tmp_path, "cover.jpg")
    assert may_edit_photo(editor, "shared", tmp_path, "ausschuss.jpg")


def test_bearbeiten_im_durchlauf_ohne_album_json(tmp_path):
    """Durchlauf-Ordner: nichts referenziert, also darf der Beitragende."""
    from routers.deps import may_edit_photo
    assert may_edit_photo(_Session("editor"), "shared", tmp_path, "frisch.jpg")


def test_bearbeiten_viewer_im_familienbestand_nie(tmp_path):
    from routers.deps import may_edit_photo
    assert not may_edit_photo(_Session("viewer"), "shared", tmp_path, "egal.jpg")
    assert not may_edit_photo(_Session("editor", is_demo=True), "shared",
                              tmp_path, "egal.jpg")
