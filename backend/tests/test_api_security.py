"""Regressionstests zum Sicherheits-Audit vom 02.09.2026 (Paket 1):

H1 restore_backup-Traversal, H2 Shared-Space-Rollenprüfung in get_path,
H3 Eigentümer-Filter im Suchindex, M1 Dateinamens-Validierung (Stored XSS).
"""
from pathlib import Path
from urllib.parse import quote

import config

import pytest

from tests.conftest import ADMIN, VIEWER


# ── H1: restore_backup ────────────────────────────────────────────────────

def test_restore_rejects_absolute_and_traversal_paths(client, login, personal_album):
    login()
    url = f"/api/album/personal/{quote(personal_album)}/restore"
    for evil in ("/data/users.json", "../../album.json", "..%2F..%2Fx",
                 "album.json.backup_x", "irgendwas.txt"):
        r = client.post(f"{url}?backup_filename={quote(evil)}")
        assert r.status_code == 400, f"'{evil}' kam durch: {r.status_code}"


def test_restore_valid_pattern_roundtrip(client, login, personal_album):
    """Gültiges Backup-Muster funktioniert weiter: update erzeugt ein
    Backup, restore stellt es wieder her."""
    login()
    url = f"/api/album/personal/{quote(personal_album)}"
    data = client.get(url).json()
    r = client.post(f"{url}/update", json={
        "version": "1.4", "meta": data["meta"],
        "elements": [{"id": "0001", "type": "photo", "file": "p1.jpg"}],
    })
    backup = r.json()["backup"]
    r = client.post(f"{url}/restore?backup_filename={quote(backup)}")
    assert r.status_code == 200, r.text


# ── H2: Shared nur für admin/editor ───────────────────────────────────────

def test_viewer_may_read_shared_but_not_write(client, login):
    """Rechtemodell-Entscheidung 04.09.2026 (B1): `viewer` darf den
    Familienbestand LESEN — vorher lieferte das Rollen-Gate aus H2 auch
    dafür 403 und die Rolle sah gar nichts. Geschrieben wird nichts.

    404 statt 403 heißt: am Rollen-Gate vorbei, nur die Datei fehlt.
    """
    import config
    from tests.conftest import make_jpeg

    # Echtes Shared-Album anlegen (als Admin), damit Lesen etwas liefert
    folder = "2020 - Shared Lesetest"
    make_jpeg(config.PHOTO_PATH_SHARED / folder / "p1.jpg")
    login()
    r = client.post("/api/album/create", json={
        "space": "shared", "folder_name": folder, "thumbnail": "p1.jpg",
    })
    assert r.status_code == 200, r.text

    client.cookies.clear()
    login(VIEWER[0], VIEWER[1])

    # Lesen: geht jetzt
    r = client.get(f"/api/album/shared/{quote(folder)}")
    assert r.status_code == 200, r.text
    assert client.get(f"/api/thumbnail/shared/{quote(folder)}/p1.jpg?size=sm").status_code == 200
    # Shared taucht in der Albenliste auf
    spaces = {a["space"] for a in client.get("/api/albums").json()["albums"]}
    assert "shared" in spaces

    # Gate passiert, Datei fehlt → 404 (nicht 403)
    assert client.get("/api/album/shared/GibtsNicht").status_code == 404

    # Schreiben bleibt gesperrt
    assert client.post("/api/album/create", json={
        "space": "shared", "folder_name": "2020 - Verboten", "thumbnail": "p1.jpg",
    }).status_code == 403
    r = client.post(f"/api/album/shared/{quote(folder)}/update", json={
        "version": "1.4", "meta": {}, "elements": [],
    })
    assert r.status_code == 403
    assert client.delete(f"/api/album/shared/{quote(folder)}").status_code == 403


def test_demo_cannot_read_shared_space(client, login, users_file):
    """Demo bleibt vom Familienbestand ausgeschlossen — /demo-login ist
    öffentlich; das war der akute Teil von H2 und gilt unverändert."""
    from core import userdb

    data = userdb.load_raw()
    personal = config.USERS_JSON_PATH.parent / "personal-demoacc"
    personal.mkdir(parents=True, exist_ok=True)
    data["users"]["demoacc"] = {
        "pw_hash": userdb.hash_password("demo12345678"),
        "role": "viewer",
        "personal_path": str(personal),
        "is_demo": True,
    }
    userdb.save_raw(data)

    client.cookies.clear()
    assert client.post("/api/login", json={
        "username": "demoacc", "password": "demo12345678",
    }).status_code == 200

    assert client.get("/api/album/shared/Egal").status_code == 403
    assert client.get("/api/thumbnail/shared/A/b.jpg?size=sm").status_code == 403
    assert client.get("/api/photo/shared/A/b.jpg/download").status_code == 403
    spaces = {a["space"] for a in client.get("/api/albums").json()["albums"]}
    assert "shared" not in spaces


def test_admin_shared_space_still_passes_role_gate(client, login):
    """Admin kommt am Rollen-Gate vorbei (404 = Datei fehlt, nicht 403)."""
    login()
    assert client.get("/api/album/shared/GibtsNicht").status_code == 404


# ── H3: Suchindex-Eigentümer ─────────────────────────────────────────────

def test_search_personal_entries_are_owner_scoped(client, login, personal_album, monkeypatch, tmp_path):
    search_mod = pytest.importorskip("routers.search", reason="Suchmodul nicht mitgeliefert")

    monkeypatch.setattr(search_mod, "_INDEX_FILE", tmp_path / "idx.json")

    login()
    url = f"/api/album/personal/{quote(personal_album)}"
    meta = client.get(url).json()["meta"]
    secret = "Xylophonwetter1234"
    r = client.post(f"{url}/update", json={
        "version": "1.4", "meta": meta,
        "elements": [
            {"id": "0001", "type": "photo", "file": "p1.jpg"},
            {"id": "0002", "type": "text", "text": f"Geheim: {secret}"},
        ],
    })
    assert r.status_code == 200

    import config
    admin_personal = config.PHOTO_PATH_SHARED.parent / f"personal-{ADMIN[0]}"
    search_mod._build_index_sync(admin_personal, ADMIN[0])

    # Eigentümer findet seinen Text
    r = client.get(f"/api/search?q={secret}")
    assert r.status_code == 200
    hits = r.json()["results"]
    assert any(secret in h.get("text", "") for h in hits)

    # Anderer Nutzer findet ihn NICHT (Index enthält Admin-Personal)
    client.cookies.clear()
    login(VIEWER[0], VIEWER[1])
    r = client.get(f"/api/search?q={secret}")
    assert r.status_code == 200
    assert r.json()["results"] == []


# ── M1: Dateinamens-Validierung ──────────────────────────────────────────

@pytest.mark.parametrize("evil", [
    "<img src=x onerror=alert(1)>.jpg",
    'a".jpg',
    "a/b.jpg",
    "a\\b.jpg",
    "..",
])
def test_update_rejects_markup_and_paths_in_filenames(client, login, personal_album, evil):
    login()
    url = f"/api/album/personal/{quote(personal_album)}"
    meta = client.get(url).json()["meta"]
    r = client.post(f"{url}/update", json={
        "version": "1.4", "meta": meta,
        "elements": [{"id": "0001", "type": "photo", "file": evil}],
    })
    assert r.status_code == 400, f"'{evil}' kam durch"


def test_update_accepts_normal_filenames(client, login, personal_album):
    login()
    url = f"/api/album/personal/{quote(personal_album)}"
    meta = client.get(url).json()["meta"]
    r = client.post(f"{url}/update", json={
        "version": "1.4", "meta": meta,
        "elements": [{"id": "0001", "type": "photo",
                      "file": "2024-06-10 Strand & Meer (Tag 1).jpg"}],
    })
    assert r.status_code == 200, r.text

# ── Paket 2 (Audit 03.09.2026): M3/N1/N2/N3/N5 ───────────────────────────

def test_media_endpoints_reject_traversal(client, login, personal_album):
    """M3: resolve_media_path fängt Traversal in album_name/filename ab
    (URL-encodete Punkte umgehen die Router-Normalisierung von httpx)."""
    login()
    for url in (
        f"/api/photo/personal/{quote(personal_album)}/..%2F..%2Fusers.json/exif",
        "/api/document/personal/..%2F..%2Fdata/users.json",
        "/api/gpx-files/personal/..%2F..%2Fdata",
    ):
        r = client.get(url)
        assert r.status_code in (400, 404), f"'{url}' kam durch: {r.status_code}"
        assert "users.json" != r.text  # nie Dateiinhalt


def test_document_extension_whitelist(client, login, personal_album):
    """N3: /api/document liefert nur bekannte Dokumenttypen aus —
    album.json & Co. bekommen 415, auch wenn die Datei existiert."""
    import config
    login()
    album_dir = config.USERS_JSON_PATH.parent / f"personal-{ADMIN[0]}" / personal_album
    (album_dir / "notes.xyz").write_text("geheim")
    r = client.get(f"/api/document/personal/{quote(personal_album)}/notes.xyz")
    assert r.status_code == 415, r.text
    r = client.get(f"/api/document/personal/{quote(personal_album)}/album.json")
    assert r.status_code == 415, r.text


def test_demo_login_keeps_real_session(client, login, monkeypatch):
    """N1: ein untergeschobener /demo-login-Link zerstört keine echte
    Session — eingeloggte Nutzer werden unangetastet zu / geleitet."""
    import config
    monkeypatch.setattr(config, "DEMO_ENABLED", True)
    monkeypatch.setattr(config, "DEMO_PASSWORD", "egal")
    login()
    r = client.get("/demo-login", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"
    # Session lebt weiter und ist noch der Admin
    r = client.get("/api/whoami")
    assert r.status_code == 200
    assert r.json()["user"] == ADMIN[0]


# ── Demo-Zugang ist eine Eigenschaft der Installation (06.09.2026) ──────
#
# Bis dahin war /demo-login oeffentlich, demo_enabled stand auf true und
# die Zugangsdaten (mpd-demo/mpd-demo) im Code. In einer verkauften
# Installation waere das eine schlafende Tuer: Legt der Kunde ein Konto
# dieses Namens an, entscheidet allein sein Passwort — und seit dem
# 04.09. liest jedes echte Konto den Familienbestand.

def test_demo_route_gibt_es_ohne_schalter_nicht(client, login, monkeypatch):
    """Vorgabe ist AUS: keine Route, auch nicht fuer Angemeldete."""
    import config
    monkeypatch.setattr(config, "DEMO_ENABLED", False)
    login()
    assert client.get("/demo-login", follow_redirects=False).status_code == 404


def test_demo_route_ist_ohne_schalter_nicht_oeffentlich(client, monkeypatch):
    """Ohne Anmeldung faehrt die Middleware auf /login statt durchzulassen."""
    import config
    monkeypatch.setattr(config, "DEMO_ENABLED", False)
    r = client.get("/demo-login", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/login" in r.headers.get("location", "")


def test_schalter_ohne_passwort_bleibt_aus(client, login, monkeypatch):
    """MPD_DEMO=1 allein reicht nicht — ohne Passwort kein Zugang."""
    import config
    monkeypatch.setattr(config, "DEMO_ENABLED", True)
    monkeypatch.setattr(config, "DEMO_PASSWORD", "")
    login()
    assert client.get("/demo-login", follow_redirects=False).status_code == 404


def test_gleichnamiges_konto_wird_nicht_ausgesperrt(client, monkeypatch):
    """Ein Kundenkonto, das zufaellig wie das Demo-Konto heisst, darf sich
    anmelden — der Kill-Switch gilt nur, wo es einen Demo-Zugang gibt."""
    import config
    from core import settings as _settings
    monkeypatch.setattr(config, "DEMO_ENABLED", False)
    monkeypatch.setattr(_settings, "get", lambda k, d=None: False)
    r = client.post("/api/login", json={"username": config.DEMO_USER,
                                        "password": "falsch-aber-egal"})
    # 401 (Passwort falsch), NICHT 403 (Demo abgeschaltet)
    assert r.status_code == 401


def test_publish_bad_token_gets_rate_limited(client, monkeypatch):
    """N5: falsche X-Publish-Token werden wie Share-Token gedrosselt
    (5 Fehlversuche → 429)."""
    import config
    monkeypatch.setattr(config, "NOTIFY_PUBLISH_TOKEN", "test-publish-token")
    for _ in range(5):
        r = client.post("/api/notify/publish", json={"title": "x"},
                        headers={"X-Publish-Token": "falsch"})
        assert r.status_code == 401
    r = client.post("/api/notify/publish", json={"title": "x"},
                    headers={"X-Publish-Token": "falsch"})
    assert r.status_code == 429
    # und selbst der RICHTIGE Token wartet die Sperre ab
    r = client.post("/api/notify/publish", json={"title": "x"},
                    headers={"X-Publish-Token": "test-publish-token"})
    assert r.status_code == 429


# ── M3 nachgezogen 05.09.2026: Symlinks bei den SCHREIBENDEN Endpunkten ──

def _plant_symlink_escape(personal_album: str, target: Path) -> str:
    """Legt im Album einen Symlink an, der aus dem Space hinauszeigt.

    So etwas entsteht nicht ueber die API, sondern ueber den SMB-Zugriff auf
    die Fotoordner — den hat in einer Familieninstallation jeder.
    """
    import config
    album_dir = config.USERS_JSON_PATH.parent / f"personal-{ADMIN[0]}" / personal_album
    link = album_dir / "ausbruch.jpg"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(target)
    return link.name


def test_photo_edit_rejects_symlink_escape(client, login, personal_album, tmp_path):
    """Ein Foto, das ein Symlink nach draussen ist, darf nicht bearbeitet
    werden. Die Endpunkte in photo_edit.py LESEN nicht, sie SCHREIBEN —
    ohne diese Pruefung waere ein Symlink im Albumordner die Moeglichkeit,
    fremde Dateien zu ueberschreiben."""
    from urllib.parse import quote as _q
    # Ein ECHTES JPEG als Opfer — sonst beweist der Test zu wenig: ohne die
    # Haertung kam die Anfrage bis in den Bildbearbeiter und scheiterte nur
    # daran, dass die Zieldatei kein gueltiges Bild war.
    from tests.conftest import make_jpeg
    opfer = tmp_path / "fremd.jpg"
    make_jpeg(opfer, color=(10, 20, 30))
    vorher = opfer.read_bytes()
    name = _plant_symlink_escape(personal_album, opfer)

    login()
    alb = _q(personal_album)
    faelle = (
        ("post",   f"/api/photo/personal/{alb}/{name}/edit",           {"brightness": 10}),
        ("delete", f"/api/photo/personal/{alb}/{name}/edit",           None),
        ("post",   f"/api/photo/personal/{alb}/{name}/rename/preview", {"new_stem": "x"}),
        ("post",   f"/api/photo/personal/{alb}/{name}/rename",         {"new_stem": "x"}),
        ("post",   f"/api/photo/personal/{alb}/{name}/exif",           {}),
    )
    for method, url, body in faelle:
        fn = getattr(client, method)
        r = fn(url, json=body) if body is not None else fn(url)
        assert r.status_code == 400, f"{method.upper()} {url} kam durch: {r.status_code} {r.text[:200]}"

    assert opfer.read_bytes() == vorher, "fremde Datei wurde veraendert"


def test_photo_edit_still_works_on_a_normal_photo(client, login, personal_album):
    """Gegenprobe: die Haertung darf den Normalfall nicht kaputtmachen."""
    from urllib.parse import quote as _q
    login()
    r = client.post(f"/api/photo/personal/{_q(personal_album)}/p1.jpg/edit",
                    json={"brightness": 5})
    assert r.status_code == 200, r.text
