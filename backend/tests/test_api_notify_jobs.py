"""Benachrichtigungs-Startpaket (03.09.2026): Fan-out-Adressierung,
Upload-Tages-Digest, Kuratier-Wochenbericht, publish → nur Admins."""
import io
from urllib.parse import quote

from PIL import Image

import config
from core import notifydb, notify_jobs
from tests.conftest import ADMIN, VIEWER


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (90, 90, 200)).save(buf, "JPEG")
    return buf.getvalue()


def _register_and_pull(client, dev):
    client.put(f"/api/notify/devices/{dev}", json={"name": "t"})
    r = client.get(f"/api/notify/pull?device_id={dev}&since=0")
    return r.json()["notifications"]


def test_notify_many_fanout(client, login):
    ids = notifydb.notify_many(recipients=[ADMIN[0], VIEWER[0]],
                               type="test", title="t", body="fanout")
    assert len(ids) == 2

    login()
    admin_notes = _register_and_pull(client, "fan-admin")
    assert any(n["body"] == "fanout" for n in admin_notes)

    client.cookies.clear()
    login(VIEWER[0], VIEWER[1])
    viewer_notes = _register_and_pull(client, "fan-viewer")
    assert any(n["body"] == "fanout" for n in viewer_notes)


def test_publish_goes_to_admins_only(client, login, monkeypatch):
    monkeypatch.setattr(config, "NOTIFY_PUBLISH_TOKEN", "geheim-test-token")
    r = client.post("/api/notify/publish",
                    headers={"X-Publish-Token": "geheim-test-token"},
                    json={"type": "alert", "title": "Wächter", "body": "Testalarm"})
    assert r.status_code == 200, r.text

    login()
    assert any(n["body"] == "Testalarm"
               for n in _register_and_pull(client, "pub-admin"))

    client.cookies.clear()
    login(VIEWER[0], VIEWER[1])
    assert not any(n["body"] == "Testalarm"
                   for n in _register_and_pull(client, "pub-viewer"))


def test_upload_digest_excludes_actor(client, login, personal_album):
    """Einsortieren → Journal → Digest-Lauf: Meldung an alle außer dem
    Verursacher, mit space/album/count im Payload."""
    notifydb.drain_journal()  # Einträge früherer Tests (Import-Suite) leeren
    login()
    fn = "2026-09-03_10-00-00.jpg"
    r = client.put(f"/api/album/personal/{quote('2026')}/file/{quote(fn)}", content=_jpeg())
    assert r.status_code == 200, r.text
    r = client.post(f"/api/album/personal/{quote(personal_album)}/import-file",
                    json={"from_space": "personal", "from_album": "2026", "filename": fn})
    assert r.status_code == 200, r.text

    assert notify_jobs.run_upload_digest() == 1
    assert notify_jobs.run_upload_digest() == 0  # Journal geleert

    # Verursacher (Admin) bekommt sie NICHT …
    assert not any(n["type"] == "upload" and n["payload"].get("album") == personal_album
                   for n in _register_and_pull(client, "dig-admin"))
    # … der andere Nutzer schon, mit korrektem Payload
    client.cookies.clear()
    login(VIEWER[0], VIEWER[1])
    hits = [n for n in _register_and_pull(client, "dig-viewer")
            if n["type"] == "upload" and n["payload"].get("album") == personal_album]
    assert len(hits) == 1
    assert hits[0]["payload"]["count"] == 1
    assert hits[0]["payload"]["space"] == "personal"
    assert fn in hits[0]["payload"]["files"]


def test_curation_report_counts_shared_inbox(client, login, monkeypatch, tmp_path):
    from core import timeline_index
    from tests.conftest import make_jpeg

    # Unreferenzierte Datei im Shared-Space anlegen
    inbox = config.PHOTO_PATH_SHARED / "2026"
    make_jpeg(inbox / "2020-01-05_09-00-00.jpg")
    timeline_index.invalidate_timeline_index()

    assert notify_jobs.run_curation_report() is True

    login()
    hits = [n for n in _register_and_pull(client, "cur-admin") if n["type"] == "curation"]
    assert hits and hits[-1]["payload"]["count"] >= 1
    assert hits[-1]["payload"]["oldest_days"] >= 0
