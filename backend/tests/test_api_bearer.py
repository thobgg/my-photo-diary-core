"""Bearer-Token-Auth (additiv 09/2026): Login mit want_token, Authorization-
Header gleichwertig zum mpd_session-Cookie. Cookie-Verhalten unverändert."""
from tests.conftest import ADMIN


def _login_with_token(client):
    r = client.post("/api/login", json={
        "username": ADMIN[0], "password": ADMIN[1], "want_token": True,
    })
    assert r.status_code == 200
    return r


def test_login_without_flag_has_no_token(client):
    """Bisheriges Verhalten: ohne want_token kein Token im JSON."""
    r = client.post("/api/login", json={"username": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 200
    body = r.json()
    assert "token" not in body
    assert body["ok"] is True
    assert "mpd_session" in r.cookies


def test_login_want_token_returns_token(client):
    body = _login_with_token(client).json()
    assert body["token"]
    assert body["expires_in"] > 0
    # Cookie wird trotzdem weiter gesetzt (Web-Kompatibilität)
    assert body["token"] == client.cookies.get("mpd_session")


def test_bearer_grants_access_without_cookie(client):
    token = _login_with_token(client).json()["token"]
    client.cookies.clear()

    # Ohne alles: 401 (Middleware)
    assert client.get("/api/albums").status_code == 401
    # Mit Bearer: durchgelassen — Middleware UND require_session
    r = client.get("/api/albums", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    r = client.get("/api/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["user"] == ADMIN[0]


def test_bearer_invalid_token_401(client):
    assert client.get(
        "/api/albums", headers={"Authorization": "Bearer quatsch"}
    ).status_code == 401


def test_logout_via_bearer_invalidates_session(client):
    token = _login_with_token(client).json()["token"]
    client.cookies.clear()
    hdr = {"Authorization": f"Bearer {token}"}

    assert client.post("/api/logout", headers=hdr).status_code == 200
    assert client.get("/api/albums", headers=hdr).status_code == 401
