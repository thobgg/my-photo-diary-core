"""Die oeffentliche Basisadresse — geraten ist schlimmer als abgeleitet.

Bis 05.09.2026 hatte MPD_BASE_URL den festen Vorgabewert
die Adresse der Entwicklungsinstallation. Fuer die stimmte das
zufaellig; jede andere Installation ohne gesetzten Wert haette Share-Mails
mit Links auf einen FREMDEN Server verschickt.

Jetzt gilt: konfigurierter Wert gewinnt, sonst aus dem Request abgeleitet —
und /api/whoami liefert das Feld nur, wenn es wirklich konfiguriert ist.
"""

import config
from routers.deps import public_base_url


class _Req:
    """Minimaler Request-Ersatz: nur was public_base_url anfasst."""
    def __init__(self, headers, scheme="http"):
        self.headers = headers
        self.url = type("U", (), {"scheme": scheme})()


def test_configured_value_wins(monkeypatch):
    monkeypatch.setattr(config, "MPD_BASE_URL", "https://konfiguriert.example")
    r = _Req({"host": "ganz-woanders.example"})
    assert public_base_url(r) == "https://konfiguriert.example"


def test_derives_from_host_when_unset(monkeypatch):
    monkeypatch.setattr(config, "MPD_BASE_URL", "")
    r = _Req({"host": "meine-nas.example:8443"}, scheme="http")
    assert public_base_url(r) == "http://meine-nas.example:8443"


def test_reverse_proxy_headers_win_over_host(monkeypatch):
    """Hinter dem DSM-Reverse-Proxy stehen Schema und Host in X-Forwarded-*.
    Ohne das kaeme http:// heraus, obwohl der Gast https benutzt."""
    monkeypatch.setattr(config, "MPD_BASE_URL", "")
    r = _Req({
        "host": "127.0.0.1:8000",
        "x-forwarded-proto": "https",
        "x-forwarded-host": "mpd.example.org",
    }, scheme="http")
    assert public_base_url(r) == "https://mpd.example.org"


def test_forwarded_list_takes_the_first_entry(monkeypatch):
    """Mehrere Proxys haengen kommasepariert an — der erste ist der Client."""
    monkeypatch.setattr(config, "MPD_BASE_URL", "")
    r = _Req({
        "host": "x",
        "x-forwarded-proto": "https, http",
        "x-forwarded-host": "aussen.example.org, innen.local",
    })
    assert public_base_url(r) == "https://aussen.example.org"


# --- Das Feld fuer die App ----------------------------------------------

def test_whoami_omits_base_url_when_not_configured(client, login, monkeypatch):
    """Lieber kein Feld als ein geratenes: sonst bekaeme ein Kunde die
    Adresse einer fremden Installation ausgeliefert."""
    import main
    monkeypatch.setattr(main.config, "MPD_BASE_URL_CONFIGURED", False, raising=False)
    monkeypatch.setattr(config, "MPD_BASE_URL_CONFIGURED", False)
    login()
    d = client.get("/api/whoami").json()
    assert "base_url" not in d


def test_whoami_reports_base_url_when_configured(client, login, monkeypatch):
    monkeypatch.setattr(config, "MPD_BASE_URL", "https://mpd.example.org")
    monkeypatch.setattr(config, "MPD_BASE_URL_CONFIGURED", True)
    login()
    d = client.get("/api/whoami").json()
    assert d.get("base_url") == "https://mpd.example.org"
