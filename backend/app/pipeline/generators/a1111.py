"""Automatic1111 / Stable Diffusion WebUI client — the other local, free option.

Routes, verified against the WebUI's modules/api/api.py and models.py:
  POST /sdapi/v1/txt2img   {"prompt", "negative_prompt", "steps", "cfg_scale",
                            "width", "height", "sampler_name", "seed",
                            "override_settings"}
                           -> {"images": ["<base64 png>", ...],
                               "parameters": {...}, "info": "<json string>"}
  GET  /sdapi/v1/sd-models -> [{"title", "model_name", "hash", "sha256",
                                "filename", "config"}]   (reachability + which
                                checkpoints the server actually has)

The WebUI only mounts /sdapi when it was started with --api, so a WebUI that
answers on its port and 404s on /sdapi is a distinct, very common failure with
its own message.
"""
from __future__ import annotations

import base64
import binascii
from pathlib import Path

import httpx

from .registry import LocalServerDown, ProviderRefused

CONNECT_TIMEOUT = 3.0
PROBE_TIMEOUT = httpx.Timeout(5.0, connect=CONNECT_TIMEOUT)
# txt2img is synchronous: the HTTP call is held open for the entire sampling
# run and there is no job id to poll. 30 steps of an SDXL model at 1080x1920 is
# under a minute on a recent GPU but several minutes on CPU-only hardware, and
# the first request of the session also loads the checkpoint. A short read
# timeout here would kill renders that were about to finish.
TIMEOUT = httpx.Timeout(900.0, connect=CONNECT_TIMEOUT)

DEFAULT_STEPS = 28
DEFAULT_CFG = 6.5
DEFAULT_NEGATIVE = "text, watermark, logo, blurry, low quality, deformed"

# The short is 1080x1920, but diffusion models fall apart above their training
# resolution, so we sample at a well-behaved 9:16 size and let the renderer
# upscale. 832x1472 is a multiple of 16 and close to SDXL's sweet spot.
ASPECT_SIZE = {"9:16": (832, 1472), "16:9": (1472, 832), "1:1": (1024, 1024)}


def _down(base_url: str, exc: Exception | None = None) -> LocalServerDown:
    return LocalServerDown(
        f"Stable Diffusion WebUI is not answering at {base_url}. Start it with "
        f"the API on — `./webui.sh --api` (or --api --listen for another "
        f"machine) — or point A1111_URL at the host that runs it."
        + (f" ({type(exc).__name__})" if exc else "")
    )


def _api_off(base_url: str) -> LocalServerDown:
    return LocalServerDown(
        f"Something is answering at {base_url} but /sdapi is not there. That is "
        f"a WebUI started without --api: restart it as `./webui.sh --api`."
    )


def probe(base_url: str) -> str:
    url = base_url.rstrip("/")
    try:
        r = httpx.get(f"{url}/sdapi/v1/sd-models", timeout=PROBE_TIMEOUT)
    except httpx.HTTPError as exc:
        raise _down(url, exc) from exc
    if r.status_code == 404:
        raise _api_off(url)
    r.raise_for_status()
    models = r.json() or []
    if not models:
        raise LocalServerDown(
            f"The WebUI at {url} has no checkpoint installed — drop a .safetensors "
            f"model into models/Stable-diffusion and reload it."
        )
    names = ", ".join(m.get("model_name", "?") for m in models[:3])
    return f"WebUI responding at {url} — {len(models)} checkpoint(s): {names}"


def list_models(base_url: str) -> list[dict]:
    url = base_url.rstrip("/")
    try:
        r = httpx.get(f"{url}/sdapi/v1/sd-models", timeout=PROBE_TIMEOUT)
    except httpx.HTTPError as exc:
        raise _down(url, exc) from exc
    if r.status_code == 404:
        raise _api_off(url)
    r.raise_for_status()
    return r.json() or []


def generate_image(prompt: str, out_path: Path, base_url: str = "",
                   aspect: str = "9:16", checkpoint: str = "",
                   steps: int = DEFAULT_STEPS, seed: int = -1,
                   log=lambda m, *_: None) -> Path:
    from ...config import settings

    url = (base_url or settings.a1111_url).rstrip("/")
    width, height = ASPECT_SIZE.get(aspect, ASPECT_SIZE["9:16"])
    body: dict = {
        "prompt": prompt,
        "negative_prompt": DEFAULT_NEGATIVE,
        "steps": steps,
        "cfg_scale": DEFAULT_CFG,
        "width": width,
        "height": height,
        "seed": seed,
        # Keeping the render out of the server's output folder: this image
        # belongs to a job directory, not to somebody's gallery.
        "save_images": False,
    }
    if checkpoint:
        body["override_settings"] = {"sd_model_checkpoint": checkpoint}

    log(f"a1111: rendering {width}x{height} — {prompt[:60]}")
    try:
        r = httpx.post(f"{url}/sdapi/v1/txt2img", json=body, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        raise _down(url, exc) from exc
    if r.status_code == 404:
        raise _api_off(url)
    if r.status_code == 422:
        # The WebUI validated the payload and said no — a sampler it does not
        # have, a checkpoint it cannot find. No retry will fix it.
        raise ProviderRefused(f"The WebUI rejected the request: {_error(r)}")
    r.raise_for_status()

    images = (r.json() or {}).get("images") or []
    if not images:
        raise RuntimeError("The WebUI returned no image (an interrupted or "
                           "out-of-memory render usually shows up like this)")
    try:
        out_path.write_bytes(base64.b64decode(images[0]))
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(f"The WebUI returned an image that is not base64: {exc}") from exc
    log(f"a1111: image saved to {out_path.name}")
    return out_path


def _error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    detail = payload.get("detail") or payload.get("error") or payload
    if isinstance(detail, list) and detail:
        detail = detail[0].get("msg", detail[0])
    return str(detail)[:300]
