"""Footage de fundo que você traz: gameplay, parkour, satisfying.

O formato mais comum de short narrado hoje é uma história falada por cima de
um vídeo que não tem nada a ver com ela — Minecraft parkour, Subway Surfers,
alguém cortando sabonete. A imagem não ilustra nada: ela existe para a mão não
subir a tela. Nenhum banco de stock tem isso, e a busca por b-roll nunca vai
devolver parkour de Minecraft.

Então é material seu: um vídeo longo, uma vez, reaproveitado em todos os
shorts.

Duas coisas fazem isso funcionar na prática, e as duas são sobre repetição:

* O arquivo é BAIXADO UMA VEZ e fica em cache pelo endereço. Duas horas de
  parkour não podem ser baixadas de novo a cada short — e não são: dez shorts
  do mesmo canal usam o mesmo arquivo.
* Cada short pega um TRECHO DIFERENTE. Dez vídeos abrindo no mesmo frame é o
  que faz um perfil parecer automático; a janela é sorteada dentro do que o
  arquivo tem, e a semente é o próprio job, então o mesmo job re-renderizado
  dá no mesmo trecho.

O áudio do gameplay nunca entra. A narração é dona do áudio, e som de jogo por
baixo de uma história é a diferença entre "fundo" e "dois vídeos ao mesmo
tempo".
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from . import ingest, render

# Onde os vídeos de fundo ficam. Fora de `jobs/`, porque não pertencem a um
# job: pertencem a você, e são usados por todos.
def cache_dir() -> Path:
    path = settings.data_dir / "fundos"
    path.mkdir(parents=True, exist_ok=True)
    return path


# Margem cortada das duas pontas ao sortear o trecho. Começo de vídeo é
# intro/logo e fim é tela de inscrever-se — nenhum dos dois é o que se quer
# por baixo de uma narração.
EDGE = 8.0


# A pasta onde você larga os seus próprios gameplays. Qualquer arquivo de vídeo
# colocado aqui aparece como fundo disponível, sem download e sem cadastro —
# arrastar para uma pasta é menos fricção que qualquer formulário.
def stocks_dir() -> Path:
    path = settings.data_dir / "stocks"
    path.mkdir(parents=True, exist_ok=True)
    return path


VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}

# Gameplays longos e livres para uso, para quem não tem nenhum à mão. São
# pontos de partida verificados no momento em que foram anotados, não uma
# promessa: um vídeo do YouTube pode sair do ar ou mudar de licença, e por isso
# o que aparece na tela é a licença que o canal declarou, para você conferir.
STOCK_CATALOG = [
    {"id": "minecraft-parkour-10", "name": "Minecraft Parkour · 10 min",
     "url": "https://www.youtube.com/watch?v=__NQWLBA7Co",
     "kind": "minecraft", "declared": "Free to Use (declarado pelo canal)",
     "minutes": 10},
    {"id": "minecraft-parkour-7", "name": "Minecraft Parkour 4K · 7 min",
     "url": "https://www.youtube.com/watch?v=XBIaqOm0RKQ",
     "kind": "minecraft", "declared": "Free to Use (declarado pelo canal)",
     "minutes": 7},
    {"id": "minecraft-parkour-30", "name": "Minecraft Parkour · 30 min",
     "url": "https://www.youtube.com/watch?v=u7kdVe8q5zs",
     "kind": "minecraft", "declared": "No Copyright (declarado pelo canal)",
     "minutes": 30},
    {"id": "minecraft-parkour-80", "name": "Minecraft Parkour relaxante · 80 min",
     "url": "https://www.youtube.com/watch?v=n_Dv4JMiwK8",
     "kind": "minecraft", "declared": "declarado pelo canal", "minutes": 80},
]


@dataclass
class Fundo:
    """Um arquivo de footage guardado, pronto para virar fundo."""
    id: str
    name: str
    path: Path
    seconds: float
    source_url: str = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "seconds": round(self.seconds, 2),
                "source_url": self.source_url,
                "size_mb": round(self.path.stat().st_size / 1_048_576, 1)
                if self.path.exists() else 0.0}


def _key(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]


def fetch(url: str, log=lambda m, level="info": None) -> Fundo:
    """O arquivo para este endereço, baixando só se ainda não estiver aqui."""
    url = (url or "").strip()
    if not ingest.is_url(url):
        raise ValueError(f"Isso não é um link de vídeo: {url}")

    key = _key(url)
    home = cache_dir() / key
    meta_path = home / "fundo.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        path = home / meta["file"]
        if path.exists():
            log(f"Fundo em cache: {meta['name']} ({meta['seconds']:.0f}s)")
            return Fundo(id=key, name=meta["name"], path=path,
                         seconds=float(meta["seconds"]), source_url=url)
        # O registro sobreviveu ao arquivo (disco limpo pela metade): baixa de
        # novo em vez de falhar apontando para um caminho que não existe.

    home.mkdir(parents=True, exist_ok=True)
    log(f"Baixando o fundo de {url} — uma vez só; os próximos shorts reusam.")
    path, info = ingest.download_video(url, home)
    if path is None or not path.exists():
        raise RuntimeError(f"Nada foi baixado de {url}")

    seconds = render.probe_duration(path)
    if seconds <= 0:
        raise RuntimeError("O vídeo baixou mas não tem duração legível.")
    name = (info.get("title") or path.stem)[:120]
    meta_path.write_text(json.dumps(
        {"name": name, "file": path.name, "seconds": seconds, "url": url},
        ensure_ascii=False), encoding="utf-8")
    log(f"Fundo guardado: {name} ({seconds:.0f}s)")
    return Fundo(id=key, name=name, path=path, seconds=seconds, source_url=url)


def adopt(source: Path, name: str = "",
          log=lambda m, level="info": None) -> Fundo:
    """Um arquivo enviado por upload, copiado para o cache."""
    if not source.exists():
        raise FileNotFoundError("O arquivo enviado não está mais no disco.")
    key = _key(f"upload:{source.name}:{source.stat().st_size}")
    home = cache_dir() / key
    home.mkdir(parents=True, exist_ok=True)
    dest = home / f"fundo{source.suffix.lower() or '.mp4'}"
    if not dest.exists():
        shutil.copy(source, dest)
    seconds = render.probe_duration(dest)
    if seconds <= 0:
        raise RuntimeError("O arquivo enviado não tem duração legível.")
    label = name.strip() or source.stem
    (home / "fundo.json").write_text(json.dumps(
        {"name": label, "file": dest.name, "seconds": seconds, "url": ""},
        ensure_ascii=False), encoding="utf-8")
    log(f"Fundo guardado: {label} ({seconds:.0f}s)")
    return Fundo(id=key, name=label, path=dest, seconds=seconds)


def from_folder() -> list[dict]:
    """Os vídeos que você largou em `data/stocks/`.

    Sem cadastro e sem cópia: o arquivo fica onde está e é lido de lá. Copiar
    um gameplay de duas horas para "guardar" seria duplicar gigabytes por
    nada.
    """
    out = []
    for path in sorted(stocks_dir().iterdir()):
        if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        seconds = render.probe_duration(path)
        if seconds <= 0:
            continue
        out.append(Fundo(id=f"pasta:{path.name}", name=path.stem, path=path,
                         seconds=seconds).as_dict() | {"origem": "pasta"})
    return out


def catalog() -> list[dict]:
    """O catálogo de gameplays livres, dizendo quais já estão aqui."""
    saved = {item["source_url"] for item in listar() if item["source_url"]}
    return [dict(entry, saved=entry["url"] in saved) for entry in STOCK_CATALOG]


def listar() -> list[dict]:
    """Os fundos já guardados."""
    out = []
    for home in sorted(cache_dir().glob("*")):
        meta_path = home / "fundo.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        path = home / meta.get("file", "")
        if not path.exists():
            continue
        out.append(Fundo(id=home.name, name=meta.get("name", home.name),
                         path=path, seconds=float(meta.get("seconds") or 0),
                         source_url=meta.get("url", "")).as_dict()
                   | {"origem": "baixado"})
    return out


def todos() -> list[dict]:
    """Baixados e largados na pasta, juntos — é tudo fundo."""
    return listar() + from_folder()


def get(fundo_id: str) -> Fundo | None:
    if fundo_id.startswith("pasta:"):
        path = stocks_dir() / fundo_id.split(":", 1)[1]
        if not path.exists():
            return None
        seconds = render.probe_duration(path)
        return Fundo(id=fundo_id, name=path.stem, path=path,
                     seconds=seconds) if seconds > 0 else None

    home = cache_dir() / fundo_id
    meta_path = home / "fundo.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    path = home / meta.get("file", "")
    if not path.exists():
        return None
    return Fundo(id=fundo_id, name=meta.get("name", fundo_id), path=path,
                 seconds=float(meta.get("seconds") or 0),
                 source_url=meta.get("url", ""))


def remove(fundo_id: str) -> bool:
    home = cache_dir() / fundo_id
    if not home.exists():
        return False
    shutil.rmtree(home, ignore_errors=True)
    return True


def window(fundo: Fundo, duration: float, seed: str = "") -> float:
    """Onde começar dentro do arquivo.

    Sorteado, mas determinístico pela semente: o mesmo job re-renderizado abre
    no mesmo trecho, e dois jobs diferentes não abrem no mesmo frame. Dez
    shorts com a mesma abertura é o que faz um perfil parecer automático.
    """
    usable = fundo.seconds - duration - EDGE
    if usable <= EDGE:
        # Arquivo curto: começa do início e o render dá a volta quando acabar.
        return 0.0
    return round(random.Random(seed or fundo.id).uniform(EDGE, usable), 2)


def build(fundo: Fundo, duration: float, out: Path, seed: str = "",
          fill: str = "preencher", log=lambda m, level="info": None) -> Path:
    """O fundo pronto: trecho sorteado, mudo, no quadro do short.

    `-stream_loop` cobre o arquivo mais curto que a narração — vinte segundos
    de parkour por baixo de noventa de história dá a volta em vez de acabar em
    preto.
    """
    start = window(fundo, duration, seed)
    log(f"Fundo: {fundo.name} a partir de {start:.0f}s "
        f"({duration:.0f}s, sem áudio)")
    debar = render.content_crop(fundo.path)
    vf = debar + (render.FILL_919 if fill == "preencher" else render.FIT_919)
    render._run([  # noqa: SLF001 — o único runner de ffmpeg do projeto
        "ffmpeg", "-y", "-stream_loop", "-1", "-ss", f"{start:.2f}",
        "-i", str(fundo.path), "-t", f"{duration:.2f}", "-an", "-vf", vf,
        "-r", str(settings.fps), "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "21", "-pix_fmt", "yuv420p", str(out)])
    return out
