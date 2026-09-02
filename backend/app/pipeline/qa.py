"""QA automatizado: audita o arquivo final contra o formato exigido de Short.

Roda sobre o MP4 já renderizado — não confia no que o pipeline *acha* que fez.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..config import settings
from ..schemas import JobInput, QAIssue, QAReport

TARGET_W, TARGET_H = settings.width, settings.height
TARGET_RATIO = 9 / 16
SAFE_BOTTOM = 340
SAFE_TOP = 200

SEVERITY_WEIGHT = {"fatal": 100, "erro": 25, "aviso": 8, "info": 0}


def _ffprobe(video: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(video)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def _ffmpeg_stderr(args: list[str]) -> str:
    proc = subprocess.run(["ffmpeg", *args], capture_output=True, text=True)
    return proc.stderr


def _loudness(video: Path) -> dict:
    stderr = _ffmpeg_stderr(
        ["-hide_banner", "-nostats", "-i", str(video),
         "-af", "ebur128=peak=true", "-f", "null", "-"]
    )
    metrics: dict = {}
    tail = stderr[-4000:]
    for key, pattern in (
        ("integrated_lufs", r"I:\s+(-?\d+\.?\d*)\s+LUFS"),
        ("loudness_range", r"LRA:\s+(-?\d+\.?\d*)\s+LU"),
        ("true_peak_dbfs", r"Peak:\s+(-?\d+\.?\d*)\s+dBFS"),
    ):
        found = re.findall(pattern, tail)
        if found:
            metrics[key] = float(found[-1])
    return metrics


def _black_bars(video: Path, duration: float) -> tuple[int, int, int, int] | None:
    """cropdetect: se a área útil for menor que o quadro, há barras pretas."""
    sample_at = max(duration * 0.35, 0.5)
    stderr = _ffmpeg_stderr(
        ["-hide_banner", "-nostats", "-ss", f"{sample_at:.2f}", "-i", str(video),
         "-t", "3", "-vf", "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"]
    )
    matches = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", stderr)
    if not matches:
        return None
    w, h, x, y = (int(v) for v in matches[-1])
    return w, h, x, y


def _black_frames(video: Path) -> list[tuple[float, float]]:
    """Trechos em que a tela fica praticamente preta.

    Um short com buraco visual longo (fundo mais curto que o áudio, clipe
    removido no editor) parecia perfeito para o resto da auditoria — resolução,
    codec e áudio continuam corretos — então precisa de uma checagem própria.
    """
    stderr = _ffmpeg_stderr(
        ["-hide_banner", "-nostats", "-i", str(video),
         "-vf", "blackdetect=d=0.8:pix_th=0.10", "-f", "null", "-"]
    )
    out: list[tuple[float, float]] = []
    for match in re.finditer(
            r"black_start:(\d+\.?\d*)\s+black_end:(\d+\.?\d*)", stderr):
        out.append((float(match.group(1)), float(match.group(2))))
    return out


def _silence(video: Path) -> list[tuple[float, float]]:
    stderr = _ffmpeg_stderr(
        ["-hide_banner", "-nostats", "-i", str(video),
         "-af", "silencedetect=noise=-45dB:d=0.8", "-f", "null", "-"]
    )
    starts = [float(v) for v in re.findall(r"silence_start:\s*(-?\d+\.?\d*)", stderr)]
    ends = [float(v) for v in re.findall(r"silence_end:\s*(-?\d+\.?\d*)", stderr)]
    return list(zip(starts, ends))


def _faststart(video: Path) -> bool:
    head = video.read_bytes()[:2_000_000]
    moov, mdat = head.find(b"moov"), head.find(b"mdat")
    if moov == -1:
        return False
    return mdat == -1 or moov < mdat


def _ass_safe_area(ass_path: Path) -> list[QAIssue]:
    issues: list[QAIssue] = []
    if not ass_path.exists():
        issues.append(QAIssue(check="legenda_arquivo", severity="erro",
                              message="Arquivo .ass de legenda não encontrado.",
                              fix="Regerar as legendas antes de renderizar."))
        return issues

    text = ass_path.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        if not line.startswith("Style: Legenda"):
            continue
        fields = line.split(":", 1)[1].split(",")
        try:
            font_size = float(fields[2])
            margin_l, margin_r, margin_v = (float(fields[i]) for i in (19, 20, 21))
        except (IndexError, ValueError):
            break
        if margin_v < SAFE_BOTTOM * 0.6:
            issues.append(QAIssue(
                check="safe_area_inferior", severity="erro",
                message=f"MarginV={margin_v:.0f}px: a legenda entra na área da UI do app.",
                fix=f"Usar MarginV >= {SAFE_BOTTOM}px ou caption_position='centro'."))
        if min(margin_l, margin_r) < 60:
            issues.append(QAIssue(
                check="safe_area_lateral", severity="aviso",
                message="Margem lateral menor que 60px; risco de corte nas bordas.",
                fix="Aumentar MarginL/MarginR para pelo menos 90px."))
        if font_size < 54:
            issues.append(QAIssue(
                check="tamanho_fonte", severity="aviso",
                message=f"Fonte {font_size:.0f}px é pequena para tela vertical de celular.",
                fix="Usar fonte >= 72px."))
        break

    dialogues = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    if not dialogues:
        issues.append(QAIssue(check="legenda_vazia", severity="fatal",
                              message="Nenhuma legenda no arquivo .ass.",
                              fix="Verificar timings da narração."))
    return issues


def _union_seconds(spans: list[tuple[float, float]]) -> float:
    """Tempo coberto pela união dos intervalos (karaokê gera spans sobrepostos)."""
    total, end_cursor = 0.0, float("-inf")
    for start, end in sorted(spans):
        if end <= start:
            continue
        start = max(start, end_cursor)
        if end > start:
            total += end - start
            end_cursor = end
    return total


def _overlay_safe_area(manifest: Path, duration: float) -> tuple[list[QAIssue], dict]:
    """Confere as sobreposições realmente queimadas no vídeo."""
    issues: list[QAIssue] = []
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    if not entries:
        return [QAIssue(check="legenda_vazia", severity="fatal",
                        message="Nenhuma sobreposição de legenda no vídeo.",
                        fix="Reprocessar as legendas.")], {}

    captions_only = [e for e in entries if e.get("kind", "caption") == "caption"]
    if not captions_only:
        return [QAIssue(check="legenda_vazia", severity="fatal",
                        message="Vídeo sem legendas queimadas.",
                        fix="Reprocessar as legendas.")], {}

    lowest = max(e["y"] for e in captions_only)
    leftmost = min(e["x"] for e in captions_only)
    covered = _union_seconds(
        [(e["start"], min(e["end"], duration)) for e in captions_only])

    if lowest > TARGET_H - SAFE_BOTTOM:
        issues.append(QAIssue(
            check="safe_area_inferior", severity="erro",
            message=f"Legenda começa em y={lowest}px, dentro dos {SAFE_BOTTOM}px "
                    "reservados para a UI do TikTok/Shorts.",
            fix="Usar caption_position='centro'."))
    if leftmost < 60:
        issues.append(QAIssue(
            check="safe_area_lateral", severity="aviso",
            message=f"Legenda a {leftmost}px da borda esquerda.",
            fix="Aumentar a margem lateral para 90px."))
    for entry in captions_only:
        if entry["end"] > duration + 0.5:
            issues.append(QAIssue(
                check="legenda_fora_do_video", severity="aviso",
                message="Há legenda cronometrada além do fim do vídeo.",
                fix="Alinhar a duração do fundo com a narração."))
            break
    if duration and covered / duration < 0.55:
        issues.append(QAIssue(
            check="cobertura_legenda", severity="aviso",
            message=f"Legendas cobrem apenas {covered / duration:.0%} do vídeo; "
                    "shorts sem legenda perdem retenção.",
            fix="Revisar os timings da narração."))

    return issues, {"overlay_count": len(entries),
                    "caption_coverage": round(covered / duration, 3) if duration else 0}


def audit(video: Path, ass_path: Path | None = None,
          expected_duration: float | None = None) -> QAReport:
    issues: list[QAIssue] = []
    metrics: dict = {}

    if not video.exists() or video.stat().st_size < 20_000:
        return QAReport(passed=False, score=0, metrics={},
                        issues=[QAIssue(check="arquivo", severity="fatal",
                                        message="Vídeo final ausente ou vazio.",
                                        fix="Reexecutar a renderização.")])

    probe = _ffprobe(video)
    fmt = probe.get("format", {})
    video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    audio_streams = [s for s in probe["streams"] if s["codec_type"] == "audio"]

    if not video_streams:
        return QAReport(passed=False, score=0, metrics={},
                        issues=[QAIssue(check="stream_video", severity="fatal",
                                        message="Arquivo sem stream de vídeo.", fix="")])

    vs = video_streams[0]
    width, height = int(vs["width"]), int(vs["height"])
    duration = float(fmt.get("duration", 0) or vs.get("duration", 0) or 0)
    ratio = width / height if height else 0
    fps = _parse_fps(vs.get("r_frame_rate", "0/1"))

    metrics.update({
        "width": width, "height": height, "aspect_ratio": round(ratio, 4),
        "duration_s": round(duration, 2), "fps": round(fps, 2),
        "video_codec": vs.get("codec_name"), "pix_fmt": vs.get("pix_fmt"),
        "size_mb": round(int(fmt.get("size", 0)) / 1_048_576, 2),
        "bitrate_kbps": round(int(fmt.get("bit_rate", 0)) / 1000),
        "sar": vs.get("sample_aspect_ratio", "1:1"),
    })

    # --- 1. proporção 9:16 ---
    if (width, height) != (TARGET_W, TARGET_H):
        severity = "erro" if abs(ratio - TARGET_RATIO) < 0.01 else "fatal"
        issues.append(QAIssue(
            check="resolucao", severity=severity,
            message=f"Resolução {width}x{height}; o alvo é {TARGET_W}x{TARGET_H}.",
            fix=f"Renderizar com -s {TARGET_W}x{TARGET_H}."))
    if abs(ratio - TARGET_RATIO) > 0.005:
        issues.append(QAIssue(
            check="proporcao", severity="fatal",
            message=f"Proporção {ratio:.3f} não é 9:16 ({TARGET_RATIO:.3f}). "
                    "Plataformas vão adicionar barras.",
            fix="Recompor o fundo com crop/pad para 1080x1920."))
    if vs.get("sample_aspect_ratio", "1:1") not in ("1:1", "0:1", None):
        issues.append(QAIssue(
            check="sar", severity="erro",
            message=f"SAR {vs.get('sample_aspect_ratio')} distorce a imagem.",
            fix="Adicionar setsar=1 no filtro de vídeo."))

    # --- 2. duração ---
    if duration < settings.min_short_seconds:
        issues.append(QAIssue(
            check="duracao_minima", severity="erro",
            message=f"{duration:.1f}s abaixo do mínimo útil de {settings.min_short_seconds}s.",
            fix="Alongar o roteiro ou reduzir a velocidade da narração."))
    if duration > settings.max_short_seconds:
        issues.append(QAIssue(
            check="duracao_maxima", severity="erro",
            message=f"{duration:.1f}s acima do limite de {settings.max_short_seconds}s "
                    "(Shorts corta acima de 180s; TikTok muda de feed).",
            fix="Encurtar o roteiro."))
    if expected_duration and abs(duration - expected_duration) > 1.5:
        issues.append(QAIssue(
            check="sincronia_duracao", severity="aviso",
            message=f"Vídeo {duration:.1f}s vs narração {expected_duration:.1f}s.",
            fix="Verificar -shortest e o corte do fundo."))

    # --- 3. codec / compatibilidade ---
    if vs.get("codec_name") != "h264":
        issues.append(QAIssue(
            check="codec_video", severity="erro",
            message=f"Codec {vs.get('codec_name')} não é H.264.",
            fix="Renderizar com -c:v libx264."))
    if vs.get("pix_fmt") != "yuv420p":
        issues.append(QAIssue(
            check="pix_fmt", severity="erro",
            message=f"pix_fmt {vs.get('pix_fmt')} quebra em players móveis.",
            fix="Adicionar -pix_fmt yuv420p."))
    if fps < 24:
        issues.append(QAIssue(
            check="fps", severity="aviso",
            message=f"{fps:.1f} fps é baixo para movimento fluido.",
            fix="Renderizar a 30 fps."))
    if not _faststart(video):
        issues.append(QAIssue(
            check="faststart", severity="aviso",
            message="Átomo moov no fim do arquivo; upload e preview ficam lentos.",
            fix="Adicionar -movflags +faststart."))

    # --- 4. áudio ---
    if not audio_streams:
        issues.append(QAIssue(
            check="stream_audio", severity="fatal",
            message="Vídeo sem áudio — o algoritmo despriorizza shorts mudos.",
            fix="Mapear a narração na renderização."))
    else:
        audio = audio_streams[0]
        metrics["audio_codec"] = audio.get("codec_name")
        metrics["sample_rate"] = audio.get("sample_rate")
        if audio.get("codec_name") != "aac":
            issues.append(QAIssue(
                check="codec_audio", severity="aviso",
                message=f"Áudio em {audio.get('codec_name')}; AAC é o padrão aceito.",
                fix="Usar -c:a aac -b:a 192k."))
        loud = _loudness(video)
        metrics.update(loud)
        lufs = loud.get("integrated_lufs")
        if lufs is not None:
            if lufs > -11:
                issues.append(QAIssue(
                    check="loudness", severity="aviso",
                    message=f"{lufs:.1f} LUFS: alto demais, plataformas vão atenuar.",
                    fix="Normalizar com loudnorm=I=-14."))
            elif lufs < -19:
                issues.append(QAIssue(
                    check="loudness", severity="erro",
                    message=f"{lufs:.1f} LUFS: baixo demais, som fraco no celular.",
                    fix="Normalizar com loudnorm=I=-14."))
        peak = loud.get("true_peak_dbfs")
        if peak is not None and peak > -0.5:
            issues.append(QAIssue(
                check="true_peak", severity="aviso",
                message=f"Pico real {peak:.1f} dBFS: risco de clipping após recodificação.",
                fix="Limitar em -1.5 dBTP."))

        silences = _silence(video)
        metrics["silence_blocks"] = len(silences)
        for start, end in silences:
            if start <= 0.4:
                issues.append(QAIssue(
                    check="silencio_inicial", severity="erro",
                    message=f"Silêncio nos primeiros {end:.1f}s: mata a retenção do hook.",
                    fix="Cortar o silêncio inicial da narração."))
                break
        for start, end in silences:
            if duration and end >= duration - 0.3 and (end - start) > 1.2:
                issues.append(QAIssue(
                    check="silencio_final", severity="aviso",
                    message=f"{end - start:.1f}s de silêncio no fim.",
                    fix="Cortar o final ou encurtar o fundo."))
                break

    # --- 5. tela preta / buraco visual ---
    blacks = _black_frames(video)
    black_total = sum(end - start for start, end in blacks)
    if blacks:
        metrics["black_seconds"] = round(black_total, 2)
        metrics["black_ratio"] = round(black_total / duration, 3) if duration else 0
    longest = max((end - start for start, end in blacks), default=0.0)
    if duration and black_total / duration > 0.15:
        issues.append(QAIssue(
            check="tela_preta", severity="erro",
            message=f"{black_total:.1f}s de tela preta ({black_total / duration:.0%} "
                    "do vídeo): há buraco visual na montagem.",
            fix="Estender os clipes de vídeo ou encurtar o áudio na linha do tempo."))
    elif longest > 1.5:
        issues.append(QAIssue(
            check="tela_preta", severity="aviso",
            message=f"Trecho de {longest:.1f}s com a tela preta.",
            fix="Preencher a lacuna na linha do tempo."))

    # --- barras pretas / letterbox ---
    crop = _black_bars(video, duration)
    if crop:
        cw, ch, cx, cy = crop
        metrics["cropdetect"] = f"{cw}x{ch}+{cx}+{cy}"
        if cw < width * 0.97 or ch < height * 0.97:
            issues.append(QAIssue(
                check="barras_pretas", severity="erro",
                message=f"Área útil {cw}x{ch} menor que o quadro {width}x{height}: "
                        "há letterbox/pillarbox.",
                fix="Usar fundo desfocado para preencher em vez de pad."))

    # --- 6. legendas / safe area ---
    if ass_path:
        issues.extend(_ass_safe_area(Path(ass_path)))
    manifest = video.parent / "overlays.json"
    if manifest.exists():
        overlay_issues, overlay_metrics = _overlay_safe_area(manifest, duration)
        issues.extend(overlay_issues)
        metrics.update(overlay_metrics)

    # --- 7. peso do arquivo ---
    size_mb = metrics["size_mb"]
    if size_mb > 280:
        issues.append(QAIssue(
            check="tamanho_arquivo", severity="aviso",
            message=f"{size_mb} MB é grande para upload móvel.",
            fix="Aumentar o CRF para 22-23."))

    penalty = sum(SEVERITY_WEIGHT[i.severity] for i in issues)
    score = max(0, 100 - penalty)
    passed = not any(i.severity in ("fatal", "erro") for i in issues)
    return QAReport(passed=passed, score=score, issues=issues, metrics=metrics)


def suggest_fix(report: QAReport, job: JobInput) -> tuple[str, JobInput, str] | None:
    """Traduz um erro de QA bloqueante numa ação concreta pro autoajuste.

    Retorna (descrição, job ajustado, etapa raiz a refazer) ou None quando o
    problema não tem correção automática conhecida — nesse caso o loop de
    autoajuste para e deixa o erro visível pro usuário.

    A etapa devolvida é só a RAIZ: quem chama expande na cascata de etapas
    dependentes (refazer o roteiro obriga a refazer voz, legendas, fundo e
    render, senão o ajuste não teria efeito nenhum no arquivo final).
    """
    codes = {i.check for i in report.issues if i.severity in ("fatal", "erro")}
    updated = job.model_copy(deep=True)

    if "duracao_minima" in codes:
        if updated.duration >= settings.max_short_seconds:
            return None
        updated.duration = min(updated.duration + 15, settings.max_short_seconds)
        return (f"roteiro curto demais — aumentando duração alvo para {updated.duration}s",
                updated, "script")

    if "duracao_maxima" in codes:
        if updated.duration <= settings.min_short_seconds:
            return None
        updated.duration = max(updated.duration - 15, settings.min_short_seconds)
        return (f"roteiro longo demais — reduzindo duração alvo para {updated.duration}s",
                updated, "script")

    if {"safe_area_inferior", "safe_area_lateral"} & codes and updated.caption_position != "centro":
        updated.caption_position = "centro"
        return ("legenda invadindo a área da UI do app — movendo para o centro",
                updated, "legendas")

    if {"loudness", "true_peak"} & codes and updated.music and updated.music_volume > 0.02:
        updated.music_volume = round(updated.music_volume * 0.5, 3)
        return (f"áudio fora da faixa de loudness — reduzindo trilha para {updated.music_volume}",
                updated, "render")

    if "barras_pretas" in codes and updated.background != "gradiente":
        updated.background = "gradiente"
        return ("fundo com barras pretas — trocando para gradiente sólido",
                updated, "fundo")

    return None


def _parse_fps(value: str) -> float:
    try:
        num, den = value.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except Exception:
        return 0.0
