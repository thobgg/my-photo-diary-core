"""
core/library_warmer.py

Background warmer: walks once through all albums of a space after
startup and ensures that
  1. album.json contains `resolution` + `blurhash` for every photo,
  2. all three thumb sizes (thumb/cover/preview) are in the cache.

Design:
  * Within an album: all photo tasks via `asyncio.gather()` →
    the default thread pool (8 slots) caps automatically; on a 4-core
    NAS ~4 Pillow ops in parallel max out the CPU.
  * Across albums: sequential — clear per-album progress messages.
  * Across spaces: all spaces run in parallel (via `asyncio.gather` in
    lifespan) — share the same thread pool, stay capped.
"""

import asyncio
import json
import logging
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor
from functools import partial

from core.media_types import PHOTO_EXTS
from core.thumb_pipeline import compute_photo_meta, warm_thumbs, get_thumb_path, SIZES

# Eigener, kleiner Pool (03.09.2026): Der Warmer darf den default-Pool
# von asyncio.to_thread nicht sättigen — beim ersten Wärmen der
# Durchlauf-Ordner blockierte er sonst API-Aufrufe (Timeline-Index,
# Thumb-Erzeugung) minutenlang. 3 Threads deckeln zugleich die
# Pillow-RAM-Spitzen (CLAUDE.md: ~320 MB je 100-MP-Decode).
# Größe via MPD_WARMER_THREADS (Default 3) — SPK-Wizard kann auf
# 2-GB-Geräten 1-2 setzen (Kleine-NAS-Tauglichkeit, 03.09.2026).
from config import WARMER_ENABLED, WARMER_THREADS
_WARM_POOL = ThreadPoolExecutor(max_workers=WARMER_THREADS,
                                thread_name_prefix="warmer")


async def _in_warm_pool(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_WARM_POOL, partial(fn, *args))

logger = logging.getLogger(__name__)

_PROGRESS_EVERY = 10  # log progress every N albums


async def _warm_one_photo(album_dir: Path, elem: dict) -> bool:
    """Process one photo element: migrate meta + warm thumbs.
    Returns True if album.json became dirty (meta was filled in)."""
    filename = elem.get("file")
    if not filename:
        return False
    src = album_dir / filename
    if not src.is_file():
        return False

    dirty = False

    needs_meta = not elem.get("resolution") or not elem.get("blurhash")
    if needs_meta:
        meta = await _in_warm_pool(compute_photo_meta, src)
        if meta.get("resolution") and not elem.get("resolution"):
            elem["resolution"] = meta["resolution"]
            dirty = True
        if meta.get("blurhash") and not elem.get("blurhash"):
            elem["blurhash"] = meta["blurhash"]
            dirty = True

    if not all(get_thumb_path(src, s).is_file() for s in SIZES):
        await _in_warm_pool(warm_thumbs, src)

    return dirty


async def _warm_folder_thumbs(album_dir: Path) -> int:
    """Durchlauf-Ordner ohne album.json (Kuratier-Modell 09/2026): nur
    Thumbs wärmen, keine Meta-Migration — sonst hakelt der Zeitstrahl
    beim Scrollen über Unkuratiertes (jedes Thumb entstünde erst beim
    Anblick). Performance-Paket 03.09.2026."""
    photos = [
        f for f in album_dir.iterdir()
        if f.is_file() and not f.name.startswith(".")
        and f.suffix.lower() in PHOTO_EXTS
    ]
    # Nur thumb+cover (Timeline-Bedarf) — preview/full für unkuratierte
    # Massen wäre die 36-Minuten-Falle aus CLAUDE.md in neu (03.09.2026:
    # 46-min-Grind über 24 json-lose Shared-Ordner).
    _DURCHLAUF_SIZES = ("thumb", "cover")
    todo = [p for p in photos
            if not all(get_thumb_path(p, s).is_file() for s in _DURCHLAUF_SIZES)]
    if todo:
        await asyncio.gather(
            *(_in_warm_pool(partial(warm_thumbs, sizes=_DURCHLAUF_SIZES), p) for p in todo),
            return_exceptions=True,
        )
    return len(photos)


async def _warm_one_album(album_dir: Path) -> tuple[int, bool]:
    """Warm all photos of an album in parallel. Returns (photo_count, dirty)."""
    album_json = album_dir / "album.json"
    if not album_json.is_file():
        if album_dir.name.startswith((".", "@", "#")):
            return 0, False
        return await _warm_folder_thumbs(album_dir), False

    try:
        with open(album_json, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("Warmer: album.json broken in '%s': %s", album_dir.name, e)
        return 0, False

    elements = data.get("elements", [])
    photo_elems = [e for e in elements if e.get("type") == "photo"]
    if not photo_elems:
        return 0, False

    results = await asyncio.gather(
        *(_warm_one_photo(album_dir, e) for e in photo_elems),
        return_exceptions=True,
    )

    dirty = any(r is True for r in results)
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Warmer: photo task error in '%s': %s", album_dir.name, r)

    if dirty:
        from routers.albums import _write_atomic
        try:
            _write_atomic(album_json, data)
        except Exception as e:
            logger.warning("Warmer: write failed '%s': %s", album_dir.name, e)

    return len(photo_elems), dirty


async def warm_library(base: Path, label: str = "space") -> None:
    if not WARMER_ENABLED:
        logger.info("Warmer [%s]: abgeschaltet (MPD_WARMER=0)", label)
        return
    if not base.is_dir():
        logger.info("Warmer [%s]: path missing, skip: %s", label, base)
        return

    try:
        album_dirs = sorted(d for d in base.iterdir() if d.is_dir())
    except OSError as e:
        logger.warning("Warmer [%s]: iterdir failed %s: %s", label, base, e)
        return

    logger.info("Warmer [%s] started: %d folders in %s", label, len(album_dirs), base)

    n_albums  = 0
    n_photos  = 0
    n_dirty   = 0

    for idx, album_dir in enumerate(album_dirs, start=1):
        try:
            photos, dirty = await _warm_one_album(album_dir)
        except asyncio.CancelledError:
            logger.info("Warmer [%s] cancelled at %s", label, album_dir.name)
            raise
        except Exception as e:
            logger.warning("Warmer: '%s' skipped: %s", album_dir.name, e)
            continue

        if photos == 0:
            continue
        n_albums += 1
        n_photos += photos
        if dirty:
            n_dirty += 1

        if n_albums % _PROGRESS_EVERY == 0:
            logger.info(
                "Warmer [%s] progress: %d albums, %d photos, %d album JSONs filled in",
                label, n_albums, n_photos, n_dirty,
            )

    logger.info(
        "Warmer [%s] done: %d albums, %d photos, %d album JSONs filled in",
        label, n_albums, n_photos, n_dirty,
    )


def discover_personal_spaces(homes_root: Path = None) -> list[tuple[str, Path]]:
    """Finds all <HOMES_ROOT>/<user>/<PERSONAL_SUBDIR> directories that
    contain at least one album.json. Returns: list of (user, path).
    Defaults kommen aus config (MPD_HOMES_ROOT/MPD_PERSONAL_SUBDIR, SPK)."""
    from config import HOMES_ROOT, PERSONAL_SUBDIR
    if homes_root is None:
        homes_root = HOMES_ROOT
    if not homes_root.is_dir():
        return []
    found = []
    try:
        for user_home in sorted(homes_root.iterdir()):
            if not user_home.is_dir():
                continue
            photos = user_home / PERSONAL_SUBDIR
            if not photos.is_dir():
                continue
            # Has at least one album.json in the tree? Cheap check via iter.
            has_album = False
            try:
                for child in photos.iterdir():
                    if (child / "album.json").is_file():
                        has_album = True
                        break
            except OSError:
                continue
            if has_album:
                found.append((user_home.name, photos))
    except OSError as e:
        logger.warning("Warmer: homes scan failed: %s", e)
    return found
