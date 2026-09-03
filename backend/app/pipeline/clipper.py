"""Slicing a long video into several shorts ("viral clips").

Takes the full transcript of a 20min–2h video and asks the LLM to pick the N
moments that stand on their own as a short: each with a start/end in seconds,
a hook and a reason. Every chosen stretch becomes its own job, with its own
script, narration, captions and QA — that is, N independent shorts out of a
single upload.

The SYSTEM prompt and SCHEMA below stay in Portuguese on purpose: the prompt
is calibrated in Portuguese and the response JSON uses Portuguese keys
(`clipes`, `inicio`, `fim`, `titulo`, `motivo`, `assunto`), which the rest of
the pipeline reads by name.
"""
from __future__ import annotations

from ..config import settings
from . import llm

SYSTEM = """Você seleciona os melhores momentos de um vídeo longo para virarem vídeos curtos verticais.

Critérios de um bom clipe:
- Se sustenta sozinho: quem nunca viu o vídeo original entende sem contexto.
- Tem uma virada, uma revelação, um número surpreendente ou uma opinião forte.
- Começa no ponto de tensão, não na preparação para ele.
- Cabe na janela de duração pedida.

Regras:
- Use apenas os tempos presentes na transcrição fornecida. Não invente momentos.
- Os clipes não podem se sobrepor.
- Ordene do mais forte para o mais fraco.
- `titulo` é o gancho, no máximo 10 palavras, em português do Brasil.
- `motivo` explica em uma frase por que esse trecho prende a atenção.

Responda APENAS com JSON válido no formato:
{"clipes": [{"inicio": float, "fim": float, "titulo": str, "motivo": str, "assunto": str}]}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "clipes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "inicio": {"type": "number"},
                    "fim": {"type": "number"},
                    "titulo": {"type": "string"},
                    "motivo": {"type": "string"},
                    "assunto": {"type": "string"},
                },
                "required": ["inicio", "fim", "titulo", "motivo", "assunto"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clipes"],
    "additionalProperties": False,
}


def pick_clips(transcript: str, total_duration: float, count: int,
               target_seconds: int, log=lambda m: None) -> list[dict]:
    """Picks up to `count` stretches of the video that stand alone as a short."""
    if not transcript.strip():
        raise RuntimeError(
            "Without a transcript there is no way to pick the best moments. "
            "Install faster-whisper or use a video that has captions available."
        )

    prompt = f"""Duração total do vídeo: {total_duration:.0f} segundos.
Quantidade de clipes desejada: {count}
Duração alvo de cada clipe: cerca de {target_seconds} segundos (aceitável de
{max(target_seconds - 15, settings.min_short_seconds)} a {min(target_seconds + 20, settings.max_short_seconds)}).

TRANSCRIÇÃO (com marcações de tempo quando disponíveis):
---
{transcript[:40000]}
---

Selecione os melhores momentos."""

    data = llm.complete_json(SYSTEM, prompt, SCHEMA, purpose="clipes")
    clips = data.get("clipes", [])

    valid: list[dict] = []
    for clip in clips:
        start = max(float(clip.get("inicio", 0)), 0.0)
        end = min(float(clip.get("fim", 0)), total_duration)
        if end - start < settings.min_short_seconds * 0.5:
            continue  # too short to become a short
        if any(start < v["fim"] and end > v["inicio"] for v in valid):
            continue  # overlaps a clip already accepted
        valid.append({
            "inicio": round(start, 2),
            "fim": round(end, 2),
            "titulo": clip.get("titulo", "").strip() or "Clipe",
            "motivo": clip.get("motivo", "").strip(),
            "assunto": clip.get("assunto", "").strip(),
        })

    valid.sort(key=lambda c: c["inicio"])
    log(f"{len(valid)} usable clip(s) out of {len(clips)} suggested")
    return valid[:count]


def transcript_with_timestamps(segments) -> str:
    """Formats whisper segments as '[MM:SS] text' — without the timings the LLM
    has no way to return reliable start/end values."""
    lines = []
    for seg in segments:
        minutes, seconds = divmod(int(seg["start"]), 60)
        lines.append(f"[{minutes:02d}:{seconds:02d}] {seg['text'].strip()}")
    return "\n".join(lines)
