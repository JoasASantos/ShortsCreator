"""Nano Banana Pro — Google's image model, through the Gemini API.

API: https://ai.google.dev/gemini-api/docs/image-generation
  POST /v1beta/models/{model}:generateContent
       header x-goog-api-key: <key>
       {"contents": [{"role": "user", "parts": [{"text": ...},
                      {"inline_data": {"mime_type": ..., "data": "<base64>"}}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                             "imageConfig": {"aspectRatio": "9:16"}}}
    -> {"candidates": [{"content": {"parts": [{"inlineData":
           {"mimeType": "image/png", "data": "<base64>"}}]},
         "finishReason": ...}], "promptFeedback": {...}}

One synchronous call, no job to poll — the image comes back in the response
body. The same endpoint does image_to_image: an extra inline_data part in the
request is the reference to edit.

Responses mix modalities: the model likes to narrate what it drew, so the parts
list holds text as well and the image has to be looked for rather than indexed.
"""
from __future__ import annotations

import base64
import binascii
import mimetypes
from pathlib import Path

import httpx

from .registry import ProviderRefused

BASE = "https://generativelanguage.googleapis.com/v1beta"
# Nano Banana Pro renders in the request itself, and a 2K image with a
# reasoning pass regularly takes over a minute. There is no request id to come
# back to, so giving up early throws away work that was already paid for.
TIMEOUT = httpx.Timeout(240.0, connect=10.0)
PROBE_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

MODELS = {
    "gemini-3-pro-image-preview": "Nano Banana Pro",
    "gemini-2.5-flash-image": "Nano Banana",
}
DEFAULT_MODEL = "gemini-3-pro-image-preview"

# Aspect ratios the imageConfig accepts. Anything else is dropped rather than
# sent, because an unknown value fails the whole request.
ASPECTS = {"1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"}

# finishReason / blockReason values that mean "not this prompt, ever".
REFUSALS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "IMAGE_SAFETY",
            "RECITATION", "SPII"}


def _headers(creds: dict) -> dict:
    return {"x-goog-api-key": creds.get("api_key", ""),
            "Content-Type": "application/json"}


def _redact(text: str, creds: dict) -> str:
    """Belt and braces before anything reaches a log or an HTTP response: the
    key travels in a header, so the service has no reason to echo it, but an
    error string is not the place to find out we were wrong."""
    key = creds.get("api_key", "")
    return text.replace(key, "***") if key else text


def verify(creds: dict) -> str:
    """GET /models is the only call that proves the key without spending an
    image generation."""
    r = httpx.get(f"{BASE}/models", headers=_headers(creds), timeout=PROBE_TIMEOUT)
    if r.status_code in (400, 401, 403):
        raise RuntimeError("Key rejected by the Gemini API")
    r.raise_for_status()
    names = {m.get("name", "").rsplit("/", 1)[-1] for m in r.json().get("models") or []}
    available = sorted(name for name in MODELS if name in names)
    if not available:
        # A valid key on a project without the image models is a real state and
        # worth naming: the connector test passing would otherwise promise
        # something the render cannot deliver.
        return ("Gemini responding, but no Nano Banana image model is enabled "
                "for this key — enable the image models in AI Studio")
    return f"Gemini responding — image models available: {', '.join(available)}"


def generate_image(prompt: str, out_path: Path, creds: dict,
                   model: str = DEFAULT_MODEL, aspect: str = "9:16",
                   reference: Path | None = None,
                   log=lambda m, *_: None) -> Path:
    if model not in MODELS:
        model = DEFAULT_MODEL
    parts: list[dict] = [{"text": prompt}]
    if reference is not None:
        parts.append({"inline_data": {
            "mime_type": mimetypes.guess_type(reference.name)[0] or "image/png",
            "data": base64.b64encode(reference.read_bytes()).decode("ascii"),
        }})
    config: dict = {"responseModalities": ["TEXT", "IMAGE"]}
    if aspect in ASPECTS:
        config["imageConfig"] = {"aspectRatio": aspect}
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": config}

    log(f"nanobanana: {MODELS[model]} — {prompt[:60]}")
    r = httpx.post(f"{BASE}/models/{model}:generateContent",
                   headers=_headers(creds), json=body, timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise ProviderRefused("The Gemini API rejected the key. Check it on the "
                              "Accounts screen.")
    if r.status_code == 429:
        raise ProviderRefused("The Gemini quota for this key is spent — no retry "
                              "will fix it today.")
    if r.status_code == 400:
        raise ProviderRefused(f"The Gemini API refused the request: "
                              f"{_redact(_error(r), creds)}")
    r.raise_for_status()
    payload = r.json()

    blocked = (payload.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        raise ProviderRefused(f"Nano Banana blocked the prompt ({blocked}). "
                              f"Rewrite the background prompt.")

    data, mime = _find_image(payload)
    if data is None:
        finish = ((payload.get("candidates") or [{}])[0]).get("finishReason", "")
        if finish in REFUSALS:
            raise ProviderRefused(f"Nano Banana returned no image ({finish}) — "
                                  f"the prompt has to change.")
        raise RuntimeError(f"Nano Banana answered without an image "
                           f"(finishReason={finish or 'unknown'})")
    try:
        raw = base64.b64decode(data)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(f"Nano Banana returned image data that is not base64: {exc}") from exc

    # The response says what it actually produced; honouring it keeps a JPEG
    # from being written under a .png name and confusing ffmpeg later.
    suffix = mimetypes.guess_extension(mime or "") or ""
    if suffix in (".jpe", ".jpeg"):
        suffix = ".jpg"
    target = (out_path.with_suffix(suffix)
              if suffix and suffix != out_path.suffix.lower() else out_path)
    target.write_bytes(raw)
    log(f"nanobanana: image saved to {target.name}")
    return target


def _find_image(payload: dict) -> tuple[str | None, str]:
    """The image part, wherever it sits. Both spellings are accepted because
    the REST API answers in camelCase while its own docs write the request in
    snake_case, and hand-built payloads in the wild use either."""
    for candidate in payload.get("candidates") or []:
        for part in ((candidate.get("content") or {}).get("parts") or []):
            inline = part.get("inlineData") or part.get("inline_data")
            if isinstance(inline, dict) and inline.get("data"):
                return inline["data"], inline.get("mimeType") or inline.get("mime_type") or ""
    return None, ""


def _error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    error = payload.get("error") or {}
    if isinstance(error, dict):
        return str(error.get("message") or error)[:300]
    return str(error)[:300]
