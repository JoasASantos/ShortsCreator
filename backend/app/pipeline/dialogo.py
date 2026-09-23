"""Personagens conversando, e a conversa contando a história.

O formato: dois ou três personagens na tela, cada um com a sua voz e a sua
cara, revezando falas curtas. A teoria, a história ou a explicação aparece na
troca — ninguém narra, eles descobrem junto com quem assiste. Por baixo, a
footage que segura o dedo (gameplay, parkour), que é o modo `video_fundo`.

Por que é um pipeline próprio e não o de narração com outra voz: o roteiro
normal é um bloco de narração com uma voz do começo ao fim. Aqui cada fala tem
um dono, e o dono decide três coisas ao mesmo tempo — qual voz sintetiza, qual
imagem aparece, de que lado da tela ela fica. Isso não é uma variação de
parâmetro, é outra montagem.

O que NÃO muda: sai uma Timeline igual à de todo mundo. Editor, QA, capa e
publicação funcionam sem saber que o roteiro tinha personagens.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .. import db
from ..config import settings
from . import (captions as captions_mod, elenco as elenco_mod, fundos, llm,
               notify, render, script as script_mod, timeline as timeline_mod,
               timeline_render, tts)
from .timeline import AudioClip, CaptionCue, MediaOverlay, Timeline, VideoClip

# O que `JobInput.edit_mode` carrega para uma conversa.
MODE = "dialogo"

# Respiro entre uma fala e a próxima. Sem isso a conversa atropela e soa como
# uma pessoa só lendo dois papéis; muito mais que isso e a troca morre.
GAP = 0.18

# Falas por vídeo. Um short de 60s com falas de 3s cabe ~18; acima disso o
# modelo começa a escrever um roteiro de cinema em vez de um short.
MAX_LINES = 28


@dataclass
class Fala:
    """Uma fala: de quem é, o que diz, e onde ficou no tempo."""
    person_id: str
    name: str
    text: str
    audio: Path | None = None
    start: float = 0.0
    seconds: float = 0.0


@dataclass
class Conversa:
    lines: list[Fala] = field(default_factory=list)
    title: str = ""
    description: str = ""
    hashtags: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return round(max((l.start + l.seconds for l in self.lines), default=0.0), 3)


DIALOGO_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"speaker": {"type": "string"},
                               "text": {"type": "string"}},
                "required": ["speaker", "text"],
            },
        },
    },
    "required": ["title", "description", "hashtags", "lines"],
}

DIALOGO_SYSTEM = """Você escreve conversas curtas para vídeo vertical.

Dois ou três personagens na tela, revezando falas. A história, a teoria ou a
explicação aparece NA TROCA — ninguém narra e ninguém dá aula. Eles descobrem
junto com quem assiste.

COMO ESCREVER
- Uma fala por vez, com o nome de quem fala. Só nomes do elenco recebido.
- Fala curta: uma ideia por fala, raramente mais de 20 palavras. É conversa,
  não monólogo dividido em pedaços.
- A primeira fala é o gancho e tem que funcionar sozinha: uma pergunta, uma
  afirmação absurda, um "você sabia que". Sem "fala galera".
- Revezem de verdade. Um personagem que fala cinco vezes seguidas virou
  narrador, e aí o formato perdeu a graça.
- Cada personagem tem um jeito: quem pergunta pergunta, quem explica explica,
  quem duvida duvida. Mantenha isso do começo ao fim.
- Termine no ponto mais alto, não numa despedida. "E é por isso que..." mata
  a retenção que o resto construiu.
- Tudo em {language}, escrito para ser DITO.

O QUE NÃO FAZER
- Não escreva narração, rubrica, ação entre parênteses, nem nome no meio da
  fala. Só o que sai da boca.
- Não invente fato como se fosse certeza. Teoria é teoria, e a conversa pode
  dizer isso sem estragar a graça."""


def write(subject: str, cast: list[elenco_mod.Personagem], seconds: int = 45,
          instruction: str = "", language: str = "pt-BR",
          log=lambda m, level="info": None) -> Conversa:
    """A conversa sobre `subject`, escrita para este elenco."""
    if not subject.strip():
        raise ValueError("Diga sobre o que é a conversa.")
    if len(cast) < 2:
        raise ValueError(
            "Uma conversa precisa de pelo menos dois personagens. Crie-os na "
            "tela de Elenco: nome, voz e imagem.")

    # ~2 palavras/s faladas, e uma fala média de 12 palavras: é assim que se
    # sabe quantas falas cabem no tempo pedido, em vez de escrever demais e
    # cortar depois.
    wanted = max(4, min(int(seconds * 2 / 12), MAX_LINES))
    system = DIALOGO_SYSTEM.replace("{language}",
                                    script_mod.language_name(language))
    prompt = f"""ELENCO (use só estes nomes):
{elenco_mod.cast_brief(cast)}

ASSUNTO: {subject.strip()}
Duração alvo: {seconds}s — cerca de {wanted} falas.
{f"Instrução: {instruction.strip()}" if instruction.strip() else ""}

Escreva a conversa."""

    data = llm.complete_json(system, prompt, DIALOGO_SCHEMA, max_tokens=3000,
                            purpose="dialogo")
    lines: list[Fala] = []
    unknown: set[str] = set()
    for item in (data.get("lines") or [])[:MAX_LINES]:
        text = str(item.get("text") or "").strip()
        speaker = str(item.get("speaker") or "").strip()
        if not text:
            continue
        person = elenco_mod.by_name(speaker)
        if person is None or all(person.id != c.id for c in cast):
            # O modelo inventou alguém. A fala continua valendo: ela vai para
            # o primeiro do elenco, e o log diz o que aconteceu — perder uma
            # fala boa por causa de um nome é pior.
            unknown.add(speaker or "(sem nome)")
            person = cast[0]
        lines.append(Fala(person_id=person.id, name=person.name, text=text))

    if not lines:
        raise RuntimeError("O modelo não devolveu nenhuma fala.")
    if unknown:
        log(f"Falas atribuídas a quem não está no elenco ({', '.join(sorted(unknown))}) "
            f"foram para {cast[0].name}.", "warn")

    speakers = {line.person_id for line in lines}
    if len(speakers) < 2:
        log("A conversa saiu com um personagem só — vai soar como narração.",
            "warn")

    return Conversa(lines=lines,
                    title=str(data.get("title") or subject.strip())[:120],
                    description=str(data.get("description") or ""),
                    hashtags=[h if h.startswith("#") else f"#{h}"
                              for h in (data.get("hashtags") or [])][:8])


def speak(conversa: Conversa, job_dir: Path, cast: dict[str, elenco_mod.Personagem],
          language: str, log=lambda m, level="info": None) -> None:
    """Sintetiza cada fala com a voz do seu dono e as põe em sequência."""
    work = job_dir / "falas"
    work.mkdir(parents=True, exist_ok=True)

    cursor = 0.0
    for index, line in enumerate(conversa.lines):
        person = cast[line.person_id]
        voice = db.get_voice(person.voice_id) if person.voice_id else None
        dest = work / f"fala_{index:03d}.mp3"
        narration = tts.synthesize(
            line.text, dest, voice,
            lambda m, level="info": log(str(m), level), language=language)
        line.audio = narration.audio_path
        line.seconds = narration.duration
        line.start = round(cursor, 3)
        cursor += line.seconds + GAP

    log(f"{len(conversa.lines)} fala(s), {conversa.duration:.1f}s de conversa")


def build_timeline(job_dir: Path, conversa: Conversa,
                   cast: dict[str, elenco_mod.Personagem],
                   background: Path, job) -> Timeline:
    """A conversa como timeline: fundo inteiro, falas em sequência, cada
    personagem aparecendo enquanto fala."""
    duration = conversa.duration
    video = [VideoClip(id=timeline_mod._new_id("v"),  # noqa: SLF001
                       source=background.name, in_point=0.0,
                       out_point=round(duration, 3), start=0.0,
                       kind="video", mute=True)]

    audio: list[AudioClip] = []
    cues: list[CaptionCue] = []
    media: list[MediaOverlay] = []

    for line in conversa.lines:
        if line.audio is None:
            continue
        audio.append(AudioClip(
            id=timeline_mod._new_id("a"),  # noqa: SLF001
            source=str(Path(line.audio).relative_to(job_dir)),
            in_point=0.0, out_point=round(line.seconds, 3),
            start=line.start, gain=1.0, role="narration"))

        # A legenda é a fala, palavra a palavra dentro do próprio espaço dela.
        for word in tts.estimate_words(line.text, max(line.seconds, 0.4)):
            cues.append(CaptionCue(
                id=timeline_mod._new_id("c"),  # noqa: SLF001
                text=word["word"],
                start=round(line.start + word["start"], 3),
                end=round(line.start + word["end"], 3)))

        person = cast[line.person_id]
        if person.image and Path(person.image).exists():
            picture = job_dir / f"pers_{person.id}{Path(person.image).suffix}"
            if not picture.exists():
                shutil.copy(person.image, picture)
            geometry = person.geometry()
            media.append(MediaOverlay(
                id=timeline_mod._new_id("m"),  # noqa: SLF001
                source=picture.name,
                start=line.start,
                # Um respiro depois da fala: a imagem sumir no exato fim da
                # última sílaba pisca.
                end=round(line.start + line.seconds + GAP, 3),
                x=geometry["x"], y=geometry["y"], width=geometry["width"],
                kind="image"))

    return Timeline(
        duration=round(duration, 3), video=video, audio=audio, captions=cues,
        media=media, caption_style=job.caption_style,
        caption_position=job.caption_position, watermark=job.watermark,
        watermark_position=job.watermark_position,
        watermark_size=job.watermark_size,
        watermark_opacity=job.watermark_opacity,
    ).normalize()


def run(job_id: str, job, job_dir: Path, log, stage) -> dict:
    """Escrever a conversa, dar voz a cada um, montar e renderizar."""
    from . import qa as qa_mod

    render.ensure_ffmpeg()

    stage("roteiro")
    cast = [p for p in (elenco_mod.get(pid) for pid in job.cast) if p]
    if len(cast) < 2:
        raise RuntimeError(
            "Escolha pelo menos dois personagens para a conversa. Eles são "
            "criados na tela de Elenco, com nome, voz e imagem.")
    conversa = write(job.source, cast, job.duration, job.instruction,
                     job.language, log)
    log(f"Conversa: {conversa.title}")

    stage("voz")
    by_id = {p.id: p for p in cast}
    speak(conversa, job_dir, by_id, job.language, log)

    stage("fundo")
    background = _background(job, job_dir, conversa.duration, log)

    stage("legendas")
    words = [{"word": w["word"], "start": round(line.start + w["start"], 3),
              "end": round(line.start + w["end"], 3)}
             for line in conversa.lines
             for w in tts.estimate_words(line.text, max(line.seconds, 0.4))]
    ass_path = captions_mod.build_ass(
        words, job_dir / "captions.ass", style=job.caption_style,
        position=job.caption_position, title="", watermark=job.watermark,
        watermark_position=job.watermark_position,
        watermark_size=job.watermark_size,
        watermark_opacity=job.watermark_opacity)
    captions_mod.build_srt(words, job_dir / "captions.srt")

    edl = build_timeline(job_dir, conversa, by_id, background, job)
    timeline_mod.save(job_dir, edl)

    stage("render")
    final = job_dir / "short.mp4"
    timeline_render.render_timeline(job_dir, edl, final, log=lambda m: log(m))
    render.make_thumbnail(final, job_dir / "thumb.jpg",
                          at=min(1.0, edl.duration / 4))

    stage("qa")
    report = qa_mod.audit(final, ass_path, expected_duration=edl.duration)
    log(f"QA: score {report.score}/100 — "
        f"{'PASSED' if report.passed else 'FAILED'}")

    shutil.copy(final, settings.outputs_dir / f"{job_id}.mp4")

    result = {
        "mode": MODE,
        "title": conversa.title,
        "description": conversa.description,
        "hashtags": conversa.hashtags,
        "duration": round(edl.duration, 2),
        "video": f"/api/jobs/{job_id}/file/short.mp4",
        "thumbnail": f"/api/jobs/{job_id}/file/thumb.jpg",
        "captions_srt": f"/api/jobs/{job_id}/file/captions.srt",
        "words": words,
        "source_kind": "dialogo",
        "edit_mode": job.edit_mode,
        "cast": [p.as_dict() for p in cast],
        "lines": [{"speaker": line.name, "text": line.text,
                   "start": line.start, "seconds": round(line.seconds, 2)}
                  for line in conversa.lines],
    }
    (job_dir / "dialogo.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    db.update_job(job_id, status="done", stage="qa", progress=1.0,
                  title=conversa.title,
                  result_json=json.dumps(result, ensure_ascii=False),
                  qa_json=report.model_dump_json())
    notify.job_done(job_id, conversa.title, edl.duration, report.score,
                    report.passed)
    return result


def _background(job, job_dir: Path, duration: float, log) -> Path:
    """A footage por baixo. Gameplay é o caso comum; gradiente é o que sobra."""
    out = job_dir / "background.mp4"
    chosen = (job.fundo or "").strip()
    if chosen:
        fundo = fundos.get(chosen) or fundos.fetch(chosen, log)
        return fundos.build(fundo, duration, out, seed=job_dir.name,
                            fill=job.background_fill, log=log)
    log("Sem footage escolhida: a conversa vai sobre um gradiente. Guarde um "
        "gameplay na tela de Fundos para o formato ficar completo.", "warn")
    render.background_gradient(duration, job.niche, out, scroll="nenhum")
    return out
