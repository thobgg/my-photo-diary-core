"""
core/photo_edit.py
Photo editor pipeline — rolling backups, Pillow transforms, atomic write,
rename with album.json update.

Destructive edits:
  - Before every save the current file is backed up in a rolling chain:
    <name>.bak (newest) … <name>.bak{CAP-1} (oldest)
  - Pillow processes in a single pass, atomic rename over the original
  - MPD thumb cache: stale cache entries are deleted; new ones are
    generated automatically on the next request (cache key includes mtime).

Rename:
  - After rename, all .bak sibling files move along.
  - album.json is updated (elements[*].file and meta.thumbnail).
"""

import json
import logging
import math
import os
import shutil
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, ImageEnhance, ImageOps

from core import exif as _exif
from core import thumb_pipeline

logger = logging.getLogger(__name__)

JPEG_QUALITY = 92
BACKUP_CAP   = 5   # .bak, .bak1, .bak2, .bak3, .bak4   (5 versions rolling)


# ---------------------------------------------------------------------------
# Backup (rolling: <name>.bak, <name>.bak1 … <name>.bak{CAP-1})
# ---------------------------------------------------------------------------

def _backup_name(photo_path: Path, idx: int) -> Path:
    """Path of the idx-th backup slot. idx=0 is `<file>.bak` (newest),
    idx>0 is `<file>.bakN`. Keeps file extensions visible to file managers."""
    suffix = ".bak" if idx == 0 else f".bak{idx}"
    return photo_path.with_name(photo_path.name + suffix)


def backup_original(photo_path: Path) -> Path:
    """
    Rolling backup before each save:
      oldest (.bak{CAP-1}) is dropped, all roll one step further,
      current file becomes .bak (newest undo step).
    """
    oldest = _backup_name(photo_path, BACKUP_CAP - 1)
    if oldest.is_file():
        oldest.unlink()

    for i in range(BACKUP_CAP - 2, -1, -1):
        src = _backup_name(photo_path, i)
        dst = _backup_name(photo_path, i + 1)
        if src.is_file():
            src.rename(dst)

    backup_path = _backup_name(photo_path, 0)
    shutil.copy2(photo_path, backup_path)
    return backup_path


def latest_backup(photo_path: Path) -> Optional[Path]:
    """Newest backup slot (`<file>.bak`) if it exists. Used by the reset
    button to find the immediate undo target."""
    backup_path = _backup_name(photo_path, 0)
    return backup_path if backup_path.is_file() else None


def restore_latest_backup(photo_path: Path) -> Optional[Path]:
    """
    Reset: .bak → original (one step back). Backup chain stays intact,
    so further resets are possible.
    """
    backup = latest_backup(photo_path)
    if backup is None:
        return None
    shutil.copy2(backup, photo_path)
    return backup


# ---------------------------------------------------------------------------
# Pillow pipeline (all transforms in one pass, single re-encode)
# ---------------------------------------------------------------------------

_ORIENTATION_TAG = 0x0112


def apply_transforms(src_path: Path, params: dict) -> bytes:
    """
    Read src_path, apply all transforms, return JPEG bytes.
    EXIF is preserved, orientation tag reset to 1 (since the
    EXIF rotation is already baked into the pixels by exif_transpose).

    Order:
      exif_transpose → rotate (90° steps) → flip_h → flip_v
      → straighten + auto-crop → user crop → brightness → contrast → saturation
    """
    img = Image.open(src_path)

    img  = ImageOps.exif_transpose(img)
    exif = img.getexif()
    if _ORIENTATION_TAG in exif:
        exif[_ORIENTATION_TAG] = 1

    # Frontend rotation: positive degrees = clockwise (CSS convention),
    # PIL.rotate rotates counter-clockwise → flip the sign.
    rotation = int(params.get("rotation", 0) or 0)
    rotation = ((rotation % 360) + 360) % 360
    if rotation:
        img = img.rotate(-rotation, expand=True)

    if params.get("flip"):
        img = ImageOps.mirror(img)
    if params.get("flip_v"):
        img = ImageOps.flip(img)

    straighten = float(params.get("straighten", 0) or 0)
    if abs(straighten) > 0.01:
        img  = img.rotate(-straighten, expand=False, resample=Image.BICUBIC)
        w, h = img.size
        wr, hr = _largest_inscribed_rect(w, h, math.radians(straighten))
        wr, hr = int(wr), int(hr)
        if 0 < wr < w and 0 < hr < h:
            left = (w - wr) // 2
            top  = (h - hr) // 2
            img  = img.crop((left, top, left + wr, top + hr))

    crop = params.get("crop")
    if isinstance(crop, dict) and all(k in crop for k in ("x", "y", "w", "h")):
        w, h = img.size
        x  = max(0.0, min(1.0, float(crop["x"])))
        y  = max(0.0, min(1.0, float(crop["y"])))
        cw = max(0.01, min(1.0 - x, float(crop["w"])))
        ch = max(0.01, min(1.0 - y, float(crop["h"])))
        img = img.crop((
            int(x * w), int(y * h),
            int((x + cw) * w), int((y + ch) * h),
        ))

    # ┌─ ACHTUNG: Diese drei Zeilen haben eine ZWEITE Umsetzung ─────────┐
    # │ Die native App (de.bgghome.mpd) rechnet Tonwerte fuer Aufnahmen  │
    # │ am Geraet selbst, BEVOR sie hochlaedt — und hat dafuer Pillows   │
    # │ Rechnung Zeile fuer Zeile nachgebaut (Kotlin, 15 Tests gegen     │
    # │ diese Formeln): Kontrast um den Bildmittelwert, 601-Gewichte     │
    # │ fuer die Graustufe, abschneiden statt runden. Stand 07.09.2026.  │
    # │                                                                  │
    # │ Wer hier die REIHENFOLGE (Helligkeit → Kontrast → Saettigung),   │
    # │ die Faktorformel (1 + wert/100) oder die Pillow-Klassen aendert, │
    # │ muss es in docs/API_CHANGES.md eintragen. Sonst rechnen zwei     │
    # │ verschieden — und das sieht man einem Foto nicht an: Es ist       │
    # │ nicht kaputt, es ist nur anders. Kein Test hier faengt das,       │
    # │ weil beide Seiten je fuer sich richtig bleiben.                   │
    # │                                                                  │
    # │ Aenderungen gehoeren nach docs/API_CHANGES.md, auch wenn sich    │
    # │ kein Feld aendert — der Vertrag ist hier die Rechnung.           │
    # └──────────────────────────────────────────────────────────────────┘
    brightness = int(params.get("brightness", 0) or 0)
    contrast   = int(params.get("contrast",   0) or 0)
    saturation = int(params.get("saturation", 0) or 0)
    if brightness:
        img = ImageEnhance.Brightness(img).enhance(1 + brightness / 100)
    if contrast:
        img = ImageEnhance.Contrast(img).enhance(1 + contrast / 100)
    if saturation:
        img = ImageEnhance.Color(img).enhance(1 + saturation / 100)

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    buf = BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True, exif=exif.tobytes())
    return buf.getvalue()


def _largest_inscribed_rect(w: int, h: int, angle_rad: float) -> tuple[float, float]:
    """
    Largest axis-aligned rectangle inside a w×h rectangle rotated by angle_rad.
    Formula: stackoverflow.com/q/16702966
    """
    angle_rad = abs(angle_rad) % math.pi
    if angle_rad > math.pi / 2:
        angle_rad = math.pi - angle_rad
    if angle_rad < 1e-10:
        return float(w), float(h)

    side_long  = max(w, h)
    side_short = min(w, h)
    sin_a, cos_a = math.sin(angle_rad), math.cos(angle_rad)

    if side_short <= 2 * sin_a * cos_a * side_long or abs(sin_a - cos_a) < 1e-10:
        x  = 0.5 * side_short
        wr = x / sin_a if w >= h else x / cos_a
        hr = x / cos_a if w >= h else x / sin_a
    else:
        cos_2a = cos_a * cos_a - sin_a * sin_a
        wr = (w * cos_a - h * sin_a) / cos_2a
        hr = (h * cos_a - w * sin_a) / cos_2a
    return wr, hr


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def write_atomic(target: Path, data: bytes) -> None:
    """Write bytes atomically: temp file in same folder, then os.replace."""
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.stem}.tmp.", suffix=target.suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


# ---------------------------------------------------------------------------
# Thumb-cache invalidation (v2 pipeline)
# ---------------------------------------------------------------------------

def _old_thumb_paths(photo_path: Path) -> list[Path]:
    """Cache paths before save — based on current mtime."""
    return [thumb_pipeline.get_thumb_path(photo_path, size)
            for size in thumb_pipeline.VALID_SIZES]


def _prune_paths(paths: list[Path]) -> None:
    for p in paths:
        if p.is_file():
            try:
                p.unlink()
            except OSError as e:
                logger.warning("Thumb cache prune failed (%s): %s", p, e)


# ---------------------------------------------------------------------------
# High-level orchestration (top-level for routers)
# ---------------------------------------------------------------------------

def save_edit(base: Path, album_name: str, filename: str, params: dict) -> dict:
    """
    Complete save path: rolling backup → Pillow transform → atomic write
    → delete stale thumb-cache entries.

    Return: {"backup": "<backup-filename>"}
    """
    photo_path = base / album_name / filename
    if not photo_path.is_file():
        raise FileNotFoundError(f"Photo not found: {photo_path}")

    stale_thumbs = _old_thumb_paths(photo_path)
    backup_path  = backup_original(photo_path)
    edited_bytes = apply_transforms(photo_path, params)
    write_atomic(photo_path, edited_bytes)
    _prune_paths(stale_thumbs)

    # Pixel ops (rotate 90/270, crop, straighten) change dimensions.
    # Recompute and persist resolution + blurhash so the album grid
    # renders with the correct aspect ratio immediately.
    new_meta = thumb_pipeline.compute_photo_meta(photo_path)
    album_json_updated = _update_album_json_photo_meta(
        photo_path.parent, filename,
        new_meta.get("resolution"), new_meta.get("blurhash"),
    )

    return {"backup": backup_path.name, "album_json_updated": album_json_updated}


def reset_to_original(base: Path, album_name: str, filename: str) -> dict:
    """
    Reset: newest backup (.bak) → original path, delete stale thumb entries.
    The backup chain stays intact so further resets are possible.

    Return: {"restored_from": "<backup-filename>"}
    """
    photo_path = base / album_name / filename
    stale_thumbs = _old_thumb_paths(photo_path)
    restored     = restore_latest_backup(photo_path)
    if restored is None:
        raise FileNotFoundError(f"No backup available for {filename}")

    _prune_paths(stale_thumbs)

    return {"restored_from": restored.name}


# ---------------------------------------------------------------------------
# Rename (file + .bak siblings + album.json)
# ---------------------------------------------------------------------------

def _sanitize_stem(raw: str) -> str:
    """Filename only, no path segments. Leading/trailing dots removed."""
    s = (raw or "").strip().replace("\x00", "")
    for ch in ("/", "\\"):
        s = s.replace(ch, "")
    s = s.strip(".")
    return s


def compute_rename_target(album_dir: Path, old_name: str, new_stem: str) -> tuple[str, bool]:
    """
    Returns (final_filename, collision).
      - Extension is preserved (from old_name)
      - On name collision _1, _2 … is appended until free
      - If final_name == old_name: no collision, no suffix
    """
    stem = _sanitize_stem(new_stem)
    if not stem:
        raise ValueError("Invalid filename")
    ext = Path(old_name).suffix
    candidate = stem + ext
    if candidate == old_name:
        return candidate, False
    if not (album_dir / candidate).exists():
        return candidate, False
    i = 1
    while (album_dir / f"{stem}_{i}{ext}").exists():
        i += 1
    return f"{stem}_{i}{ext}", True


def _update_album_json_photo_meta(album_dir: Path, filename: str,
                                  resolution: dict | None, blurhash: str | None) -> bool:
    """Update a photo element's resolution + blurhash in album.json. Returns: changed?

    Why: pixel edits (rotate 90/270, crop, straighten) change the on-disk
    dimensions. Without this update the album grid keeps the pre-edit
    aspect ratio until something else triggers a rebuild.
    """
    album_json = album_dir / "album.json"
    if not album_json.is_file():
        return False
    try:
        with open(album_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("album.json not readable for edit update (%s): %s", album_json, e)
        return False

    changed = False
    for elem in data.get("elements", []):
        if elem.get("type") != "photo" or elem.get("file") != filename:
            continue
        if resolution and elem.get("resolution") != resolution:
            elem["resolution"] = resolution
            changed = True
        if blurhash and elem.get("blurhash") != blurhash:
            elem["blurhash"] = blurhash
            changed = True

    if not changed:
        return False
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    write_atomic(album_json, payload)
    return True


def _reposition_photo_by_name(elements: list, filename: str) -> bool:
    """Move the photo element `filename` to its chronological slot among photos.

    Albums are largely chronological (filenames are ISO date stamps, so a
    lexicographic compare equals chronological order). After a rename — e.g. a
    photo that carried a wrong year and therefore sat in the wrong place — the
    photo belongs elsewhere. We pull it out and re-insert it directly before the
    first photo whose filename sorts after it. Non-photo elements (text,
    separator, map) keep their order; only the photo slots into place.

    Returns: moved?
    """
    idx = next((i for i, e in enumerate(elements)
                if e.get("type") == "photo" and e.get("file") == filename), None)
    if idx is None:
        return False
    elem = elements[idx]
    rest = elements[:idx] + elements[idx + 1:]

    later = next((i for i, e in enumerate(rest)
                  if e.get("type") == "photo" and (e.get("file") or "") > filename), None)
    if later is not None:
        insert_at = later
    else:
        last_photo = max((i for i, e in enumerate(rest) if e.get("type") == "photo"),
                         default=-1)
        insert_at = last_photo + 1

    new_list = rest[:insert_at] + [elem] + rest[insert_at:]
    if new_list == elements:
        return False
    elements[:] = new_list
    return True


def _update_album_json_for_rename(album_dir: Path, old_name: str, new_name: str) -> bool:
    """Update album.json: elements[*].file, meta.thumbnail, and re-sort the
    renamed photo into its chronological position. Returns: changed?"""
    album_json = album_dir / "album.json"
    if not album_json.is_file():
        return False
    try:
        with open(album_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("album.json not readable for rename update (%s): %s", album_json, e)
        return False

    changed = False
    for elem in data.get("elements", []):
        if elem.get("file") == old_name:
            elem["file"] = new_name
            changed = True
    meta = data.get("meta") or {}
    if meta.get("thumbnail") == old_name:
        meta["thumbnail"] = new_name
        changed = True

    # Chronological reposition of the renamed photo (filenames are ISO dates).
    if _reposition_photo_by_name(data.get("elements", []), new_name):
        changed = True

    if not changed:
        return False

    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    write_atomic(album_json, payload)
    return True


def rename_photo(base: Path, album_name: str, old_name: str, new_stem: str) -> dict:
    """
    Full rename: file + all .bak siblings + thumb-cache invalidation
    + album.json update.

    Return: {"renamed": "<final_name>" | None, "collision": bool,
             "album_json_updated": bool}
    """
    album_dir = base / album_name
    old_path  = album_dir / old_name
    if not old_path.is_file():
        raise FileNotFoundError(f"Photo not found: {old_path}")

    final_name, collision = compute_rename_target(album_dir, old_name, new_stem)
    if final_name == old_name:
        return {"renamed": None, "collision": False, "album_json_updated": False}

    new_path     = album_dir / final_name
    stale_thumbs = _old_thumb_paths(old_path)

    old_path.rename(new_path)

    # Move .bak siblings along (<old>.bak, <old>.bak1 … <old>.bak{CAP-1})
    for i in range(BACKUP_CAP):
        suffix = ".bak" if i == 0 else f".bak{i}"
        bak = album_dir / f"{old_name}{suffix}"
        if bak.is_file():
            bak.rename(album_dir / f"{final_name}{suffix}")

    _prune_paths(stale_thumbs)

    try:
        album_updated = _update_album_json_for_rename(album_dir, old_name, final_name)
    except Exception:
        logger.exception("album.json update after rename failed")
        album_updated = False

    return {"renamed": final_name, "collision": collision, "album_json_updated": album_updated}


# ---------------------------------------------------------------------------
# EXIF write (metadata only, no pixel change — via piexif)
# ---------------------------------------------------------------------------

_JPEG_EXTS = {".jpg", ".jpeg"}


def _dec_to_gps_rational(decimal: float) -> tuple:
    """Decimal degrees → ((deg,1),(min,1),(sec*10000,10000)) for EXIF GPS."""
    decimal = abs(float(decimal))
    d = int(decimal)
    m_full = (decimal - d) * 60
    m = int(m_full)
    s = (m_full - m) * 60
    return ((d, 1), (m, 1), (int(round(s * 10000)), 10000))


def _normalize_datetime_for_exif(dt_str: str) -> str:
    """Frontend sends 'YYYY-MM-DDTHH:MM:SS' → EXIF wants 'YYYY:MM:DD HH:MM:SS'."""
    s = dt_str.strip()
    if "T" in s:
        date_part, time_part = s.split("T", 1)
    elif " " in s:
        date_part, time_part = s.split(" ", 1)
    else:
        raise ValueError(f"Unexpected date format: {dt_str}")
    date_part = date_part.replace("-", ":")
    if len(time_part) == 5:          # "14:30" → "14:30:00"
        time_part = time_part + ":00"
    return f"{date_part} {time_part}"


def save_metadata(base: Path, album_name: str, filename: str, meta: dict) -> dict:
    """
    Writes DateTimeOriginal and/or GPS into the EXIF data of the JPEG file.
    Metadata-only via piexif — JPEG pixels remain byte-identical.
    Rolling backup before write. Optional rename afterwards (meta.new_stem).

    meta: {
      "datetime": "YYYY-MM-DDTHH:MM:SS" | None,
      "gps":      {"lat": float, "lon": float, "alt"?: float} | None,
      "new_stem": str | None,
    }

    Return: {"backup": "<name>.bak", "renamed": "<new_name>" | None, ...}
    """
    import piexif

    photo_path = base / album_name / filename
    if not photo_path.is_file():
        raise FileNotFoundError(f"Photo not found: {photo_path}")
    if photo_path.suffix.lower() not in _JPEG_EXTS:
        raise ValueError(f"EXIF write only supported for JPEG (not {photo_path.suffix})")

    dt       = meta.get("datetime")
    gps      = meta.get("gps") or {}
    new_stem = (meta.get("new_stem") or "").strip()

    has_dt  = bool(dt)
    has_gps = "lat" in gps and "lon" in gps
    if not (has_dt or has_gps or new_stem):
        return {"backup": None, "renamed": None, "wrote_exif": False}

    stale_thumbs = _old_thumb_paths(photo_path)
    backup_path  = backup_original(photo_path) if (has_dt or has_gps) else None

    if has_dt or has_gps:
        exif_dict = piexif.load(str(photo_path))

        if has_dt:
            exif_val = _normalize_datetime_for_exif(dt).encode("utf-8")
            exif_dict.setdefault("Exif", {})
            exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal]  = exif_val
            exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = exif_val
            exif_dict.setdefault("0th", {})
            exif_dict["0th"][piexif.ImageIFD.DateTime] = exif_val

        if has_gps:
            lat = float(gps["lat"])
            lon = float(gps["lon"])
            gps_ifd = exif_dict.setdefault("GPS", {})
            gps_ifd[piexif.GPSIFD.GPSLatitudeRef]  = b"N" if lat >= 0 else b"S"
            gps_ifd[piexif.GPSIFD.GPSLatitude]     = _dec_to_gps_rational(lat)
            gps_ifd[piexif.GPSIFD.GPSLongitudeRef] = b"E" if lon >= 0 else b"W"
            gps_ifd[piexif.GPSIFD.GPSLongitude]    = _dec_to_gps_rational(lon)
            if "alt" in gps and gps["alt"] is not None:
                alt = float(gps["alt"])
                gps_ifd[piexif.GPSIFD.GPSAltitudeRef] = 0 if alt >= 0 else 1
                gps_ifd[piexif.GPSIFD.GPSAltitude]    = (int(round(abs(alt) * 1000)), 1000)

        exif_bytes = piexif.dump(exif_dict)

        # Atomic: copy into temp file, inject EXIF there, then replace.
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=photo_path.parent, prefix=f".{photo_path.stem}.exif.tmp.", suffix=photo_path.suffix,
        )
        os.close(tmp_fd)
        try:
            shutil.copy2(photo_path, tmp_path)
            piexif.insert(exif_bytes, tmp_path)
            os.replace(tmp_path, photo_path)
        except Exception:
            if os.path.exists(tmp_path):
                try: os.unlink(tmp_path)
                except OSError: pass
            raise

        _prune_paths(stale_thumbs)
        _exif.invalidate_exif_cache(photo_path.parent, photo_path.name)

    result = {
        "backup"    : backup_path.name if backup_path else None,
        "wrote_exif": has_dt or has_gps,
        "renamed"   : None,
    }

    if new_stem:
        current_stem = Path(filename).stem
        if new_stem != current_stem:
            rn = rename_photo(base, album_name, filename, new_stem)
            result["renamed"] = rn.get("renamed")
            result["collision"] = rn.get("collision", False)

    return result
