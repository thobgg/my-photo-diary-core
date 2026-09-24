"""
core/http_range.py
HTTP-Range-Unterstützung für Datei-Endpunkte (additiv, September 2026).

Starlette 0.35 (Begleiter von FastAPI 0.109) beantwortet Range-Header in
FileResponse noch nicht — verifiziert am 01.09.2026: GET mit
`Range: bytes=0-99` lieferte 200 mit vollem Body. Native Player
(ExoPlayer/MediaPlayer) brauchen für Seeking aber 206 + Content-Range.

`ranged_file_response()` ersetzt FileResponse als Drop-in:

- **Ohne Range-Header** wird exakt das bisherige FileResponse
  zurückgegeben — Web-Frontend und WebView-Wrapper sehen keinerlei
  Unterschied.
- **Mit Range-Header** kommt eine 206-Teilantwort mit Content-Range,
  Accept-Ranges und korrektem Content-Length.
- Syntaktisch kaputte Range-Header werden per RFC 9110 ignoriert (volle
  200-Antwort); nicht erfüllbare Bereiche geben 416 mit
  `Content-Range: bytes */<size>`.
- Mehrfachbereiche (`bytes=0-1,5-9`) beantworten wir nicht mit
  multipart/byteranges, sondern mit der vollen 200-Antwort — kein
  relevanter Client fordert das an.
"""

import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import FileResponse, Response, StreamingResponse

_CHUNK_SIZE = 1024 * 1024  # 1 MB pro Lese-Happen

# Genau ein Bereich: "bytes=start-end", "bytes=start-" oder "bytes=-suffix"
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _iter_file_range(path: Path, start: int, length: int):
    """Synchroner Generator — Starlette iteriert ihn im Threadpool."""
    with open(path, "rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def ranged_file_response(
    request   : Request,
    path      : Path,
    media_type: Optional[str] = None,
    headers   : Optional[dict] = None,
    filename  : Optional[str] = None,
) -> Response:
    """Drop-in für FileResponse mit Range-Unterstützung (Doku im Modulkopf)."""
    range_header = request.headers.get("range")
    file_size    = path.stat().st_size

    def _full_response() -> FileResponse:
        return FileResponse(
            path       = str(path),
            media_type = media_type,
            headers    = headers,
            filename   = filename,
        )

    if not range_header:
        return _full_response()

    m = _RANGE_RE.match(range_header.strip())
    if not m or (not m.group(1) and not m.group(2)):
        # Syntaktisch unbrauchbar → Range ignorieren (RFC 9110 §14.2)
        return _full_response()

    if m.group(1):
        start = int(m.group(1))
        end   = int(m.group(2)) if m.group(2) else file_size - 1
    else:
        # Suffix-Range "bytes=-N": die letzten N Bytes
        suffix = int(m.group(2))
        if suffix == 0:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        start = max(file_size - suffix, 0)
        end   = file_size - 1

    if start >= file_size or start > end:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    end    = min(end, file_size - 1)
    length = end - start + 1

    resp_headers = dict(headers or {})
    resp_headers.update({
        "Content-Range" : f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges" : "bytes",
        "Content-Length": str(length),
    })
    if filename:
        resp_headers.setdefault(
            "Content-Disposition",
            f"attachment; filename*=utf-8''{quote(filename)}",
        )

    return StreamingResponse(
        _iter_file_range(path, start, length),
        status_code = 206,
        media_type  = media_type or "application/octet-stream",
        headers     = resp_headers,
    )
