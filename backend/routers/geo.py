"""
routers/geo.py — General GPS endpoints (currently: reverse-geocoding for lightbox topbar)

GET /api/geo/reverse?lat=…&lon=…  →  {"name": "Hamburg" | null}
"""

import asyncio
import logging

from fastapi import APIRouter, Query, Request

from core.i18n import lang_from_request
from geo_reverse import reverse_city
from routers.deps import require_session

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/geo/reverse")
async def reverse(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
):
    """
    City-level reverse geocoding for a single coordinate (lightbox topbar).
    Auth-required to prevent open-internet abuse.
    Backed by Nominatim — no key needed, FS-cached.
    """
    require_session(request)

    lang = lang_from_request(request)
    name = await asyncio.to_thread(reverse_city, lat, lon, None, lang)
    return {"name": name}
