"""Die Endungslisten sind EINE Liste — und die MIME-Karten passen dazu.

Vor dem 07.09.2026 stand die Foto-Menge an vier Stellen. Solange es vier
sind, erscheint nach jeder Erweiterung frueher oder spaeter eine Datei in
der Liste, die nicht vorgewaermt wird — oder umgekehrt. Diese Tests
halten die Zusammenfuehrung fest: sie pruefen nicht auf Gleichheit,
sondern auf IDENTITAET. Wer irgendwo wieder eine eigene Menge hinschreibt,
faellt hier auf, auch wenn sie im selben Moment noch denselben Inhalt hat.
"""
from core import media_types


def test_alle_module_teilen_dieselbe_foto_menge():
    from core import filesystem_photos, library_warmer
    from routers import deps

    assert deps._PHOTO_EXTS               is media_types.PHOTO_EXTS
    assert filesystem_photos._PHOTO_EXTS  is media_types.PHOTO_EXTS
    assert library_warmer.PHOTO_EXTS      is media_types.PHOTO_EXTS
    try:                                  # Stats ist ein Plus-Modul
        import stats_scanner
    except ImportError:
        return
    assert stats_scanner._PHOTO_EXTS      is media_types.PHOTO_EXTS


def test_alle_module_teilen_dieselbe_video_menge():
    from core import filesystem_photos
    from routers import deps

    assert deps._VIDEO_EXTS              is media_types.VIDEO_EXTS
    assert filesystem_photos._VIDEO_EXTS is media_types.VIDEO_EXTS
    assert deps._MEDIA_EXTS              is media_types.MEDIA_EXTS


def test_mime_karten_decken_ihre_endungen_vollstaendig_ab():
    """Der Fehler vom 07.09. in Regelform: eine Endung ohne MIME-Eintrag
    faellt auf den Rueckfall und wird falsch etikettiert."""
    fehlend_foto  = media_types.PHOTO_EXTS - set(media_types.PHOTO_MIME)
    fehlend_video = media_types.VIDEO_EXTS - set(media_types.VIDEO_MIME)
    assert not fehlend_foto,  f"Foto-Endungen ohne MIME-Typ: {sorted(fehlend_foto)}"
    assert not fehlend_video, f"Video-Endungen ohne MIME-Typ: {sorted(fehlend_video)}"

    assert all(v.startswith("image/") for v in media_types.PHOTO_MIME.values())
    assert all(v.startswith("video/") for v in media_types.VIDEO_MIME.values())


def test_mime_karten_erfinden_keine_endungen():
    """Andersherum genauso: ein MIME-Eintrag fuer eine Endung, die MPD gar
    nicht anzeigt, ist toter Code und meist ein vergessener Halbschritt."""
    assert set(media_types.PHOTO_MIME) <= media_types.PHOTO_EXTS
    assert set(media_types.VIDEO_MIME) <= media_types.VIDEO_EXTS


def test_bearbeitbar_ist_teilmenge_von_anzeigbar():
    """Anzeigen ist harmlos, Zurueckschreiben nicht. MPD darf nie ein
    Format ueberschreiben, das es nur anzeigen kann."""
    assert media_types.EDITABLE_EXTS <= media_types.PHOTO_EXTS


def test_die_am_07_09_dazugekommenen_formate_sind_da():
    """Namentlich, damit ein Wegfall auffaellt statt stillschweigend zu
    passieren. Alles ohne neue Abhaengigkeit: Pillow kann tif/bmp/jfif
    nativ, ffmpeg liegt seit jeher im Image."""
    for e in (".tif", ".tiff", ".bmp", ".jfif"):
        assert e in media_types.PHOTO_EXTS, e
    for e in (".mts", ".m2ts", ".wmv", ".mpg", ".mpeg"):
        assert e in media_types.VIDEO_EXTS, e


def test_tiff_wird_angezeigt_aber_nicht_ueberschrieben():
    """Der Grund fuer EDITABLE_EXTS. Ein TIFF kann mehrseitig sein;
    Pillow schriebe beim Speichern EINE Seite zurueck, und bei einem
    eingescannten Album waeren die uebrigen weg."""
    assert ".tif" in media_types.PHOTO_EXTS
    assert ".tif" not in media_types.EDITABLE_EXTS
    assert media_types.EDITABLE_EXTS < media_types.PHOTO_EXTS   # echte Teilmenge


# ---------------------------------------------------------------------------
# Was der Ordner enthaelt, MPD aber nicht anzeigen kann
# ---------------------------------------------------------------------------

def test_unsupported_in_zaehlt_nach_endung(tmp_path):
    for name in ("a.cr2", "b.cr2", "c.nef", "urlaub.jpg", "film.mp4"):
        (tmp_path / name).write_bytes(b"x")
    treffer = media_types.unsupported_in(tmp_path)
    # Nach Anzahl sortiert, haeufigste zuerst
    assert treffer == [{"ext": ".cr2", "count": 2}, {"ext": ".nef", "count": 1}]


def test_unsupported_in_uebergeht_was_dazugehoert(tmp_path):
    """Sonst meldet der Hinweis Betriebsdateien als „fehlend" — und wird
    zu Rauschen, das niemand mehr liest."""
    (tmp_path / "album.json").write_text("{}")
    (tmp_path / ".mpd_stats.json").write_text("{}")
    (tmp_path / "foto.jpg").write_bytes(b"x")
    (tmp_path / "foto.jpg.bak").write_bytes(b"x")
    (tmp_path / "foto.jpg.bak3").write_bytes(b"x")
    (tmp_path / "ton.mp3").write_bytes(b"x")
    (tmp_path / "text.pdf").write_bytes(b"x")
    (tmp_path / "spur.gpx").write_bytes(b"x")
    (tmp_path / ".mpd_backups").mkdir()
    assert media_types.unsupported_in(tmp_path) == []


def test_unsupported_in_meldet_dateien_ohne_endung(tmp_path):
    (tmp_path / "IMG_4711").write_bytes(b"x")
    assert media_types.unsupported_in(tmp_path) == [{"ext": "(ohne Endung)", "count": 1}]


def test_unsupported_in_ueberlebt_einen_unlesbaren_ordner(tmp_path):
    """Der Hinweis ist Beiwerk — er darf das Auflisten nie umbringen."""
    assert media_types.unsupported_in(tmp_path / "gibt-es-nicht") == []


def test_neue_formate_gelten_nicht_mehr_als_unbekannt(tmp_path):
    """Gegenprobe zur Erweiterung: Was seit 07.09. angezeigt wird, darf
    nicht gleichzeitig als „kann MPD nicht" gemeldet werden."""
    for name in ("scan.tif", "opa.bmp", "camcorder.mts", "alt.mpg"):
        (tmp_path / name).write_bytes(b"x")
    assert media_types.unsupported_in(tmp_path) == []


# ---------------------------------------------------------------------------
# is_editable — die FORMAT-Haelfte der Bearbeitbarkeit
# ---------------------------------------------------------------------------

def test_is_editable_folgt_editable_exts():
    assert media_types.is_editable("urlaub.jpg")
    assert media_types.is_editable("URLAUB.JPG")        # Endung ohne Ruecksicht auf Schreibung
    assert media_types.is_editable("bild.jfif")         # ist eine JPEG-Datei
    assert not media_types.is_editable("scan.tif")      # kann mehrseitig sein
    assert not media_types.is_editable("opa.bmp")
    assert not media_types.is_editable("film.mp4")      # Videos bearbeitet MPD nicht
    assert not media_types.is_editable("IMG_4711")      # ohne Endung


def test_is_editable_und_endpunkt_ziehen_dieselbe_grenze():
    """Der Sinn des Feldes: Was `editable_format: true` meldet, darf im
    Endpunkt nicht am Format scheitern — sonst zeigt die App einen Stift,
    der in ein 400 laeuft. Genau die Richtung, die niemandem auffaellt."""
    from routers import photo_edit  # noqa: F401  (Import prueft die Verdrahtung)
    for endung in media_types.PHOTO_EXTS | media_types.VIDEO_EXTS:
        gemeldet = media_types.is_editable("x" + endung)
        erlaubt  = endung in media_types.EDITABLE_EXTS
        assert gemeldet == erlaubt, endung


def test_begleitdateien_sind_kein_fehlalarm(tmp_path):
    """Am Bestand gemessen (07.09.2026): In einem einzigen Album lagen 396
    Google-Takeout-Beipackzettel. Ohne diese Ausnahme haette der Hinweis
    im Einfuege-Dialog als Erstes „396 mal .json" gemeldet — technisch
    wahr, praktisch irrefuehrend, und danach liest ihn niemand mehr."""
    (tmp_path / "20231014_043426.jpg").write_bytes(b"x")
    (tmp_path / "20231014_043426.jpg.supplemental-metadata.json").write_text("{}")
    (tmp_path / "bild.xmp").write_text("")
    (tmp_path / "IMG_0001.aae").write_text("")
    (tmp_path / "film.thm").write_bytes(b"x")
    (tmp_path / "desktop.ini").write_text("")
    (tmp_path / "Thumbs.db").write_bytes(b"x")
    assert media_types.unsupported_in(tmp_path) == []

    # Aber echtes Fremdmaterial bleibt sichtbar
    (tmp_path / "rohbild.cr2").write_bytes(b"x")
    assert media_types.unsupported_in(tmp_path) == [{"ext": ".cr2", "count": 1}]
