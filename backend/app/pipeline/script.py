"""Short script generation from the source material."""
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
    "games": "Fale como jogador: mecânica, momento e comunidade. Cite o jogo, o estúdio e a data; nada de review genérica.",
    "saude": "Explique o mecanismo no corpo com uma analogia simples e cite o estudo ou a instituição. Sem diagnóstico, sem prescrever — informe e mande procurar um profissional.",
    "politica": "Fato, data e quem disse — sempre atribuído à fonte. Apresente os lados envolvidos e o que está em jogo; nada de opinião nem de torcida.",
    "generico": "Priorize clareza, ritmo e uma ideia central por short.",
}

# Words the script is written to per second of finished narration — the whole
# budget, pauses between sentences included. It is not the speaking rate: the
# voice says about 2.9 words a second and then stops for the best part of a
# second at every full stop, so writing to the speaking rate produces a script
# that overshoots its target and has to be read without breathing to fit.
#
# Measured end to end against edge-tts at the house rate, on scripts written
# under the one-idea-per-sentence rule below: 103 words came out as 53s, or
# 1.94 a second. Short sentences are the reason it is this low — each one buys
# its own pause, which is the whole point, and that pause is part of the
# budget. Raising this is how a short starts sounding rushed again; the old
# value of 2.6 was the speaking rate with the pauses forgotten, and every
# script written to it ran long.
WORDS_PER_SECOND = 2.0

BASE_RULES = f"""Regras absolutas:
- O primeiro segmento é um HOOK de no máximo 12 palavras que gera curiosidade ou choque. Sem "olá", sem "hoje eu vou falar".
- Frases curtas, faladas. Você escreve para OUVIR, não para ler. Nada de bullets, markdown, emoji ou parênteses no texto narrado.
- UMA IDEIA POR FRASE. No máximo 16 palavras por frase. Não empilhe orações com dois-pontos, ponto e vírgula ou vírgulas em sequência: quem ouve não tem onde respirar e a narração sai atropelada. Prefira duas frases curtas a uma longa.
- Nenhum número escrito por extenso incorretamente: escreva "2024" e "70%" do jeito que se fala.
- Densidade: ~{str(WORDS_PER_SECOND).replace(".", ",")} palavras por segundo de narração.
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
    "roteiro": "",  # does not go through the LLM — see build_script
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

# The interface exists in five languages, and the script has to come out in the
# language that was asked for — the prompt used to hardcode "português do Brasil"
# and ignore job.language, so a Spanish interface kept producing narration in
# Portuguese.
LANGUAGE_NAMES = {
    "pt": "português do Brasil",
    "en": "inglês (English)",
    "es": "espanhol (español)",
    "ru": "russo (русский)",
    "zh": "chinês simplificado (简体中文)",
    "fr": "francês (français)",
    "de": "alemão (Deutsch)",
    "it": "italiano",
    "ja": "japonês (日本語)",
}


def language_name(tag: str) -> str:
    """'es-ES' -> 'espanhol (español)'. An unknown tag comes back as itself,
    which is still a useful instruction for the model."""
    tag = (tag or "").strip()
    if not tag:
        return LANGUAGE_NAMES["pt"]
    return LANGUAGE_NAMES.get(tag.split("-")[0].lower(), tag)


def _in_language(template: str, tag: str) -> str:
    """Injects the language into the prompt.

    `str.format` is no good here: these prompts carry JSON examples, and the `{`
    braces get read as placeholders — hence replacing the marker directly.
    """
    return template.replace("{language}", language_name(tag))


SYSTEM_TEMPLATE = """Você é roteirista sênior de vídeos curtos verticais (YouTube Shorts, TikTok, Reels).

O ROTEIRO INTEIRO — título, descrição, hashtags e todo texto narrado — deve ser
escrito em {language}. Escreva como um nativo desse idioma escreveria, não como
uma tradução: expressões, ritmo e referências naturais para quem fala esse idioma.

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

    words_target = int(job.duration * WORDS_PER_SECOND)
    guide = NICHE_GUIDE.get(job.niche, NICHE_GUIDE["generico"])

    addendum = KIND_ADDENDUM.get(material.kind, "")
    if job.edit_mode == "resumo" and material.kind == "video":
        addendum = f"{addendum}\n\n{RESUMO_ADDENDUM}".strip()

    angle_guide = ANGLE_GUIDE.get(job.angle, "")
    if angle_guide:
        addendum = f"{addendum}\n\n{angle_guide}".strip()
    # A free-form instruction or an explicit angle means the user wants to talk
    # about a SUBJECT, not to caption whatever goes by on the screen.
    if job.instruction.strip() or (job.angle != "auto" and material.kind == "video"):
        addendum = f"{addendum}\n\n{VISUAL_IS_SUPPORT}".strip()

    system = _in_language(
        SYSTEM_TEMPLATE.format(addendum=addendum or "Você narra um short comum.",
                               rules=BASE_RULES, language="{language}"),
        job.language)

    source_duration = material.metadata.get("duration")
    duration_note = (
        f"Duração do material de origem: {int(source_duration)}s — bem mais longo que o "
        f"short, então condense e distribua ao longo de todo esse período."
        if job.edit_mode == "resumo" and source_duration else ""
    )

    briefing = (f"INSTRUÇÃO DO USUÁRIO PARA ESTE VÍDEO (prioridade máxima):\n"
                f"{job.instruction.strip()}\n" if job.instruction.strip() else "")

    # Whatever already worked on the user's channel goes in as hook reference.
    # Empty until there is a measured publication — the prompt then stays as usual.
    from . import metrics as metrics_mod

    try:
        channel_insights = metrics_mod.insights(job.niche)
    except Exception:  # noqa: BLE001 — the briefing is a bonus, never a blocker
        channel_insights = ""

    prompt = f"""{briefing}Nicho: {job.niche}
Diretriz do nicho: {guide}
Idioma: {job.language}
Duração alvo: {job.duration} segundos (~{words_target} palavras narradas no total)
CTA desejado: {job.cta or "livre"}
Hook agressivo: {"sim" if job.hook_hard else "moderado"}
{duration_note}
{channel_insights}

MATERIAL DE ORIGEM ({material.kind}):
---
{material.context()}
---

Gere o roteiro do short."""

    data = llm.complete_json(system, prompt, SCHEMA, purpose="roteiro")

    segments = [ScriptSegment(**s) for s in data.get("segments", []) if s.get("text")]
    if not segments:
        raise RuntimeError("The LLM returned no script segments.")

    hashtags = [h if h.startswith("#") else f"#{h}" for h in data.get("hashtags", [])]

    return ShortScript(
        title=data.get("title", material.title or "Short"),
        description=data.get("description", ""),
        hashtags=hashtags[:12],
        segments=segments,
        estimated_seconds=int(data.get("estimated_seconds", job.duration)),
    )


def _script_from_pasted_text(job: JobInput, material: SourceMaterial) -> ShortScript:
    """source_type='roteiro' — no LLM. Just splits the user's text into spoken
    segments."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", material.text.strip()) if s.strip()]
    if not sentences:
        raise RuntimeError("Empty script.")

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
        # fallback only when the pasted text has no usable first line
        title=material.title or "Script",
        description="Script supplied by the user.",
        hashtags=[],
        segments=segments,
        estimated_seconds=job.duration,
    )


def full_narration(script: ShortScript) -> str:
    return " ".join(s.text.strip() for s in script.segments)


def segment_durations_covering(script: ShortScript, words: list[dict],
                               total: float) -> list[float]:
    """Background duration per segment, covering the ENTIRE timeline.

    `segment_time_spans` returns only the spoken span of each segment; the sum
    ignores the pauses between them and comes out shorter than the narration —
    which made the background end before the audio and `-shortest` cut off the
    ending. Here each segment stretches to the start of the next one (the last
    one to the end).
    """
    spans = segment_time_spans(script, words)
    if not spans:
        return [total]
    starts = [start for start, _ in spans]
    bounds = starts[1:] + [total]
    durations = [max(bound - start, 0.8) for start, bound in zip(starts, bounds)]
    # the first segment absorbs the leading silence, if there is any
    durations[0] += max(starts[0], 0.0)
    return durations


def segment_time_spans(script: ShortScript, words: list[dict]) -> list[tuple[float, float]]:
    """Maps each script segment to the time span it takes up in the narration —
    consuming `words` in order, counting words per segment. Approximate (it
    assumes 1:1 tokenization with `.split()`), but enough to decide where to cut
    the background (Ken Burns per image, video highlights)."""
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


REFINE_SYSTEM = """Você reescreve roteiros de vídeos curtos verticais seguindo uma instrução do usuário.

O roteiro reescrito continua em {language} — o mesmo idioma do roteiro recebido.

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
    """Rewrites an existing script following a natural-language instruction.

    This is the path for tweaks like "make the hook more aggressive", "cut it in
    half" or "drop the jargon" without losing the rest of the script that was
    already approved.
    """
    current = script.model_dump()
    context = material.context(limit=8000) if material else ""

    prompt = f"""INSTRUÇÃO DO USUÁRIO:
{instruction}

ROTEIRO ATUAL (JSON):
{json.dumps(current, ensure_ascii=False, indent=2)}

Duração alvo: {job.duration} segundos (~{int(job.duration * WORDS_PER_SECOND)} palavras narradas)
Nicho: {job.niche}
""" + (f"""
MATERIAL DE ORIGEM (para checar fatos, não copie literalmente):
---
{context}
---
""" if context else "") + """
Reescreva o roteiro aplicando a instrução."""

    data = llm.complete_json(_in_language(REFINE_SYSTEM, job.language),
                             prompt, SCHEMA, purpose="refinar")
    segments = [ScriptSegment(**s) for s in data.get("segments", []) if s.get("text")]
    if not segments:
        raise RuntimeError("The model returned no segments when refining the script.")

    hashtags = [h if h.startswith("#") else f"#{h}" for h in
                data.get("hashtags", script.hashtags)]
    return ShortScript(
        title=data.get("title", script.title),
        description=data.get("description", script.description),
        hashtags=hashtags[:12],
        segments=segments,
        estimated_seconds=int(data.get("estimated_seconds", job.duration)),
    )


CAPTION_SYSTEM = """Você escreve o TEXTO DE PUBLICAÇÃO de vídeos curtos verticais.

Todo o texto de publicação sai em {language}, no registro que a audiência desse
idioma espera nessas plataformas.

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
    """Generates the short's post copy (title, description and hashtags)."""
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

    data = llm.complete_json(_in_language(CAPTION_SYSTEM, job.language),
                             prompt, CAPTION_SCHEMA, purpose="legenda_post")
    hashtags = [h if h.startswith("#") else f"#{h}" for h in data.get("hashtags", [])]
    data["hashtags"] = hashtags[:10]
    return data


HOOKS_SYSTEM = """Você escreve GANCHOS (a primeira frase falada) de vídeos curtos verticais.

Os ganchos saem em {language}, o mesmo idioma do roteiro.

O gancho decide se a pessoa fica ou desliza. Ele precisa:
- Ter no máximo 12 palavras, faladas, sem "olá" nem "hoje eu vou".
- Ser específico ao conteúdo do roteiro — nada genérico que serviria pra qualquer vídeo.
- Cada variante usa um MECANISMO diferente. Escolha entre: pergunta direta, número
  ou dado concreto, contradição/quebra de expectativa, promessa de resultado,
  cena in medias res, afirmação polêmica, "a maioria erra isso".
- Não repita o mecanismo do gancho atual.
- Não invente fatos que não estão no roteiro.

Responda APENAS com JSON válido no formato:
{"hooks": [{"text": str, "mechanism": str, "why": str}]}"""

HOOKS_SCHEMA = {
    "type": "object",
    "properties": {
        "hooks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "mechanism": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["text", "mechanism", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["hooks"],
    "additionalProperties": False,
}


def build_hook_variants(script: ShortScript, job: JobInput, count: int = 3) -> list[dict]:
    """Hook alternatives for the same script — the basis of the A/B test."""
    current = next((s.text for s in script.segments if s.kind == "hook"),
                   script.segments[0].text if script.segments else "")
    body = " ".join(s.text for s in script.segments if s.kind != "hook")

    from . import metrics as metrics_mod

    try:
        channel_insights = metrics_mod.insights(job.niche)
    except Exception:  # noqa: BLE001
        channel_insights = ""

    prompt = f"""Nicho: {job.niche}
Título: {script.title}
Gancho atual: "{current}"
{channel_insights}

RESTO DO ROTEIRO (o gancho precisa levar pra isso):
---
{body}
---

Escreva {count} ganchos alternativos, cada um com um mecanismo diferente."""

    data = llm.complete_json(_in_language(HOOKS_SYSTEM, job.language),
                             prompt, HOOKS_SCHEMA, purpose="hooks")
    hooks = [h for h in data.get("hooks", []) if h.get("text", "").strip()]
    if not hooks:
        raise RuntimeError("The model returned no alternative hooks.")
    return hooks[:count]


def with_hook(script: ShortScript, hook_text: str) -> ShortScript:
    """Copy of the script with the first segment swapped for the chosen hook."""
    segments = [s.model_copy() for s in script.segments]
    idx = next((i for i, s in enumerate(segments) if s.kind == "hook"), 0)
    if segments:
        segments[idx].text = hook_text.strip()
        segments[idx].kind = "hook"
    return script.model_copy(update={"segments": segments})
