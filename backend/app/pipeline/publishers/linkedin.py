"""Post de vídeo nativo no LinkedIn pela Posts API (versionada).

  1. POST /rest/videos?action=initializeUpload -> uploadUrl + video URN
  2. PUT do arquivo inteiro na uploadUrl (ETag volta no header)
  3. POST /rest/videos?action=finalizeUpload com o ETag
  4. POST /rest/posts com content.media.id = URN do vídeo

Precisa de token com `w_member_social`. Se `author_urn` não vier, descobre
pelo /v2/userinfo (exige `openid` e `profile`).
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx

API = "https://api.linkedin.com"
VERSION = "202409"
TIMEOUT = 90.0


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def resolve_author(creds: dict) -> str:
    urn = (creds.get("author_urn") or "").strip()
    if urn:
        return urn
    r = httpx.get(f"{API}/v2/userinfo",
                  headers={"Authorization": f"Bearer {creds.get('access_token', '')}"},
                  timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(
            "Sem author_urn e o token não tem escopo openid/profile para "
            f"descobrir sozinho ({r.status_code}). Informe o URN em Contas.")
    return f"urn:li:person:{r.json()['sub']}"


def verify(creds: dict) -> str:
    urn = resolve_author(creds)
    try:
        r = httpx.get(f"{API}/v2/userinfo",
                      headers={"Authorization": f"Bearer {creds.get('access_token', '')}"},
                      timeout=TIMEOUT)
        name = r.json().get("name", "")
    except Exception:  # noqa: BLE001
        name = ""
    return f"Token válido para {name or urn}"


def profile_name(creds: dict) -> str:
    """Nome do autor. Levanta se o token não servir.

    Um token só com `w_member_social` não consegue ler o perfil; nesse caso o
    URN informado à mão já identifica a conta e serve como nome.
    """
    r = httpx.get(f"{API}/v2/userinfo",
                  headers={"Authorization": f"Bearer {creds.get('access_token', '')}"},
                  timeout=TIMEOUT)
    if r.status_code == 200:
        name = r.json().get("name")
        if name:
            return name
    urn = (creds.get("author_urn") or "").strip()
    if urn:
        return urn
    raise RuntimeError(
        f"Token recusado pelo LinkedIn ({r.status_code}) e sem author_urn para "
        "identificar a conta. Informe o URN em Contas.")


def upload(video: Path, payload: dict, credentials: dict, account_id: str) -> dict:
    token = credentials.get("access_token")
    if not token:
        raise RuntimeError("Conta LinkedIn sem access_token")
    author = resolve_author(credentials)
    headers = _headers(token)
    size = video.stat().st_size

    init = httpx.post(
        f"{API}/rest/videos?action=initializeUpload", headers=headers,
        json={"initializeUploadRequest": {
            "owner": author, "fileSizeBytes": size,
            "uploadCaptions": False, "uploadThumbnail": False}},
        timeout=TIMEOUT,
    )
    if init.status_code >= 400:
        raise RuntimeError(f"LinkedIn recusou o upload: {init.text[:300]}")
    value = init.json()["value"]
    video_urn = value["video"]
    instructions = value["uploadInstructions"]

    etags = []
    with video.open("rb") as fh:
        for part in instructions:
            first, last = part["firstByte"], part["lastByte"]
            fh.seek(first)
            chunk = fh.read(last - first + 1)
            put = httpx.put(part["uploadUrl"], content=chunk,
                            headers={"Content-Type": "application/octet-stream"},
                            timeout=900)
            put.raise_for_status()
            etags.append(put.headers.get("etag", ""))

    fin = httpx.post(
        f"{API}/rest/videos?action=finalizeUpload", headers=headers,
        json={"finalizeUploadRequest": {
            "video": video_urn, "uploadToken": value.get("uploadToken", ""),
            "uploadedPartIds": etags}},
        timeout=TIMEOUT,
    )
    if fin.status_code >= 400:
        raise RuntimeError(f"LinkedIn não finalizou o upload: {fin.text[:300]}")

    _wait_available(video_urn, headers)

    title = payload.get("title") or "Short"
    text = payload.get("description") or title
    hashtags = payload.get("tags", [])
    if hashtags:
        text = f"{text}\n\n" + " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags)

    visibility = "PUBLIC" if payload.get("privacy", "private") == "public" else "CONNECTIONS"
    post = httpx.post(
        f"{API}/rest/posts", headers=headers,
        json={
            "author": author,
            "commentary": text[:3000],
            "visibility": visibility,
            "distribution": {"feedDistribution": "MAIN_FEED",
                             "targetEntities": [], "thirdPartyDistributionChannels": []},
            "content": {"media": {"title": title[:200], "id": video_urn}},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        },
        timeout=TIMEOUT,
    )
    if post.status_code >= 400:
        raise RuntimeError(f"LinkedIn recusou o post: {post.text[:300]}")
    post_urn = post.headers.get("x-restli-id", "")
    return {"platform": "linkedin", "video_id": post_urn or video_urn,
            "url": f"https://www.linkedin.com/feed/update/{post_urn}" if post_urn else "",
            "video_urn": video_urn}


def _wait_available(video_urn: str, headers: dict, tries: int = 30) -> None:
    for _ in range(tries):
        r = httpx.get(f"{API}/rest/videos/{httpx.QueryParams({'u': video_urn})['u']}",
                      headers=headers, timeout=TIMEOUT)
        if r.status_code == 200:
            status = r.json().get("status")
            if status == "AVAILABLE":
                return
            if status == "PROCESSING_FAILED":
                raise RuntimeError("LinkedIn falhou ao processar o vídeo")
        time.sleep(5)
    raise RuntimeError("LinkedIn não terminou de processar o vídeo em tempo")
