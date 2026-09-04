"""Film studio: a premise becomes a story, a cast, a screenplay and shots.

Everything else in this project starts from material that already exists — a
video, a repository, a link — and condenses it into one short. This module goes
the other way: the user brings a premise and instructions, and the story itself
is what gets developed.

It is deliberately a staged pipeline, not one call from premise to film:

    premise + instruction
        -> bible       (logline, theme, tone, look, setting, three acts)
        -> characters  (who they are AND how they look, verbatim, forever)
        -> screenplay  (scenes, beats, dialogue, intended duration)
        -> shots       (one ready-to-send generation prompt per shot)
        -> generate    (AI video per shot + TTS per line -> Timeline -> MP4)

Each stage is its own function, reads the stages before it as context, and is
persisted on its own. That is the point: generating a film costs real money and
twenty minutes, so the user has to be able to read what the model wrote, fix it,
and only then move on. A single black-box call would spend that money on a story
nobody approved.

The finished film is a normal `Timeline` inside a normal job directory, so the
existing timeline editor, QA, the cover builder and publishing all work on it
with nothing special-cased — exactly as `reels` does for a recording of your own.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from .. import db
from ..config import settings
from ..schemas import JobInput
from . import (captions as captions_mod, llm, notify, qa as qa_mod, render,
               timeline as timeline_mod, timeline_render, tts, videogen)
from .script import language_name
from .timeline import AudioClip, CaptionCue, Timeline, VideoClip

# What the job produced by a film carries in `result_json.mode`, the same way a
# reel marks itself. The dashboard reads it to tell the two apart.
MODE = "filme"

# The development stages, in order. The URL segment is the stage name.
STAGES = ("bible", "characters", "screenplay", "shots")

STAGE_COLUMN = {
    "bible": "bible_json",
    "characters": "characters_json",
    "screenplay": "screenplay_json",
    "shots": "shots_json",
}

# Stages invalidated when a stage is (re)written — the same invalidation
# cascade the orchestrator applies to a short's pipeline. Rewriting the bible
# changes the tone every later stage was written against, and characters can be
# renamed, which would leave the screenplay talking about people who no longer
# exist. So the derived documents are dropped instead of being left silently
# inconsistent, and the API says which ones went.
CASCADE = {
    "bible": ("characters", "screenplay", "shots"),
    "characters": ("screenplay", "shots"),
    "screenplay": ("shots",),
    "shots": (),
}

# A generator produces a few seconds per call — a shot is a shot, not a scene.
MIN_SHOT_SECONDS = 3
MAX_SHOT_SECONDS = 12
DEFAULT_SHOT_SECONDS = 8

MIN_SCENES = 1
MAX_SCENES = 24

# Hard ceiling for one run. Not a technical limit: at ~8s a shot this is already
# several minutes of paid generation, and a model that answers with 300 shots is
# about to spend somebody's money by accident.
MAX_SHOTS = 80

# The film's job while the shots are being generated. It must NOT be "queued" or
# "running": `worker.start()` requeues jobs in those states after a restart, and
# the shorts orchestrator would then run its own pipeline over a film's
# directory. The film's own queue is what resumes it.
JOB_STATUS_GENERATING = "gerando"


class StageNotReady(RuntimeError):
    """A stage was asked for before the stage it is built on exists."""


def _in_language(template: str, tag: str) -> str:
    """Injects the language into the prompt.

    `str.format` is no good here: these prompts carry JSON examples, and the `{`
    braces get read as placeholders — hence replacing the marker directly.
    """
    return template.replace("{language}", language_name(tag))


def _hash(text: str) -> str:
    """Fingerprint of a prompt or a line, so a resume can tell whether what is
    already on disk still matches what is being asked for now."""
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:12]


def options_of(film: dict) -> dict:
    """The film's free-form settings with the defaults filled in."""
    raw = film.get("options_json") or "{}"
    stored = json.loads(raw) if isinstance(raw, str) else dict(raw)
    return {
        "language": stored.get("language", "pt-BR"),
        "niche": stored.get("niche", "cinema"),
        "aspect": stored.get("aspect", "9:16"),
        "scenes": int(stored.get("scenes", 6)),
        "shot_seconds": int(stored.get("shot_seconds", DEFAULT_SHOT_SECONDS)),
        "voice_id": stored.get("voice_id"),
        "caption_style": stored.get("caption_style", "bloco"),
        "caption_position": stored.get("caption_position", "baixo"),
        "watermark": stored.get("watermark", ""),
    }


def document(film: dict, stage: str) -> dict | None:
    raw = film.get(STAGE_COLUMN[stage])
    if not raw:
        return None
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


# --------------------------------------------------------------------------
# Stage 1 — the story bible
# --------------------------------------------------------------------------

BIBLE_SYSTEM = """Você é desenvolvedor de histórias para cinema. Recebe uma premissa e
transforma em uma bíblia de história enxuta e filmável.

O conteúdo criativo — título, logline, tema, tom, ambientação e atos — é escrito
em {language}. Escreva como um nativo desse idioma escreveria, não como tradução.

Regras absolutas:
- A logline tem UMA frase: quem é o protagonista, o que ele quer, o que impede e o que está em jogo.
- O tema é a ideia que o filme defende, não o assunto. "Vingança custa mais do que o dano" é tema; "vingança" não é.
- O tom descreve como o filme SOA para quem assiste (ex.: "tensão contida, humor seco, silêncios longos").
- `look` é a direção de fotografia em UMA linha e em INGLÊS — ela vai literalmente
  dentro do prompt enviado ao gerador de vídeo, e esses modelos respondem melhor
  em inglês (ex.: "gritty handheld 35mm, teal and amber night palette, hard practical lights").
- Três atos: montagem, confronto e resolução. Cada ato traz o `turning_point`, o evento
  concreto que muda a direção da história — não um resumo do ato.
- Nada de markdown, título de seção, lista numerada ou emoji dentro dos campos.
- Respeite a instrução do usuário acima de qualquer convenção de gênero.

Responda APENAS com JSON válido no formato:
{"title": str, "logline": str, "theme": str, "tone": str, "look": str,
 "setting": str, "acts": [{"act": int, "name": str, "summary": str, "turning_point": str}]}"""

BIBLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "logline": {"type": "string"},
        "theme": {"type": "string"},
        "tone": {"type": "string"},
        "look": {"type": "string"},
        "setting": {"type": "string"},
        "acts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "act": {"type": "integer"},
                    "name": {"type": "string"},
                    "summary": {"type": "string"},
                    "turning_point": {"type": "string"},
                },
                "required": ["act", "name", "summary", "turning_point"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "logline", "theme", "tone", "look", "setting", "acts"],
    "additionalProperties": False,
}


def develop_bible(premise: str, instruction: str, options: dict) -> dict:
    briefing = (f"INSTRUÇÃO DO USUÁRIO (prioridade máxima):\n{instruction.strip()}\n\n"
                if instruction.strip() else "")

    prompt = f"""{briefing}PREMISSA:
{premise.strip()}

Gênero/nicho de referência: {options["niche"]}
Idioma da obra: {options["language"]}
Tamanho previsto: {options["scenes"]} cenas de mais ou menos {options["shot_seconds"]} segundos por plano.

Desenvolva a bíblia da história."""

    data = llm.complete_json(_in_language(BIBLE_SYSTEM, options["language"]),
                             prompt, BIBLE_SCHEMA, purpose="filme_biblia")
    return _normalize_bible(data)


def _normalize_bible(data: dict) -> dict:
    logline = str(data.get("logline", "")).strip()
    if not logline:
        raise RuntimeError("The model returned a story bible with no logline.")

    acts = []
    for index, act in enumerate(data.get("acts") or [], start=1):
        if not isinstance(act, dict) or not str(act.get("summary", "")).strip():
            continue
        acts.append({
            "act": int(act.get("act") or index),
            "name": str(act.get("name", "")).strip(),
            "summary": str(act["summary"]).strip(),
            "turning_point": str(act.get("turning_point", "")).strip(),
        })
    if not acts:
        raise RuntimeError("The model returned a story bible with no acts.")

    return {
        "title": str(data.get("title", "")).strip() or "Untitled",
        "logline": logline,
        "theme": str(data.get("theme", "")).strip(),
        "tone": str(data.get("tone", "")).strip(),
        # kept in English on purpose: it is pasted into every shot prompt
        "look": str(data.get("look", "")).strip(),
        "setting": str(data.get("setting", "")).strip(),
        "acts": acts,
    }


# --------------------------------------------------------------------------
# Stage 2 — the cast
# --------------------------------------------------------------------------

CHARACTERS_SYSTEM = """Você cria o elenco de um filme a partir da bíblia da história.

Nome, papel e personalidade saem em {language}.

O campo `visual` é diferente de todos os outros e é o mais importante deste
estágio: ele é a descrição física que será COLADA LITERALMENTE dentro do prompt
de cada plano em que a personagem aparece. Por isso:
- `visual` é escrito em INGLÊS, no formato de prompt: idade aparente, etnia, cabelo,
  rosto, corpo, roupa fixa e um detalhe inconfundível (cicatriz, óculos, tatuagem, casaco).
- É uma descrição ESTÁVEL, que vale do primeiro ao último plano. Nada de emoção,
  ação, cenário ou iluminação — isso muda a cada plano e entra em outro lugar.
- Concreto e visível. "misteriosa" não é visual; "gaunt woman in her 50s, cropped
  grey hair, deep scar across the left eyebrow, worn olive field jacket" é.
- Sem nome próprio dentro do `visual`: o gerador não sabe quem é.

Regras gerais:
- Entre 2 e 6 personagens. Personagem que não aparece em cena nenhuma não existe.
- `role` diz a função na história (protagonista, antagonista, mentor, contraponto).
- `voice` descreve a voz em uma linha, para escolher a narração depois.

Responda APENAS com JSON válido no formato:
{"characters": [{"name": str, "role": str, "personality": str, "visual": str, "voice": str}]}"""

CHARACTERS_SCHEMA = {
    "type": "object",
    "properties": {
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "personality": {"type": "string"},
                    "visual": {"type": "string"},
                    "voice": {"type": "string"},
                },
                "required": ["name", "role", "personality", "visual", "voice"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["characters"],
    "additionalProperties": False,
}


def develop_characters(bible: dict, instruction: str, options: dict) -> dict:
    briefing = (f"INSTRUÇÃO DO USUÁRIO (prioridade máxima):\n{instruction.strip()}\n\n"
                if instruction.strip() else "")

    prompt = f"""{briefing}BÍBLIA DA HISTÓRIA:
{json.dumps(bible, ensure_ascii=False, indent=2)}

Crie o elenco desse filme."""

    data = llm.complete_json(_in_language(CHARACTERS_SYSTEM, options["language"]),
                             prompt, CHARACTERS_SCHEMA, purpose="filme_personagens")
    return _normalize_characters(data)


def _normalize_characters(data: dict) -> dict:
    characters = []
    seen: set[str] = set()
    for item in data.get("characters") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        visual = str(item.get("visual", "")).strip()
        key = name.lower()
        if not name or not visual or key in seen:
            continue
        seen.add(key)
        characters.append({
            "name": name,
            "role": str(item.get("role", "")).strip(),
            "personality": str(item.get("personality", "")).strip(),
            # This string is reused byte for byte in every shot prompt the
            # character appears in — see `compose_shot_prompt`.
            "visual": visual,
            "voice": str(item.get("voice", "")).strip(),
            # Optional: a voice from /api/voices, so a character can be dubbed
            # by their own voice instead of the film's default narrator. The
            # model never fills this in; the user does, by editing the stage.
            "voice_id": item.get("voice_id") or None,
        })
    if not characters:
        raise RuntimeError(
            "The model returned no usable character — each one needs at least a "
            "name and a visual description.")
    return {"characters": characters}


# --------------------------------------------------------------------------
# Stage 3 — the screenplay
# --------------------------------------------------------------------------

SCREENPLAY_SYSTEM = """Você escreve o roteiro de um filme a partir da bíblia e do elenco.

Todo texto ouvido pelo público — narração e diálogo — sai em {language}. Já
`location` e `time_of_day` descrevem o cenário para a equipe e podem ficar curtos.

Regras absolutas:
- Cada cena tem UM beat: a coisa que muda nela. Cena que não muda nada sai do roteiro.
- `beat` é o que acontece, em uma frase, no presente ("Ana descobre o bilhete no bolso do casaco").
- `characters` lista apenas quem APARECE em cena, com o nome exatamente como está no elenco.
- Diálogo falado, curto, em voz alta. Sem rubrica entre parênteses, sem markdown, sem emoji.
- `narration` só quando existe narrador. Cena sem narrador leva string vazia.
- Cena sem fala nenhuma é permitida e às vezes é a melhor escolha — deixe `dialogue` vazio.
- `seconds` é a duração pretendida da cena, entre 4 e 40.
- Distribua as cenas pelos três atos na proporção da bíblia. A última cena resolve o filme.

Responda APENAS com JSON válido no formato:
{"scenes": [{"scene": int, "act": int, "location": str, "time_of_day": str,
 "beat": str, "characters": [str], "narration": str,
 "dialogue": [{"character": str, "line": str}], "seconds": int}]}"""

SCREENPLAY_SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene": {"type": "integer"},
                    "act": {"type": "integer"},
                    "location": {"type": "string"},
                    "time_of_day": {"type": "string"},
                    "beat": {"type": "string"},
                    "characters": {"type": "array", "items": {"type": "string"}},
                    "narration": {"type": "string"},
                    "dialogue": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"character": {"type": "string"},
                                           "line": {"type": "string"}},
                            "required": ["character", "line"],
                            "additionalProperties": False,
                        },
                    },
                    "seconds": {"type": "integer"},
                },
                "required": ["scene", "act", "location", "time_of_day", "beat",
                             "characters", "narration", "dialogue", "seconds"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scenes"],
    "additionalProperties": False,
}


def write_screenplay(bible: dict, characters: dict, instruction: str,
                     options: dict) -> dict:
    briefing = (f"INSTRUÇÃO DO USUÁRIO (prioridade máxima):\n{instruction.strip()}\n\n"
                if instruction.strip() else "")

    prompt = f"""{briefing}BÍBLIA DA HISTÓRIA:
{json.dumps(bible, ensure_ascii=False, indent=2)}

ELENCO (use estes nomes exatamente):
{_cast_briefing(characters)}

Escreva {options["scenes"]} cenas."""

    data = llm.complete_json(_in_language(SCREENPLAY_SYSTEM, options["language"]),
                             prompt, SCREENPLAY_SCHEMA, purpose="filme_roteiro")
    return _normalize_screenplay(data)


def _cast_briefing(characters: dict) -> str:
    """The cast as the screenwriter needs it: who they are, not how they look.

    The visual descriptions are left out on purpose — they are long, they are in
    another language, and a screenwriting prompt that carries them comes back
    with the physical description narrated out loud in the dialogue.
    """
    lines = []
    for character in characters.get("characters", []):
        lines.append(f"- {character['name']} ({character.get('role', '')}): "
                     f"{character.get('personality', '')}")
    return "\n".join(lines)


def _normalize_screenplay(data: dict) -> dict:
    scenes = []
    for index, scene in enumerate(data.get("scenes") or [], start=1):
        if not isinstance(scene, dict):
            continue
        beat = str(scene.get("beat", "")).strip()
        if not beat:
            continue
        dialogue = []
        for line in scene.get("dialogue") or []:
            if not isinstance(line, dict):
                continue
            text = str(line.get("line", "")).strip()
            if text:
                dialogue.append({"character": str(line.get("character", "")).strip(),
                                 "line": text})
        try:
            seconds = int(scene.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0
        scenes.append({
            "scene": int(scene.get("scene") or index),
            "act": int(scene.get("act") or 1),
            "location": str(scene.get("location", "")).strip(),
            "time_of_day": str(scene.get("time_of_day", "")).strip(),
            "beat": beat,
            "characters": [str(c).strip() for c in (scene.get("characters") or [])
                           if str(c).strip()],
            "narration": str(scene.get("narration", "")).strip(),
            "dialogue": dialogue,
            "seconds": min(max(seconds or 12, 4), 40),
        })
    if not scenes:
        raise RuntimeError("The model returned a screenplay with no scenes.")
    return {"scenes": scenes}


# --------------------------------------------------------------------------
# Stage 4 — the shot list
# --------------------------------------------------------------------------

SHOTS_SYSTEM = """Você é diretor de fotografia e decupa um roteiro em planos para geração de vídeo por IA.

TODO o conteúdo deste estágio sai em INGLÊS, inclusive para filmes em outro
idioma: `action` e `camera` vão direto para o modelo de vídeo, e esses modelos
respondem muito melhor em inglês. A fala das personagens não entra aqui — ela é
gravada em outro estágio, no idioma do filme.

Regras absolutas:
- De 1 a 4 planos por cena. Cena inteira em plano único é aceitável quando o beat é simples.
- `action` descreve o que a CÂMERA VÊ acontecer, em uma ou duas frases, no presente.
  Descreva ação e ambiente — nunca a aparência física de quem aparece: isso é
  acrescentado depois, palavra por palavra, a partir do elenco.
- `camera` é o enquadramento e o movimento ("slow dolly in, low angle, shallow depth of field").
- `characters` lista quem aparece NO PLANO, com o nome exatamente como está no elenco.
  Plano de objeto, paisagem ou detalhe leva lista vazia.
- `seconds` entre {min_seconds} e {max_seconds}: é um plano, não uma cena inteira.
- Nada de texto na tela, legenda, letreiro ou logotipo dentro do `action`.

Responda APENAS com JSON válido no formato:
{"shots": [{"scene": int, "shot": int, "action": str, "camera": str,
 "characters": [str], "seconds": int}]}"""

SHOTS_SCHEMA = {
    "type": "object",
    "properties": {
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene": {"type": "integer"},
                    "shot": {"type": "integer"},
                    "action": {"type": "string"},
                    "camera": {"type": "string"},
                    "characters": {"type": "array", "items": {"type": "string"}},
                    "seconds": {"type": "integer"},
                },
                "required": ["scene", "shot", "action", "camera", "characters",
                             "seconds"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["shots"],
    "additionalProperties": False,
}


def build_shot_list(bible: dict, characters: dict, screenplay: dict,
                    instruction: str, options: dict) -> dict:
    briefing = (f"INSTRUÇÃO DO USUÁRIO (prioridade máxima):\n{instruction.strip()}\n\n"
                if instruction.strip() else "")

    system = (SHOTS_SYSTEM
              .replace("{min_seconds}", str(MIN_SHOT_SECONDS))
              .replace("{max_seconds}", str(MAX_SHOT_SECONDS)))

    prompt = f"""{briefing}TOM DO FILME: {bible.get("tone", "")}
FOTOGRAFIA (look): {bible.get("look", "")}
AMBIENTAÇÃO: {bible.get("setting", "")}

ELENCO EM CENA (nomes exatos):
{", ".join(c["name"] for c in characters.get("characters", []))}

ROTEIRO:
{json.dumps(screenplay, ensure_ascii=False, indent=2)}

Decupe o roteiro em planos. Alvo de {options["shot_seconds"]} segundos por plano."""

    data = llm.complete_json(system, prompt, SHOTS_SCHEMA, purpose="filme_planos")
    return _normalize_shots(data, characters, bible, options)


def _normalize_shots(data: dict, characters: dict, bible: dict, options: dict,
                     keep_prompts: bool = False) -> dict:
    shots = []
    counters: dict[int, int] = {}
    for item in data.get("shots") or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip()
        if not action:
            continue
        scene = int(item.get("scene") or 1)
        counters[scene] = counters.get(scene, 0) + 1
        number = int(item.get("shot") or counters[scene])
        try:
            seconds = int(item.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0
        shot = {
            "key": f"s{scene:02d}_p{number:02d}",
            "scene": scene,
            "shot": number,
            "action": action,
            "camera": str(item.get("camera", "")).strip(),
            "characters": [str(c).strip() for c in (item.get("characters") or [])
                           if str(c).strip()],
            "seconds": min(max(seconds or options["shot_seconds"], MIN_SHOT_SECONDS),
                           MAX_SHOT_SECONDS),
        }
        existing = str(item.get("prompt", "")).strip()
        shot["prompt"] = (existing if keep_prompts and existing
                          else compose_shot_prompt(shot, characters, bible))
        shots.append(shot)

    if not shots:
        raise RuntimeError("The model returned no usable shot.")
    if len(shots) > MAX_SHOTS:
        raise RuntimeError(
            f"The shot list came back with {len(shots)} shots, past the "
            f"{MAX_SHOTS} ceiling for a single film. Ask for fewer scenes or "
            f"regenerate the shot list with an instruction to cut it down.")
    return {"shots": shots}


def compose_shot_prompt(shot: dict, characters: dict, bible: dict) -> str:
    """The prompt actually sent to the video generator, for one shot.

    Each character's `visual` description is pasted in VERBATIM, in every single
    shot that character appears in. That repetition is the only thing keeping a
    face from changing between scenes: the generators are stateless — every shot
    is a fresh call that knows nothing about the previous one — so the only
    continuity available is the words being byte-for-byte identical each time.

    Which is also why this is assembled in code instead of being asked of the
    model: told to "reuse the description", an LLM paraphrases, and a paraphrased
    face is a different face.
    """
    by_name = {c["name"].strip().lower(): c
               for c in characters.get("characters", [])}

    parts = [shot["action"].rstrip(". ")]
    if shot.get("camera"):
        parts.append(shot["camera"].rstrip(". "))

    for name in shot.get("characters", []):
        character = by_name.get(name.strip().lower())
        if character and character.get("visual"):
            parts.append(f"{character['name']} is {character['visual'].rstrip('. ')}")

    if bible.get("look"):
        parts.append(bible["look"].rstrip(". "))
    if bible.get("tone"):
        parts.append(f"overall tone: {bible['tone'].rstrip('. ')}")
    parts.append("cinematic, no on-screen text, no subtitles")
    return ". ".join(p for p in parts if p) + "."


# --------------------------------------------------------------------------
# Running one stage from a stored film
# --------------------------------------------------------------------------

def develop_stage(film: dict, stage: str, instruction: str = "") -> dict:
    """Runs a single stage for a stored film, reusing the earlier stages.

    Raises `StageNotReady` when what it is built on does not exist yet — the
    router turns that into a 400 with the same message.
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown stage: {stage}")

    options = options_of(film)
    instruction = instruction.strip() or (film.get("instruction") or "")

    if stage == "bible":
        return develop_bible(film["premise"], instruction, options)

    bible = document(film, "bible")
    if bible is None:
        raise StageNotReady("Develop the story bible first.")
    if stage == "characters":
        return develop_characters(bible, instruction, options)

    characters = document(film, "characters")
    if characters is None:
        raise StageNotReady("Develop the characters before the screenplay.")
    if stage == "screenplay":
        return write_screenplay(bible, characters, instruction, options)

    screenplay = document(film, "screenplay")
    if screenplay is None:
        raise StageNotReady("Write the screenplay before the shot list.")
    return build_shot_list(bible, characters, screenplay, instruction, options)


def accept_stage(film: dict, stage: str, data: dict) -> dict:
    """Validates and normalizes a stage the USER wrote or edited by hand.

    The user's version goes through the same normalizer the model's output does,
    so an edited stage carries the same guarantees — including the shot prompts,
    which are recomposed from the current cast unless the caller edited a prompt
    itself.
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown stage: {stage}")
    if not isinstance(data, dict):
        raise ValueError("Send the stage as a JSON object.")

    options = options_of(film)
    if stage == "bible":
        return _normalize_bible(data)
    if stage == "characters":
        return _normalize_characters(data)
    if stage == "screenplay":
        return _normalize_screenplay(data)

    characters = document(film, "characters") or {"characters": []}
    bible = document(film, "bible") or {}
    return _normalize_shots(data, characters, bible, options, keep_prompts=True)


def save_stage(film: dict, stage: str, doc: dict) -> list[str]:
    """Persists a stage and drops the stages derived from it.

    Returns the names of the invalidated stages so the caller can say what has
    to be regenerated. The per-shot generation ledger is deliberately KEPT: it
    is fingerprinted by prompt, so footage whose prompt survived the rewrite is
    reused instead of being paid for twice.
    """
    invalidated = [s for s in CASCADE[stage] if film.get(STAGE_COLUMN[s])]
    fields: dict = {STAGE_COLUMN[stage]: json.dumps(doc, ensure_ascii=False)}
    for downstream in CASCADE[stage]:
        fields[STAGE_COLUMN[downstream]] = None

    fields["stage"] = stage
    fields["status"] = "ready" if stage == "shots" else "developing"
    fields["error"] = None
    if stage == "bible" and doc.get("title"):
        fields["title"] = doc["title"]
    db.update_film(film["id"], **fields)
    return invalidated


# --------------------------------------------------------------------------
# Cost honesty
# --------------------------------------------------------------------------

def video_provider() -> dict:
    """Which AI video generator this run would be billed to.

    Asked through `videogen.plan()` — the very list `generate_clip` walks — so
    what the estimate promises is what the run will actually call. A failure to
    even work that out is reported as "not configured" with the reason, because
    an estimate must never be the thing that breaks a poll.
    """
    try:
        candidates = videogen.plan()
    except Exception as exc:  # noqa: BLE001
        return {"id": "", "name": "", "model": "", "cost": "",
                "configured": False, "reason": str(exc)}

    chosen = next((c for c in candidates if c.eligible), None)
    if chosen is None:
        # Still name the provider it would have preferred: "configure this one"
        # is more useful than "none".
        intended = candidates[0] if candidates else None
        return {"id": getattr(intended, "provider_id", ""),
                "name": getattr(intended, "label", ""),
                "model": "", "cost": getattr(intended, "cost", ""),
                "configured": False,
                "reason": getattr(intended, "reason",
                                  "No AI video generator is available.")}
    return {"id": chosen.provider_id, "name": chosen.label,
            "model": chosen.model, "cost": chosen.cost,
            "configured": True, "reason": chosen.reason}


def estimate(film: dict) -> dict:
    """What generating this film would cost, in shots, seconds and provider.

    Reported before anything is generated. A film is minutes of paid video
    generation, and nobody should discover the size of the bill from the
    invoice.
    """
    shots_doc = document(film, "shots") or {"shots": []}
    shots = shots_doc.get("shots", [])
    screenplay = document(film, "screenplay") or {"scenes": []}
    ledger = _ledger_index(film)
    work = _work_dir(film)

    reused = {s["key"] for s in shots
              if _reusable(ledger.get(s["key"]), s["prompt"], work)}
    to_generate = [s for s in shots if s["key"] not in reused]
    lines = sum(len(scene_lines(scene)) for scene in screenplay.get("scenes", []))

    return {
        "shots": len(shots),
        "seconds": sum(int(s["seconds"]) for s in shots),
        "scenes": len(screenplay.get("scenes", [])),
        "narration_lines": lines,
        # A resume pays only for what is not already on disk.
        "shots_to_generate": len(to_generate),
        "seconds_to_generate": sum(int(s["seconds"]) for s in to_generate),
        "reused_shots": len(reused),
        "provider": video_provider(),
    }


# --------------------------------------------------------------------------
# Generation and assembly
# --------------------------------------------------------------------------

def scene_lines(scene: dict) -> list[dict]:
    """Everything spoken in a scene, in the order it is heard.

    Narration first (it sets the scene), then the dialogue as written. The key
    is stable across runs, which is what lets a resume find audio it already
    synthesized.
    """
    lines: list[dict] = []
    number = int(scene.get("scene") or 0)
    if scene.get("narration"):
        lines.append({"key": f"s{number:02d}_l00", "character": "",
                      "text": scene["narration"]})
    for index, line in enumerate(scene.get("dialogue") or [], start=1):
        lines.append({"key": f"s{number:02d}_l{index:02d}",
                      "character": line.get("character", ""),
                      "text": line.get("line", "")})
    return [line for line in lines if line["text"].strip()]


def _ledger_index(film: dict) -> dict[str, dict]:
    raw = film.get("progress_json") or "[]"
    entries = json.loads(raw) if isinstance(raw, str) else list(raw)
    return {entry["key"]: entry for entry in entries if entry.get("key")}


def _reusable(entry: dict | None, text: str, work: Path | None) -> bool:
    """Whether what a previous run produced for this key can be reused as is.

    Three things have to hold: the previous attempt succeeded, the file is still
    there, and the prompt has not changed since. A placeholder is deliberately
    NOT reusable — it is a failure marker, and a resume is the chance to try
    that shot again.
    """
    if entry is None or work is None:
        return False
    if entry.get("status") != "ok" or entry.get("hash") != _hash(text):
        return False
    return (work / entry.get("file", "")).exists()


def _work_dir(film: dict) -> Path | None:
    job_id = film.get("job_id")
    return settings.jobs_dir / job_id if job_id else None


def _voice_for(name: str, characters: dict, options: dict) -> dict | None:
    """The voice a line is spoken in: the character's own if they were given
    one, otherwise the film's narrator."""
    for character in characters.get("characters", []):
        if character["name"].strip().lower() == (name or "").strip().lower():
            if character.get("voice_id"):
                voice = db.get_voice(character["voice_id"])
                if voice is not None:
                    return voice
            break
    return db.get_voice(options["voice_id"]) if options["voice_id"] else None


def generate(film_id: str) -> dict:
    """Generates every shot, records the narration, assembles and renders.

    Runs on the worker's film queue — a film is many minutes of work and does
    not fit in an HTTP request.

    The two things that matter most here are stated once and hold throughout:
    a shot that fails to generate leaves a placeholder and the film still gets
    assembled, and every shot that succeeded is written to the ledger
    immediately, so a resume never pays for it twice.
    """
    row = db.get_film(film_id)
    if row is None:
        raise RuntimeError(f"Film {film_id} does not exist")

    shots_doc = document(row, "shots")
    screenplay = document(row, "screenplay")
    characters = document(row, "characters") or {"characters": []}
    bible = document(row, "bible") or {}
    if not shots_doc or not screenplay:
        raise StageNotReady("Develop the shot list before generating the film.")

    render.ensure_ffmpeg()
    options = options_of(row)
    shots = shots_doc["shots"]

    job_id = row.get("job_id") or _create_film_job(film_id, row, bible, options)
    work = settings.job_dir(job_id)
    llm.current_job.set(job_id)

    def log(message: str, level: str = "info") -> None:
        db.log_event(job_id, message, level)

    # A shot deleted from the list between runs leaves an entry behind; kept, it
    # would be reported as part of a film it is no longer in.
    live_keys = {shot["key"] for shot in shots} | {
        line["key"] for scene in screenplay.get("scenes", [])
        for line in scene_lines(scene)}
    ledger = {key: entry for key, entry in _ledger_index(row).items()
              if key in live_keys}

    def flush(status: str = "generating", stage: str = "") -> None:
        db.update_film(film_id, status=status, stage=stage or None,
                       progress_json=json.dumps(list(ledger.values()),
                                                ensure_ascii=False))

    try:
        db.update_film(film_id, status="generating", error=None)
        log(f"Film {film_id}: {len(shots)} shot(s), "
            f"{sum(int(s['seconds']) for s in shots)}s of generated video")

        _generate_shots(shots, ledger, work, options, log, flush)
        _record_narration(screenplay, characters, ledger, work, options, log, flush)

        flush(stage="assembling")
        edl = build_timeline(work, screenplay, shots, ledger, options)
        timeline_mod.save(work, edl)
        log(f"Timeline: {len(edl.video)} clip(s), {len(edl.audio)} audio track(s), "
            f"{len(edl.captions)} caption(s), {edl.duration:.1f}s")

        flush(stage="render")
        final = work / "short.mp4"
        timeline_render.render_timeline(work, edl, final, log=lambda m: log(m))
        render.make_thumbnail(final, work / "thumb.jpg", at=min(1.0, edl.duration / 4))
        words = timeline_mod.words_from_captions(edl.captions)
        captions_mod.build_srt(words, work / "captions.srt")
        shutil.copy(final, settings.outputs_dir / f"{job_id}.mp4")

        report = qa_mod.audit(final, None)
        log(f"QA: score {report.score}/100 — "
            f"{'PASSED' if report.passed else 'FAILED'}",
            "info" if report.passed else "warn")

        title = bible.get("title") or row.get("title") or "Film"
        failures = [e for e in ledger.values() if e.get("status") != "ok"]
        result = {
            "mode": MODE,
            "film_id": film_id,
            "title": title,
            "description": bible.get("logline", ""),
            "hashtags": [],
            "duration": round(edl.duration, 2),
            "video": f"/api/jobs/{job_id}/file/short.mp4",
            "thumbnail": f"/api/jobs/{job_id}/file/thumb.jpg",
            "captions_srt": f"/api/jobs/{job_id}/file/captions.srt",
            "words": words,
            "shots": list(ledger.values()),
            "edit_mode": "narrar_por_cima",
            "source_kind": "filme",
        }
        db.update_job(job_id, status="done", stage="qa", progress=1.0, title=title,
                      result_json=json.dumps(result, ensure_ascii=False),
                      qa_json=report.model_dump_json(), error=None)
        db.update_film(film_id, status="done", stage="done", title=title,
                       progress_json=json.dumps(list(ledger.values()),
                                                ensure_ascii=False),
                       error=None)
        if failures:
            log(f"{len(failures)} shot(s) came out as placeholders — regenerate "
                f"the film to retry only those.", "warn")
        notify.job_done(job_id, title, edl.duration, report.score, report.passed)
        return result

    except Exception as exc:  # noqa: BLE001 — the reason has to reach the UI
        db.log_event(job_id, f"Failure: {exc}", "error")
        db.update_film(film_id, status="error", error=str(exc),
                       progress_json=json.dumps(list(ledger.values()),
                                                ensure_ascii=False))
        db.update_job(job_id, status="error", error=str(exc))
        raise


def _create_film_job(film_id: str, film: dict, bible: dict, options: dict) -> str:
    """The job a film becomes.

    A film is not generated *by* the job queue — it has its own queue and its own
    stages — but its result has to BE a job, because that is what publishing, QA,
    the outputs screen and the timeline editor all read. So the job is created
    up front and the film is generated straight into its directory: nothing has
    to be copied afterwards, and `GET /api/jobs/{id}/timeline` works the moment
    the film is done.
    """
    job = JobInput(
        source_type="tema",
        source=film["premise"][:400],
        instruction=film.get("instruction") or "",
        niche=options["niche"],
        language=options["language"],
        voice_id=options["voice_id"],
        caption_style=options["caption_style"],
        caption_position=options["caption_position"],
        watermark=options["watermark"],
        music=False,
        qa_autofix=False,
    )
    title = bible.get("title") or film.get("title") or "Film"
    job_id = db.create_job(job.model_dump(), title)
    # Straight out of "queued": see JOB_STATUS_GENERATING.
    db.update_job(job_id, status=JOB_STATUS_GENERATING, stage=MODE, progress=0.05)
    db.update_film(film_id, job_id=job_id)
    db.log_event(job_id, f"Film {film_id}: generating from the shot list")
    return job_id


def _generate_shots(shots: list[dict], ledger: dict[str, dict], work: Path,
                    options: dict, log, flush) -> None:
    """One AI video clip per shot, with the failures written down.

    Losing a twenty-minute run because shot 14 of 30 came back with a provider
    error would be the worst possible behaviour here, so a failed shot becomes a
    gradient placeholder of the right length and the film carries on. The
    failure is recorded per shot — the API reports it, and the next run retries
    exactly those.
    """
    unavailable = ""
    for index, shot in enumerate(shots, start=1):
        key = shot["key"]
        dest = work / f"shot_{key}.mp4"
        seconds = int(shot["seconds"])

        if _reusable(ledger.get(key), shot["prompt"], work):
            log(f"Shot {index}/{len(shots)} ({key}): reusing the clip already "
                f"generated")
            continue

        flush(stage=f"shot {index}/{len(shots)}")
        entry = {"kind": "shot", "key": key, "scene": shot["scene"],
                 "file": dest.name, "seconds": seconds,
                 "hash": _hash(shot["prompt"]), "status": "ok", "error": ""}

        if unavailable:
            # No generator is configured: the first shot already said so, and
            # calling the same failure another 29 times only wastes the user's
            # time.
            entry.update(status="placeholder", error=unavailable)
            _placeholder(dest, seconds, options, log)
        else:
            try:
                log(f"Shot {index}/{len(shots)} ({key}): generating {seconds}s")
                videogen.generate_clip(shot["prompt"], seconds, dest,
                                       aspect=options["aspect"],
                                       log=lambda m, *_: log(str(m)))
            except videogen.VideoGenNotConfigured as exc:
                unavailable = str(exc)
                entry.update(status="placeholder", error=unavailable)
                log(f"Shot {key}: {exc}", "warn")
                _placeholder(dest, seconds, options, log)
            except Exception as exc:  # noqa: BLE001 — one shot, not the film
                entry.update(status="placeholder", error=str(exc)[:400])
                log(f"Shot {key} failed ({exc}) — placing a placeholder and "
                    f"carrying on", "warn")
                _placeholder(dest, seconds, options, log)

        if entry["status"] == "ok":
            entry["seconds"] = round(render.probe_duration(dest) or seconds, 3)
        ledger[key] = entry
        # Written after every single shot: a crash on shot 15 must not throw
        # away the fourteen that are already paid for.
        flush(stage=f"shot {index}/{len(shots)}")


def _placeholder(dest: Path, seconds: float, options: dict, log) -> None:
    """Stand-in footage for a shot that could not be generated.

    A gradient of the right length keeps the timeline's timing intact, so the
    film still assembles and the user can replace just that clip in the editor.
    """
    try:
        render.background_gradient(max(float(seconds), 1.0), options["niche"], dest)
    except Exception as exc:  # noqa: BLE001
        log(f"Could not even build the placeholder for {dest.name}: {exc}", "warn")


def _record_narration(screenplay: dict, characters: dict, ledger: dict[str, dict],
                      work: Path, options: dict, log, flush) -> None:
    """One audio file per spoken line, in the voice of whoever says it.

    Same rule as the shots: a line that fails to synthesize is recorded as a
    failure and the film goes on without it — the caption still carries the
    text, so the scene is not lost.
    """
    for scene in screenplay.get("scenes", []):
        for line in scene_lines(scene):
            key = line["key"]
            dest = work / f"line_{key}.mp3"
            if _reusable(ledger.get(key), line["text"], work):
                continue

            flush(stage="narration")
            entry = {"kind": "line", "key": key, "scene": scene.get("scene"),
                     "character": line["character"], "file": dest.name,
                     "hash": _hash(line["text"]), "status": "ok", "error": ""}
            try:
                voice = _voice_for(line["character"], characters, options)
                narration = tts.synthesize(line["text"], dest, voice,
                                           lambda m, level="info": log(str(m), level))
                entry["seconds"] = round(narration.duration, 3)
            except Exception as exc:  # noqa: BLE001 — one line, not the film
                entry.update(status="failed", error=str(exc)[:400], seconds=0.0)
                log(f"Line {key} was not recorded ({exc}) — it stays as a "
                    f"caption only", "warn")
            ledger[key] = entry
            flush(stage="narration")


def build_timeline(work: Path, screenplay: dict, shots: list[dict],
                   ledger: dict[str, dict], options: dict) -> Timeline:
    """Everything generated, laid out as an editable timeline.

    Scene by scene, in screenplay order: the scene's shots on the video track,
    its spoken lines on the audio track with a caption each. The result is a
    plain `Timeline`, which is what makes a finished film editable in the
    existing editor — cut a shot, move a line, rewrite a caption, re-render,
    without going back through the LLM or the generator.
    """
    video: list[VideoClip] = []
    audio: list[AudioClip] = []
    cues: list[CaptionCue] = []

    by_scene: dict[int, list[dict]] = {}
    for shot in shots:
        by_scene.setdefault(int(shot["scene"]), []).append(shot)

    cursor = 0.0
    for scene in screenplay.get("scenes", []):
        number = int(scene.get("scene") or 0)
        scene_start = cursor
        placed: list[VideoClip] = []

        for shot in by_scene.get(number, []):
            entry = ledger.get(shot["key"])
            if entry is None or not (work / entry.get("file", "")).exists():
                continue
            length = max(float(entry.get("seconds") or shot["seconds"]), 0.5)
            clip = VideoClip(
                id=timeline_mod._new_id("v"),  # noqa: SLF001
                source=entry["file"], in_point=0.0,
                out_point=round(length, 3), start=round(cursor, 3),
            )
            video.append(clip)
            placed.append(clip)
            cursor += length
        video_end = cursor

        spoken = scene_start
        for line in scene_lines(scene):
            entry = ledger.get(line["key"])
            length = float((entry or {}).get("seconds") or 0.0)
            has_audio = (entry is not None and entry.get("status") == "ok"
                         and (work / entry.get("file", "")).exists() and length > 0)
            if not has_audio:
                # No audio for this line: it still gets a caption, sized by the
                # reading speed, so a failed synthesis does not silently drop
                # the line out of the film.
                length = max(len(line["text"].split()) / 2.6, 1.2)
            else:
                audio.append(AudioClip(
                    id=timeline_mod._new_id("a"),  # noqa: SLF001
                    source=entry["file"], in_point=0.0,
                    out_point=round(length, 3), start=round(spoken, 3),
                    gain=1.0, role="narration",
                ))
            cues.append(CaptionCue(
                id=timeline_mod._new_id("c"),  # noqa: SLF001
                text=line["text"], start=round(spoken, 3),
                end=round(spoken + length, 3),
            ))
            spoken += length

        # A scene's dialogue routinely runs longer than the footage generated for
        # it. Rather than cutting to black, the scene's last shot is stretched to
        # cover the rest: the timeline renderer loops a clip whose out_point runs
        # past its source file, so the picture holds while the line finishes.
        if placed and spoken > video_end + 0.04:
            placed[-1].out_point = round(placed[-1].out_point + (spoken - video_end), 3)
        cursor = max(video_end, spoken)

    return Timeline(
        duration=round(cursor, 3), video=video, audio=audio, captions=cues,
        caption_style=options["caption_style"],
        caption_position=options["caption_position"],
        watermark=options["watermark"],
    ).normalize()
