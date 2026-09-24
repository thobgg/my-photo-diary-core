"""Benachrichtigungs-API (additiv 09/2026): Geräteregister, Poll, Ack,
Nutzer-Isolation, Dump-Datei. Ereignisse werden direkt über core.notifydb
eingespeist (der einzige Produzent ist der Memories-Scheduler)."""
import uuid

import pytest

import config
from core import notifydb
from tests.conftest import ADMIN, VIEWER


def _register(client, device_id, name="Testgerät"):
    return client.put(f"/api/notify/devices/{device_id}",
                      json={"name": name, "platform": "android"})


def test_register_pull_ack_roundtrip(client, login):
    login()
    dev = uuid.uuid4().hex
    r = _register(client, dev)
    assert r.status_code == 200
    assert r.json()["last_acked_id"] == 0

    # Leerer Pull
    r = client.get(f"/api/notify/pull?device_id={dev}")
    assert r.status_code == 200
    assert r.json()["notifications"] == []

    # Ereignis einspeisen (Broadcast wie beim Memories-Job)
    nid = notifydb.notify(recipient="*", type="memories",
                          title="My Photo Diary", body="Testmeldung",
                          payload={"deep_link": "mpd://memories"})

    r = client.get(f"/api/notify/pull?device_id={dev}")
    data = r.json()
    assert [n["id"] for n in data["notifications"]] == [nid]
    assert data["notifications"][0]["payload"]["deep_link"] == "mpd://memories"
    assert data["cursor"] == nid

    # Ack setzt den Cursor — nächster Pull ist leer
    r = client.post("/api/notify/ack", json={"device_id": dev, "last_id": nid})
    assert r.status_code == 200
    assert client.get(f"/api/notify/pull?device_id={dev}").json()["notifications"] == []

    # since=0 überstimmt den Cursor (erneutes Abholen möglich)
    r = client.get(f"/api/notify/pull?device_id={dev}&since=0")
    assert nid in [n["id"] for n in r.json()["notifications"]]


def test_recipient_isolation(client, login):
    """Nutzer sehen Broadcast + Eigenes, nie die Ereignisse anderer."""
    login(user=VIEWER[0], pw=VIEWER[1])
    dev = uuid.uuid4().hex
    _register(client, dev)

    only_admin = notifydb.notify(recipient=ADMIN[0], type="test",
                                 title="t", body="nur admin")
    for_viewer = notifydb.notify(recipient=VIEWER[0], type="test",
                                 title="t", body="nur vera")

    ids = [n["id"] for n in
           client.get(f"/api/notify/pull?device_id={dev}&since=0").json()["notifications"]]
    assert for_viewer in ids
    assert only_admin not in ids


def test_foreign_device_rejected(client, login):
    """device_id ist an den User gebunden: fremde Übernahme und fremdes
    Ack scheitern."""
    login()
    dev = uuid.uuid4().hex
    assert _register(client, dev).status_code == 200

    client.cookies.clear()
    login(user=VIEWER[0], pw=VIEWER[1])
    assert _register(client, dev).status_code == 403
    assert client.post("/api/notify/ack",
                       json={"device_id": dev, "last_id": 1}).status_code == 404
    assert client.get(f"/api/notify/pull?device_id={dev}").status_code == 404


def test_device_list_and_delete(client, login):
    login()
    dev = uuid.uuid4().hex
    _register(client, dev, name="Tab S8 Ultra")

    devices = client.get("/api/notify/devices").json()["devices"]
    assert dev in [d["device_id"] for d in devices]

    assert client.delete(f"/api/notify/devices/{dev}").json()["ok"] is True
    assert client.get(f"/api/notify/pull?device_id={dev}").status_code == 404


def test_unknown_device_404(client, login):
    login()
    assert client.get("/api/notify/pull?device_id=gibtsnicht").status_code == 404


def test_requires_auth(client):
    assert client.get("/api/notify/pull?device_id=x").status_code == 401
    assert client.put("/api/notify/devices/x", json={}).status_code == 401


def test_dump_file_written(client, login):
    """Jeder strukturelle Schreibvorgang erneuert den menschenlesbaren
    SQL-Dump neben der DB."""
    notifydb.notify(recipient="*", type="test", title="t", body="dump-check")
    dump = config.NOTIFY_DB_PATH.with_suffix(".sql")
    assert dump.is_file()
    text = dump.read_text(encoding="utf-8")
    assert "CREATE TABLE" in text
    assert "dump-check" in text


def test_memories_producer_message():
    """Der Wortlaut von ntfy-Push und In-App-Eintrag kommt aus derselben
    Funktion."""
    pytest.importorskip("diary_memories", reason="Memories-Modul nicht mitgeliefert")
    from diary_memories.scheduler import build_memories_message
    results = [{"years_ago": 3, "photos": [1, 2]}, {"years_ago": 7, "photos": [3]}]
    assert build_memories_message(results) == "Heute vor 3 bis 7 Jahren – 3 Erinnerungen 📷"


# -- POST /api/notify/publish (Fremd-Einlieferung, 03.09.2026) --------------

def test_publish_ohne_token_konfiguration_404(client, monkeypatch):
    monkeypatch.setattr(config, "NOTIFY_PUBLISH_TOKEN", "")
    r = client.post("/api/notify/publish", json={"title": "x"})
    assert r.status_code == 404


def test_publish_falscher_token_401(client, monkeypatch):
    monkeypatch.setattr(config, "NOTIFY_PUBLISH_TOKEN", "geheim")
    r = client.post("/api/notify/publish", json={"title": "x"},
                    headers={"X-Publish-Token": "falsch"})
    assert r.status_code == 401


def test_publish_roundtrip_bis_zum_pull(client, login, monkeypatch):
    monkeypatch.setattr(config, "NOTIFY_PUBLISH_TOKEN", "geheim")
    r = client.post(
        "/api/notify/publish",
        json={"type": "alert", "title": "Harvester!",
              "body": "Auffaellige Zugriffe auf db-kies",
              "payload": {"source": "db-kies"}},
        headers={"X-Publish-Token": "geheim"},
    )
    assert r.status_code == 200
    nid = r.json()["id"]

    login()
    import uuid as _uuid
    dev = _uuid.uuid4().hex
    assert _register(client, dev).status_code == 200
    got = client.get(f"/api/notify/pull?device_id={dev}").json()["notifications"]
    match = [n for n in got if n["id"] == nid]
    assert match and match[0]["type"] == "alert"
    assert match[0]["payload"]["source"] == "db-kies"


def test_publish_leerer_inhalt_400(client, monkeypatch):
    monkeypatch.setattr(config, "NOTIFY_PUBLISH_TOKEN", "geheim")
    r = client.post("/api/notify/publish", json={"payload": {}},
                    headers={"X-Publish-Token": "geheim"})
    assert r.status_code == 400
