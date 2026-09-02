"""Geração de roteiro do short a partir do material de origem."""
from __future__ import annotations

import json
import re

from ..schemas import JobInput, ScriptSegment, ShortScript
from . import llm
from .ingest import SourceMaterial

NICHE_GUIDE = {
    "tecnologia": "Foque em impacto prático e no 'porquê isso importa agora'. Cite números e nomes reais.",
    "ciberseguranca": "Explique o vetor de ataque e a defesa. Sem instruções ofensivas acionáveis — só conceito e mitigação.",
    "programacao": "Mostre o problema, o truque e o ganho. Vale citar sintaxe curta na tela.",
    "cinema": "Use tensão narrativa, curiosidades de bastidor e referências visuais.",
    "historia": "Comece pelo detalhe chocante, depois contextualize a data e as consequências.",
    "ciencia": "Traduza o mecanismo em analogia concreta. Precisão factual acima de hype.",
    "curiosidades": "Fato surpreendente na primeira frase, sequência de reviravoltas rápidas.",
    "negocios": "Números, decisão e consequência. Evite jargão vazio.",
    "generico": "Priorize clareza, ritmo e uma ideia central por short.",
}

BASE_RULES = """Regras absolutas:
- O primeiro segmento é um HOOK de no máximo 12 palavras que gera curiosidade ou choque. Sem "olá", sem "hoje eu vou falar".
- Frases curtas, faladas. Você escreve para OUVIR, não para ler. Nada de bullets, markdown, emoji ou parênteses no texto narrado.
- Nenhum número escrito por extenso incorretamente: escreva "2024" e "70%" do jeito que se fala.
- Densidade: ~2,6 palavras por segundo de narração.
- Cada segmento traz `broll_query` em INGLÊS (2-4 palavras) para buscar vídeo de fundo, e `on_screen` com no máximo 5 palavras para texto de destaque.
- Último segmento é o CTA, curto.
- Fidelidade factual: use apenas fatos presentes no material fornecido. Se o material for raso, mantenha afirmações genéricas em vez de inventar dados."""

KIND_ADDENDUM = {
    "github": (
        "Você está narrando sobre um REPOSITÓRIO DE CÓDIGO. Explique o que o projeto faz, "
        "qual problema resolve, a stack/linguagem principal e algo que se destaque na "
        "arquitetura (um padrão, uma decisão de design, um trecho de código interessante). "
        "Fale como quem apresenta um projeto open source pra outro programador — direto, "
        "sem elogio vazio tipo 'código limpo'. Se houver um arquivo de entrada (app.py, "
        "index.ts, main.go), pode citar seu papel."
    ),
    "imagem": (
        "Você está narrando sobre uma ou mais IMAGENS/FOTOS estáticas, sem vídeo de fundo — "
        "a câmera vai só dar zoom/pan lento sobre elas. Escreva como quem descreve, contextualiza "
        "ou conta uma história a partir do que está na imagem, sem depender de movimento na tela."
    ),
    "roteiro": "",  # não passa por LLM — ver build_script
}

ANGLE_GUIDE = {
    "auto": "",
    "critica": (
        "Faça uma CRÍTICA da obra: um julgamento com fundamento — o que funciona, "
        "o que não funciona e por quê. Tenha uma posição clara em vez de resumo neutro."
    ),
    "contexto": (
        "Conte o CONTEXTO e os bastidores da obra: como foi feita, o que estava "
        "acontecendo na época, decisões de produção, recepção do público."
    ),
    "analise": (
        "ANALISE em profundidade: desmonte o mecanismo, a técnica ou a construção. "
        "Mostre como aquilo foi feito e por que produz o efeito que produz."
    ),
    "historia": (
        "CONTE UMA HISTÓRIA com começo, tensão e desfecho. Narrativa acima de lista de fatos."
    ),
    "explicacao": (
        "EXPLIQUE o assunto para quem nunca ouviu falar. Use analogia concreta e "
        "evite jargão sem tradução."
    ),
    "enredo": (
        "RESUMA O ENREDO da obra: o que acontece, com quem e como termina, sem "
        "estragar a experiência mais do que o necessário para o short fazer sentido."
    ),
    "curiosidade": (
        "Traga CURIOSIDADES e fatos pouco conhecidos, em sequência rápida, cada um "
        "mais surpreendente que o anterior."
    ),
    "tutorial": (
        "Ensine COMO FAZER, em passos curtos e acionáveis, na ordem de execução."
    ),
}

VISUAL_IS_SUPPORT = (
    "IMPORTANTE — o material visual é APOIO, não o assunto. Não descreva o que "
    "aparece na tela nem narre a cena passo a passo. Fale do tema pedido usando "
    "as imagens só como ilustração de fundo."
)

RESUMO_ADDENDUM = (
    "Você está fazendo um RESUMO CONDENSADO de um vídeo/episódio bem mais longo que o short "
    "final. Distribua os pontos ao longo de TODO o material (não só o começo) — cubra "
    "abertura, desenvolvimento e conclusão/desfecho na proporção certa. Cite o que teria "
    "acontecido cronologicamente, sem se prender a um único trecho. Seja um resumo analítico, "
    "não uma transcrição truncada."
)

SYSTEM_TEMPLATE = """Você é roteirista sênior de vídeos curtos verticais (YouTube Shorts, TikTok, Reels) em português do Brasil.

{addendum}

{rules}

Responda APENAS com JSON válido no formato:
{{"title": str, "description": str, "hashtags": [str], "estimated_seconds": int,
 "segments": [{{"kind": "hook"|"corpo"|"cta", "text": str, "broll_query": str, "on_screen": str}}]}}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "estimated_seconds": {"type": "integer"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["hook", "corpo", "cta"]},
                    "text": {"type": "string"},
                    "broll_query": {"type": "string"},
                    "on_screen": {"type": "string"},
                },
                "required": ["kind", "text", "broll_query", "on_screen"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "description", "hashtags", "estimated_seconds", "segments"],
    "additionalProperties": False,
}


def build_script(job: JobInput, material: SourceMaterial) -> ShortScript:
    if material.kind == "roteiro":
        return _script_from_pasted_text(job, material)

    words_target = int(job.duration * 2.6)
    guide = NICHE_GUIDE.get(job.niche, NICHE_GUIDE["generico"])

    addendum = KIND_ADDENDUM.get(material.kind, "")
    if job.edit_mode == "resumo" and material.kind == "video":
        addendum = f"{addendum}\n\n{RESUMO_ADDENDUM}".strip()

    angle_guide = ANGLE_GUIDE.get(job.angle, "")
    if angle_guide:
        addendum = f"{addendum}\n\n{angle_guide}".strip()
    # Instrução livre ou ângulo explícito significam que o usuário quer falar de
    # um ASSUNTO, e não legendar o que passa na tela.
    if job.instruction.strip() or (job.angle != "auto" and material.kind == "video"):
        addendum = f"{addendum}\n\n{VISUAL_IS_SUPPORT}".strip()

    system = SYSTEM_TEMPLATE.format(addendum=addendum or "Você narra um short comum.",
                                    rules=BASE_RULES)

    source_duration = material.metadata.get("duration")
    duration_note = (
        f"Duração do material de origem: {int(source_duration)}s — bem mais longo que o "
        f"short, então condense e distribua ao longo de todo esse período."
        if job.edit_mode == "resumo" and source_duration else ""
    )

    briefing = (f"INSTRUÇÃO DO USUÁRIO PARA ESTE VÍDEO (prioridade máxima):\n"
                f"{job.instruction.strip()}\n" if job.instruction.strip() else "")

    prompt = f"""{briefing}Nicho: {job.niche}
Diretriz do nicho: {guide}
Idioma: {job.language}
Duração alvo: {job.duration} segundos (~{words_target} palavras narradas no total)
CTA desejado: {job.cta or "livre"}
Hook agressivo: {"sim" if job.hook_hard else "moderado"}
{duration_note}

MATERIAL DE ORIGEM ({material.kind}):
---
{material.context()}
---

Gere o roteiro do short."""

    data = llm.complete_json(system, prompt, SCHEMA)

    segments = [ScriptSegment(**s) for s in data.get("segments", []) if s.get("text")]
    if not segments:
        raise RuntimeError("LLM não retornou segmentos de roteiro.")

    hashtags = [h if h.startswith("#") else f"#{h}" for h in data.get("hashtags", [])]

    return ShortScript(
        title=data.get("title", material.title or "Short"),
        description=data.get("description", ""),
        hashtags=hashtags[:12],
        segments=segments,
        estimated_seconds=int(data.get("estimated_seconds", job.duration)),
    )


def _script_from_pasted_text(job: JobInput, material: SourceMaterial) -> ShortScript:
    """source_type='roteiro' — sem LLM. Só divide o texto do usuário em segmentos falados."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", material.text.strip()) if s.strip()]
    if not sentences:
        raise RuntimeError("Roteiro vazio.")

    segments: list[ScriptSegment] = []
    buffer = ""
    for sentence in sentences:
        candidate = f"{buffer} {sentence}".strip() if buffer else sentence
        if len(candidate.split()) > 22 and buffer:
            segments.append(ScriptSegment(kind="corpo", text=buffer))
            buffer = sentence
        else:
            buffer = candidate
    if buffer:
        segments.append(ScriptSegment(kind="corpo", text=buffer))

    if segments:
        segments[0].kind = "hook"
    if job.cta and (len(segments) < 2 or job.cta not in segments[-1].text):
        segments.append(ScriptSegment(kind="cta", text=job.cta))
    elif segments:
        segments[-1].kind = "cta"

    return ShortScript(
        title=material.title or "Roteiro",
        description="Roteiro fornecido pelo usuário.",
        hashtags=[],
        segments=segments,
        estimated_seconds=job.duration,
    )


def full_narration(script: ShortScript) -> str:
    return " ".join(s.text.strip() for s in script.segments)


def segment_durations_covering(script: ShortScript, words: list[dict],
                               total: float) -> list[float]:
    """Duração de fundo por segmento, cobrindo a linha do tempo INTEIRA.

    `segment_time_spans` devolve só o intervalo falado de cada segmento; a soma
    ignora as pausas entre eles e fica menor que a narração — o que fazia o
    fundo terminar antes do áudio e o `-shortest` cortar o final.
    Aqui cada segmento se estende até o início do próximo (o último até o fim).
    """
    spans = segment_time_spans(script, words)
    if not spans:
        return [total]
    starts = [start for start, _ in spans]
    bounds = starts[1:] + [total]
    durations = [max(bound - start, 0.8) for start, bound in zip(starts, bounds)]
    # o primeiro segmento absorve o silêncio inicial, se houver
    durations[0] += max(starts[0], 0.0)
    return durations


def segment_time_spans(script: ShortScript, words: list[dict]) -> list[tuple[float, float]]:
    """Mapeia cada segmento do roteiro pro intervalo de tempo que ele ocupa na
    narração — consumindo `words` na ordem, contando palavras por segmento.
    Aproximado (assume tokenização 1:1 com `.split()`), mas suficiente pra
    decidir onde cortar o fundo (Ken Burns por imagem, destaques de vídeo)."""
    spans: list[tuple[float, float]] = []
    cursor = 0
    for segment in script.segments:
        n = max(len(segment.text.split()), 1)
        chunk = words[cursor:cursor + n]
        if chunk:
            spans.append((chunk[0]["start"], chunk[-1]["end"]))
        else:
            last_end = spans[-1][1] if spans else 0.0
            spans.append((last_end, last_end))
        cursor += n
    return spans


REFINE_SYSTEM = """Você reescreve roteiros de vídeos curtos verticais em português do Brasil seguindo uma instrução do usuário.

Regras:
- Aplique EXATAMENTE a instrução dada. Não faça mudanças que não foram pedidas.
- Preserve o que a instrução não menciona: se ela fala do hook, o resto continua igual.
- Mantenha o formato falado: frases curtas, sem markdown, emoji ou parênteses.
- Não invente fatos que não estavam no roteiro original nem no material de origem.
- O primeiro segmento continua sendo o hook e o último o CTA, a menos que a instrução peça outra coisa.
- `broll_query` em inglês (2-4 palavras) e `on_screen` com no máximo 5 palavras.

Responda APENAS com JSON válido no mesmo formato do roteiro recebido."""


def refine_script(script: ShortScript, instruction: str, job: JobInput,
                  material: SourceMaterial | None = None) -> ShortScript:
    """Reescreve um roteiro existente conforme uma instrução em linguagem natural.

    É o caminho para ajustes do tipo "deixa o hook mais agressivo", "corta pela
    metade" ou "tira o jargão" sem perder o resto do roteiro já aprovado.
    """
    current = script.model_dump()
    context = material.context(limit=8000) if material else ""

    prompt = f"""INSTRUÇÃO DO USUÁRIO:
{instruction}

ROTEIRO ATUAL (JSON):
{json.dumps(current, ensure_ascii=False, indent=2)}

Duração alvo: {job.duration} segundos (~{int(job.duration * 2.6)} palavras narradas)
Nicho: {job.niche}
""" + (f"""
MATERIAL DE ORIGEM (para checar fatos, não copie literalmente):
---
{context}
---
""" if context else "") + """
Reescreva o roteiro aplicando a instrução."""

    data = llm.complete_json(REFINE_SYSTEM, prompt, SCHEMA)
    segments = [ScriptSegment(**s) for s in data.get("segments", []) if s.get("text")]
    if not segments:
        raise RuntimeError("O modelo não devolveu segmentos ao refinar o roteiro.")

    hashtags = [h if h.startswith("#") else f"#{h}" for h in
                data.get("hashtags", script.hashtags)]
    return ShortScript(
        title=data.get("title", script.title),
        description=data.get("description", script.description),
        hashtags=hashtags[:12],
        segments=segments,
        estimated_seconds=int(data.get("estimated_seconds", job.duration)),
    )


CAPTION_SYSTEM = """Você escreve o TEXTO DE PUBLICAÇÃO de vídeos curtos verticais em português do Brasil.

Não é o roteiro narrado — é o que vai escrito no post, ao lado do vídeo.

Regras por plataforma:
- youtube_titulo: até 70 caracteres, direto, sem clickbait vazio. Nada de aspas.
- youtube_descricao: 2 a 4 linhas. Primeira linha reforça o gancho, as outras dão contexto. Termina com as hashtags.
- tiktok_legenda: até 150 caracteres, tom mais solto e conversado, com 3 a 5 hashtags no fim.
- instagram_legenda: até 200 caracteres, pode ter uma quebra de linha e uma pergunta que puxe comentário.
- hashtags: 5 a 8, sem repetir palavra, misturando termo amplo e termo de nicho. Sempre com #.

Use só fatos que estão no roteiro. Não invente número, data ou nome que não apareça nele.

Responda APENAS com JSON válido no formato:
{"youtube_titulo": str, "youtube_descricao": str, "tiktok_legenda": str,
 "instagram_legenda": str, "hashtags": [str]}"""

CAPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "youtube_titulo": {"type": "string"},
        "youtube_descricao": {"type": "string"},
        "tiktok_legenda": {"type": "string"},
        "instagram_legenda": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["youtube_titulo", "youtube_descricao", "tiktok_legenda",
                 "instagram_legenda", "hashtags"],
    "additionalProperties": False,
}


def build_post_caption(script: ShortScript, job: JobInput,
                       instruction: str = "") -> dict:
    """Gera o texto de publicação (título, descrição e hashtags) do short."""
    narration = full_narration(script)
    extra = f"\nPedido extra do usuário: {instruction.strip()}\n" if instruction.strip() else ""

    prompt = f"""Nicho: {job.niche}
Título atual do vídeo: {script.title}
{extra}
ROTEIRO NARRADO NO VÍDEO:
---
{narration}
---

Escreva o texto de publicação."""

    data = llm.complete_json(CAPTION_SYSTEM, prompt, CAPTION_SCHEMA)
    hashtags = [h if h.startswith("#") else f"#{h}" for h in data.get("hashtags", [])]
    data["hashtags"] = hashtags[:10]
    return data
