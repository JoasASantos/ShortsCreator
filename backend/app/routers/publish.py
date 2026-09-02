from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from .. import db
from ..config import settings
from ..schemas import PublishRequest

router = APIRouter(prefix="/api/publish", tags=["publish"])


@router.get("/accounts")
def list_accounts():
    return [
        {k: v for k, v in account.items() if k != "credentials_json"}
        for account in db.list_accounts()
    ]


@router.delete("/accounts/{account_id}")
def delete_account(account_id: str):
    db.delete_account(account_id)
    return {"deleted": account_id}


# ---------------- YouTube OAuth ----------------

@router.get("/youtube/auth")
def youtube_auth():
    from ..pipeline.publishers import youtube

    redirect = f"{settings.public_api_url}/api/publish/youtube/callback"
    url, _ = youtube.auth_url_flow(redirect)
    return {"auth_url": url}


@router.get("/youtube/callback")
def youtube_callback(code: str = Query(...)):
    from ..pipeline.publishers import youtube

    redirect = f"{settings.public_api_url}/api/publish/youtube/callback"
    credentials = youtube.exchange_code(code, redirect)
    name = youtube.channel_name(credentials)
    db.create_account("youtube", name, credentials)
    return RedirectResponse("http://localhost:3000/contas?connected=youtube")


# ---------------- TikTok OAuth ----------------

@router.get("/tiktok/auth")
def tiktok_auth():
    from ..pipeline import connectors
    from ..pipeline.publishers import tiktok

    if not connectors.is_configured("tiktok"):
        raise HTTPException(400, "Cadastre client_key/client_secret do TikTok em Contas")
    return {"auth_url": tiktok.auth_url(state=db.new_id("state"))}


@router.get("/tiktok/callback")
def tiktok_callback(code: str = Query(...)):
    from ..pipeline.publishers import tiktok

    token = tiktok.exchange_code(code)
    info = {}
    try:
        info = tiktok.creator_info(token["access_token"])
    except Exception:
        pass
    name = info.get("creator_nickname") or "Conta TikTok"
    db.create_account("tiktok", name, token)
    return RedirectResponse("http://localhost:3000/contas?connected=tiktok")


# ---------------- publicação / agendamento ----------------

@router.post("")
def publish(request: PublishRequest):
    job = db.get_job(request.job_id)
    if job is None or job["status"] != "done":
        raise HTTPException(400, "Job precisa estar concluído")

    qa_report = json.loads(job["qa_json"] or "{}")
    if qa_report and not qa_report.get("passed"):
        fatal = [i for i in qa_report.get("issues", []) if i["severity"] == "fatal"]
        if fatal:
            raise HTTPException(
                400, f"QA reprovou com erro fatal: {fatal[0]['message']}")

    if db.get_account(request.account_id) is None:
        raise HTTPException(404, "Conta não encontrada")

    result = json.loads(job["result_json"] or "{}")
    payload = {
        "title": request.title or result.get("title", "Short"),
        "description": request.description or result.get("description", ""),
        "tags": request.tags or result.get("hashtags", []),
        "privacy": request.privacy,
        "publish_at": request.publish_at,
        "direct_post": request.platform == "tiktok" and request.privacy == "public",
        "privacy_level": "PUBLIC_TO_EVERYONE" if request.privacy == "public" else "SELF_ONLY",
    }

    publish_at = request.publish_at or datetime.now(timezone.utc).isoformat()
    schedule_id = db.create_schedule(request.job_id, request.account_id,
                                     request.platform, publish_at, payload)
    return {"schedule_id": schedule_id, "publish_at": publish_at,
            "status": "pending"}


@router.get("/schedules")
def list_schedules():
    return db.list_schedules()


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: str):
    db.delete_schedule(schedule_id)
    return {"deleted": schedule_id}


@router.post("/schedules/{schedule_id}/run")
def run_now(schedule_id: str):
    db.update_schedule(schedule_id, publish_at=datetime.now(timezone.utc).isoformat(),
                       status="pending")
    return {"scheduled": schedule_id}
