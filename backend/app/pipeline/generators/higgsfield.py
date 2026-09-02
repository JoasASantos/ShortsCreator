"""Cliente Higgsfield — geração de vídeo por IA para o fundo do short.

API: https://docs.higgsfield.ai/docs — base https://api.higgsfield.ai,
auth `Authorization: Key {key_id}:{key_secret}`. Fluxo é sempre o mesmo:
POST no modelo escolhido -> {request_id, status_url} -> poll em
GET /requests/{request_id}/status até completed/failed -> baixa `video.url`.

Modelos expostos aqui são os que aceitam texto->vídeo em 9:16 nativo
(aspect_ratio) ou por padrão vertical; cobre o essencial sem replicar o
catálogo inteiro (~50 modelos) do Higgsfield.
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx

BASE = "https://api.higgsfield.ai"
TIMEOUT = 60.0
POLL_INTERVAL = 4.0
POLL_TIMEOUT = 480.0

# id curto -> (path, campo de proporção, valor 9:16)
MODELS = {
    "seedance-lite": ("/bytedance/seedance/v1/lite/text-to-video", "aspect_ratio", "9:16"),
    "seedance-pro": ("/bytedance/seedance/v1/pro/fast/text-to-video", "aspect_ratio", "9:16"),
    "hailuo-standard": ("/minimax/hailuo-02/standard/text-to-video", None, None),
    "hailuo-pro": ("/minimax/hailuo-02/pro/text-to-video", None, None),
    "kling-2.5-pro": ("/kling-video/v2.5-turbo/pro/text-to-video", None, None),
    "sora-2": ("/sora-2/text-to-video", None, None),
    "wan-2.5": ("/wan-25-preview/text-to-video", None, None),
}
DEFAULT_MODEL = "seedance-lite"


def _headers(creds: dict) -> dict:
    key_id = creds.get("key_id", "")
    key_secret = creds.get("key_secret", "")
    return {"Authorization": f"Key {key_id}:{key_secret}",
            "Content-Type": "application/json"}


def verify(creds: dict) -> str:
    """Chamada mais barata possível só pra provar que o par de chaves vale:
    dispara uma geração curta e cancela em seguida."""
    path, ratio_field, ratio_value = MODELS[DEFAULT_MODEL]
    body = {"prompt": "teste de conexão, cena estática de uma cidade"}
    if ratio_field:
        body[ratio_field] = ratio_value
    r = httpx.post(f"{BASE}{path}", headers=_headers(creds), json=body,
                   timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise RuntimeError("Chave/segredo recusados pela Higgsfield")
    r.raise_for_status()
    data = r.json()
    cancel_url = data.get("cancel_url")
    if cancel_url:
        try:
            httpx.post(cancel_url, headers=_headers(creds), timeout=10)
        except httpx.HTTPError:
            pass
    return "Higgsfield respondendo — geração de teste aceita e cancelada"


def generate_clip(prompt: str, duration: float, out_path: Path, creds: dict,
                  model: str = DEFAULT_MODEL, log=lambda m: None) -> Path:
    if model not in MODELS:
        model = DEFAULT_MODEL
    path, ratio_field, ratio_value = MODELS[model]
    body: dict = {"prompt": prompt}
    if ratio_field:
        body[ratio_field] = ratio_value
    log(f"higgsfield: pedindo clipe ({model}) — {prompt[:60]}")
    r = httpx.post(f"{BASE}{path}", headers=_headers(creds), json=body,
                   timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise RuntimeError("Chave/segredo recusados pela Higgsfield")
    r.raise_for_status()
    data = r.json()
    status_url = data.get("status_url") or f"{BASE}/requests/{data['request_id']}/status"

    deadline = time.monotonic() + POLL_TIMEOUT
    while time.monotonic() < deadline:
        s = httpx.get(status_url, headers=_headers(creds), timeout=TIMEOUT)
        s.raise_for_status()
        info = s.json()
        state = info.get("status")
        if state == "completed":
            video = info.get("video") or {}
            url = video.get("url") if isinstance(video, dict) else None
            if not url:
                raise RuntimeError("Higgsfield concluiu mas não trouxe URL de vídeo")
            with httpx.stream("GET", url, timeout=TIMEOUT) as resp:
                resp.raise_for_status()
                with out_path.open("wb") as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
            log("higgsfield: clipe baixado")
            return out_path
        if state in ("failed", "nsfw", "canceled"):
            raise RuntimeError(f"Higgsfield: geração terminou em '{state}' "
                              f"({info.get('error') or 'sem detalhe'})")
        time.sleep(POLL_INTERVAL)
    raise RuntimeError("Higgsfield: tempo limite esperando a geração terminar")
