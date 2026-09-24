"""Teilen v3: Die Mail zu einem Einzelfoto-Share darf nicht vom Album reden
— und vor allem nicht das Albumcover mitschicken.

Der Fehler, den diese Tests festhalten: eine per Mail geteilte EINZELNE Datei
kuendigte sich als Album an (Betreff, Text) und bettete als Vorschaubild das
Albumcover ein. Damit bekam der Empfaenger ein Foto zugestellt, das gar nicht
geteilt worden war.
"""
import io

from PIL import Image

from core import shares
from routers.share import (
    _build_cover_jpeg_for_mail,
    _build_share_mail_html,
    _build_share_mail_text,
)

# Farben aus conftest.make_jpeg: p1 ist das Albumcover, p2 das Nachbarfoto.
COVER_RGB = (200, 150, 40)   # p1.jpg — orange
OTHER_RGB = (40, 90, 160)    # p2.jpg — blau


def _mean_rgb(jpeg_bytes):
    img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    px = list(img.getdata())
    n = len(px)
    return tuple(sum(c[i] for c in px) // n for i in range(3))


def _dist(rgb, ref):
    """Euklidischer Farbabstand — fuer 'ist das ueberhaupt dieses Bild?'."""
    return sum((rgb[i] - ref[i]) ** 2 for i in range(3)) ** 0.5


def _closer_to(rgb, a, b):
    """Welche Referenzfarbe liegt naeher? Gibt 'a' oder 'b' zurueck."""
    da = sum((rgb[i] - a[i]) ** 2 for i in range(3))
    db = sum((rgb[i] - b[i]) ** 2 for i in range(3))
    return "a" if da <= db else "b"


def _photo_share(client, album, filename):
    r = client.post("/api/share/create", json={
        "space": "personal", "album_name": album, "file": filename,
    })
    assert r.status_code == 200, r.text
    return shares.resolve(r.json()["token"])


def _album_share(client, album):
    r = client.post("/api/share/create", json={
        "space": "personal", "album_name": album,
    })
    assert r.status_code == 200, r.text
    return shares.resolve(r.json()["token"])


# --- Das Vorschaubild: der ernstere Teil ---------------------------------

def test_photo_share_mail_shows_the_shared_photo(client, login, personal_album):
    """Foto-Share von p2 → das Bild in der Mail ist p2, nicht das Cover p1."""
    login()
    info = _photo_share(client, personal_album, "p2.jpg")

    jpeg = _build_cover_jpeg_for_mail(info)
    assert jpeg, "kein Vorschaubild erzeugt"

    rgb = _mean_rgb(jpeg)
    assert _dist(rgb, OTHER_RGB) < 30, (
        f"Mail zeigt das falsche Bild: {rgb} ist nicht das geteilte Foto "
        f"{OTHER_RGB} (Albumcover waere {COVER_RGB})"
    )


def test_album_share_mail_still_shows_the_cover(client, login, personal_album):
    """Gegenprobe: beim Album-Share bleibt es beim Albumcover."""
    login()
    info = _album_share(client, personal_album)

    jpeg = _build_cover_jpeg_for_mail(info)
    assert jpeg, "kein Vorschaubild erzeugt"

    rgb = _mean_rgb(jpeg)
    assert _dist(rgb, COVER_RGB) < 30, (
        f"Album-Mail zeigt nicht mehr das Cover {COVER_RGB}: {rgb}"
    )


def test_photo_locked_after_sharing_drops_out_of_the_mail(client, login, personal_album):
    """Ein Foto, das NACH dem Teilen gesperrt wird, darf nicht mehr in die Mail.

    Beim Anlegen verweigert der Server einen Share auf ein gesperrtes Foto
    bereits mit 403 — die Luecke ist also nur die spaetere Sperre.
    """
    login()
    info = _photo_share(client, personal_album, "p2.jpg")

    # jetzt erst sperren
    data = client.get(f"/api/album/personal/{personal_album}").json()
    for el in data["elements"]:
        if el.get("file") == "p2.jpg":
            el["locked"] = True
    r = client.post(f"/api/album/personal/{personal_album}/update",
                    json={"meta": data["meta"], "elements": data["elements"]})
    assert r.status_code == 200, r.text

    jpeg = _build_cover_jpeg_for_mail(info)
    if jpeg:  # Rueckfall auf das Standardbild ist erlaubt — p2 nicht
        rgb = _mean_rgb(jpeg)
        assert _dist(rgb, OTHER_RGB) > 50, (
            f"gesperrtes Foto landet trotzdem in der Mail: {rgb} liegt zu nah "
            f"am geteilten Foto {OTHER_RGB}"
        )


# --- Betreff und Text -----------------------------------------------------

def test_photo_mail_text_says_photo_and_hides_album_name():
    body = _build_share_mail_text(
        url="https://example.invalid/share/tok",
        album_name="Geheimes Album 2019", sender_name="Anna",
        to_name=None, personal_message="", lang="de", is_photo=True,
    )
    assert "Foto" in body
    assert "Geheimes Album 2019" not in body, "Albumtitel gehoert nicht in die Foto-Mail"


def test_photo_mail_html_says_photo_and_hides_album_name():
    body = _build_share_mail_html(
        url="https://example.invalid/share/tok",
        album_name="Geheimes Album 2019", sender_name="Anna",
        to_name=None, personal_message="",
        has_cover=True, has_logo=False, lang="de", is_photo=True,
    )
    assert "Foto" in body
    assert "Geheimes Album 2019" not in body, "Albumtitel gehoert nicht in die Foto-Mail"


def test_album_mail_still_names_the_album():
    """Gegenprobe: der Album-Fall ist unveraendert."""
    text = _build_share_mail_text(
        url="https://example.invalid/share/tok",
        album_name="Toskana 2019", sender_name="Anna",
        to_name=None, personal_message="", lang="de",
    )
    html_body = _build_share_mail_html(
        url="https://example.invalid/share/tok",
        album_name="Toskana 2019", sender_name="Anna",
        to_name=None, personal_message="",
        has_cover=True, has_logo=False, lang="de",
    )
    assert "Toskana 2019" in text
    assert "Toskana 2019" in html_body


def test_photo_mail_keys_exist_in_both_languages():
    from core.i18n import t_lang
    for lang in ("de", "en"):
        for key in ("mail.share_subject_photo", "mail.share_intro_photo",
                    "mail.share_open_label_photo", "mail.share_open_btn_photo",
                    "mail.share_photo_alt"):
            val = t_lang(key, lang)
            assert val and not val.startswith("mail."), f"{key} fehlt in {lang}"
