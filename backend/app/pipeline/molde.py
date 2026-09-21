"""Clonar a FORMA de um vídeo, e fazer outros com ela.

A ideia vem do que um vídeo que funcionou realmente ensina. Não é o assunto —
esse é dele. É a forma: quantos segundos o gancho tem antes de dizer a que
veio, quantas palavras cabem antes do primeiro corte, com que frequência a
imagem muda, onde a virada acontece, quanto tempo sobra para a chamada final.
Isso se repete em vídeo nenhum por acaso, e é a parte que dá para reaproveitar
sem copiar ninguém.

O fluxo:

    vídeo de referência  ->  analisar  ->  MOLDE (batidas, ritmo, densidade)
    molde + assunto novo ->  roteiro que cabe nas mesmas batidas  ->  short

O molde é ancorado em PALAVRAS, não em segundos. Um roteiro novo nunca tem
exatamente o mesmo tamanho do original, e uma batida definida em segundos
obriga a encher ou cortar na marra; definida em palavras, ela estica junto com
a fala e o ritmo se mantém.

O que NÃO sai daqui é conteúdo do vídeo original: nem a fala, nem as imagens,
nem a ideia. O molde guarda números e papéis — "gancho de 2.4s, 9 palavras,
corte seco" — e é isso que alimenta o roteiro novo. O texto da referência fica
no registro para você conferir o que foi medido, e nunca entra no prompt de
escrita.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import highlights, llm, reels, script as script_mod
from ..schemas import ScriptSegment, ShortScript

# Uma batida é uma unidade de sentido na fala, cortada onde a pessoa respira.
# Abaixo disso não é batida, é hesitação.
MIN_BEAT_SECONDS = 0.8
# Pausa que separa duas batidas. Medida em fala real, não em pontuação: a
# transcrição raramente traz vírgula onde a pessoa de fato parou.
BEAT_GAP = 0.42

# Teto do que o molde carrega. Um vídeo de dez minutos tem cem batidas e nada
# disso ajuda a escrever um short.
MAX_BEATS = 24


@dataclass
class Beat:
    """Uma batida do molde: o papel que cumpre e o espaço que ocupa."""
    kind: str            # "gancho" | "corpo" | "virada" | "fecho"
    seconds: float
    words: int
    cuts: int            # cortes de imagem dentro dela
    starts_at: float     # onde começava no original, só para leitura humana

    @property
    def pace(self) -> float:
        return round(self.words / self.seconds, 2) if self.seconds else 0.0


@dataclass
class Template:
    """A forma de um vídeo, sem o conteúdo dele."""
    id: str = ""
    name: str = ""
    source_url: str = ""
    seconds: float = 0.0
    words: int = 0
    beats: list[Beat] = field(default_factory=list)
    cuts: int = 0
    language: str = ""
    # O texto medido. Fica guardado para auditoria e NUNCA vai para o prompt
    # que escreve o roteiro novo.
    reference_text: str = ""

    @property
    def pace(self) -> float:
        """Palavras por segundo faladas — o ritmo que o roteiro novo copia."""
        return round(self.words / self.seconds, 2) if self.seconds else 0.0

    @property
    def cuts_per_minute(self) -> float:
        return round(self.cuts / (self.seconds / 60), 1) if self.seconds else 0.0

    @property
    def hook_seconds(self) -> float:
        return self.beats[0].seconds if self.beats else 0.0

    def as_dict(self) -> dict:
        data = asdict(self)
        data.update(pace=self.pace, cuts_per_minute=self.cuts_per_minute,
                    hook_seconds=self.hook_seconds)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Template":
        beats = [Beat(**b) for b in data.get("beats") or []]
        return cls(id=data.get("id", ""), name=data.get("name", ""),
                   source_url=data.get("source_url", ""),
                   seconds=float(data.get("seconds") or 0.0),
                   words=int(data.get("words") or 0), beats=beats,
                   cuts=int(data.get("cuts") or 0),
                   language=data.get("language", ""),
                   reference_text=data.get("reference_text", ""))


def analyze(video: Path, name: str = "", source_url: str = "",
            log=lambda m, level="info": None) -> Template:
    """Medir a forma de um vídeo de referência.

    Duas medições independentes, cruzadas: a fala (palavras com tempo) e a
    imagem (cortes de cena). Uma sem a outra descreve metade do vídeo — um
    monólogo de plano fixo e um vídeo com corte a cada segundo podem ter
    exatamente o mesmo texto.
    """
    words, language = reels.transcribe_words(video, log)
    if not words:
        raise ValueError(
            "Nada foi transcrito deste vídeo, então não há fala para medir. "
            "Um molde é feito do ritmo da fala e dos cortes; sem fala, o que "
            "sobra é só a contagem de cortes.")

    duration = highlights.probe_duration(video)
    cuts = highlights.detect_scene_cuts(video)
    log(f"{len(words)} palavra(s), {len(cuts)} corte(s) de cena em "
        f"{duration:.1f}s")

    groups = _group(words)
    beats = _beats(groups, cuts, duration)
    template = Template(
        name=name or video.stem, source_url=source_url,
        seconds=round(duration, 2), words=len(words), beats=beats,
        cuts=len(cuts), language=language,
        reference_text=" ".join(w["word"] for w in words)[:4000])
    log(f"Molde: {len(beats)} batida(s), {template.pace} palavras/s, "
        f"{template.cuts_per_minute} cortes/min, gancho de "
        f"{template.hook_seconds:.1f}s")
    return template


def _group(words: list[dict]) -> list[list[dict]]:
    """Agrupa palavras em batidas, cortando onde a pessoa respirou."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    for word in words:
        if current:
            gap = float(word["start"]) - float(current[-1]["end"])
            span = float(current[-1]["end"]) - float(current[0]["start"])
            if gap > BEAT_GAP and span >= MIN_BEAT_SECONDS:
                groups.append(current)
                current = []
        current.append(word)
    if current:
        groups.append(current)
    return groups


def _beats(groups: list[list[dict]], cuts: list[float],
           duration: float) -> list[Beat]:
    """As batidas com seu papel. O papel vem da POSIÇÃO, não do texto.

    Um gancho é o que abre, um fecho é o que encerra, e a virada é a batida
    mais densa do miolo — onde a pessoa acelera é quase sempre onde ela entrega
    o ponto. Nada disso lê o que foi dito: o molde não carrega o assunto de
    ninguém.
    """
    if not groups:
        return []
    if len(groups) > MAX_BEATS:
        groups = _merge_to(groups, MAX_BEATS)

    beats: list[Beat] = []
    for index, group in enumerate(groups):
        start = float(group[0]["start"])
        end = float(group[-1]["end"])
        inside = sum(1 for cut in cuts if start <= cut < end)
        beats.append(Beat(kind="corpo", seconds=round(max(end - start, 0.1), 2),
                          words=len(group), cuts=inside,
                          starts_at=round(start, 2)))

    beats[0].kind = "gancho"
    if len(beats) > 1:
        beats[-1].kind = "fecho"
    middle = beats[1:-1]
    if middle:
        fastest = max(middle, key=lambda beat: beat.pace)
        fastest.kind = "virada"
    return beats


def _merge_to(groups: list[list[dict]], limit: int) -> list[list[dict]]:
    """Junta as batidas mais curtas até caber no teto, preservando a ordem."""
    merged = [list(group) for group in groups]
    while len(merged) > limit:
        shortest = min(
            range(len(merged) - 1),
            key=lambda i: (float(merged[i][-1]["end"]) - float(merged[i][0]["start"])))
        merged[shortest].extend(merged.pop(shortest + 1))
    return merged


# --------------------------------------------------------------------------
# Escrever um roteiro novo que cabe no molde
# --------------------------------------------------------------------------

VARIANT_SYSTEM = """Você escreve roteiros de short obedecendo a uma FORMA dada.

A forma vem de um vídeo que funcionou: quantas batidas ele tem, quanto tempo e
quantas palavras cabem em cada uma, e com que frequência a imagem corta. O
assunto é OUTRO e é seu.

REGRAS DA FORMA
- Uma batida de saída para cada batida da forma, na mesma ordem.
- A contagem de palavras de cada batida é o alvo. Fique dentro de ±15%: o
  ritmo do vídeo é essa contagem, e estourar nela desmonta a forma.
- `gancho` abre sem preâmbulo: ele tem que funcionar sozinho no primeiro
  segundo, porque é o que decide se alguém fica.
- `virada` é onde o ponto principal entra. É a batida mais rápida da forma.
- `fecho` encerra e faz a chamada, se houver.
- Escreva para ser DITO em voz alta em {language}: contração, frase curta, uma
  ideia por frase.

O QUE NÃO FAZER
- Não copie, não parafraseie e não cite o vídeo de referência. Você não recebeu
  o conteúdo dele — só a forma. Se algo lhe parecer familiar, é coincidência e
  não é para ser perseguido.
- Não descreva a forma dentro do roteiro ("neste gancho eu vou...").
- Não invente fato sobre o assunto. Na dúvida, escreva o que é verificável."""

VARIANT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "text": {"type": "string"},
                    "broll_query": {"type": "string"},
                },
                "required": ["i", "text", "broll_query"],
            },
        },
    },
    "required": ["title", "description", "hashtags", "beats"],
}


def shape_brief(template: Template) -> str:
    """A forma, escrita como instrução. Números e papéis, nada do conteúdo."""
    lines = [f"FORMA ({template.seconds:.0f}s, {template.words} palavras, "
             f"{template.pace} palavras/s, {template.cuts_per_minute} cortes/min):"]
    for index, beat in enumerate(template.beats):
        lines.append(f"{index}. {beat.kind}: {beat.words} palavras em "
                     f"{beat.seconds:.1f}s ({beat.cuts} corte(s) de imagem)")
    return "\n".join(lines)


def write_variant(template: Template, subject: str, instruction: str = "",
                  language: str = "pt-BR", niche: str = "generico",
                  log=lambda m, level="info": None) -> ShortScript:
    """Um roteiro novo sobre `subject`, no formato do molde."""
    if not subject.strip():
        raise ValueError("Diga sobre o que é o vídeo novo.")
    if not template.beats:
        raise ValueError("Este molde não tem batidas — analise o vídeo de novo.")

    system = VARIANT_SYSTEM.replace(
        "{language}", script_mod.language_name(language))
    prompt = f"""{shape_brief(template)}

ASSUNTO DO VÍDEO NOVO: {subject.strip()}
Nicho: {niche}
{f"Instrução: {instruction.strip()}" if instruction.strip() else ""}

Escreva uma batida para cada batida da forma, com o mesmo número `i`.
`broll_query` é a busca de imagem daquela batida, em INGLÊS e concreta
(o que se vê, não o conceito)."""

    data = llm.complete_json(system, prompt, VARIANT_SCHEMA, max_tokens=4000,
                             purpose="molde")
    by_index = {int(item["i"]): item for item in data.get("beats") or []
                if str(item.get("text") or "").strip()}
    if not by_index:
        raise RuntimeError("O modelo não devolveu nenhuma batida.")

    segments: list[ScriptSegment] = []
    for index, beat in enumerate(template.beats):
        item = by_index.get(index)
        if item is None:
            continue
        segments.append(ScriptSegment(
            kind=_segment_kind(beat.kind),
            text=str(item["text"]).strip(),
            broll_query=str(item.get("broll_query") or "").strip()))

    if not segments:
        raise RuntimeError("Nenhuma batida do molde foi preenchida.")

    written = sum(len(s.text.split()) for s in segments)
    log(f"Variante escrita: {len(segments)} batida(s), {written} palavras "
        f"(a forma pede {template.words})")
    return ShortScript(
        title=str(data.get("title") or subject.strip())[:120],
        description=str(data.get("description") or ""),
        hashtags=[h if h.startswith("#") else f"#{h}"
                  for h in (data.get("hashtags") or [])][:8],
        segments=segments,
        estimated_seconds=int(round(template.seconds)))


def _segment_kind(beat_kind: str) -> str:
    """As batidas do molde no vocabulário que o resto do pipeline usa."""
    return {"gancho": "hook", "fecho": "cta"}.get(beat_kind, "corpo")


def as_script_text(script: ShortScript) -> str:
    """O roteiro como texto, que é o que `source_type="roteiro"` recebe.

    É assim que a variante entra no pipeline normal: sem caminho paralelo, sem
    estágio novo, e com o editor, o QA e a publicação funcionando igual.
    """
    return "\n\n".join(segment.text for segment in script.segments)


def slugify(text: str) -> str:
    clean = re.sub(r"[^\w\s-]", "", (text or "").strip().lower())
    return re.sub(r"[\s_-]+", "-", clean)[:60] or "molde"


def dumps(template: Template) -> str:
    return json.dumps(template.as_dict(), ensure_ascii=False)
