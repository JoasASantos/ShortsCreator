"""Ingestão: artigo, vídeo (link ou upload), repositório GitHub, imagens ou texto/roteiro."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..config import settings
from ..routers import uploads as uploads_router
from ..schemas import JobInput

VIDEO_HOSTS = (
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com", "vimeo.com",
    "twitch.tv", "twitter.com", "x.com", "facebook.com", "reddit.com",
)

GITHUB_HOSTS = ("github.com", "www.github.com")

# Arquivos que valem a pena ler na íntegra para entender o projeto — nessa ordem.
GITHUB_PRIORITY_FILES = (
    "README.md", "readme.md", "README.rst", "README",
    "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
    "composer.json", "pom.xml", "build.gradle",
)
GITHUB_ENTRY_HINTS = (
    "main.py", "app.py", "index.js", "index.ts", "main.go", "main.rs",
    "src/main.py", "src/index.ts", "src/App.tsx", "src/App.jsx",
)
GITHUB_SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", ".next", "venv", ".venv",
    "__pycache__", "vendor", "target", ".cache",
}


@dataclass
class SourceMaterial:
    kind: str  # "artigo" | "video" | "tema" | "texto" | "github" | "imagem" | "roteiro"
    title: str = ""
    text: str = ""
    url: str = ""
    video_path: Path | None = None
    video_paths: list[Path] = field(default_factory=list)
    transcript: str = ""
    image_paths: list[Path] = field(default_factory=list)
    repo_path: Path | None = None
    repo_tree: str = ""
    metadata: dict = field(default_factory=dict)

    def context(self, limit: int = 24000) -> str:
        parts = [p for p in (self.title, self.repo_tree, self.text, self.transcript) if p]
        return "\n\n".join(parts)[:limit]


def is_url(value: str) -> bool:
    return bool(re.match(r"^https?://", value.strip(), re.I))


def is_video_url(url: str) -> bool:
    return any(host in url.lower() for host in VIDEO_HOSTS)


def is_github_url(url: str) -> bool:
    return any(host in url.lower() for host in GITHUB_HOSTS)


def ingest(job: JobInput, job_dir: Path, log=lambda m: None) -> SourceMaterial:
    source_type = job.source_type
    source = job.source.strip()

    if source_type == "roteiro":
        return SourceMaterial(kind="roteiro", title=_first_line(source), text=source)

    if source_type == "imagem":
        return _ingest_images(job.attachments, job_dir, log)

    if source_type == "github" or (is_url(source) and is_github_url(source)):
        log(f"Clonando repositório {source}")
        return _ingest_github(source, log)

    if source_type == "video":
        if job.attachments:
            log(f"Usando {len(job.attachments)} vídeo(s) enviado(s) por upload")
            return _ingest_local_videos(job.attachments, job_dir, log)
        log(f"Baixando vídeo de {source}")
        return _ingest_video(source, job_dir, log)

    if source_type == "texto" or (source_type == "tema" and len(source) > 400):
        return SourceMaterial(kind="texto", title=source[:80], text=source)

    if source_type == "tema" and not is_url(source):
        return SourceMaterial(kind="tema", title=source, text=source)

    if not is_url(source):
        return SourceMaterial(kind="tema", title=source, text=source)

    if is_video_url(source):
        log(f"Baixando vídeo de {source}")
        return _ingest_video(source, job_dir, log)

    log(f"Extraindo artigo de {source}")
    return _ingest_article(source)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            break
    else:
        return "Roteiro"
    sentence = re.split(r"(?<=[.!?])\s", line, maxsplit=1)[0]
    if len(sentence) <= 90:
        return sentence
    return sentence[:87].rsplit(" ", 1)[0] + "…"


# ---------------------------- artigo ----------------------------

def _ingest_article(url: str) -> SourceMaterial:
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0 (compatible; ShortsCreator/1.0)"}
    resp = httpx.get(url, headers=headers, timeout=45, follow_redirects=True)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"].strip()

    article = soup.find("article") or soup.find("main") or soup.body
    paragraphs = [p.get_text(" ", strip=True) for p in (article or soup).find_all("p")]
    text = "\n".join(p for p in paragraphs if len(p) > 40)
    if not text:
        text = (article or soup).get_text(" ", strip=True)

    return SourceMaterial(kind="artigo", title=title, text=text[:40000], url=url)


# ---------------------------- vídeo (link) ----------------------------

def _ingest_video(url: str, job_dir: Path, log) -> SourceMaterial:
    out_tpl = str(job_dir / "source.%(ext)s")
    cmd = [
        *ytdlp_command(), "--no-playlist", "--no-warnings",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-auto-subs", "--write-subs",
        # Idiomas exatos, não glob: "pt.*" casa com dezenas de variantes
        # traduzidas e o YouTube responde 429, abortando o download inteiro.
        "--sub-langs", "pt-BR,pt,en",
        "--convert-subs", "srt",
        # legenda é opcional; falha nela não pode derrubar o vídeo
        "--ignore-errors",
        "-o", out_tpl, url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    video_exists = any(job_dir.glob("source.mp4")) or any(
        p for p in job_dir.glob("source.*") if p.suffix in {".mkv", ".webm", ".mov"}
    )
    if proc.returncode != 0 and not video_exists:
        tail = (proc.stderr or proc.stdout).strip()[-500:]
        raise RuntimeError(f"yt-dlp falhou: {tail}")

    video_path = next(iter(sorted(job_dir.glob("source.mp4"))), None)
    if video_path is None:
        candidates = [p for p in job_dir.glob("source.*")
                      if p.suffix in {".mkv", ".webm", ".mov"}]
        video_path = candidates[0] if candidates else None

    info: dict = {}
    info_file = job_dir / "source.info.json"
    if info_file.exists():
        info = json.loads(info_file.read_text(encoding="utf-8"))

    transcript = _read_subtitles(job_dir)
    if not transcript and video_path:
        log("Sem legenda disponível; tentando transcrever com faster-whisper")
        transcript = _whisper_transcribe(video_path, log)

    return SourceMaterial(
        kind="video",
        title=info.get("title", ""),
        text=info.get("description", "") or "",
        url=url,
        video_path=video_path,
        video_paths=[video_path] if video_path else [],
        transcript=transcript,
        metadata={
            "duration": info.get("duration"),
            "uploader": info.get("uploader"),
            "width": info.get("width"),
            "height": info.get("height"),
        },
    )


# ---------------------------- vídeo (upload local: episódio, trailer etc.) ----------------------------

def _ingest_local_videos(attachment_ids: list[str], job_dir: Path,
                        log) -> SourceMaterial:
    """Aceita 1 ou N vídeos. Com vários, o material é tratado como uma
    sequência: os trechos de fundo são distribuídos entre todos eles."""
    paths: list[Path] = []
    durations: list[float] = []
    transcripts: list[str] = []

    for index, attachment_id in enumerate(attachment_ids):
        src = uploads_router.resolve(attachment_id)
        dest = job_dir / f"source_{index:02d}{src.suffix}"
        shutil.copy(src, dest)
        duration = _probe_duration(dest)
        paths.append(dest)
        durations.append(duration)
        log(f"Vídeo {index + 1}/{len(attachment_ids)}: {src.name} ({duration:.0f}s)")

        if duration:
            text = _whisper_transcribe(dest, log)
            if text:
                prefix = f"[vídeo {index + 1}] " if len(attachment_ids) > 1 else ""
                transcripts.append(prefix + text)

    total = sum(durations)
    if len(paths) > 1:
        log(f"{len(paths)} vídeos, {total:.0f}s de material somado")

    return SourceMaterial(
        kind="video",
        title=paths[0].stem if paths else "",
        text="",
        video_path=paths[0] if paths else None,
        video_paths=paths,
        transcript="\n\n".join(transcripts),
        metadata={"duration": total, "clip_durations": durations,
                  "clip_count": len(paths)},
    )


def _probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


# ---------------------------- GitHub ----------------------------

def _ingest_github(url: str, log) -> SourceMaterial:
    if not shutil.which("git"):
        raise RuntimeError("git não encontrado no PATH — necessário para ler repositórios.")

    match = re.search(r"github\.com[/:]([^/]+)/([^/#?]+)", url)
    if not match:
        raise RuntimeError(f"URL de GitHub não reconhecida: {url}")
    owner, repo = match.group(1), match.group(2).removesuffix(".git")
    dest = settings.repos_dir / f"{owner}__{repo}"

    if dest.exists():
        log(f"Repositório em cache: {owner}/{repo}")
        subprocess.run(["git", "-C", str(dest), "pull", "--ff-only"],
                       capture_output=True, text=True, timeout=60)
    else:
        clone_url = f"https://github.com/{owner}/{repo}.git"
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", clone_url, str(dest)],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Falha ao clonar {clone_url}: {proc.stderr.strip()[-400:]}")

    files = _list_repo_files(dest)
    tree = _render_tree(dest, files)
    log(f"{len(files)} arquivos relevantes encontrados; lendo os principais")

    picked = _pick_key_files(dest, files)
    chunks: list[str] = []
    for rel in picked:
        content = (dest / rel).read_text(encoding="utf-8", errors="ignore")
        chunks.append(f"### {rel}\n{content[:settings.github_max_file_chars]}")

    languages = _guess_languages(files)
    text = "\n\n".join(chunks)

    return SourceMaterial(
        kind="github",
        title=f"{owner}/{repo}",
        text=text[:60000],
        url=url,
        repo_path=dest,
        repo_tree=tree,
        metadata={"owner": owner, "repo": repo, "languages": languages,
                  "file_count": len(files)},
    )


def _list_repo_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in GITHUB_SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.stat().st_size > 400_000:
            continue
        out.append(path.relative_to(root))
    return sorted(out)


def _render_tree(root: Path, files: list[Path], max_lines: int = 90) -> str:
    lines = [str(f) for f in files[:max_lines]]
    header = f"Estrutura de {root.name} ({len(files)} arquivos):"
    return header + "\n" + "\n".join(lines)


def _pick_key_files(root: Path, files: list[Path]) -> list[Path]:
    # nomes duplicados (README.md na raiz vs numa subpasta) devem preferir o
    # arquivo mais raso — é o que de fato descreve o projeto como um todo.
    by_name: dict[str, Path] = {}
    for f in sorted(files, key=lambda f: len(f.parts)):
        by_name.setdefault(f.name, f)
    picked: list[Path] = []
    for name in GITHUB_PRIORITY_FILES:
        if name in by_name and by_name[name] not in picked:
            picked.append(by_name[name])
    for hint in GITHUB_ENTRY_HINTS:
        candidate = Path(hint)
        if candidate in files and candidate not in picked:
            picked.append(candidate)
    # completa com os arquivos de código mais "centrais" (menor profundidade primeiro,
    # ignorando testes/docs/exemplos — eles raramente explicam o que o projeto faz)
    code_ext = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".php"}
    noise_dirs = {"tests", "test", "docs", "doc", "examples", "example", "__tests__"}
    remaining = sorted(
        (f for f in files if f.suffix in code_ext and f not in picked
         and not any(part.lower() in noise_dirs for part in f.parts[:-1])),
        key=lambda f: (len(f.parts), str(f)),
    )
    for f in remaining:
        if len(picked) >= settings.github_max_files:
            break
        picked.append(f)
    return picked[: settings.github_max_files]


def _guess_languages(files: list[Path]) -> list[str]:
    ext_lang = {
        ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
        ".js": "JavaScript", ".jsx": "JavaScript", ".go": "Go", ".rs": "Rust",
        ".java": "Java", ".rb": "Ruby", ".php": "PHP", ".c": "C", ".cpp": "C++",
        ".cs": "C#", ".swift": "Swift", ".kt": "Kotlin",
    }
    counts: dict[str, int] = {}
    for f in files:
        lang = ext_lang.get(f.suffix)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return [lang for lang, _ in sorted(counts.items(), key=lambda kv: -kv[1])][:5]


# ---------------------------- imagens ----------------------------

def _ingest_images(attachment_ids: list[str], job_dir: Path, log) -> SourceMaterial:
    if not attachment_ids:
        raise RuntimeError("Nenhuma imagem enviada. Faça upload antes de criar o job.")

    paths: list[Path] = []
    for i, attachment_id in enumerate(attachment_ids):
        src = uploads_router.resolve(attachment_id)
        dest = job_dir / f"image_{i:02d}{src.suffix}"
        shutil.copy(src, dest)
        paths.append(dest)

    log(f"{len(paths)} imagem(ns) recebida(s)")
    return SourceMaterial(kind="imagem", title="", text="", image_paths=paths)


# ---------------------------- utilidades compartilhadas ----------------------------

def ytdlp_command() -> list[str]:
    """yt-dlp pode estar no PATH ou apenas dentro do venv — resolve os dois casos."""
    binary = shutil.which("yt-dlp")
    if binary:
        return [binary]
    try:
        import yt_dlp  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "yt-dlp não encontrado. Instale com: pip install yt-dlp"
        ) from exc
    return [sys.executable, "-m", "yt_dlp"]


def _read_subtitles(job_dir: Path) -> str:
    for srt in sorted(job_dir.glob("source*.srt")):
        text = _srt_to_text(srt.read_text(encoding="utf-8", errors="ignore"))
        if text:
            return text
    return ""


def _srt_to_text(srt: str) -> str:
    lines: list[str] = []
    for line in srt.splitlines():
        line = line.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if not lines or lines[-1] != line:
            lines.append(line)
    return " ".join(lines)


def _whisper_transcribe(video_path: Path, log) -> str:
    return " ".join(s["text"] for s in whisper_segments(video_path, log))


def whisper_segments(video_path: Path, log=lambda m: None) -> list[dict]:
    """Transcreve devolvendo os segmentos com tempo — o clipper precisa dos
    timestamps para escolher início e fim de cada trecho."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log("faster-whisper não instalado; seguindo sem transcrição")
        return []
    model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(video_path), language=None, vad_filter=True)
    return [{"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
            for s in segments]
