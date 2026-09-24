"""
core/qr_min.py — QR-Code erzeugen, nur mit der Standardbibliothek.

Warum selbst gebaut (11.09.2026): Aus dem SPK-Fremdnutzer-Durchlauf kam
der Befund, dass die schwaechste Stelle der Einrichtung nicht die
Installation ist, sondern der erste Start der App — die Serveradresse
muss auf dem Handy abgetippt werden (`http://192.168.1.109:8089`).
Ein QR-Code loest das, und zwar genau dann, wenn noch nichts eingerichtet
ist: **frisch installierte NAS, Handy im WLAN, oft ohne Internet.** Ein
CDN-Skript waere ausgerechnet in diesem Moment die falsche Wahl (siehe
ARCHITEKTUR.md: „Ohne Internet funktionieren Karten und Drag & Drop
nicht"). Eine neue Python-Abhaengigkeit fuer 250 Zeilen Mathematik
ebenfalls — dasselbe Argument wie bei `core/rsa_min.py`.

Umfang mit Absicht klein: **Byte-Modus, Fehlerkorrektur M, Versionen 1
bis 10** (bis 213 Zeichen). Das deckt jede Serveradresse ab; laengere
Texte lehnt der Encoder ab, statt still etwas Falsches zu liefern.
Fehlerkorrektur M statt L, weil der Code vom Bildschirm abfotografiert
wird — Glanz und Moire fressen Redundanz.

Geprueft wird in `tests/test_qr_min.py` **ohne Fremdbibliothek**: Der
Test dekodiert das Ergebnis zurueck und rechnet die Reed-Solomon-
Syndrome nach — damit stehen Kodierung, Maskierung, Platzierung,
Blockverschraenkung und Formatinformation fest. Waehrend der Entwicklung
zusaetzlich gegen `qrencode` gehalten: Die Matrizen sind identisch,
solange beide dieselbe Maske waehlen (bei 84 Stichproben 60 mal). Die
Abweichung liegt in der dritten Bewertungsregel, die der Standard am
Rand des Symbols mehrdeutig beschreibt; hier folgt sie der verbreiteten
Auslegung mit gedachtem hellen Rand. **Das ist eine Frage der
Lesbarkeit, nicht der Gueltigkeit** — jede der acht Masken ergibt einen
standardkonformen Code, und jeder Leser kommt mit beiden zurecht.
"""

from __future__ import annotations

from typing import List

# ── Tabellen aus ISO/IEC 18004, nur Level M, Versionen 1..10 ──────────
# (Datencodewoerter gesamt, EC-Codewoerter je Block, Bloecke Gruppe 1,
#  Datencodewoerter je Block Gruppe 1, Bloecke Gruppe 2, dito Gruppe 2)
_SPEC = {
    1:  (16,  10, 1, 16, 0, 0),
    2:  (28,  16, 1, 28, 0, 0),
    3:  (44,  26, 1, 44, 0, 0),
    4:  (64,  18, 2, 32, 0, 0),
    5:  (86,  24, 2, 43, 0, 0),
    6:  (108, 16, 4, 27, 0, 0),
    7:  (124, 18, 4, 31, 0, 0),
    8:  (154, 22, 2, 38, 2, 39),
    9:  (182, 22, 3, 36, 2, 37),
    10: (216, 26, 4, 43, 1, 44),
}

# Ausrichtungsmuster-Mitten je Version (Version 1 hat keine)
_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46],
    10: [6, 28, 50],
}

_ECC_LEVEL_BITS = 0b00          # Level M
_MAX_BYTES = 213                # Version 10, Level M, Byte-Modus


# ── Galois-Feld GF(256), Generator 0x11d ──────────────────────────────
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11d
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(n: int) -> List[int]:
    """Generatorpolynom fuer n Fehlerkorrektur-Codewoerter."""
    poly = [1]
    for i in range(n):
        poly = _poly_mul(poly, [1, _EXP[i]])
    return poly


def _poly_mul(a: List[int], b: List[int]) -> List[int]:
    out = [0] * (len(a) + len(b) - 1)
    for i, ai in enumerate(a):
        for j, bj in enumerate(b):
            out[i + j] ^= _gf_mul(ai, bj)
    return out


def _rs_ec(data: List[int], n: int) -> List[int]:
    """Fehlerkorrektur-Codewoerter zu einem Datenblock."""
    gen = _rs_generator(n)
    rest = list(data) + [0] * n
    for i in range(len(data)):
        factor = rest[i]
        if factor == 0:
            continue
        for j, g in enumerate(gen):
            rest[i + j] ^= _gf_mul(g, factor)
    return rest[len(data):]


# ── Bitstrom ──────────────────────────────────────────────────────────
class _Bits:
    def __init__(self) -> None:
        self.bits: List[int] = []

    def put(self, value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def __len__(self) -> int:
        return len(self.bits)


def _pick_version(n: int) -> int:
    for v in range(1, 11):
        # 4 Bit Modus + 8/16 Bit Laenge + Nutzdaten
        count_bits = 8 if v < 10 else 16
        if (4 + count_bits + n * 8) <= _SPEC[v][0] * 8:
            return v
    raise ValueError(f"Text zu lang fuer Version 10 (Level M): {n} Bytes, max {_MAX_BYTES}")


def _encode_data(text: str) -> tuple:
    raw = text.encode("utf-8")
    if len(raw) > _MAX_BYTES:
        raise ValueError(f"Text zu lang: {len(raw)} Bytes, max {_MAX_BYTES}")
    version = _pick_version(len(raw))
    total_data, ec_per_block, g1, g1_len, g2, g2_len = _SPEC[version]

    bits = _Bits()
    bits.put(0b0100, 4)                                   # Byte-Modus
    bits.put(len(raw), 8 if version < 10 else 16)
    for byte in raw:
        bits.put(byte, 8)

    capacity = total_data * 8
    bits.put(0, min(4, capacity - len(bits)))              # Abschluss
    while len(bits) % 8:
        bits.bits.append(0)
    pad = [0xEC, 0x11]
    i = 0
    while len(bits) < capacity:
        bits.put(pad[i % 2], 8)
        i += 1

    codewords = [int("".join(map(str, bits.bits[i:i + 8])), 2)
                 for i in range(0, len(bits.bits), 8)]

    # In Bloecke schneiden, EC je Block, dann verschraenkt zusammensetzen
    blocks, ecs, pos = [], [], 0
    for count, length in ((g1, g1_len), (g2, g2_len)):
        for _ in range(count):
            block = codewords[pos:pos + length]
            pos += length
            blocks.append(block)
            ecs.append(_rs_ec(block, ec_per_block))

    out: List[int] = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec_per_block):
        for e in ecs:
            out.append(e[i])
    return version, out


# ── Matrix ────────────────────────────────────────────────────────────
def _new_matrix(size: int):
    return [[None] * size for _ in range(size)]


def _place_function_patterns(m, version: int) -> None:
    size = len(m)

    def finder(r0: int, c0: int) -> None:
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if not (0 <= rr < size and 0 <= cc < size):
                    continue
                dark = (0 <= r <= 6 and c in (0, 6)) or (0 <= c <= 6 and r in (0, 6)) \
                    or (2 <= r <= 4 and 2 <= c <= 4)
                m[rr][cc] = 1 if dark else 0

    finder(0, 0); finder(0, size - 7); finder(size - 7, 0)

    for i in range(8, size - 8):                           # Taktmuster
        bit = 1 if i % 2 == 0 else 0
        m[6][i] = bit
        m[i][6] = bit

    centers = _ALIGN[version]
    for r in centers:
        for c in centers:
            if (r < 9 and c < 9) or (r < 9 and c > size - 10) or (r > size - 10 and c < 9):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    m[r + dr][c + dc] = 1 if (max(abs(dr), abs(dc)) != 1) else 0

    m[size - 8][8] = 1                                     # immer dunkel

    for i in range(9):                                     # Platz fuer Formatinfo
        if m[8][i] is None:
            m[8][i] = 0
        if m[i][8] is None:
            m[i][8] = 0
    for i in range(8):
        if m[8][size - 1 - i] is None:
            m[8][size - 1 - i] = 0
        if m[size - 1 - i][8] is None:
            m[size - 1 - i][8] = 0

    if version >= 7:                                       # Platz fuer Versionsinfo
        for i in range(6):
            for j in range(3):
                m[size - 11 + j][i] = 0
                m[i][size - 11 + j] = 0


def _reserved(version: int, size: int):
    """Maske der Funktionsfelder — dort liegen keine Daten."""
    r = _new_matrix(size)
    _place_function_patterns(r, version)
    return [[cell is not None for cell in row] for row in r]


def _place_data(m, reserved, codewords: List[int]) -> None:
    size = len(m)
    bits = []
    for cw in codewords:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)
    idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:                                       # Taktspalte ueberspringen
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if reserved[row][c]:
                    continue
                m[row][c] = bits[idx] if idx < len(bits) else 0
                idx += 1
        col -= 2
        upward = not upward


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _count_finder_like(history: List[int]) -> int:
    """Wie oft steckt im Lauflaengen-Fenster das Verhaeltnis 1:1:3:1:1 mit
    vier hellen Modulen an einer Seite? Zaehlt beide Seiten getrennt."""
    n = history[1]
    core = (n > 0 and history[2] == n and history[3] == n * 3
            and history[4] == n and history[5] == n)
    return ((1 if core and history[0] >= n * 4 and history[6] >= n else 0)
            + (1 if core and history[6] >= n * 4 and history[0] >= n else 0))


def _penalty(m) -> int:
    """Bewertung nach ISO/IEC 18004 — je kleiner, desto besser lesbar.

    Die dritte Regel (suchmusteraehnliche Streifen, 40 Punkte) ist die
    Falle: Der Standard zaehlt sie auch dort, wo die vier hellen Module
    teilweise AUSSERHALB des Symbols liegen — am Rand, neben den
    Suchmustern, also genau da, wo sie haeufig sind. Eine einfache Suche
    nach dem 11er-Fenster innerhalb der Matrix findet zu wenige und
    waehlt am Ende eine andere Maske als jeder andere Encoder. Deshalb
    das Lauflaengen-Fenster mit gedachtem hellen Rand."""
    size = len(m)
    score = 0

    for lines in (m, list(zip(*m))):
        for line in lines:
            # Start immer bei „hell, Laenge 0" — nicht bei der Farbe des
            # ersten Moduls. Die erste Spalte ist im QR immer dunkel; wer
            # mit ihr startet, laesst den gedachten hellen Rand davor weg
            # und zaehlt am linken Suchmuster ein Muster zu wenig.
            run_color, run_len = 0, 0
            history = [0] * 7
            for cell in line:
                if cell == run_color:
                    run_len += 1
                    if run_len == 5:
                        score += 3
                    elif run_len > 5:
                        score += 1
                else:
                    # Lauf abgeschlossen: ins Fenster schieben. Der erste
                    # Lauf bekommt den gedachten hellen Rand dazu.
                    add = run_len + (size if history[0] == 0 else 0)
                    history = [add] + history[:-1]
                    if not run_color:
                        score += _count_finder_like(history) * 40
                    run_color, run_len = cell, 1
            # Zeilenende: laufenden Lauf abschliessen, hellen Rand anhaengen
            if run_color:
                history = [run_len + (size if history[0] == 0 else 0)] + history[:-1]
                run_len = 0
            run_len += size
            history = [run_len + (size if history[0] == 0 else 0)] + history[:-1]
            score += _count_finder_like(history) * 40

    # Regel 2: gleichfarbige 2x2-Bloecke
    for r in range(size - 1):
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3

    # Regel 4: Abweichung vom halb-dunklen Bild, in Fuenferschritten
    dark = sum(sum(row) for row in m)
    total = size * size
    k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    score += k * 10
    return score


def _format_bits(mask: int) -> int:
    """15 Bit: 5 Datenbits (Level + Maske), 10 BCH-Bits, XOR mit der
    Standardmaske — sonst waere „Level M, Maske 0" ein leeres Feld."""
    data = (_ECC_LEVEL_BITS << 3) | mask
    rem = data << 10
    while rem.bit_length() > 10:
        rem ^= 0b10100110111 << (rem.bit_length() - 11)
    return ((data << 10) | rem) ^ 0b101010000010010


def _version_bits(version: int) -> int:
    """18 Bit: 6 Datenbits, 12 BCH-Bits. Erst ab Version 7 im Code."""
    rem = version << 12
    while rem.bit_length() > 12:
        rem ^= 0b1111100100101 << (rem.bit_length() - 13)
    return (version << 12) | rem


def _place_format(m, version: int, mask: int) -> None:
    """Formatinfo zweimal eintragen — und zwar MSB zuerst.

    Die Reihenfolge ist die Falle: Das 15-Bit-Wort wird mit dem
    HOECHSTWERTIGEN Bit an der ersten Position abgelegt (m[8][0] bzw.
    m[size-1][8]). Andersherum entsteht ein Code, den kein Leser
    annimmt — die Daten stimmen, nur der Kopf nicht. Gegen `qrencode`
    gemessen, nicht geraten."""
    size = len(m)
    bits = [(_format_bits(mask) >> (14 - i)) & 1 for i in range(15)]

    # Kopie 1, um das Suchmuster oben links
    for i in range(6):
        m[8][i] = bits[i]
    m[8][7] = bits[6]
    m[8][8] = bits[7]
    m[7][8] = bits[8]
    for i in range(9, 15):
        m[14 - i][8] = bits[i]

    # Kopie 2: SIEBEN senkrecht unten links (Bits 0..6), dann das immer
    # dunkle Modul, dann ACHT waagerecht rechts (Bits 7..14). Die
    # Aufteilung 7+8 ist leicht mit 8+7 zu verwechseln — dann fehlt genau
    # ein Modul bei (8, size-8), und der Code ist unlesbar.
    for i in range(7):
        m[size - 1 - i][8] = bits[i]
    m[size - 8][8] = 1                                     # bleibt dunkel
    for i in range(7, 15):
        m[8][size - 15 + i] = bits[i]

    if version >= 7:
        ver = _version_bits(version)
        for i in range(18):
            bit = (ver >> i) & 1
            m[i // 3][size - 11 + i % 3] = bit
            m[size - 11 + i % 3][i // 3] = bit


def matrix(text: str) -> List[List[int]]:
    """Modulmatrix: 1 = dunkel, 0 = hell. Ohne Ruhezone."""
    version, codewords = _encode_data(text)
    size = 17 + 4 * version
    reserved = _reserved(version, size)

    best, best_score = None, None
    for mask_id, mask_fn in enumerate(_MASKS):
        m = _new_matrix(size)
        _place_function_patterns(m, version)
        _place_data(m, reserved, codewords)
        for r in range(size):
            for c in range(size):
                if not reserved[r][c] and mask_fn(r, c):
                    m[r][c] ^= 1
        _place_format(m, version, mask_id)
        score = _penalty(m)
        if best_score is None or score < best_score:
            best, best_score = m, score
    return best


def svg(text: str, *, module: int = 8, quiet: int = 4, dark: str = "#111",
        light: str = "#fff", title: str = "") -> str:
    """QR-Code als SVG — ein Pfad fuer alle dunklen Module.

    Bewusst SVG und nicht PNG: scharf in jeder Groesse, ein paar Kilobyte,
    und Pillow muss kein Bild zeichnen."""
    m = matrix(text)
    size = len(m)
    total = (size + 2 * quiet) * module
    parts = []
    for r in range(size):
        for c in range(size):
            if m[r][c]:
                x = (c + quiet) * module
                y = (r + quiet) * module
                parts.append(f"M{x} {y}h{module}v{module}h-{module}z")
    path = "".join(parts)
    label = f"<title>{title}</title>" if title else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="{total}" '
        f'viewBox="0 0 {total} {total}" shape-rendering="crispEdges" role="img">'
        f'{label}<rect width="{total}" height="{total}" fill="{light}"/>'
        f'<path d="{path}" fill="{dark}"/></svg>'
    )
