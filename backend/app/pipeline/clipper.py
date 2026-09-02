"""Fatiamento de um vídeo longo em vários shorts ("clipes virais").

Recebe a transcrição inteira de um vídeo de 20min–2h e pede ao LLM que escolha
os N momentos que funcionam sozinhos como short: cada um com começo/fim em
segundos, um gancho e um motivo. Cada trecho escolhido vira um job próprio,
com seu roteiro, narração, legenda e QA — ou seja, N shorts independentes a
partir de um upload só.
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
    """Escolhe até `count` trechos do vídeo que funcionam como short sozinhos."""
    if not transcript.strip():
        raise RuntimeError(
            "Sem transcrição não dá para escolher os melhores momentos. "
            "Instale faster-whisper ou use um vídeo com legenda disponível."
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

    data = llm.complete_json(SYSTEM, prompt, SCHEMA)
    clips = data.get("clipes", [])

    valid: list[dict] = []
    for clip in clips:
        start = max(float(clip.get("inicio", 0)), 0.0)
        end = min(float(clip.get("fim", 0)), total_duration)
        if end - start < settings.min_short_seconds * 0.5:
            continue  # curto demais para virar short
        if any(start < v["fim"] and end > v["inicio"] for v in valid):
            continue  # sobreposto a um clipe já aceito
        valid.append({
            "inicio": round(start, 2),
            "fim": round(end, 2),
            "titulo": clip.get("titulo", "").strip() or "Clipe",
            "motivo": clip.get("motivo", "").strip(),
            "assunto": clip.get("assunto", "").strip(),
        })

    valid.sort(key=lambda c: c["inicio"])
    log(f"{len(valid)} clipe(s) aproveitável(is) de {len(clips)} sugerido(s)")
    return valid[:count]


def transcript_with_timestamps(segments) -> str:
    """Formata segmentos do whisper como '[MM:SS] texto' — sem os tempos o LLM
    não tem como devolver início/fim confiáveis."""
    lines = []
    for seg in segments:
        minutes, seconds = divmod(int(seg["start"]), 60)
        lines.append(f"[{minutes:02d}:{seconds:02d}] {seg['text'].strip()}")
    return "\n".join(lines)
