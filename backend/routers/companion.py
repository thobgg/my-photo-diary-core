"""
routers/companion.py
Write Companion — multi-provider phrasing aid for text blocks.

Supported providers: anthropic, groq, mistral, gemini
Opt-in: active when companion_api_key is set in admin settings OR
        ANTHROPIC_API_KEY is set in mpd.env (backwards compat for anthropic).

GET  /api/companion/status   → {enabled, model, provider}
POST /api/companion/suggest  → {suggestions: [{label, text}, ...]}
"""
import difflib
import json
import logging

import requests
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from config import ANTHROPIC_API_KEY, COMPANION_MODEL
from core import settings as _settings
from core.i18n import t as _t
from routers.deps import require_session
from core import ratelimit

logger = logging.getLogger(__name__)
router = APIRouter()


class CompanionSuggestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)


_MAX_INPUT_CHARS = 4000
_TIMEOUT = 20

# Default model per provider. Anthropic uses COMPANION_MODEL from env instead.
_DEFAULT_MODELS = {
    "anthropic": None,           # resolved via COMPANION_MODEL env var
    "groq"     : "llama-3.3-70b-versatile",
    "mistral"  : "mistral-small-latest",
    "gemini"   : "gemini-2.0-flash-lite",
}

_SYSTEM_PROMPT = """Du bist ein behutsamer Schreib-Begleiter für ein privates Foto-Tagebuch.

Regeln:
- Der vom Nutzer geschriebene Text ist der Wahrheitskern. Erfinde NICHTS hinzu — keine neuen Orte, Namen, Gefühle, Details.
- Behalte die Stimme des Nutzers: Wenn er knapp schreibt, bleib knapp. Wenn er persönlich schreibt, bleib persönlich.
- Verbessere NUR: Grammatik, Rechtschreibung, Satzfluss, kleine Umstellungen.
- Kein Duzen/Siezen ändern. Keine Emojis hinzufügen, keine entfernen.
- Wenn der Text schon gut ist, mach nur minimale Änderungen.

Antworte AUSSCHLIESSLICH mit gültigem JSON in genau diesem Format:
{"suggestions": [
  {"label": "Sanft poliert", "text": "..."},
  {"label": "Wärmer",         "text": "..."},
  {"label": "Knapper",        "text": "..."}
]}

Kein Text vor oder nach dem JSON. Kein Markdown-Codeblock."""


def _resolve_key_and_provider() -> tuple[str, str]:
    """Return (api_key, provider). Empty key means companion is disabled."""
    provider = _settings.get("companion_provider") or "anthropic"
    key      = _settings.get("companion_api_key") or ""
    if not key and provider == "anthropic":
        key = ANTHROPIC_API_KEY  # env-var fallback for backwards compat
    return key, provider


def _resolve_model(provider: str) -> str:
    if provider == "anthropic":
        return COMPANION_MODEL
    return _DEFAULT_MODELS.get(provider, "")


def _call_anthropic(key: str, model: str, text: str) -> requests.Response:
    return requests.post(
        "https://api.anthropic.com/v1/messages",
        json={
            "model"     : model,
            "max_tokens": 1024,
            "system"    : _SYSTEM_PROMPT,
            "messages"  : [{"role": "user", "content": text}],
        },
        headers={
            "x-api-key"        : key,
            "anthropic-version": "2023-06-01",
            "content-type"     : "application/json",
        },
        timeout=_TIMEOUT,
    )


def _call_openai_compat(url: str, key: str, model: str, text: str) -> requests.Response:
    return requests.post(
        url,
        json={
            "model"     : model,
            "max_tokens": 1024,
            "messages"  : [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": text},
            ],
        },
        headers={
            "Authorization": f"Bearer {key}",
            "content-type" : "application/json",
        },
        timeout=_TIMEOUT,
    )


def _call_gemini(key: str, model: str, text: str) -> requests.Response:
    return requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        json={
            "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
            "contents"          : [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig"  : {"maxOutputTokens": 1024},
        },
        params ={"key": key},
        headers={"content-type": "application/json"},
        timeout=_TIMEOUT,
    )


_OPENAI_COMPAT_URLS = {
    "groq"   : "https://api.groq.com/openai/v1/chat/completions",
    "mistral": "https://api.mistral.ai/v1/chat/completions",
}


def _dispatch(provider: str, key: str, model: str, text: str) -> requests.Response:
    if provider == "anthropic":
        return _call_anthropic(key, model, text)
    if provider in _OPENAI_COMPAT_URLS:
        return _call_openai_compat(_OPENAI_COMPAT_URLS[provider], key, model, text)
    if provider == "gemini":
        return _call_gemini(key, model, text)
    raise ValueError(f"Unknown provider: {provider!r}")


def _extract_text(body: dict, provider: str) -> str:
    if provider == "anthropic":
        blocks = body.get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    if provider in ("groq", "mistral"):
        return body["choices"][0]["message"]["content"].strip()
    if provider == "gemini":
        return body["candidates"][0]["content"]["parts"][0]["text"].strip()
    raise ValueError(f"Unknown provider: {provider!r}")


@router.get("/api/companion/status")
async def companion_status(request: Request):
    require_session(request)
    try:
        key, provider = _resolve_key_and_provider()
        enabled = bool(key)
    except Exception as e:
        logger.error("Companion status error: %s", e)
        return {"enabled": False, "model": "", "provider": ""}
    logger.info("Companion status: enabled=%s provider=%s", enabled, provider)
    return {
        "enabled" : enabled,
        "model"   : _resolve_model(provider) if enabled else "",
        "provider": provider if enabled else "",
    }


# N5 (Audit 03.09.2026): der Endpunkt kostet API-Guthaben — einfacher
# Cooldown je Nutzer gegen Hämmern (3 s reichen für echte Tipparbeit).
import time as _time
_SUGGEST_COOLDOWN_S = 3.0
_last_suggest: dict = {}

# N5 (Audit 02.09.): Die 3-Sekunden-Sperre verhindert Salven, aber keinen
# Dauerlauf — sie erlaubt rechnerisch 1.200 Aufrufe je Stunde und Nutzer,
# jeder davon auf einen kostenpflichtigen API-Schluessel. Dafuer braucht es
# keinen Angreifer, eine haengende Wiederholschleife im Editor genuegt.
# Deshalb zusaetzlich ein Stundenkontingent je Nutzer.
_SUGGEST_PER_HOUR = 60
_SUGGEST_WINDOW_S = 3600


@router.post("/api/companion/suggest")
async def companion_suggest(request: Request, body: CompanionSuggestRequest):
    session = require_session(request)

    key, provider = _resolve_key_and_provider()
    if not key:
        raise HTTPException(503, _t("companion.disabled", request))

    if session.is_demo:
        raise HTTPException(403, _t("companion.demo_unavailable", request))

    now = _time.monotonic()
    if now - _last_suggest.get(session.user, 0.0) < _SUGGEST_COOLDOWN_S:
        raise HTTPException(429, _t("companion.rate_limited", request))
    _last_suggest[session.user] = now

    # Stundenkontingent. Bewusst NACH der Sekundensperre: wer im
    # Sekundentakt haemmert, soll gar nicht erst Kontingent verbrauchen.
    if not ratelimit.allow(f"companion:{session.user}",
                           limit=_SUGGEST_PER_HOUR, window=_SUGGEST_WINDOW_S):
        logger.warning("Companion-Kontingent erschoepft fuer %s (%d/h)",
                       session.user, _SUGGEST_PER_HOUR)
        raise HTTPException(429, _t("companion.quota_exhausted", request,
                                    {"n": _SUGGEST_PER_HOUR}))

    text = body.text.strip()
    if not text:
        raise HTTPException(400, _t("companion.empty_text", request))
    if len(text) > _MAX_INPUT_CHARS:
        raise HTTPException(413, _t("companion.text_too_long", request, {"n": _MAX_INPUT_CHARS}))

    model = _resolve_model(provider)

    try:
        resp = _dispatch(provider, key, model, text)
    except requests.Timeout:
        raise HTTPException(504, _t("companion.timeout", request))
    except requests.RequestException as e:
        logger.error("Companion HTTP error (%s): %s", provider, e)
        raise HTTPException(502, _t("companion.unreachable", request))

    if resp.status_code != 200:
        logger.warning("Companion API %s %s: %s", provider, resp.status_code, resp.text[:300])
        raise HTTPException(502, _t("companion.api_error", request, {"code": resp.status_code}))

    raw = ""
    try:
        resp_body = resp.json()
        raw = _extract_text(resp_body, provider)

        if raw.startswith("```"):
            first_nl = raw.find("\n")
            if first_nl != -1:
                raw = raw[first_nl + 1:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        parsed      = json.loads(raw)
        suggestions = parsed.get("suggestions", [])
    except (ValueError, KeyError) as e:
        logger.error("Companion parse error (%s): %s — raw=%s", provider, e, raw[:300] or "?")
        raise HTTPException(502, _t("companion.parse_error", request))

    cleaned = []
    for s in suggestions[:3]:
        if isinstance(s, dict) and isinstance(s.get("text"), str) and s["text"].strip():
            suggestion_text = s["text"].strip()
            similarity = difflib.SequenceMatcher(None, text, suggestion_text).ratio()
            if similarity >= 0.95:
                continue
            cleaned.append({
                "label": str(s.get("label") or "Variante"),
                "text" : suggestion_text,
            })

    if not cleaned:
        raise HTTPException(502, _t("companion.no_suggestions", request))

    logger.info("Companion OK (%s/%s): %d suggestions, input=%d chars",
                provider, model, len(cleaned), len(text))
    return {"suggestions": cleaned}
