"""Memories-Rangfolge (08.09.2026): ueber den Tag verteilt, verschiedene
Orte zuerst, deterministisch — reine Funktion, kein Dateizugriff."""
from datetime import datetime

from diary_memories.scanner import rank_photos


def _p(name, hh, mm=0, gps=None, album="A"):
    return {"filename": name, "album": album, "gps": gps,
            "dt": datetime(2020, 5, 1, hh, mm) if hh is not None else None}


def test_reihum_ueber_den_tag():
    cands = [_p("a", 7), _p("b", 7, 30), _p("c", 13), _p("d", 15), _p("e", 21), _p("f", 3), _p("g", 10)]
    ranked = [p["filename"] for p in rank_photos(cands)]
    # Runde 1: morgen, vormittag, mittag, nachmittag, abend, nacht — je eines;
    # b (07:30) liegt auf der Slot-Mitte, a (07:00) daneben.
    assert ranked == ["b", "g", "c", "d", "e", "f", "a"]


def test_orte_zuerst():
    """Neuer Ort schlaegt Slot-Mitte — aber erst, wenn der Ort schon einmal
    vertreten ist."""
    hotel = (43.7700, 11.2500); dom = (43.7731, 11.2560)   # ~600 m auseinander
    cands = [_p("x", 12, 30, gps=hotel), _p("y", 13, 10, gps=hotel), _p("z", 8, 0, gps=hotel),
             _p("w", 8, 30, gps=dom)]
    ranked = [p["filename"] for p in rank_photos(cands)]
    # Runde 1: morgen → z (Hotel, Slot-Mitte 07:30 naeher als w? nein: w ist
    # neuer Ort, aber noch ist KEIN Ort gesehen → Slot-Mitte entscheidet:
    # z 08:00 (30 min) vor w 08:30 (60 min)); mittag → y (13:10 naeher an
    # 13:00 als x 12:30). Runde 2: morgen → w (Dom, neuer Ort); mittag → x.
    assert ranked == ["z", "y", "w", "x"]


def test_ohne_uhrzeit_zuletzt_und_deterministisch():
    cands = [_p("n1", None), _p("n2", None), _p("m", 9)]
    r1 = [p["filename"] for p in rank_photos(cands)]
    r2 = [p["filename"] for p in rank_photos(list(reversed(cands)))]
    assert r1 == r2 == ["m", "n1", "n2"]


def test_leer():
    assert rank_photos([]) == []


# ── Wuerfel (13.09.2026) ─────────────────────────────────────────────────
# Regeln bleiben, innerhalb der Gruppe wird gewuerfelt: an einem Tag
# stabil, von Jahr zu Jahr verschieden.

def _many(n, hh):
    """n Fotos im selben Tagesabschnitt, am selben Ort (kein GPS, ein
    Album) — also EINE Gruppe. Hier und nur hier darf gewuerfelt werden."""
    return [_p(f"f{i:02d}", hh, i % 60) for i in range(n)]


def test_wuerfel_ist_an_einem_tag_stabil():
    cands = _many(12, 10)
    a = [p["filename"] for p in rank_photos(cands, seed="2026-09-13|2019")]
    b = [p["filename"] for p in rank_photos(list(reversed(cands)), seed="2026-09-13|2019")]
    assert a == b                      # zweimal oeffnen = dasselbe Bild
    assert a[0] != "f00"               # nicht mehr die alte Rangfolge


def test_wuerfel_wechselt_von_jahr_zu_jahr():
    cands = _many(12, 10)
    heute  = [p["filename"] for p in rank_photos(cands, seed="2026-09-13|2019")]
    naechs = [p["filename"] for p in rank_photos(cands, seed="2027-09-13|2019")]
    assert heute[:3] != naechs[:3]


def test_wuerfel_haelt_die_gruppen_ein():
    """Reihum ueber den Tag bleibt: die ersten sechs decken sechs
    Abschnitte ab, egal wie gewuerfelt wird."""
    cands = []
    for hh in (7, 10, 13, 15, 21, 3):
        cands += [_p(f"{hh:02d}-{i}", hh, i * 5) for i in range(4)]
    ranked = [p["filename"] for p in rank_photos(cands, seed="2026-09-13|2019")]
    assert len(ranked) == len(cands)
    assert sorted(int(n.split("-")[0]) for n in ranked[:6]) == [3, 7, 10, 13, 15, 21]


def test_wuerfel_haelt_die_orte_ein():
    """Verschiedene Orte zuerst schlaegt den Wuerfel: der Dom kommt in
    Runde 2, obwohl die Gruppe 'morgen' drei Hotel-Fotos hat."""
    hotel = (43.7700, 11.2500); dom = (43.7731, 11.2560)
    cands = [_p("h1", 8, 0, gps=hotel), _p("h2", 8, 10, gps=hotel),
             _p("h3", 8, 20, gps=hotel), _p("dom", 8, 30, gps=dom)]
    ranked = [p["filename"] for p in rank_photos(cands, seed="2026-09-13|2019")]
    assert ranked[0].startswith("h") and ranked[1] == "dom"


def test_ohne_startwert_bleibt_alles_beim_alten():
    cands = _many(12, 10)
    assert rank_photos(cands) == rank_photos(cands, seed="")


def test_wuerfel_nimmt_keine_uhrzeit():
    """Der Startwert kommt aus dem Datum. Laeuft der 30-Minuten-Cache
    mitten am Tag ab, muss derselbe Startwert dasselbe liefern."""
    cands = _many(12, 10)
    vormittags  = [p["filename"] for p in rank_photos(cands, seed="2026-09-13|2019")]
    nachmittags = [p["filename"] for p in rank_photos(cands, seed="2026-09-13|2019")]
    assert vormittags == nachmittags
