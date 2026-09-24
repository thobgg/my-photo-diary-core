"""Kuratier-Modell C+ (02.09.2026): Editoren dürfen im Shared-Space
BEITRAGEN (Upload in den Durchlauf, Einsortieren/Wiederbeleben,
Ausschuss löschen), aber nicht KURATIEREN (Alben gestalten, Referenziertes
löschen). Dazu: Löschen kann jetzt auch Videos."""
import io
from urllib.parse import quote

from PIL import Image

from tests.test_api_modules import _upsert_user

EDITOR = ("emil", "emil12345678")


def _jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 20, 20)).save(buf, "JPEG")
    return buf.getvalue()


def _login_editor(client, users_file):
    _upsert_user(EDITOR[0], EDITOR[1], "editor", None)
    r = client.post("/api/login", json={"username": EDITOR[0], "password": EDITOR[1]})
    assert r.status_code == 200


def _upload(client, space, album, filename, data=None):
    return client.put(f"/api/album/{space}/{quote(album)}/file/{quote(filename)}",
                      content=data or _jpeg_bytes())


def test_editor_contributes_to_shared_stream(client, users_file, login):
    """Der Kernfall: Editor fotografiert in den Familien-Durchlauf,
    sortiert in ein Familienalbum ein — Gestalten bleibt verboten."""
    login()  # Admin legt das Familienalbum an
    r = client.post("/api/album/create-empty",
                    json={"space": "shared", "folder_name": "2026 - Familie CPlus"})
    assert r.status_code == 200, r.text

    client.cookies.clear()
    _login_editor(client, users_file)

    # Upload in den Shared-Durchlauf (Ordner entsteht automatisch)
    r = _upload(client, "shared", "2026", "2026-09-02_18-00-00-s.jpg")
    assert r.status_code == 200, r.text

    # Einsortieren ins Familienalbum
    r = client.post(f"/api/album/shared/{quote('2026 - Familie CPlus')}/import-file",
                    json={"from_space": "shared", "from_album": "2026",
                          "filename": "2026-09-02_18-00-00-s.jpg"})
    assert r.status_code == 200, r.text

    # Gestalten (update) bleibt 403 — Kurator-Sache
    album = client.get(f"/api/album/shared/{quote('2026 - Familie CPlus')}").json()
    r = client.post(f"/api/album/shared/{quote('2026 - Familie CPlus')}/update",
                    json={"version": "1.4", "meta": album["meta"], "elements": []})
    assert r.status_code == 403


def test_editor_deletes_only_unreferenced(client, users_file, login):
    login()
    r = client.post("/api/album/create-empty",
                    json={"space": "shared", "folder_name": "2026 - Familie Del"})
    assert r.status_code == 200, r.text

    client.cookies.clear()
    _login_editor(client, users_file)
    assert _upload(client, "shared", "2026", "2026-09-02_19-00-00-s.jpg").status_code == 200
    assert _upload(client, "shared", "2026", "2026-09-02_19-30-00-s.jpg").status_code == 200
    r = client.post(f"/api/album/shared/{quote('2026 - Familie Del')}/import-file",
                    json={"from_space": "shared", "from_album": "2026",
                          "filename": "2026-09-02_19-30-00-s.jpg"})
    assert r.status_code == 200, r.text

    # Unreferenzierter Ausschuss im Durchlauf → Editor darf löschen
    r = client.request("DELETE", f"/api/album/shared/{quote('2026')}/file",
                       json={"filename": "2026-09-02_19-00-00-s.jpg"})
    assert r.status_code == 200, r.text

    # Referenziert im Familienalbum → nur der Kurator
    r = client.request("DELETE", f"/api/album/shared/{quote('2026 - Familie Del')}/file",
                       json={"filename": "2026-09-02_19-30-00-s.jpg"})
    assert r.status_code == 403


def test_delete_supports_videos_now(client, login, personal_album):
    """Randfall 1 aus dem Stresstest: Videos waren nicht löschbar."""
    login()
    assert _upload(client, "personal", personal_album, "2026-09-02_20-00-00.mp4",
                   data=b"\x00\x00\x00\x18ftypmp42fakevideo").status_code == 200
    r = client.request("DELETE", f"/api/album/personal/{quote(personal_album)}/file",
                       json={"filename": "2026-09-02_20-00-00.mp4",
                             "album_context": True})   # Loeschschutz 06.09.
    assert r.status_code == 200, r.text
