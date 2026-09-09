"""GPT Image 2.5 — OpenAI's image model, through the Images API.

API: https://developers.openai.com/api/docs/guides/image-generation

  POST /v1/images/generations   header Authorization: Bearer <key>
       {"model": "gpt-image-2.5-sunburst", "prompt": ..., "n": 1,
        "size": "2048x1152", "quality": "high", "output_format": "png"}
    -> {"data": [{"b64_json": "<base64>"}]}

  POST /v1/images/edits         multipart
       model, prompt, image=<file>, mask=<file, optional>
    -> the same shape

One synchronous call, no job to poll: the bytes come back in the response.
`/edits` is the image_to_image path — the reference is sent as a file part
rather than as base64 inside JSON.

Two models, and the difference matters when a documentary needs forty stills:
`sunburst` is the precise one and takes longer, `flare` brings the same
quality improvements at speed. Sunburst is the default because a still that
will sit on screen for six seconds is worth the wait.
"""
from __future__ import annotations

import base64
import binascii
import mimetypes
from pathlib import Path

import httpx

from .registry import ProviderRefused

BASE = "https://api.openai.com/v1"

# A high-quality 2K render regularly runs past a minute, and there is no
# request id to come back to — giving up early throws away work already paid
# for. The connect timeout stays short: an unreachable API is unreachable now.
TIMEOUT = httpx.Timeout(300.0, connect=10.0)
PROBE_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

MODELS = {
    "gpt-image-2.5-sunburst": "GPT Image 2.5 Sunburst",
    "gpt-image-2.5-flare": "GPT Image 2.5 Flare",
}
DEFAULT_MODEL = "gpt-image-2.5-sunburst"

# Sizes per frame shape.
#
# The API takes any WIDTHxHEIGHT whose sides are multiples of 16 within its
# pixel budget, so these are exact ratios rather than the documented
# "recommended" ones: 1536x1024 is 3:2, which is visibly not 16:9 and would
# arrive at the compositor needing to be cropped or padded — the very thing
# the framing work exists to avoid. 2048x1152 is 16:9 to the pixel and 1152 is
# 72*16, so it is a legal size.
SIZES = {
    "16:9": "2048x1152",
    "9:16": "1152x2048",
    "1:1": "1024x1024",
    "4:5": "1024x1280",
    "3:2": "1536x1024",
    "2:3": "1024x1536",
}
DEFAULT_SIZE = SIZES["9:16"]

QUALITIES = {"low", "medium", "high", "xhigh", "max", "auto"}
DEFAULT_QUALITY = "high"


def _headers(creds: dict) -> dict:
    return {"Authorization": f"Bearer {creds.get('api_key', '')}"}


def _redact(text: str, creds: dict) -> str:
    """The key travels in a header, so the service has no reason to echo it —
    but an error string is not the place to find out we were wrong."""
    key = creds.get("api_key", "")
    return text.replace(key, "***") if key else text


def verify(creds: dict) -> str:
    """GET /models proves the key without spending a generation."""
    r = httpx.get(f"{BASE}/models", headers=_headers(creds), timeout=PROBE_TIMEOUT)
    if r.status_code in (401, 403):
        raise RuntimeError("Key rejected by the OpenAI API")
    r.raise_for_status()
    names = {m.get("id", "") for m in r.json().get("data") or []}
    available = sorted(name for name in MODELS if name in names)
    if not available:
        # A valid key without access to the image models is a real state, and
        # worth naming: a connector test that merely passed would promise
        # something the render cannot deliver.
        return ("OpenAI responding, but no GPT Image 2.5 model is available to "
                "this key — check the project's model access")
    return f"OpenAI responding — image models available: {', '.join(available)}"


def _size_for(aspect: str, size: str = "") -> str:
    if size:
        return size
    return SIZES.get(aspect, DEFAULT_SIZE)


def generate_image(prompt: str, out_path: Path, creds: dict,
                   model: str = DEFAULT_MODEL, aspect: str = "9:16",
                   reference: Path | None = None, quality: str = DEFAULT_QUALITY,
                   size: str = "", log=lambda m, *_: None) -> Path:
    """One still, written to `out_path`.

    With a `reference` this goes to /edits instead: same model, the reference
    as a file part. That is how a documentary keeps a look consistent across
    stills — the previous frame becomes the reference for the next.
    """
    if model not in MODELS:
        model = DEFAULT_MODEL
    if quality not in QUALITIES:
        quality = DEFAULT_QUALITY

    chosen_size = _size_for(aspect, size)
    log(f"gptimage: {MODELS[model]} {chosen_size} — {prompt[:60]}")

    if reference is not None and reference.exists():
        mime = mimetypes.guess_type(reference.name)[0] or "image/png"
        response = httpx.post(
            f"{BASE}/images/edits",
            headers=_headers(creds),
            data={"model": model, "prompt": prompt, "size": chosen_size,
                  "quality": quality, "n": "1"},
            files={"image": (reference.name, reference.read_bytes(), mime)},
            timeout=TIMEOUT,
        )
    else:
        response = httpx.post(
            f"{BASE}/images/generations",
            headers={**_headers(creds), "Content-Type": "application/json"},
            json={"model": model, "prompt": prompt, "n": 1,
                  "size": chosen_size, "quality": quality,
                  "output_format": "png"},
            timeout=TIMEOUT,
        )

    _raise_for_refusal(response, creds)
    response.raise_for_status()

    encoded = _find_image(response.json())
    if encoded is None:
        raise RuntimeError("The OpenAI Images API answered without an image.")
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(encoded))
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(f"The image came back undecodable: {exc}") from exc
    return out_path


def _raise_for_refusal(response: httpx.Response, creds: dict) -> None:
    """Separate a refusal from a failure.

    A refusal will never succeed on retry, so the chain must move to the next
    provider; a 500 or a timeout is worth propagating so it is not mistaken
    for "this prompt is impossible". `tts._synthesize_with` set that
    distinction and every provider here keeps it.
    """
    if response.status_code in (401, 403):
        raise ProviderRefused(
            "The OpenAI API rejected the key. Check it on the Accounts screen.")
    if response.status_code == 429:
        raise ProviderRefused(
            "The OpenAI quota for this key is spent — no retry will fix it "
            "today.")
    if response.status_code == 400:
        raise ProviderRefused(
            f"The OpenAI Images API refused the request: "
            f"{_redact(_error(response), creds)}")


def _find_image(payload: dict) -> str | None:
    """The base64 of the first image in the response.

    Looked for rather than indexed: the entry can carry a URL instead, and a
    refusal comes back as an entry with neither.
    """
    for entry in payload.get("data") or []:
        if isinstance(entry, dict) and entry.get("b64_json"):
            return entry["b64_json"]
    return None


def _error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)[:300]
    return str(body)[:300]
