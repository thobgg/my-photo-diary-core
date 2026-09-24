"""Tests für den Erstanmelde-Zwangswechsel (must_change_password, 04.09.2026)
und POST /api/account/change-password."""
from core import userdb
from tests.conftest import ADMIN, VIEWER


def _set_flag(user, flag=True):
    assert userdb.set_must_change_password(user, flag)


def test_flag_locks_api_but_allows_whoami_and_change(client, login):
    login(VIEWER[0], VIEWER[1])
    _set_flag(VIEWER[0])
    try:
        # Gesperrt: normale API → 403 mit Kennung
        r = client.get("/api/albums")
        assert r.status_code == 403
        assert r.json().get("code") == "must_change_password"
        # Seiten-Request → Umleitung auf die Wechselseite
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/change-password"
        # Erlaubt: whoami (meldet das Flag), Seite, Logout-Route
        r = client.get("/api/whoami")
        assert r.status_code == 200
        assert r.json()["must_change_password"] is True
        assert client.get("/change-password").status_code == 200
    finally:
        _set_flag(VIEWER[0], False)


def test_change_password_clears_flag_and_unlocks(client, login):
    login(VIEWER[0], VIEWER[1])
    _set_flag(VIEWER[0])
    new_pw = "ganz-neues-passwort-123"
    r = client.post("/api/account/change-password", json={
        "old_password": VIEWER[1], "new_password": new_pw,
    })
    assert r.status_code == 200, r.text
    # Flag weg, API wieder offen
    assert client.get("/api/albums").status_code == 200
    assert client.get("/api/whoami").json()["must_change_password"] is False
    # Neues Passwort gilt, altes nicht mehr
    client.cookies.clear()
    r = client.post("/api/login", json={"username": VIEWER[0], "password": VIEWER[1]})
    assert r.status_code == 401
    r = client.post("/api/login", json={"username": VIEWER[0], "password": new_pw})
    assert r.status_code == 200
    # zurückdrehen für die übrigen Tests
    assert userdb.change_password(VIEWER[0], VIEWER[1])


def test_change_password_validations(client, login):
    login()
    # falsches Alt-Passwort
    r = client.post("/api/account/change-password", json={
        "old_password": "voellig-falsch", "new_password": "ein-langes-neues-pw",
    })
    assert r.status_code == 401
    # zu kurz
    r = client.post("/api/account/change-password", json={
        "old_password": ADMIN[1], "new_password": "kurz",
    })
    assert r.status_code == 400
    # identisch zum alten
    r = client.post("/api/account/change-password", json={
        "old_password": ADMIN[1], "new_password": ADMIN[1],
    })
    assert r.status_code == 400
