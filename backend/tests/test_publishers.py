"""Publicadores: conta só existe com token que responde, e erro é legível."""
from __future__ import annotations

import json

import httpx
import pytest

from app import db
from app.config import settings
from app.pipeline import connectors
from app.pipeline.publishers import PLATFORM_LABEL, dispatch, instagram, linkedin


def test_todas_as_plataformas_tem_rotulo():
    assert set(PLATFORM_LABEL) == {"youtube", "tiktok", "instagram", "linkedin"}


# ------------------------------------------------------- conta só se valer

def test_instagram_com_token_invalido_nao_cria_conta_publicavel(monkeypatch):
    """Antes virava uma conta com nome genérico que só falhava na publicação."""
    monkeypatch.setattr(instagram.httpx, "get", lambda *a, **k: httpx.Response(
        400, json={"error": {"message": "Invalid OAuth access token", "code": 190}}))

    connectors.save("instagram", {"access_token": "ruim", "ig_user_id": "1"})
    assert connectors.is_configured("instagram") is True
    assert [a for a in db.list_accounts() if a["platform"] == "instagram"] == []


def test_instagram_com_token_valido_cria_conta_com_o_arroba(monkeypatch):
    monkeypatch.setattr(instagram.httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"username": "meucanal"}))

    connectors.save("instagram", {"access_token": "bom", "ig_user_id": "1"})
    contas = [a for a in db.list_accounts() if a["platform"] == "instagram"]
    assert len(contas) == 1
    assert contas[0]["display_name"] == "@meucanal"


def test_salvar_de_novo_atualiza_em_vez_de_duplicar(monkeypatch):
    monkeypatch.setattr(instagram.httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"username": "meucanal"}))
    connectors.save("instagram", {"access_token": "bom", "ig_user_id": "1"})
    connectors.save("instagram", {"access_token": "outro", "ig_user_id": "1"})
    assert len([a for a in db.list_accounts() if a["platform"] == "instagram"]) == 1


def test_limpar_conector_remove_a_conta(monkeypatch):
    monkeypatch.setattr(instagram.httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"username": "x"}))
    connectors.save("instagram", {"access_token": "bom", "ig_user_id": "1"})
    connectors.clear("instagram")
    assert [a for a in db.list_accounts() if a["platform"] == "instagram"] == []


def test_linkedin_sem_leitura_de_perfil_usa_o_urn(monkeypatch):
    """Token só com w_member_social não lê o perfil — o URN informado à mão
    identifica a conta e é o suficiente para publicar."""
    monkeypatch.setattr(linkedin.httpx, "get",
                        lambda *a, **k: httpx.Response(403, json={}))
    nome = linkedin.profile_name({"access_token": "escrita",
                                  "author_urn": "urn:li:person:ABC"})
    assert nome == "urn:li:person:ABC"


def test_linkedin_sem_urn_e_sem_leitura_falha_explicando(monkeypatch):
    monkeypatch.setattr(linkedin.httpx, "get",
                        lambda *a, **k: httpx.Response(401, json={}))
    with pytest.raises(RuntimeError, match="author_urn"):
        linkedin.profile_name({"access_token": "ruim", "author_urn": ""})


def test_linkedin_com_openid_usa_o_nome_real(monkeypatch):
    monkeypatch.setattr(linkedin.httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"name": "Maria Silva", "sub": "abc"}))
    assert linkedin.profile_name({"access_token": "completo"}) == "Maria Silva"


# ------------------------------------------------------------- Instagram URL

def test_instagram_exige_url_publica(monkeypatch):
    """A Graph API baixa o vídeo: localhost não serve, e o erro tem que dizer
    isso em vez de estourar na Meta."""
    monkeypatch.setattr(settings, "public_api_url", "http://localhost:8000")
    with pytest.raises(RuntimeError, match="URL pública"):
        instagram.public_video_url("job_x")


def test_instagram_aceita_url_de_tunel(monkeypatch):
    monkeypatch.setattr(settings, "public_api_url", "https://abc.ngrok.app/")
    assert instagram.public_video_url("job_x") == \
        "https://abc.ngrok.app/api/outputs/job_x.mp4"


# ------------------------------------------------------------------ dispatch

def _job_pronto() -> str:
    job_id = db.create_job({"source_type": "tema", "source": "x"}, "Short")
    db.update_job(job_id, status="done", result_json=json.dumps({"title": "Short"}))
    (settings.outputs_dir / f"{job_id}.mp4").write_bytes(b"fake")
    return job_id


def test_dispatch_recusa_plataforma_desconhecida():
    job_id = _job_pronto()
    account_id = db.create_account("orkut", "Perfil", {"token": "x"})
    sched = db.create_schedule(job_id, account_id, "orkut", "2026-01-01T00:00:00+00:00", {})
    with pytest.raises(RuntimeError, match="não suportada"):
        dispatch(db.get_schedule(sched))


def test_dispatch_recusa_job_nao_concluido():
    job_id = db.create_job({"source_type": "tema", "source": "x"}, "Short")
    account_id = db.create_account("youtube", "Canal", {"token": "x"})
    sched = db.create_schedule(job_id, account_id, "youtube", "2026-01-01T00:00:00+00:00", {})
    with pytest.raises(RuntimeError, match="não finalizado"):
        dispatch(db.get_schedule(sched))


def test_dispatch_recusa_video_ausente():
    job_id = db.create_job({"source_type": "tema", "source": "x"}, "Short")
    db.update_job(job_id, status="done", result_json="{}")
    account_id = db.create_account("youtube", "Canal", {"token": "x"})
    sched = db.create_schedule(job_id, account_id, "youtube", "2026-01-01T00:00:00+00:00", {})
    with pytest.raises(RuntimeError, match="ausente"):
        dispatch(db.get_schedule(sched))


def test_dispatch_passa_a_capa_e_o_instante_para_o_publicador():
    """O TikTok usa o mesmo instante da capa como frame de cobertura."""
    job_id = _job_pronto()
    job_dir = settings.job_dir(job_id)
    (job_dir / "cover.jpg").write_bytes(b"fake")
    (job_dir / "cover.json").write_text(json.dumps({"at": 4.2}), encoding="utf-8")
    account_id = db.create_account("tiktok", "Perfil", {"access_token": "x"})
    sched = db.create_schedule(job_id, account_id, "tiktok",
                              "2026-01-01T00:00:00+00:00", {"title": "T"})

    recebido: dict = {}

    from app.pipeline.publishers import tiktok

    def fake_upload(video, payload, credentials, account_id):
        recebido.update(payload)
        return {"platform": "tiktok", "publish_id": "1"}

    original = tiktok.upload
    try:
        tiktok.upload = fake_upload
        dispatch(db.get_schedule(sched))
    finally:
        tiktok.upload = original

    assert recebido["cover_at"] == 4.2
    assert recebido["cover_path"].endswith("cover.jpg")
    assert recebido["job_id"] == job_id
