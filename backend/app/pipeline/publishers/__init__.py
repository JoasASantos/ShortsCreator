from __future__ import annotations

import json

from ... import db
from ...config import settings

PLATFORM_LABEL = {
    "youtube": "YouTube Shorts",
    "tiktok": "TikTok",
    "instagram": "Instagram Reels",
    "linkedin": "LinkedIn",
}


def dispatch(schedule: dict) -> str:
    """Executa uma publicação agendada. Retorna JSON com o resultado."""
    from . import instagram, linkedin, tiktok, youtube

    account = db.get_account(schedule["account_id"])
    if account is None:
        raise RuntimeError("Conta não encontrada")

    job = db.get_job(schedule["job_id"])
    if job is None or job["status"] != "done":
        raise RuntimeError("Job não finalizado")

    video = settings.outputs_dir / f"{schedule['job_id']}.mp4"
    if not video.exists():
        raise RuntimeError("Arquivo de vídeo ausente")

    payload = json.loads(schedule["payload_json"] or "{}")
    payload["job_id"] = schedule["job_id"]
    credentials = json.loads(account["credentials_json"])

    # capa gerada pelo pipeline, quando existe
    job_dir = settings.jobs_dir / schedule["job_id"]
    cover = job_dir / "cover.jpg"
    payload["cover_path"] = str(cover) if cover.exists() else ""
    if cover.exists() and "localhost" not in settings.public_api_url:
        payload["cover_url"] = (f"{settings.public_api_url.rstrip('/')}"
                                f"/api/outputs/{schedule['job_id']}.jpg")
    cover_meta = job_dir / "cover.json"
    if cover_meta.exists():
        try:
            payload["cover_at"] = float(json.loads(cover_meta.read_text()).get("at", 1.0))
        except (ValueError, json.JSONDecodeError):
            pass

    platform = schedule["platform"]
    if platform == "youtube":
        result = youtube.upload(video, payload, credentials, account["id"])
    elif platform == "tiktok":
        result = tiktok.upload(video, payload, credentials, account["id"])
    elif platform == "instagram":
        result = instagram.upload(video, payload, credentials, account["id"])
    elif platform == "linkedin":
        result = linkedin.upload(video, payload, credentials, account["id"])
    else:
        raise RuntimeError(f"Plataforma não suportada: {platform}")

    return json.dumps(result)
