"""Performance feedback from the publications — and what the scriptwriter
learns from it.

Collects views, likes, comments, shares and average retention of every
published short (YouTube Analytics, TikTok video.query, Instagram insights)
and stores them in `metrics`. `insights()` turns that into a short briefing
that goes into the script prompt: the hooks that retained best on YOUR channel.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .. import db

MAX_AGE_DAYS = 45          # past this the short has settled; not worth a request
REFRESH_EVERY_HOURS = 6


def refresh_all(log=lambda m: None) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).isoformat()
    updated = 0
    for schedule in db.published_schedules(since):
        try:
            if refresh_schedule(schedule):
                updated += 1
        except Exception as exc:  # noqa: BLE001 — one broken account must not stop the rest
            log(f"metrics {schedule['id']}: {exc}")
    return updated


def refresh_schedule(schedule: dict) -> bool:
    result = json.loads(schedule.get("result_json") or "{}")
    video_id = result.get("video_id") or result.get("publish_id")
    if not video_id:
        return False

    account = db.get_account(schedule["account_id"])
    if account is None:
        return False
    creds = json.loads(account["credentials_json"])
    platform = schedule["platform"]

    try:
        if platform == "youtube":
            data = _youtube(video_id, creds, account["id"])
        elif platform == "tiktok":
            data = _tiktok(video_id, creds, account["id"])
        elif platform == "instagram":
            from .publishers import instagram

            data = instagram.insights(video_id, creds["access_token"])
        else:
            return False   # LinkedIn exposes no post analytics to members
        error = ""
    except Exception as exc:  # noqa: BLE001
        data, error = {}, str(exc)

    data.setdefault("published_at", schedule.get("updated_at"))
    db.upsert_metrics(schedule["id"], schedule["job_id"], platform, str(video_id),
                      result.get("url", ""), data, error)
    return not error


# ------------------------------------------------------------------ YouTube

def _youtube(video_id: str, creds: dict, account_id: str) -> dict:
    from googleapiclient.discovery import build

    from .publishers import youtube

    credentials = youtube._credentials(creds)  # noqa: SLF001
    service = build("youtube", "v3", credentials=credentials)
    resp = service.videos().list(part="statistics,snippet", id=video_id).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("Video not found on the channel (removed?)")
    stats = items[0].get("statistics", {})
    out = {
        "views": int(stats.get("viewCount", 0)),
        "likes": int(stats.get("likeCount", 0)),
        "comments": int(stats.get("commentCount", 0)),
        "shares": 0,
        "published_at": items[0].get("snippet", {}).get("publishedAt"),
    }

    # Retention requires the yt-analytics.readonly scope — accounts connected
    # before it existed don't have it; in that case we are left with the public
    # statistics only.
    if any("yt-analytics" in s for s in (creds.get("scopes") or [])):
        try:
            analytics = build("youtubeAnalytics", "v2", credentials=credentials)
            start = (out["published_at"] or "2020-01-01")[:10]
            end = datetime.now(timezone.utc).date().isoformat()
            report = analytics.reports().query(
                ids="channel==MINE", startDate=start, endDate=end,
                metrics="averageViewDuration,averageViewPercentage,shares",
                filters=f"video=={video_id}",
            ).execute()
            rows = report.get("rows") or []
            if rows:
                avg_sec, avg_pct, shares = rows[0][:3]
                out["avg_view_seconds"] = round(float(avg_sec), 1)
                out["avg_view_pct"] = round(float(avg_pct), 1)
                out["shares"] = int(shares)
        except Exception:  # noqa: BLE001 — analytics is a bonus
            pass

    if credentials.token != creds.get("token"):
        creds["token"] = credentials.token
        youtube._persist_token(account_id, creds)  # noqa: SLF001
    return out


# ------------------------------------------------------------------- TikTok

def _tiktok(video_id: str, creds: dict, account_id: str) -> dict:
    import httpx

    from .publishers import tiktok

    token = creds.get("access_token")
    if not token:
        raise RuntimeError("TikTok account without an access_token")

    def query(access_token: str) -> httpx.Response:
        return httpx.post(
            "https://open.tiktokapis.com/v2/video/query/",
            params={"fields": "id,view_count,like_count,comment_count,share_count,"
                              "create_time,share_url,duration"},
            headers={"Authorization": f"Bearer {access_token}",
                     "Content-Type": "application/json"},
            json={"filters": {"video_ids": [video_id]}},
            timeout=60,
        )

    resp = query(token)
    if resp.status_code == 401 and creds.get("refresh_token"):
        refreshed = tiktok.refresh(creds["refresh_token"])
        creds.update(refreshed)
        _persist_account(account_id, creds)
        resp = query(creds["access_token"])
    resp.raise_for_status()
    videos = resp.json().get("data", {}).get("videos", [])
    if not videos:
        # publish_id (inbox) is not the video id; only direct posts are measurable
        raise RuntimeError("Video not found — inbox publications expose no metrics")
    v = videos[0]
    created = v.get("create_time")
    return {
        "views": v.get("view_count", 0), "likes": v.get("like_count", 0),
        "comments": v.get("comment_count", 0), "shares": v.get("share_count", 0),
        "published_at": (datetime.fromtimestamp(created, tz=timezone.utc).isoformat()
                         if created else None),
    }


def _persist_account(account_id: str, credentials: dict) -> None:
    with db._lock, db.connect() as conn:  # noqa: SLF001
        conn.execute("UPDATE accounts SET credentials_json=? WHERE id=?",
                     (json.dumps(credentials), account_id))


# ---------------------------------------------------------------- insights

def insights(niche: str = "", limit: int = 5) -> str:
    """Short briefing for the script prompt: the hooks that worked best.

    Sorts by retention when it exists (that is what the algorithm rewards) and
    by views when it does not. Empty when no publication has been measured yet
    — the prompt then stays exactly as it was before.

    The briefing text below stays in Portuguese on purpose: it is injected into
    the script prompt, which is itself calibrated in Portuguese.
    """
    rows = db.list_metrics()
    if not rows:
        return ""

    scored = []
    for row in rows:
        try:
            job_input = json.loads(row.get("input_json") or "{}")
        except json.JSONDecodeError:
            job_input = {}
        if niche and job_input.get("niche") and job_input["niche"] != niche:
            continue
        job = db.get_job(row["job_id"])
        if not job or not job.get("result_json"):
            continue
        result = json.loads(job["result_json"])
        segments = (result.get("script") or {}).get("segments") or []
        hook = next((s["text"] for s in segments if s.get("kind") == "hook"), "")
        if not hook:
            continue
        retention = row.get("avg_view_pct")
        scored.append({
            "hook": hook, "title": row.get("title") or "",
            "views": row.get("views") or 0, "retention": retention,
            "score": (retention or 0) * 1000 + (row.get("views") or 0),
        })

    if not scored:
        return ""
    scored.sort(key=lambda s: s["score"], reverse=True)
    top = scored[:limit]

    lines = ["DESEMPENHO REAL DO CANAL (aprenda com o que já funcionou, sem copiar):"]
    for item in top:
        metric = (f"retenção {item['retention']:.0f}%" if item["retention"]
                  else f"{item['views']} views")
        lines.append(f'- "{item["hook"]}" — {metric}')
    lines.append("Repita o TIPO de gancho que reteve (pergunta, número, contradição, "
                 "promessa concreta), não o texto.")
    return "\n".join(lines)


def summary() -> dict:
    rows = db.list_metrics()
    total_views = sum(r.get("views") or 0 for r in rows)
    with_ret = [r["avg_view_pct"] for r in rows if r.get("avg_view_pct")]
    return {
        "published": len(rows),
        "views": total_views,
        "likes": sum(r.get("likes") or 0 for r in rows),
        "avg_retention": round(sum(with_ret) / len(with_ret), 1) if with_ret else None,
        "last_fetch": max((r["fetched_at"] for r in rows), default=None),
    }
