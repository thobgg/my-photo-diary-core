"""PUT /api/album/{space}/{album}/file/{filename} — Upload für die
Kamera-Funktion der nativen App (additiv 09/2026)."""
import io
from urllib.parse import quote

from PIL import Image

from tests.conftest import VIEWER


def _jpeg_bytes(color=(120, 60, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buf, "JPEG")
    return buf.getvalue()


def _put(client, album, filename, data=None, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"/api/album/personal/{quote(album)}/file/{quote(filename)}"
    return client.put(url + (f"?{q}" if q else ""), content=data or _jpeg_bytes())


def test_upload_requires_auth(client, personal_album):
    client.cookies.clear()  # personal_album-Fixture hat eingeloggt
    assert _put(client, personal_album, "2026-09-02_10-00-00.jpg").status_code == 401


def test_upload_into_existing_album_folder(client, login, personal_album):
    login()
    r = _put(client, personal_album, "2026-09-02_10-00-00.jpg")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["size"] > 0 and body["type"] == "photo"
    assert body["element"] is None
    assert "?size=sm" in body["thumbnails"]["sm"]  # + &v=<cachekey>

    # Datei liegt real da und taucht in der Timeline als unkuratiert auf
    r = client.get("/api/timeline?limit=500")
    item = next(i for i in r.json()["items"]
                if i["album"] == personal_album and i["file"] == "2026-09-02_10-00-00.jpg")
    assert item["referenced"] is False
    assert item["date_source"] == "filename"

    # Duplikat → 409
    assert _put(client, personal_album, "2026-09-02_10-00-00.jpg").status_code == 409


def test_upload_add_to_album(client, login, personal_album):
    login()
    r = _put(client, personal_album, "2026-09-02_11-00-00.jpg", add_to_album="true")
    assert r.status_code == 200, r.text
    elem = r.json()["element"]
    assert elem["type"] == "photo" and elem["file"] == "2026-09-02_11-00-00.jpg"

    album = client.get(f"/api/album/personal/{quote(personal_album)}").json()
    files = [e.get("file") for e in album["elements"]]
    assert "2026-09-02_11-00-00.jpg" in files

    # In der Timeline jetzt referenziert
    r = client.get("/api/timeline?limit=500")
    item = next(i for i in r.json()["items"]
                if i["album"] == personal_album and i["file"] == "2026-09-02_11-00-00.jpg")
    assert item["referenced"] is True


def test_upload_creates_inbox_folder(client, login, personal_album):
    """Eingangsordner-Fall: Ordner ohne album.json wird angelegt;
    add_to_album dorthin ist ein 400."""
    login()
    r = _put(client, "2026 - Kamera", "2026-09-02_12-00-00.jpg")
    assert r.status_code == 200, r.text
    assert _put(client, "2026 - Kamera", "2026-09-02_12-30-00.jpg",
                add_to_album="true").status_code == 400


def test_upload_rejects_bad_names_and_types(client, login, personal_album):
    login()
    assert _put(client, personal_album, "notiz.txt").status_code == 415
    assert _put(client, personal_album, ".versteckt.jpg").status_code == 400
    assert _put(client, personal_album, 'boese".jpg').status_code == 400
    assert _put(client, "a..b", "a.jpg").status_code == 400
    r = client.put(f"/api/album/personal/{quote(personal_album)}/file/leer.jpg", content=b"")
    assert r.status_code == 400


def test_upload_viewer_darf_in_den_eigenen_bereich(client, login):
    """Seit 06.09.2026 (Betrachter-Reparatur): Der eigene Bereich gehoert
    dem Konto — auch einem `viewer`. Vorher war er dort nur Zuschauer und
    sein persoenlicher Bereich damit unbenutzbar."""
    login(VIEWER[0], VIEWER[1])
    assert _put(client, "Egal", "2026-09-02_13-00-00.jpg").status_code == 200


def test_upload_viewer_nicht_in_den_familienbestand(client, login):
    """Im Gemeinsamen bleibt er Zuschauer."""
    from urllib.parse import quote
    login(VIEWER[0], VIEWER[1])
    r = client.put(f"/api/album/shared/{quote('Egal')}/file/"
                   f"{quote('2026-09-02_13-30-00.jpg')}", content=_jpeg_bytes())
    assert r.status_code == 403
