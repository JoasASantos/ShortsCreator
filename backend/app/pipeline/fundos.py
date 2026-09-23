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
                         source_url=meta.get("url", "")).as_dict())
    return out


def get(fundo_id: str) -> Fundo | None:
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
