"""POST /api/album/{space}/{album}/import-file — Einsortieren aus dem
Eingangsordner (Kuratier-Fluss, additiv 09/2026)."""
import io
from urllib.parse import quote

from PIL import Image

from tests.conftest import VIEWER


def _jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 200, 90)).save(buf, "JPEG")
    return buf.getvalue()


def _upload_inbox(client, filename):
    r = client.put(f"/api/album/personal/{quote('2026 - Kamera')}/file/{quote(filename)}",
                   content=_jpeg_bytes())
    assert r.status_code == 200, r.text


def _import(client, target_space, target_album, filename, from_album="2026 - Kamera",
            from_space="personal"):
    return client.post(
        f"/api/album/{target_space}/{quote(target_album)}/import-file",
        json={"from_space": from_space, "from_album": from_album, "filename": filename},
    )


def test_import_from_inbox_to_album(client, login, personal_album):
    login()
    _upload_inbox(client, "2026-09-02_15-00-00.jpg")

    r = _import(client, "personal", personal_album, "2026-09-02_15-00-00.jpg")
    assert r.status_code == 200, r.text
    assert r.json()["element"]["file"] == "2026-09-02_15-00-00.jpg"

    # Im Zielalbum referenziert, im Eingang verschwunden
    album = client.get(f"/api/album/personal/{quote(personal_album)}").json()
    assert "2026-09-02_15-00-00.jpg" in [e.get("file") for e in album["elements"]]
    items = client.get("/api/timeline?limit=500").json()["items"]
    mine = [i for i in items if i["file"] == "2026-09-02_15-00-00.jpg"]
    assert len(mine) == 1
    assert mine[0]["album"] == personal_album
    assert mine[0]["referenced"] is True

    # Quelle leer → erneuter Import 404
    assert _import(client, "personal", personal_album,
                   "2026-09-02_15-00-00.jpg").status_code == 404


def test_import_cross_space_to_shared_album(client, login):
    """Eingang (personal) → Familienalbum (shared): der Kernfall des
    Kuratier-Modells; Verschieben über Space-Grenzen."""
    login()
    r = client.post("/api/album/create-empty",
                    json={"space": "shared", "folder_name": "2026 - Familie Test"})
    assert r.status_code == 200, r.text
    _upload_inbox(client, "2026-09-02_16-00-00.jpg")

    r = _import(client, "shared", "2026 - Familie Test", "2026-09-02_16-00-00.jpg")
    assert r.status_code == 200, r.text
    album = client.get(f"/api/album/shared/{quote('2026 - Familie Test')}").json()
    assert "2026-09-02_16-00-00.jpg" in [e.get("file") for e in album["elements"]]


def test_import_refuses_referenced_source(client, login, personal_album):
    """Wohnsitz-Invariante: eine im Quellalbum referenzierte Datei wird
    nicht stillschweigend herausgezogen."""
    login()
    r = client.post("/api/album/create-empty",
                    json={"space": "personal", "folder_name": "2026 - Ziel Test"})
    assert r.status_code == 200, r.text
    r = _import(client, "personal", "2026 - Ziel Test", "p1.jpg",
                from_album=personal_album)
    assert r.status_code == 409
    assert "referenziert" in r.json()["detail"]


def test_import_validation(client, login, personal_album):
    login()
    # Ziel ohne album.json → 400
    _upload_inbox(client, "2026-09-02_17-00-00.jpg")
    r = _import(client, "personal", "2026 - Kamera", "2026-09-02_17-00-00.jpg")
    assert r.status_code == 400
    # kaputter Dateiname → 400, falscher Typ → 415
    assert _import(client, "personal", personal_album, "../x.jpg").status_code == 400
    assert _import(client, "personal", personal_album, "notiz.txt").status_code == 415


def test_import_viewer_darf_im_eigenen_bereich(client, login):
    """Betrachter-Reparatur 06.09.2026 — einsortieren im eigenen Bereich
    ist erlaubt; dass die Quelldatei hier fehlt, ergibt 404, nicht 403."""
    login(VIEWER[0], VIEWER[1])
    assert _import(client, "personal", "Egal", "a.jpg").status_code != 403


def test_import_viewer_nicht_im_familienbestand(client, login):
    login(VIEWER[0], VIEWER[1])
    assert _import(client, "shared", "Egal", "a.jpg").status_code == 403


def test_import_kollision_dublette_und_umbenennen(client, login, personal_album):
    """08.09.2026: Kollision im Ziel ist kein 409 mehr. Bytegleich =
    Dublette (Quelle in den Papierkorb, Element bleibt/entsteht), anderer
    Inhalt = neuer Name mit _1."""
    import config
    from tests.conftest import ADMIN, make_jpeg
    login()
    base = config.PHOTO_PATH_SHARED.parent / f"personal-{ADMIN[0]}"
    dst = base / personal_album
    inbox = base / "2039"

    # Dublette: p1.jpg liegt (identisch) schon im Album und ist referenziert
    make_jpeg(inbox / "p1.jpg")
    r = _import(client, "personal", personal_album, "p1.jpg", from_album="2039")
    assert r.status_code == 200, r.text
    assert r.json()["duplicate"] is True and r.json()["filename"] == "p1.jpg"
    assert not (inbox / "p1.jpg").exists()
    data = client.get(f"/api/album/personal/{quote(personal_album)}").json()
    assert [e["file"] for e in data["elements"]].count("p1.jpg") == 1   # nicht doppelt angehaengt

    # Anderer Inhalt, gleicher Name → p2_1.jpg
    make_jpeg(inbox / "p2.jpg", color=(1, 2, 3))
    r = _import(client, "personal", personal_album, "p2.jpg", from_album="2039")
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "p2_1.jpg" and r.json()["renamed_from"] == "p2.jpg"
    assert (dst / "p2_1.jpg").exists() and (dst / "p2.jpg").exists()
    data = client.get(f"/api/album/personal/{quote(personal_album)}").json()
    assert data["elements"][-1]["file"] == "p2_1.jpg"


def test_import_lose_datei_im_eigenen_ordner_wird_adoptiert(client, login, personal_album):
    """Vorfall 08.09.2026: Quelle == Ziel (lose Datei im Albumordner, Ziel
    dieses Album). Darf NICHT als Dublette in den Papierkorb — nur das
    Element anhaengen, Datei bleibt liegen."""
    import config
    from tests.conftest import ADMIN, make_jpeg
    login()
    dst = config.PHOTO_PATH_SHARED.parent / f"personal-{ADMIN[0]}" / personal_album
    make_jpeg(dst / "lose.jpg", color=(9, 9, 9))
    r = _import(client, "personal", personal_album, "lose.jpg", from_album=personal_album)
    assert r.status_code == 200, r.text
    assert r.json().get("adopted") is True and r.json()["filename"] == "lose.jpg"
    assert (dst / "lose.jpg").exists()
    data = client.get(f"/api/album/personal/{quote(personal_album)}").json()
    assert [e["file"] for e in data["elements"]].count("lose.jpg") == 1
    assert data["unassigned_count"] == 0
    # Zweiter Aufruf: idempotent
    r = _import(client, "personal", personal_album, "lose.jpg", from_album=personal_album)
    assert r.status_code in (200, 409)   # 409 = Quelle referenziert (dieselbe Datei) — beides ohne Verlust
    assert (dst / "lose.jpg").exists()
