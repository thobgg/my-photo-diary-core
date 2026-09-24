"""
core/thumb_pipeline.py

MPD v2 thumb pipeline: 3 sizes (thumb/cover/preview) as WebP + BlurHash.
Cache lives in config.THUMB_CACHE_DIR (outside the photo trees).

Cache key = sha1(absolute_path + mtime_ns) → invalidates automatically on edit.
Layout    = {cache}/{size}/{hash[:2]}/{hash}.webp

Formats   : WebP (Q80 for thumb/cover, Q82 for preview).
HEIC      : via pillow-heif (iPhone photos).
BlurHash  : 4×3 components, from 100px downsample.
"""

import hashlib
import io
import logging
from pathlib import Path

from PIL import Image, ImageOps

# Pillows Default (~178 MP) wirft DecompressionBombError beim 200-MP-
# Panorama (shared/2025 - das Jahr/2025-10-03_14-10-12.jpg). Eigene
# Dateien authentifizierter Familienmitglieder — wir heben die Grenze
# bewusst an statt sie abzuschalten (Upload-Endpunkt existiert seit
# 09/2026, eine Obergrenze bleibt Verteidigungslinie). Wirkt
# prozessweit für alle PIL-Nutzer (exif.py, stats_scanner, photo_edit).
Image.MAX_IMAGE_PIXELS = 500_000_000
from pillow_heif import register_heif_opener
import blurhash
import numpy as np

from config import THUMB_CACHE_DIR

register_heif_opener()


def _pil_to_array(img):
    """PIL Image → numpy uint8 array (H, W, 3) for blurhash.encode."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    return np.array(img, dtype=np.uint8)

logger = logging.getLogger(__name__)

SIZES = {
    "thumb":   400,   # mobile grid, small lists
    "cover":   900,   # tablet grid, retina desktop
    "preview": 2048,  # lightbox, fullscreen
    "full":    4096,  # zoom view detail zoom — for crisp zoom-in
}
QUALITY = {
    "thumb":   80,
    "cover":   80,
    "preview": 82,
    "full":    85,
}
VALID_SIZES = frozenset(SIZES)


# ── Cache key ─────────────────────────────────────────────────────

def _cache_key(source: Path) -> str:
    """sha1(absolute_path + mtime_ns). Edits change mtime → key changes
    automatically → stale cache entries are simply orphaned, never served."""
    try:
        mtime = source.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return hashlib.sha1(f"{source.resolve()}:{mtime}".encode("utf-8")).hexdigest()


def etag_for(source: Path) -> str:
    """Öffentlicher Alias auf den Cache-Schlüssel — als HTTP-ETag auf
    /api/thumbnail und /api/photo/…/download genutzt (routers/media.py).
    Ein Edit ändert mtime → neuer ETag, Clients revalidieren korrekt."""
    return _cache_key(source)


def get_thumb_path(source: Path, size: str) -> Path:
    """Cache file path for one (source, size) pair. Two-letter prefix
    fans entries out across subdirectories so no single dir grows huge."""
    if size not in VALID_SIZES:
        raise ValueError(f"Unknown size '{size}'; available: {sorted(VALID_SIZES)}")
    key = _cache_key(source)
    return THUMB_CACHE_DIR / size / key[:2] / f"{key}.webp"


# ── Serve thumb (lazy) ────────────────────────────────────────────

def get_thumb(source: Path, size: str) -> bytes:
    """Serve thumb; generate lazily when cache is empty."""
    cache_file = get_thumb_path(source, size)
    if cache_file.is_file():
        try:
            return cache_file.read_bytes()
        except OSError:
            pass
    return _generate_single(source, size, cache_file)


def _generate_single(source: Path, size: str, cache_file: Path) -> bytes:
    """Generate one WebP thumb at the given size and write it to cache.
    EXIF-transpose first so portrait phones look correct, then LANCZOS
    downscale (best quality/cost ratio for photo content)."""
    target  = SIZES[size]
    quality = QUALITY[size]
    with Image.open(source) as img:
        # Image.draft: JPEG bereits beim DEKODIEREN grob auf Zielgröße
        # bringen (1/2..1/8-Scale im libjpeg) — senkt den RAM-Bedarf
        # riesiger Aufnahmen um Faktor 4-64. Für Nicht-JPEG ein No-Op.
        # Fix 03.09.2026: 200-MP-Panorama riss den Container per OOM in
        # eine Absturz-Schleife, seit MAX_IMAGE_PIXELS es zulässt.
        img.draft("RGB", (target * 2, target * 2))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        img.thumbnail((target, target), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=quality, method=4)
        data = buf.getvalue()
    _write_cache(cache_file, data)
    return data


# ── BlurHash ──────────────────────────────────────────────────────

def compute_photo_meta(source: Path) -> dict:
    """Resolution + BlurHash in one Pillow open.
    Returns: {"resolution": {"width": W, "height": H} | None,
              "blurhash":   "L6..." | None}

    Resolution is captured *before* the 100px downsample so we can preserve
    the real image dimensions; the 100px downsample only affects the BlurHash
    encode step (4x3 Frequenzanteile — mehr Pixel sind verschenkt)."""
    resolution = None
    bh = None
    try:
        with Image.open(source) as img:
            # Auflösung VOR dem draft aus dem Header nehmen (draft ändert
            # die dekodierte Größe); EXIF-Rotation 90°/270° tauscht W/H.
            w, h = img.size
            try:
                if img.getexif().get(0x0112, 1) in (5, 6, 7, 8):
                    w, h = h, w
            except Exception:
                pass
            resolution = {"width": w, "height": h}
            img.draft("RGB", (400, 400))  # RAM-Deckel, s. _generate_single
            transposed = ImageOps.exif_transpose(img)
            transposed.thumbnail((100, 100), Image.Resampling.BILINEAR)
            if transposed.mode != "RGB":
                transposed = transposed.convert("RGB")
            try:
                bh = blurhash.encode(_pil_to_array(transposed), 4, 3)
            except Exception as e:
                logger.warning("BlurHash error for %s: %s", source, e)
    except Exception as e:
        logger.warning("Photo meta error for %s: %s", source, e)
    return {"resolution": resolution, "blurhash": bh}


# ── Batch: all sizes in one Pillow open ───────────────────────────

def warm_thumbs(source: Path, sizes: tuple = None) -> list:
    """
    Generate thumb sizes for `source` if missing (default: all).
    Fast skip when all requested caches exist (no Pillow open).

    Progressive in-place downsize preview → cover → thumb for
    memory efficiency on the NAS.

    sizes (03.09.2026): Teilmenge wie ("thumb", "cover") — der
    Durchlauf-Warmer erzeugt nur, was die Timeline braucht; die
    großen Varianten (preview/full) entstehen on demand.

    Returns: list of present / newly generated sizes.
    """
    wanted = sizes or tuple(SIZES.keys())
    if all(get_thumb_path(source, s).is_file() for s in wanted):
        return list(wanted)

    generated = []
    try:
        with Image.open(source) as img:
            # draft: siehe _generate_single — RAM-Deckel für Riesen-JPEGs
            largest = max(SIZES[s2] for s2 in wanted)
            img.draft("RGB", (largest * 2, largest * 2))
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            for size_name, target in sorted(
                    ((k, SIZES[k]) for k in wanted), key=lambda kv: -kv[1]):
                cache_file = get_thumb_path(source, size_name)
                img.thumbnail((target, target), Image.Resampling.LANCZOS)
                if cache_file.is_file():
                    generated.append(size_name)
                    continue
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=QUALITY[size_name], method=4)
                if _write_cache(cache_file, buf.getvalue()):
                    generated.append(size_name)
    except Exception as e:
        logger.warning("warm_thumbs error for %s: %s", source, e)
    return generated


# ── Cache writer ──────────────────────────────────────────────────

def _write_cache(cache_file: Path, data: bytes) -> bool:
    """Persist a generated thumb. Cache failures are non-fatal — we still
    return the bytes upstream, the next request just regenerates."""
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(data)
        return True
    except OSError as e:
        logger.warning("Thumb cache write failed (%s): %s", cache_file, e)
        return False


# ── Video thumb (FFmpeg frame, JPEG) ──────────────────────────────

def get_video_thumb_path(source: Path) -> Path:
    """Cache path for FFmpeg frame of a video. Layout matches photo thumbs."""
    key = _cache_key(source)
    return THUMB_CACHE_DIR / "video" / key[:2] / f"{key}.jpg"
