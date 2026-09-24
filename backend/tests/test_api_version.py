"""Produktversion und Build sind zwei Dinge.

Die dritte Stelle der Versionsnummer ist ein Commit-Zaehler, keine
Patch-Nummer — sie stand im September 2026 bei 246, waehrend vorne
unveraendert "2.0" klebte. Seither kommt die vordere Zahl aus
backend/product_version.txt und wird von Hand gepflegt; der Zaehler
bleibt automatisch, weil Synology fuer SPK-Updates monoton steigende
Versionen verlangt.
"""


def test_version_endpoint_is_public_and_splits_product_and_build(client):
    r = client.get("/api/version")
    assert r.status_code == 200, "must stay reachable without a session"
    d = r.json()

    for key in ("version", "product", "build", "build_date", "build_token"):
        assert key in d, f"/api/version verliert das Feld {key}"

    # version bleibt <Produkt>.<Build> — Clients, die nur dieses Feld
    # kennen, duerfen nicht brechen.
    assert d["version"] == f'{d["product"]}.{d["build"]}'


def test_product_version_is_not_hardcoded_to_the_old_two_oh():
    """Die vordere Zahl kommt aus der Datei, nicht aus dem Code."""
    from main import PRODUCT_VERSION, _read_product_version
    assert PRODUCT_VERSION == _read_product_version()
    assert PRODUCT_VERSION, "Produktversion darf nicht leer sein"


def test_health_reports_the_same_product_version(client, login):
    login()
    r = client.get("/health")
    assert r.status_code == 200
    d = r.json()
    v = client.get("/api/version").json()
    assert d["version"] == v["version"]
    assert d["product"] == v["product"], "health und api/version duerfen nicht auseinanderlaufen"
