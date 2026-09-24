"""Personagens: quem fala, com que voz e com que cara.

Um personagem aqui é três coisas amarradas: um nome, uma voz registrada e uma
imagem. Nada disso é novo no projeto — as vozes já existiam, a imagem é um
upload comum e a sobreposição já é o que o editor usa para picture-in-picture.
O que faltava era o vínculo, para que "o Peter diz isso" vire som e imagem sem
ninguém montar nada à mão.

O lado da tela é parte do personagem, não do roteiro. Numa conversa, quem está
à esquerda fica à esquerda o vídeo inteiro: trocar de lado no meio faz o
espectador perder de vista quem é quem, que é exatamente o que esse formato
não pode perder.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .. import db
from ..config import settings

# Onde as imagens dos personagens ficam. Fora de `jobs/`, porque um personagem
# vive além do vídeo em que apareceu.
def home() -> Path:
    path = settings.data_dir / "elenco"
    path.mkdir(parents=True, exist_ok=True)
    return path


# Onde cada lado põe o personagem, como fração da LARGURA do quadro.
SIDES = {"esquerda": 0.27, "centro": 0.50, "direita": 0.73}
DEFAULT_SIDE = "esquerda"

# O tamanho é fração da ALTURA do quadro, não da largura — e essa é a
# correção que mais muda o resultado. Um recorte de personagem é alto e
# estreito, e duas imagens com a mesma largura na tela saem com alturas
# completamente diferentes: com 46% da largura, o Peter (aspecto 0.63) ocupava
# 41% da altura e o Rick (aspecto 0.42) ocupava 62% — a cabeça dele entrava na
# zona da interface do aplicativo. Medindo por altura, "médio" é médio para
# todo mundo.
SIZES = {"pequeno": 0.30, "medio": 0.40, "grande": 0.52}
DEFAULT_SIZE = "medio"

# Onde ficam os pés, em fração da altura. Personagem apoiado num chão comum
# lê como cena; flutuando no meio da tela, lê como adesivo.
FLOOR = 0.70
# O topo da cabeça nunca sobe além disso: acima é onde o aplicativo desenha a
# própria interface por cima do vídeo.
TOP_LIMIT = 0.10


@dataclass
class Personagem:
    id: str
    name: str
    voice_id: str
    image: str = ""          # caminho absoluto, vazio = só voz
    side: str = DEFAULT_SIDE
    size: str = DEFAULT_SIZE
    note: str = ""

    def geometry(self, frame=None) -> dict:
        """Onde e de que tamanho, para a imagem que este personagem tem.

        A sobreposição é declarada em `width` (fração da largura), então a
        altura desejada é convertida usando o aspecto REAL do arquivo. Sem
        ler o arquivo não dá para acertar: duas imagens de "tamanho médio" com
        aspectos diferentes sairiam com alturas diferentes, que é exatamente o
        bug que isto conserta.
        """
        from . import formats

        frame = frame or formats.VERTICAL
        height = SIZES.get(self.size, SIZES[DEFAULT_SIZE])
        aspect = _aspect(self.image)
        # altura em px -> largura em px -> fração da largura do quadro
        width = (height * frame.height * aspect) / frame.width
        # Largura demais é tão ruim quanto altura demais: um personagem que
        # ocupa a tela inteira tapa o fundo que ele deveria estar comentando.
        width = min(width, 0.62)
        half = (width * frame.width / aspect) / frame.height / 2
        y = min(max(FLOOR - half, TOP_LIMIT + half), 1.0 - half)
        return {"x": SIDES.get(self.side, SIDES[DEFAULT_SIDE]),
                "y": round(y, 4), "width": round(width, 4)}

    def as_dict(self) -> dict:
        data = {"id": self.id, "name": self.name, "voice_id": self.voice_id,
                "side": self.side, "size": self.size, "note": self.note,
                "has_image": bool(self.image and Path(self.image).exists())}
        voice = db.get_voice(self.voice_id) if self.voice_id else None
        data["voice_name"] = voice["name"] if voice else ""
        return data


def _aspect(image: str) -> float:
    """Largura/altura do arquivo. Sem imagem, um retrato comum."""
    if not image or not Path(image).exists():
        return 0.55
    try:
        from PIL import Image

        with Image.open(image) as picture:
            width, height = picture.size
        return (width / height) if height else 0.55
    except Exception:  # noqa: BLE001 — arquivo ilegível não derruba a montagem
        return 0.55


def create(name: str, voice_id: str, image: Path | None = None,
           side: str = DEFAULT_SIDE, note: str = "",
           size: str = DEFAULT_SIZE) -> Personagem:
    label = (name or "").strip()
    if not label:
        raise ValueError("Dê um nome ao personagem.")
    if not voice_id or db.get_voice(voice_id) is None:
        raise ValueError("Escolha uma voz já registrada para este personagem.")
    if side not in SIDES:
        side = DEFAULT_SIDE
    if size not in SIZES:
        size = DEFAULT_SIZE

    person_id = db.new_id("pers")
    stored = ""
    if image is not None and image.exists():
        dest = home() / f"{person_id}{image.suffix.lower() or '.png'}"
        dest.write_bytes(image.read_bytes())
        stored = str(dest)

    db.create_personagem(person_id, label, voice_id, stored, side, note, size)
    return Personagem(id=person_id, name=label, voice_id=voice_id,
                      image=stored, side=side, size=size, note=note)


def listar() -> list[Personagem]:
    return [_row(row) for row in db.list_personagens()]


def get(person_id: str) -> Personagem | None:
    row = db.get_personagem(person_id)
    return _row(row) if row else None


def by_name(name: str) -> Personagem | None:
    """O personagem pelo nome, como o roteiro o chama.

    Sem diferenciar maiúscula nem acento de espaço: o modelo escreve "Peter" e
    o elenco tem "Peter Griffin", e insistir em igualdade exata transformaria
    isso num erro que ninguém entende.
    """
    wanted = _key(name)
    people = listar()
    for person in people:
        if _key(person.name) == wanted:
            return person
    for person in people:
        if wanted and (wanted in _key(person.name) or _key(person.name) in wanted):
            return person
    return None


def remove(person_id: str) -> bool:
    person = get(person_id)
    if person is None:
        return False
    if person.image:
        Path(person.image).unlink(missing_ok=True)
    return db.delete_personagem(person_id)


def _row(row: dict) -> Personagem:
    return Personagem(id=row["id"], name=row["name"],
                      voice_id=row["voice_id"], image=row["image_path"] or "",
                      side=row["side"] or DEFAULT_SIDE,
                      size=row.get("size") or DEFAULT_SIZE,
                      note=row["note"] or "")


def _key(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def cast_brief(people: list[Personagem]) -> str:
    """O elenco como o roteirista o lê."""
    return "\n".join(
        f"- {p.name}" + (f" ({p.note})" if p.note else "") for p in people)


def dumps(people: list[Personagem]) -> str:
    return json.dumps([p.as_dict() for p in people], ensure_ascii=False)
