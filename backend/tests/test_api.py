"""Rotas HTTP: validação de entrada, serialização e as regras de publicação."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.schemas import JobInput

client = TestClient(app)


def _job(**overrides) -> str:
    payload = JobInput(source_type="tema", source="tema de teste", **overrides)
    return db.create_job(payload.model_dump(), "Short de teste")


def _finish(job_id: str, passed: bool = True, score: int = 92) -> None:
    db.update_job(
        job_id, status="done", stage="qa", progress=1.0,
        result_json=json.dumps({"title": "Short de teste", "description": "",
                                "hashtags": [], "duration": 42.0,
                                "script": {"segments": [
                                    {"kind": "hook", "text": "Ninguém avisou disso"},
                                    {"kind": "cta", "text": "Segue pra mais."}]}}),
        qa_json=json.dumps({"passed": passed, "score": score, "issues": [], "metrics": {}}),
    )


def test_health_expoe_a_cadeia_de_modelos():
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert isinstance(body["llm_chain"], list)
    if body["llm_provider"] == "chain":
        assert body["llm_chain"], "chain configurada deveria listar os elos"
        assert {"provider", "model", "ready"} <= body["llm_chain"][0].keys()


def test_criar_job_sem_origem_da_400():
    r = client.post("/api/jobs", json={"source_type": "tema", "source": "  "})
    assert r.status_code == 400


def test_criar_job_imagem_sem_anexo_da_400():
    r = client.post("/api/jobs", json={"source_type": "imagem", "source": ""})
    assert r.status_code == 400
    assert "imagem" in r.json()["detail"].lower()


def test_listar_jobs_serializa_json_e_metricas():
    job_id = _job()
    _finish(job_id)
    rows = client.get("/api/jobs").json()
    row = next(r for r in rows if r["id"] == job_id)
    assert isinstance(row["input"], dict) and "input_json" not in row
    assert row["result"]["duration"] == 42.0
    assert row["qa"]["passed"] is True
    assert row["metrics"] is None       # nada publicado ainda


def test_detalhe_do_job_traz_llm_calls_e_retomada():
    job_id = _job()
    db.log_llm_call(job_id, "roteiro", "claude_cli", "claude-fable-5-1", 12.5, True)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert len(body["llm_calls"]) == 1
    assert body["llm_calls"][0]["model"] == "claude-fable-5-1"
    assert body["resumable_from"] is None    # sem artefatos no disco


def test_job_inexistente_da_404():
    assert client.get("/api/jobs/job_nao_existe").status_code == 404


def test_arquivo_fora_da_lista_branca_da_404():
    job_id = _job()
    r = client.get(f"/api/jobs/{job_id}/file/../../../etc/passwd")
    assert r.status_code == 404


def test_retry_com_etapa_invalida_da_400():
    job_id = _job()
    _finish(job_id)
    assert client.post(f"/api/jobs/{job_id}/retry?from=inventada").status_code == 400


def test_publicar_job_nao_concluido_da_400():
    job_id = _job()
    account_id = db.create_account("youtube", "Canal", {"token": "x"})
    r = client.post("/api/publish", json={
        "job_id": job_id, "account_id": account_id, "platform": "youtube"})
    assert r.status_code == 400


def test_publicar_com_qa_fatal_e_bloqueado():
    job_id = _job()
    db.update_job(job_id, status="done",
                  result_json=json.dumps({"title": "x", "hashtags": []}),
                  qa_json=json.dumps({"passed": False, "score": 20, "metrics": {},
                                      "issues": [{"check": "proporcao", "severity": "fatal",
                                                  "message": "não é 9:16", "fix": ""}]}))
    account_id = db.create_account("youtube", "Canal", {"token": "x"})
    r = client.post("/api/publish", json={
        "job_id": job_id, "account_id": account_id, "platform": "youtube"})
    assert r.status_code == 400
    assert "fatal" in r.json()["detail"].lower()


def test_publicar_cria_agendamento_pendente():
    job_id = _job()
    _finish(job_id)
    account_id = db.create_account("tiktok", "Perfil", {"access_token": "x"})
    r = client.post("/api/publish", json={
        "job_id": job_id, "account_id": account_id, "platform": "tiktok",
        "title": "Título", "privacy": "private"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert db.get_schedule(body["schedule_id"])["platform"] == "tiktok"


@pytest.mark.parametrize("platform", ["youtube", "tiktok", "instagram", "linkedin"])
def test_publicar_aceita_as_quatro_plataformas(platform):
    job_id = _job()
    _finish(job_id)
    account_id = db.create_account(platform, "Conta", {"access_token": "x"})
    r = client.post("/api/publish", json={
        "job_id": job_id, "account_id": account_id, "platform": platform})
    assert r.status_code == 200, r.text


def test_publicar_plataforma_desconhecida_da_422():
    job_id = _job()
    _finish(job_id)
    account_id = db.create_account("youtube", "Canal", {"token": "x"})
    r = client.post("/api/publish", json={
        "job_id": job_id, "account_id": account_id, "platform": "orkut"})
    assert r.status_code == 422


def test_metricas_vazias_tem_resumo_zerado():
    body = client.get("/api/metrics").json()
    assert body["summary"]["published"] == 0
    assert body["items"] == []


def test_metricas_agregam_por_job():
    job_id = _job()
    _finish(job_id)
    account_id = db.create_account("youtube", "Canal", {"token": "x"})
    sched = db.create_schedule(job_id, account_id, "youtube", "2026-01-01T00:00:00+00:00", {})
    db.upsert_metrics(sched, job_id, "youtube", "vid123", "https://y/1",
                      {"views": 12000, "likes": 800, "comments": 40,
                       "avg_view_pct": 61.5})
    body = client.get("/api/metrics").json()
    assert body["summary"]["views"] == 12000
    assert body["summary"]["avg_retention"] == 61.5
    assert body["items"][0]["video_id"] == "vid123"

    row = next(r for r in client.get("/api/jobs").json() if r["id"] == job_id)
    assert row["metrics"]["views"] == 12000


def test_upsert_metrics_atualiza_em_vez_de_duplicar():
    job_id = _job()
    account_id = db.create_account("youtube", "Canal", {"token": "x"})
    sched = db.create_schedule(job_id, account_id, "youtube", "2026-01-01T00:00:00+00:00", {})
    db.upsert_metrics(sched, job_id, "youtube", "vid", "", {"views": 10})
    db.upsert_metrics(sched, job_id, "youtube", "vid", "", {"views": 999})
    rows = db.metrics_for_job(job_id)
    assert len(rows) == 1 and rows[0]["views"] == 999


def test_clip_plan_fora_do_limite_da_400():
    assert client.post("/api/clips", json={"attachment_id": "upl_x", "count": 99}).status_code == 400


def test_render_de_plano_nao_pronto_da_400():
    plan_id = db.create_clip_plan("upl_x", 3, 45, {"niche": "tecnologia"})
    r = client.post(f"/api/clips/{plan_id}/render", json={"selected": [0]})
    assert r.status_code == 400


def test_outputs_recusa_nome_fora_do_padrao():
    assert client.get("/api/outputs/../../etc/passwd").status_code == 404
    assert client.get("/api/outputs/qualquer.txt").status_code == 404


def test_connectors_listam_notificacao_e_publicadores():
    body = client.get("/api/connectors").json()
    por_id = {c["id"]: c for c in body}
    assert {"telegram", "discord", "webhook"} <= por_id.keys()
    assert por_id["telegram"]["category"] == "notificacao"
    # Instagram e LinkedIn saíram de "planejado" e agora publicam
    assert por_id["instagram"]["status"] != "planejado"
    assert por_id["linkedin"]["status"] != "planejado"


def test_trends_responde_com_estrutura_esperada(monkeypatch):
    from app.pipeline import trends

    monkeypatch.setattr(trends, "fetch", lambda niche, geo: [
        {"source": "Google Trends", "title": "assunto quente", "snippet": "",
         "url": "https://x", "heat": 90, "heat_label": "50k+ buscas"}])
    body = client.get("/api/trends?niche=tecnologia").json()
    assert body["niche"] == "tecnologia"
    assert body["items"][0]["title"] == "assunto quente"
