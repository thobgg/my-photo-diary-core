"""„App verbinden" (11.09.2026): Adresse und QR-Code fuer den ersten
Start der App. Anlass war der SPK-Fremdnutzer-Durchlauf — die Adresse
musste bis dahin auf dem Handy abgetippt werden."""
import xml.etree.ElementTree as ET

import config


def test_connect_braucht_anmeldung(client):
    assert client.get("/api/connect").status_code == 401
    assert client.get("/api/connect.svg").status_code == 401


def test_local_kommt_aus_dem_request(client, login):
    """Die gelieferte Adresse ist die, ueber die gerade zugegriffen wird —
    sie funktioniert nachweislich, sonst waere die Antwort nicht da."""
    login()
    d = client.get("/api/connect").json()
    assert d["local"].startswith("http")
    assert "testserver" in d["local"] or "://" in d["local"]


def test_proxy_header_gewinnt(client, login):
    """Hinter dem DSM-Reverse-Proxy steht die echte Adresse in
    X-Forwarded-*; ohne das stuende dort der interne Container-Host."""
    login()
    d = client.get("/api/connect", headers={
        "x-forwarded-proto": "https", "x-forwarded-host": "nas.example.org"}).json()
    assert d["local"] == "https://nas.example.org"


def test_remote_nur_wenn_konfiguriert_und_abweichend(client, login, monkeypatch):
    login()
    monkeypatch.setattr(config, "MPD_BASE_URL", "")
    assert client.get("/api/connect").json()["remote"] == ""
    assert client.get("/api/connect.svg?remote=1").status_code == 404

    monkeypatch.setattr(config, "MPD_BASE_URL", "https://mpd.example.org")
    d = client.get("/api/connect").json()
    assert d["remote"] == "https://mpd.example.org"
    assert d["local"] != d["remote"]

    # Gleiche Adresse wie der Zugriff → kein zweiter Block
    d = client.get("/api/connect", headers={
        "x-forwarded-proto": "https", "x-forwarded-host": "mpd.example.org"}).json()
    assert d["remote"] == ""


def test_qr_ist_svg_und_enthaelt_die_adresse(client, login):
    login()
    r = client.get("/api/connect.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers.get("cache-control") == "no-store"
    root = ET.fromstring(r.text)
    assert root.tag.endswith("svg")
    # Der Titel traegt die Adresse — und der Code laesst sich zurueckdekodieren
    url = client.get("/api/connect").json()["local"]
    assert url in r.text


def test_qr_traegt_den_sprung_in_die_app(client, login):
    """Gegenprobe ueber den Leser aus test_qr_min: im Code steht
    mpd://setup?url=…, nicht die nackte Adresse und nicht der Umweg ueber
    die Verbinden-Seite.

    Drei Wege standen zur Wahl (15.09.2026, zweimal mit dem App-Chat
    durchgesprochen). Die nackte Adresse landet im Browser und hilft beim
    Einrichten nicht. Ein Android App Link bindet an EINE verifizierte
    Domain und ist fuer eine Selbsthoster-Software nicht zu haben. Und der
    Umweg ueber /verbinden?setup=1 zwingt einen neuen Kunden durch die
    Zertifikatswarnung seines Browsers — seine NAS hat ein
    selbstsigniertes Zertifikat, die App kann damit umgehen, der Browser
    nicht.

    Der Einwand "ohne App fuehrt der Code ins Leere" bleibt richtig und
    ist auf der Seite geloest: Gescannt wird mit dem Handy, gelesen wird
    die Seite am Rechner, und Schritt 1 nennt die Installation."""
    from urllib.parse import quote
    from tests.test_qr_min import _decode
    from core import qr_min
    login()
    url = client.get("/api/connect").json()["local"]
    ziel = f"mpd://setup?url={quote(url, safe='')}"
    assert _decode(qr_min.matrix(ziel)) == ziel

    # Und der Endpunkt liefert genau diesen Code: Byte fuer Byte dasselbe
    # SVG, das qr_min fuer das Ziel zeichnet.
    r = client.get("/api/connect.svg")
    assert r.text == qr_min.svg(ziel, module=8, quiet=4, title=url)
    # Der Titel bleibt die nackte Adresse — er ist fuer Menschen da.
    assert url in r.text


def test_qr_kodiert_die_adresse_vollstaendig(client, login):
    """Adressen mit Port und Schema muessen als Parameter heil ankommen —
    typisch beim Kunden: https://192.168.1.2:5001."""
    from urllib.parse import quote, unquote
    from tests.test_qr_min import _decode
    from core import qr_min
    login()
    d = client.get("/api/connect", headers={
        "x-forwarded-proto": "https", "x-forwarded-host": "192.168.1.2:5001"}).json()
    ziel = f"mpd://setup?url={quote(d['local'], safe='')}"
    assert "://" not in ziel.split("url=", 1)[1], "Adresse nicht kodiert"
    assert unquote(ziel.split("url=", 1)[1]) == "https://192.168.1.2:5001"
    assert _decode(qr_min.matrix(ziel)) == ziel
