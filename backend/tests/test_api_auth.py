"""Auth & Session: Login, Logout, Cookie, Middleware-Schutz."""
from tests.conftest import ADMIN, VIEWER


def test_api_without_login_401(client):
    r = client.get("/api/albums")
    assert r.status_code == 401


def test_page_without_login_redirects_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_login_page_is_public(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "My Photo Diary" in r.text


def test_login_wrong_password_401_and_no_cookie(client):
    r = client.post("/api/login", json={"username": ADMIN[0], "password": "falsch"})
    assert r.status_code == 401
    assert "mpd_session" not in r.cookies


def test_login_ok_sets_cookie_and_whoami(client, login):
    r = login()
    assert r.json()["ok"] is True
    assert "mpd_session" in client.cookies

    who = client.get("/api/whoami")
    assert who.status_code == 200
    data = who.json()
    assert data["user"] == ADMIN[0]
    assert data["role"] == "admin"


def test_logout_invalidates_session(client, login):
    login()
    assert client.get("/api/whoami").status_code == 200

    r = client.post("/api/logout")
    assert r.status_code == 200

    # Cookie ist gelöscht; und selbst der alte Token wäre serverseitig zerstört
    assert client.get("/api/albums").status_code == 401


def test_viewer_role_in_whoami(client, login):
    login(user=VIEWER[0], pw=VIEWER[1])
    data = client.get("/api/whoami").json()
    assert data["role"] == "viewer"
