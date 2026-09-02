"""Orquestra o pipeline completo: ingest -> roteiro -> voz -> legenda -> fundo
-> render -> QA. Quando o QA reprova, um loop de autoajuste tenta corrigir o
problema (duração, posição de legenda, loudness, fundo) e refaz só as etapas
afetadas — não o pipeline inteiro — até aprovar ou esgotar as tentativas.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .. import db
from ..config import settings
from ..schemas import JobInput, ShortScript
from . import (broll, captions, highlights, ingest, overlays as overlay_mod, qa,
               render, script as script_mod, timeline as timeline_mod, tts)

STAGES = [
    ("ingest", 0.08),
    ("roteiro", 0.22),
    ("voz", 0.40),
    ("legendas", 0.50),
    ("fundo", 0.68),
    ("render", 0.86),
    ("qa", 1.00),
]

# etapas afetadas quando uma etapa é refeita — cascata de invalidação
CASCADE = {
    "script": {"script", "voz", "legendas", "fundo", "render"},
    "voz": {"voz", "legendas", "fundo", "render"},
    # narração já foi editada no disco (ex.: corte de silêncio): tudo que
    # depende dos timings é refeito, mas sem re-sintetizar o áudio.
    "narracao_editada": {"legendas", "fundo", "render"},
    "legendas": {"legendas", "render"},
    "fundo": {"fundo", "render"},
    "render": {"render"},
}


def run_job(job_id: str) -> dict:
    row = db.get_job(job_id)
    if row is None:
        raise RuntimeError(f"Job {job_id} não existe")

    job = JobInput(**json.loads(row["input_json"]))
    job_dir = settings.job_dir(job_id)

    def log(message: str, level: str = "info") -> None:
        db.log_event(job_id, message, level)

    def stage(name: str) -> None:
        progress = dict(STAGES).get(name, 0.0)
        db.update_job(job_id, stage=name, progress=progress, status="running")
        log(f"Etapa: {name}")

    try:
        render.ensure_ffmpeg()

        # 1. Ingestão — só acontece uma vez, mesmo com retentativas de QA
        stage("ingest")
        material = ingest.ingest(job, job_dir, log)
        log(f"Fonte: {material.kind} — {len(material.context())} caracteres de contexto")

        if (material.kind == "github" and job.background == "auto"
                and job.scroll == "nenhum"):
            job.scroll = "codigo"
            log("Repositório sem preferência de fundo — ativando rolagem de código")

        max_attempts = max(1, job.qa_max_attempts) if job.qa_autofix else 1

        short = None
        narration = None
        ass_path = None
        overlays_list: list = []
        background = None
        final = job_dir / "short.mp4"
        duration = 0.0
        report: qa.QAReport | None = None
        attempts: list[dict] = []
        dirty = set(CASCADE["script"])  # primeira passada: tudo precisa rodar

        attempt = 1
        while True:
            if attempt > 1:
                log(f"Tentativa {attempt}/{max_attempts} de ajuste")

            if "script" in dirty:
                stage("roteiro")
                override = job_dir / "script_override.json"
                if override.exists():
                    # roteiro editado à mão no editor: respeita o texto do
                    # usuário em vez de gerar outro pelo LLM
                    short = ShortScript(**json.loads(override.read_text(encoding="utf-8")))
                    log(f"Roteiro editado manualmente: {len(short.segments)} segmentos")
                else:
                    short = script_mod.build_script(job, material)
                    log(f"Roteiro: '{short.title}' com {len(short.segments)} segmentos")
                (job_dir / "script.json").write_text(short.model_dump_json(indent=2),
                                                     encoding="utf-8")

            if "voz" in dirty:
                stage("voz")
                voice = db.get_voice(job.voice_id) if job.voice_id else None
                narration_text = script_mod.full_narration(short)
                narration = tts.synthesize(narration_text, job_dir / "narration.mp3",
                                          voice, log)
                log(f"Narração: {narration.duration:.1f}s, "
                    f"{len(narration.words)} palavras cronometradas")

            duration = min(narration.duration + 0.35, float(settings.max_short_seconds))

            if "legendas" in dirty:
                stage("legendas")
                caption_words = _shift_words(narration.words, job.caption_offset)
                if job.caption_offset:
                    log(f"Ajuste manual de sincronia: {job.caption_offset:+.2f}s")
                ass_path = captions.build_ass(
                    caption_words, job_dir / "captions.ass",
                    style=job.caption_style, position=job.caption_position,
                    title=short.title if job.title_overlay else "",
                    watermark=job.watermark,
                )
                captions.build_srt(caption_words, job_dir / "captions.srt")

                overlays_list = []
                if not render.has_filter("ass"):
                    overlay_dir = job_dir / "overlays"
                    overlays_list = overlay_mod.render_captions(
                        caption_words, overlay_dir,
                        style=job.caption_style, position=job.caption_position)
                    title_ov = (overlay_mod.render_title(short.title, overlay_dir)
                                if job.title_overlay else None)
                    mark_ov = overlay_mod.render_watermark(job.watermark, overlay_dir, duration)
                    overlays_list = [o for o in (title_ov, mark_ov) if o] + overlays_list
                    (job_dir / "overlays.json").write_text(json.dumps([
                        {"file": o.path.name, "start": o.start, "end": o.end,
                         "x": o.x, "y": o.y, "kind": o.kind} for o in overlays_list],
                        indent=2), encoding="utf-8")
                    log(f"{len(overlays_list)} sobreposições geradas")

            if "fundo" in dirty:
                stage("fundo")
                background = _build_background(job, short, narration, material,
                                               job_dir, duration, log)

            if "render" in dirty:
                stage("render")
                render.compose(job_dir, background, job_dir / "narration.mp3",
                               final, job, duration, overlays=overlays_list,
                               subtitles=ass_path)
                render.make_thumbnail(final, job_dir / "thumb.jpg",
                                      at=min(1.0, duration / 4))
                log(f"Vídeo pronto: {final.name}")

            stage("qa")
            report = qa.audit(final, ass_path, expected_duration=narration.duration)
            log(f"QA: score {report.score}/100 — {'APROVADO' if report.passed else 'REPROVADO'}",
                "info" if report.passed else "warn")
            for issue in report.issues:
                log(f"[{issue.severity}] {issue.check}: {issue.message}", issue.severity)

            if report.passed or attempt >= max_attempts:
                break

            codes = {i.check for i in report.issues if i.severity in ("fatal", "erro")}
            fix = None
            if "silencio_inicial" in codes:
                trimmed = tts.trim_leading_silence(narration, job_dir / "narration_trim.mp3")
                if trimmed is not narration:
                    narration = trimmed
                    shutil.copy(narration.audio_path, job_dir / "narration.mp3")
                    narration.audio_path = job_dir / "narration.mp3"
                    fix = ("cortando silêncio no início da narração", job,
                           "narracao_editada")
            if fix is None:
                fix = qa.suggest_fix(report, job)

            if fix is None:
                log("Nenhuma correção automática aplicável — mantendo resultado atual", "warn")
                break

            action, job, root_stage = fix
            # refazer uma etapa invalida todas as que dependem dela
            dirty = CASCADE[root_stage]
            attempts.append({"attempt": attempt, "action": action,
                             "report": report.model_dump()})
            log(f"Ajuste automático: {action}")
            attempt += 1

        published_copy = settings.outputs_dir / f"{job_id}.mp4"
        shutil.copy(final, published_copy)

        # Descreve o resultado como linha do tempo editável, para o editor de
        # vídeo poder cortar/mover/reescrever sem refazer o pipeline.
        try:
            parts = sorted(job_dir.glob("hl_*.mp4")) or sorted(job_dir.glob("kb_*.mp4")) \
                or sorted(job_dir.glob("bgpart_*.mp4")) or [background]
            music_file = next(iter(job_dir.glob("music.*")), None)
            edl = timeline_mod.build_from_job(
                job_dir, narration.words, narration.duration, parts,
                job.caption_style, job.caption_position, job.watermark,
                music=music_file, music_gain=job.music_volume,
            )
            timeline_mod.save(job_dir, edl)
            log(f"Linha do tempo: {len(edl.video)} clipe(s), {len(edl.captions)} legenda(s)")
        except Exception as exc:  # noqa: BLE001 — editor é opcional, não derruba o job
            log(f"Não foi possível montar a linha do tempo: {exc}", "warn")

        result = {
            "title": short.title,
            "description": short.description,
            "hashtags": short.hashtags,
            "duration": round(duration, 2),
            "video": f"/api/jobs/{job_id}/file/short.mp4",
            "thumbnail": f"/api/jobs/{job_id}/file/thumb.jpg",
            "captions_srt": f"/api/jobs/{job_id}/file/captions.srt",
            "script": short.model_dump(),
            "words": narration.words,
            "source_kind": material.kind,
            "edit_mode": job.edit_mode,
            "qa_attempts": attempts,
        }
        db.update_job(job_id, status="done", stage="qa", progress=1.0,
                      title=short.title,
                      result_json=json.dumps(result),
                      qa_json=report.model_dump_json())
        return result

    except Exception as exc:  # noqa: BLE001 — erro precisa chegar na UI
        db.log_event(job_id, f"Falha: {exc}", "error")
        db.update_job(job_id, status="error", error=str(exc))
        raise


def _shift_words(words: list[dict], offset: float) -> list[dict]:
    """Desloca todos os timings da legenda — o ajuste fino manual do editor,
    para quando a voz do provedor tem um atraso de ataque perceptível."""
    if not offset:
        return words
    return [{"word": w["word"],
             "start": round(max(w["start"] + offset, 0.0), 3),
             "end": round(max(w["end"] + offset, 0.0), 3)} for w in words]


def _build_background(job: JobInput, short, narration, material, job_dir: Path,
                      duration: float, log) -> Path:
    out = job_dir / "background.mp4"
    mode = job.background

    if mode == "auto":
        if material.kind == "imagem":
            mode = "imagem_kenburns"
        elif material.kind == "video" and material.video_path:
            mode = "video_fonte"
        elif settings.pexels_api_key or settings.pixabay_api_key:
            mode = "broll"
        else:
            mode = "gradiente"

    # "pan" é consumido dentro das funções de fundo (crop animado); "texto" e
    # "codigo" são painéis sobrepostos depois, então sobrevivem a este ponto.
    applied_scroll = "nenhum" if job.scroll == "pan" else job.scroll

    if mode == "imagem_kenburns":
        if not material.image_paths:
            raise RuntimeError("Modo imagem selecionado sem nenhuma imagem enviada.")
        durations = script_mod.segment_durations_covering(
            short, narration.words, duration) or [duration]
        images = material.image_paths
        cycled = [images[i % len(images)] for i in range(len(durations))]
        log(f"Fundo: {len(images)} imagem(ns) com efeito Ken Burns")
        render.background_from_images_kenburns(cycled, durations, out, job_dir)
        applied_scroll = "nenhum"  # não faz sentido rolar texto sobre Ken Burns

    elif mode == "video_fonte" and material.video_path:
        if job.edit_mode == "resumo":
            seg_durations = script_mod.segment_durations_covering(
                short, narration.words, duration)
            sources = material.video_paths or [material.video_path]
            windows = highlights.windows_across_sources(
                sources, len(seg_durations), seg_durations, log)
            log(f"Fundo: {len(windows)} destaque(s) de {len(sources)} vídeo(s)")
            render.background_from_multi_highlights(windows, out, job_dir,
                                                    durations=seg_durations)
        elif len(material.video_paths) > 1:
            log(f"Fundo: {len(material.video_paths)} vídeos em sequência, 9:16")
            render.background_from_clips(material.video_paths, duration, out, job_dir)
        else:
            log("Fundo: vídeo de origem recortado para 9:16")
            render.background_from_video(material.video_path, duration, out,
                                         scroll=job.scroll)

    elif mode == "broll":
        queries = [job.background_query] if job.background_query else \
            [s.broll_query for s in short.segments if s.broll_query]
        log(f"Fundo: B-roll para {queries[:4]}")
        clips = broll.fetch_for_queries(queries[:5], log)
        if clips:
            render.background_from_clips(clips, duration, out, job_dir)
        else:
            log("Nenhum B-roll encontrado; caindo para gradiente", "warn")
            render.background_gradient(duration, job.niche, out, scroll=job.scroll)

    elif mode == "codigo_scroll":
        log("Fundo: gradiente com rolagem de código")
        render.background_gradient(duration, job.niche, out, scroll="nenhum")
        applied_scroll = "codigo"

    elif mode == "ia_video":
        from . import videogen
        prompt = job.background_query or short.segments[0].broll_query or short.title
        videogen.generate_clip(prompt, duration, out, log=log)

    else:
        log("Fundo: gradiente gerado")
        render.background_gradient(duration, job.niche, out, scroll=job.scroll)

    if applied_scroll in ("texto", "codigo"):
        # Para repositório, rolar o conteúdo dos arquivos (código de verdade) em
        # vez de context(), que começa pela árvore de diretórios.
        if applied_scroll == "codigo" and material.text:
            source_text = material.text[:4000]
        else:
            source_text = material.context(limit=4000) or script_mod.full_narration(short)
        log(f"Aplicando scroll de {'código' if applied_scroll == 'codigo' else 'texto'}")
        panel = overlay_mod.render_scroll_panel(
            source_text, job_dir, mono=(applied_scroll == "codigo"))
        scrolled = job_dir / "background_scroll.mp4"
        render.add_scroll_panel(out, panel, duration, scrolled)
        return render.ensure_min_duration(scrolled, duration, job_dir)

    return render.ensure_min_duration(out, duration, job_dir)
