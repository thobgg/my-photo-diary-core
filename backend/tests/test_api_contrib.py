"""Teilen v2 (03.09.2026): Ablaufdatum für Share-Links + Beisteuern-Links
(Gast-Upload in Sammelordner, nie in Alben)."""
import io
import time
from urllib.parse import quote

from PIL import Image
from fastapi.testclient import TestClient

import config
from core import shares
from tests.conftest import ADMIN


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (250, 180, 40)).save(buf, "JPEG")
    return buf.getvalue()


# ── Ablaufdatum für Share-Links ───────────────────────────────────────────

def test_share_expiry(client, login, personal_album, _app):
    login()
    r = client.post("/api/share/create",
                    json={"space": "personal", "album_name": personal_album,
                          "expires_days": 7})
    assert r.status_code == 200
    token = r.json()["token"]

    lst = client.get(f"/api/share/list?space=personal&album_name={quote(personal_album)}").json()
    assert any(e["expires_at"] for e in lst["shares"])

    anon = TestClient(_app)
    assert anon.get(f"/share/{token}/api/album").status_code == 200

    # Ablauf simulieren → Link tot und Zeile entsorgt
    conn = shares._ensure_conn()
    with shares._lock:
        conn.execute("UPDATE shares SET expires_at = ? WHERE token_hash = ?",
                     (time.time() - 10, shares._hash(token)))
    assert anon.get(f"/share/{token}/api/album").status_code == 404
    assert shares.resolve(token) is None

    # Ohne expires_days: unbefristet wie bisher
    r = client.post("/api/share/create",
                    json={"space": "personal", "album_name": personal_album})
    assert r.status_code == 200
    assert anon.get(f"/share/{r.json()['token']}/api/album").status_code == 200


# ── Beisteuern-Links ──────────────────────────────────────────────────────

def _create_contrib(client, folder="2026 - Feier Test", **kw):
    body = {"space": "personal", "folder_name": folder,
            "expires_days": 14, "max_files": 3}
    body.update(kw)
    return client.post("/api/contrib/create", json=body)


def test_contrib_never_targets_albums(client, login, personal_album):
    login()
    r = _create_contrib(client, folder=personal_album)
    assert r.status_code == 400
    assert "Alben" in r.json()["detail"]


def test_contrib_guest_flow(client, login, _app):
    login()
    r = _create_contrib(client)
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    folder = config.PHOTO_PATH_SHARED.parent / f"personal-{ADMIN[0]}" / "2026 - Feier Test"
    assert folder.is_dir()  # beim Anlegen erzeugt

    anon = TestClient(_app)  # Gast: keine Session
    # Gastseite + Status öffentlich
    page = anon.get(f"/contrib/{token}")
    assert page.status_code == 200 and "beisteuern" in page.text
    assert anon.get(f"/contrib/{token}/status").json()["uploaded"] == 0

    # Upload: Servername beitrag-<gast>-<rand>, Zähler steigt
    r = anon.post(f"/contrib/{token}/upload?filename=IMG_1234.jpg&guest=Tante%20Erna",
                  content=_jpeg())
    assert r.status_code == 200, r.text
    stored = r.json()["stored_as"]
    assert stored.startswith("beitrag-tanteerna-") and stored.endswith(".jpg")
    assert (folder / stored).is_file()
    assert anon.get(f"/contrib/{token}/status").json()["uploaded"] == 1

    # Validierung: falscher Typ, leer
    assert anon.post(f"/contrib/{token}/upload?filename=notiz.txt",
                     content=b"x").status_code == 415
    assert anon.post(f"/contrib/{token}/upload?filename=a.jpg",
                     content=b"").status_code == 400

    # Limit: max_files=3 (der leere Upload oben hat den Zähler verbraucht —
    # bewusst: erst zählen, dann annehmen)
    anon.post(f"/contrib/{token}/upload?filename=b.jpg", content=_jpeg())
    r = anon.post(f"/contrib/{token}/upload?filename=c.jpg", content=_jpeg())
    assert r.status_code == 403
    assert "voll" in r.json()["detail"]

    # Owner-Liste + Widerruf
    lst = client.get("/api/contrib/list").json()["contribs"]
    mine = next(c for c in lst if c["folder_name"] == "2026 - Feier Test")
    assert mine["uploaded"] >= 2
    assert client.post("/api/contrib/revoke",
                       json={"token_hash": mine["token_hash"]}).status_code == 200
    assert anon.get(f"/contrib/{token}/status").status_code == 404
    assert anon.post(f"/contrib/{token}/upload?filename=d.jpg",
                     content=_jpeg()).status_code == 404


def test_contrib_expiry(client, login, _app):
    login()
    token = _create_contrib(client, folder="2026 - Feier Ablauf").json()["token"]
    conn = shares._ensure_conn()
    with shares._lock:
        conn.execute("UPDATE contribs SET expires_at = ? WHERE token_hash = ?",
                     (time.time() - 10, shares._hash(token)))
    anon = TestClient(_app)
    assert anon.get(f"/contrib/{token}/status").status_code == 404
