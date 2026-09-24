"""core/qr_min: QR-Codes ohne Fremdbibliothek (11.09.2026).

Der Test prueft sich selbst — er dekodiert zurueck und rechnet die
Fehlerkorrektur nach. Absicht: Er laeuft im Container, wo weder
`qrencode` noch ein Leser installiert ist. Was er nachweist:

  * Kodierung, Maskierung, Platzierung und Blockverschraenkung stimmen
    (Rueckweg liefert denselben Text),
  * die Formatinformation ist lesbar (die Maske wird daraus gewonnen),
  * die Reed-Solomon-Codewoerter stimmen (alle Syndrome null).

Was er NICHT prueft: ob die gewaehlte Maske dieselbe ist, die ein
anderer Encoder waehlen wuerde. Die Maskenwahl ist eine Optimierung der
Lesbarkeit, keine Frage der Gueltigkeit — jede der acht Masken ergibt
einen standardkonformen Code.
"""
import random
import string

import pytest

from core import qr_min


# ── Ein kleiner Leser, nur fuer diesen Test ───────────────────────────
def _read_format(m):
    """Maske aus der Formatinformation (Kopie 1) zurueckgewinnen."""
    bits = [m[8][i] for i in range(6)] + [m[8][7], m[8][8], m[7][8]] \
        + [m[14 - i][8] for i in range(9, 15)]
    value = sum(b << (14 - i) for i, b in enumerate(bits))
    for mask in range(8):
        if qr_min._format_bits(mask) == value:
            return mask
    raise AssertionError("Formatinformation gehoert zu keiner Maske")


def _syndromes_zero(block, ec):
    """Reed-Solomon: R(alpha^i) muss fuer alle Pruefstellen null sein."""
    full = list(block) + list(ec)
    for i in range(len(ec)):
        acc = 0
        for cw in full:
            acc = qr_min._gf_mul(acc, qr_min._EXP[i]) ^ cw
        if acc != 0:
            return False
    return True


def _decode(m):
    """Matrix → Text. Spiegelbild von qr_min.matrix()."""
    size = len(m)
    version = (size - 17) // 4
    mask = _read_format(m)
    reserved = qr_min._reserved(version, size)

    plain = [row[:] for row in m]
    mask_fn = qr_min._MASKS[mask]
    for r in range(size):
        for c in range(size):
            if not reserved[r][c] and mask_fn(r, c):
                plain[r][c] ^= 1

    bits, col, upward = [], size - 1, True
    while col > 0:
        if col == 6:
            col -= 1
        for row in (range(size - 1, -1, -1) if upward else range(size)):
            for c in (col, col - 1):
                if not reserved[row][c]:
                    bits.append(plain[row][c])
        col -= 2
        upward = not upward
    stream = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits) // 8 * 8, 8)]

    total_data, ec_per_block, g1, g1_len, g2, g2_len = qr_min._SPEC[version]
    lengths = [g1_len] * g1 + [g2_len] * g2
    blocks = [[] for _ in lengths]
    pos = 0
    for i in range(max(lengths)):
        for b, ln in enumerate(lengths):
            if i < ln:
                blocks[b].append(stream[pos]); pos += 1
    ecs = [[] for _ in lengths]
    for i in range(ec_per_block):
        for b in range(len(lengths)):
            ecs[b].append(stream[pos]); pos += 1
    for block, ec in zip(blocks, ecs):
        assert _syndromes_zero(block, ec), "Fehlerkorrektur stimmt nicht"

    data = [cw for block in blocks for cw in block]
    dbits = []
    for cw in data:
        dbits.extend((cw >> i) & 1 for i in range(7, -1, -1))
    mode = int("".join(map(str, dbits[:4])), 2)
    assert mode == 0b0100, f"Byte-Modus erwartet, gelesen {mode:04b}"
    count_len = 8 if version < 10 else 16
    n = int("".join(map(str, dbits[4:4 + count_len])), 2)
    start = 4 + count_len
    raw = bytes(int("".join(map(str, dbits[start + 8 * i:start + 8 * i + 8])), 2) for i in range(n))
    return raw.decode("utf-8")


# ── Tests ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "A", "MPD", "http://192.168.1.109:8089", "https://mpd.example.org",
    "http://nas.local:8089", "Grüße aus München", "x" * 213,
])
def test_hin_und_zurueck(text):
    assert _decode(qr_min.matrix(text)) == text


def test_alle_versionen():
    """Je Version ein Text an der Kapazitaetsgrenze — deckt 1..10 ab."""
    grenzen = [14, 26, 42, 62, 84, 106, 122, 152, 180, 213]
    for i, n in enumerate(grenzen, start=1):
        text = "".join(string.ascii_lowercase[j % 26] for j in range(n))
        m = qr_min.matrix(text)
        assert len(m) == 17 + 4 * i, f"Version {i}: Groesse {len(m)}"
        assert _decode(m) == text


def test_zufallstexte():
    random.seed(11)
    ab = string.ascii_letters + string.digits + ":/.-_?=&%"
    for _ in range(60):
        text = "".join(random.choice(ab) for _ in range(random.randint(1, 213)))
        assert _decode(qr_min.matrix(text)) == text


def test_zu_lang_wird_abgelehnt():
    with pytest.raises(ValueError):
        qr_min.matrix("x" * 214)


def test_umlaute_als_utf8():
    """Ein Umlaut sind zwei Bytes — die Laenge zaehlt Bytes, nicht Zeichen."""
    text = "ö" * 106
    assert _decode(qr_min.matrix(text)) == text
    with pytest.raises(ValueError):
        qr_min.matrix("ö" * 107)


def test_feste_bereiche():
    """Suchmuster, Taktmuster und das immer dunkle Modul sitzen richtig."""
    m = qr_min.matrix("http://192.168.1.109:8089")
    size = len(m)
    for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):
        assert m[r0 + 0][c0 + 0] == 1 and m[r0 + 3][c0 + 3] == 1
        assert m[r0 + 1][c0 + 1] == 0
    for i in range(8, size - 8):
        assert m[6][i] == (1 if i % 2 == 0 else 0)
        assert m[i][6] == (1 if i % 2 == 0 else 0)
    assert m[size - 8][8] == 1


def test_svg_ist_wohlgeformt():
    import xml.etree.ElementTree as ET
    out = qr_min.svg("http://192.168.1.109:8089", module=6, quiet=4, title="MPD")
    root = ET.fromstring(out)
    assert root.tag.endswith("svg")
    size = len(qr_min.matrix("http://192.168.1.109:8089"))
    assert root.get("width") == str((size + 8) * 6)
    assert "<path" in out and "MPD" in out
