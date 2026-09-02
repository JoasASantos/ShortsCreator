"""Extração de trechos de destaque de um vídeo longo (episódio, trailer, VOD).

Usa detecção de corte de cena nativa do FFmpeg (`select='gt(scene,X)'`) — não
depende de libass nem de nenhuma lib externa. A ideia: em vez de narrar sobre
um vídeo de 40 minutos do início ao fim, escolhemos N janelas espalhadas pela
linha do tempo (uma por segmento de roteiro), cada uma ancorada no corte de
cena mais próximo pra não abrir/fechar no meio de uma ação.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

SCENE_THRESHOLD = 0.28


def probe_duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip() or 0.0)


def detect_scene_cuts(video: Path, threshold: float = SCENE_THRESHOLD,
                      max_points: int = 400) -> list[float]:
    """Timestamps (segundos) onde a imagem muda bruscamente de um frame pro outro."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video),
         "-vf", f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    times = [float(m) for m in re.findall(r"pts_time:(\d+\.?\d*)", proc.stderr)]
    if len(times) > max_points:
        step = len(times) / max_points
        times = [times[int(i * step)] for i in range(max_points)]
    return times


def pick_windows(total_duration: float, count: int, window_len: float,
                 scene_cuts: list[float] | None = None,
                 skip_start: float = 1.0, skip_end: float = 1.0) -> list[tuple[float, float]]:
    """Escolhe `count` janelas de `window_len`s espalhadas por todo o vídeo.

    Cada âncora é puxada pro corte de cena mais próximo (se houver um a menos
    de 3s de distância), senão fica no ponto uniforme mesmo — melhor um corte
    seco do que perder a cobertura de uma parte do vídeo.
    """
    usable_start = min(skip_start, total_duration * 0.05)
    usable_end = max(total_duration - skip_end, usable_start + window_len)
    span = max(usable_end - usable_start - window_len, 0.1)

    anchors: list[float] = []
    for i in range(count):
        # centros distribuídos uniformemente, não nas pontas exatas
        frac = (i + 0.5) / count
        anchor = usable_start + frac * span
        if scene_cuts:
            nearest = min(scene_cuts, key=lambda t: abs(t - anchor))
            if abs(nearest - anchor) < 3.0:
                anchor = nearest
        anchors.append(max(usable_start, min(anchor, usable_end - window_len)))

    windows: list[tuple[float, float]] = []
    last_end = -1.0
    for anchor in sorted(anchors):
        start = max(anchor, last_end)
        end = min(start + window_len, usable_end)
        if end - start < window_len * 0.4:
            # não sobrou espaço suficiente (vídeo curto/muitos segmentos); comprime
            start = max(usable_start, end - window_len * 0.4)
        windows.append((round(start, 2), round(end, 2)))
        last_end = end
    return windows


def highlight_windows_for_script(video: Path, segment_count: int,
                                 segment_durations: list[float],
                                 log=lambda m: None) -> list[tuple[float, float]]:
    """Ponto de entrada usado pelo orchestrator: uma janela por segmento de corpo."""
    total = probe_duration(video)
    if total <= 0:
        raise RuntimeError("Não foi possível medir a duração do vídeo de origem.")

    avg_window = sum(segment_durations) / len(segment_durations) if segment_durations else 4.0
    log(f"Detectando cortes de cena em {total:.0f}s de vídeo…")
    cuts = detect_scene_cuts(video)
    log(f"{len(cuts)} corte(s) de cena detectado(s); montando {segment_count} destaque(s)")

    windows = pick_windows(total, segment_count, avg_window, scene_cuts=cuts)
    return windows


def windows_across_sources(sources: list[Path], segment_count: int,
                           segment_durations: list[float],
                           log=lambda m: None) -> list[tuple[Path, float, float]]:
    """Distribui os trechos entre VÁRIOS vídeos de origem.

    Cada vídeo recebe uma fatia proporcional à sua duração — um vídeo de 2min
    junto com um de 30s não pode ceder o mesmo número de trechos. Dentro de
    cada vídeo os pontos ainda são ancorados nos cortes de cena.
    """
    if len(sources) == 1:
        windows = highlight_windows_for_script(
            sources[0], segment_count, segment_durations, log)
        return [(sources[0], start, end) for start, end in windows]

    durations = [probe_duration(src) for src in sources]
    total = sum(durations) or 1.0
    log(f"Distribuindo {segment_count} trecho(s) entre {len(sources)} vídeos")

    # quantos trechos cada vídeo cede, proporcional à sua duração
    quotas = [max(1, round(segment_count * d / total)) for d in durations]
    while sum(quotas) > segment_count:
        quotas[quotas.index(max(quotas))] -= 1
    while sum(quotas) < segment_count:
        quotas[quotas.index(min(quotas))] += 1

    out: list[tuple[Path, float, float]] = []
    cursor = 0
    for source, quota, duration in zip(sources, quotas, durations):
        if quota <= 0 or duration <= 0:
            continue
        slice_durations = segment_durations[cursor:cursor + quota] or [4.0]
        avg = sum(slice_durations) / len(slice_durations)
        cuts = detect_scene_cuts(source)
        for start, end in pick_windows(duration, quota, avg, scene_cuts=cuts):
            out.append((source, start, end))
        cursor += quota
        log(f"  {source.name}: {quota} trecho(s) de {duration:.0f}s")

    return out[:segment_count]
