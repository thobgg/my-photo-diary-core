"""
core/rsa_min.py — RSA-Signaturprüfung (und -Erzeugung) ohne Fremdpakete.

Warum von Hand: Das Container-Image hat kein `cryptography`/`PyNaCl`, und
neue Pip-Pakete hieße Image-Neubau (CLAUDE.md: Deploy ist Bind-Mount +
Restart, kein --build). RSA mit PKCS#1-v1_5-Padding braucht aber nur
Ganzzahl-Arithmetik und SHA-256 aus der Standardbibliothek:

    Verifikation:  pow(signatur, e, n)  ==  EMSA-PKCS1-v1_5(SHA256(daten))
    Signieren:     pow(em, d, n)                       (nur offline, Skript)

Die Sicherheitsbetrachtung ist bewusst nüchtern: Ein Selbsthoster kann
jede Lizenzprüfung im Quelltext aushebeln — die Signatur verhindert nicht
Manipulation des eigenen Servers, sondern dass beliebige Dritte gültige
Lizenzdateien ERZEUGEN können. Dafür reicht RSA-2048 + SHA-256 vollauf.

Kein Verschlüsseln, kein Padding-Oracle-Risiko: ausschließlich
deterministische Signaturprüfung mit konstantem Vergleich.
"""

import hashlib
import secrets

# EMSA-PKCS1-v1_5: DER-Prefix für SHA-256 (RFC 8017, Abschnitt 9.2)
_SHA256_DER_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")

PUBLIC_EXPONENT = 65537


def _emsa_pkcs1_v1_5(digest: bytes, em_len: int) -> bytes:
    """0x00 0x01 FF..FF 0x00 <DER> <digest> — deterministisch, RFC 8017."""
    t = _SHA256_DER_PREFIX + digest
    ps_len = em_len - len(t) - 3
    if ps_len < 8:
        raise ValueError("Modulus zu klein fuer SHA-256-Padding")
    return b"\x00\x01" + b"\xff" * ps_len + b"\x00" + t


def verify(data: bytes, signature: int, n: int, e: int = PUBLIC_EXPONENT) -> bool:
    """True, wenn `signature` eine gueltige Unterschrift ueber `data` ist."""
    em_len = (n.bit_length() + 7) // 8
    if not 0 <= signature < n:
        return False
    expected = _emsa_pkcs1_v1_5(hashlib.sha256(data).digest(), em_len)
    actual = pow(signature, e, n).to_bytes(em_len, "big")
    return secrets.compare_digest(actual, expected)


def sign(data: bytes, n: int, d: int) -> int:
    """Unterschrift erzeugen — nur fuers Offline-Skript (privater Schluessel)."""
    em_len = (n.bit_length() + 7) // 8
    em = int.from_bytes(_emsa_pkcs1_v1_5(hashlib.sha256(data).digest(), em_len), "big")
    return pow(em, d, n)


# ---------------------------------------------------------------------------
# Schluesselerzeugung (nur Offline-Skript; Miller-Rabin nach FIPS-Manier)
# ---------------------------------------------------------------------------

def _is_probable_prime(n: int, rounds: int = 40) -> bool:
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _random_prime(bits: int) -> int:
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(candidate):
            return candidate


def generate_keypair(bits: int = 2048) -> tuple[int, int]:
    """(n, d) fuer PUBLIC_EXPONENT — dauert offline ein paar Sekunden."""
    e = PUBLIC_EXPONENT
    while True:
        p = _random_prime(bits // 2)
        q = _random_prime(bits // 2)
        if p == q:
            continue
        n = p * q
        phi = (p - 1) * (q - 1)
        if phi % e == 0:
            continue
        d = pow(e, -1, phi)
        return n, d
