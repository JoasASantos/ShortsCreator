from __future__ import annotations

import json

from ... import db
from ...config import settings


def dispatch(schedule: dict) -> str:
    """Executa uma publicação agendada. Retorna JSON com o resultado."""
    from . import tiktok, youtube

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
    credentials = json.loads(account["credentials_json"])

    if schedule["platform"] == "youtube":
        result = youtube.upload(video, payload, credentials, account["id"])
    elif schedule["platform"] == "tiktok":
        result = tiktok.upload(video, payload, credentials, account["id"])
    else:
        raise RuntimeError(f"Plataforma não suportada: {schedule['platform']}")

    return json.dumps(result)
