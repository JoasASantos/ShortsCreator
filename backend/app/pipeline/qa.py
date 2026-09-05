"""Automated QA: audits the final file against the required Short format.

It runs over the already rendered MP4 — it does not trust what the pipeline
*thinks* it did.
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
    """cropdetect: if the usable area is smaller than the frame, there are black bars."""
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
    """Stretches where the screen goes practically black.

    A short with a long visual hole (background shorter than the audio, a clip
    deleted in the editor) looked perfect to the rest of the audit — resolution,
    codec and audio are all still correct — so it needs a check of its own.
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
                              message="Subtitle .ass file not found.",
                              fix="Regenerate the subtitles before rendering."))
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
                message=f"MarginV={margin_v:.0f}px: the subtitle reaches into the app UI area.",
                fix=f"Use MarginV >= {SAFE_BOTTOM}px or caption_position='centro'."))
        if min(margin_l, margin_r) < 60:
            issues.append(QAIssue(
                check="safe_area_lateral", severity="aviso",
                message="Side margin under 60px; risk of clipping at the edges.",
                fix="Raise MarginL/MarginR to at least 90px."))
        if font_size < 54:
            issues.append(QAIssue(
                check="tamanho_fonte", severity="aviso",
                message=f"A {font_size:.0f}px font is small for a vertical phone screen.",
                fix="Use a font >= 72px."))
        break

    dialogues = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    if not dialogues:
        issues.append(QAIssue(check="legenda_vazia", severity="fatal",
                              message="No subtitles in the .ass file.",
                              fix="Check the narration timings."))
    return issues


def _union_seconds(spans: list[tuple[float, float]]) -> float:
    """Time covered by the union of the spans (karaoke produces overlapping ones)."""
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
    """Check the overlays actually burned into the video."""
    issues: list[QAIssue] = []
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    if not entries:
        return [QAIssue(check="legenda_vazia", severity="fatal",
                        message="No subtitle overlay in the video.",
                        fix="Reprocess the subtitles.")], {}

    captions_only = [e for e in entries if e.get("kind", "caption") == "caption"]
    if not captions_only:
        return [QAIssue(check="legenda_vazia", severity="fatal",
                        message="Video has no burned-in subtitles.",
                        fix="Reprocess the subtitles.")], {}

    lowest = max(e["y"] for e in captions_only)
    leftmost = min(e["x"] for e in captions_only)
    covered = _union_seconds(
        [(e["start"], min(e["end"], duration)) for e in captions_only])

    if lowest > TARGET_H - SAFE_BOTTOM:
        issues.append(QAIssue(
            check="safe_area_inferior", severity="erro",
            message=f"Subtitle starts at y={lowest}px, inside the {SAFE_BOTTOM}px "
                    "reserved for the TikTok/Shorts UI.",
            fix="Use caption_position='centro'."))
    if leftmost < 60:
        issues.append(QAIssue(
            check="safe_area_lateral", severity="aviso",
            message=f"Subtitle sits {leftmost}px from the left edge.",
            fix="Raise the side margin to 90px."))
    for entry in captions_only:
        if entry["end"] > duration + 0.5:
            issues.append(QAIssue(
                check="legenda_fora_do_video", severity="aviso",
                message="There is a subtitle timed past the end of the video.",
                fix="Align the background duration with the narration."))
            break
    if duration and covered / duration < 0.55:
        issues.append(QAIssue(
            check="cobertura_legenda", severity="aviso",
            message=f"Subtitles cover only {covered / duration:.0%} of the video; "
                    "shorts without subtitles lose retention.",
            fix="Review the narration timings."))

    return issues, {"overlay_count": len(entries),
                    "caption_coverage": round(covered / duration, 3) if duration else 0}


def audit(video: Path, ass_path: Path | None = None,
          expected_duration: float | None = None) -> QAReport:
    issues: list[QAIssue] = []
    metrics: dict = {}

    if not video.exists() or video.stat().st_size < 20_000:
        return QAReport(passed=False, score=0, metrics={},
                        issues=[QAIssue(check="arquivo", severity="fatal",
                                        message="Final video missing or empty.",
                                        fix="Run the render again.")])

    probe = _ffprobe(video)
    fmt = probe.get("format", {})
    video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    audio_streams = [s for s in probe["streams"] if s["codec_type"] == "audio"]

    if not video_streams:
        return QAReport(passed=False, score=0, metrics={},
                        issues=[QAIssue(check="stream_video", severity="fatal",
                                        message="File has no video stream.", fix="")])

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

    # --- 1. 9:16 aspect ratio ---
    if (width, height) != (TARGET_W, TARGET_H):
        severity = "erro" if abs(ratio - TARGET_RATIO) < 0.01 else "fatal"
        issues.append(QAIssue(
            check="resolucao", severity=severity,
            message=f"Resolution {width}x{height}; the target is {TARGET_W}x{TARGET_H}.",
            fix=f"Render with -s {TARGET_W}x{TARGET_H}."))
    if abs(ratio - TARGET_RATIO) > 0.005:
        issues.append(QAIssue(
            check="proporcao", severity="fatal",
            message=f"Aspect ratio {ratio:.3f} is not 9:16 ({TARGET_RATIO:.3f}). "
                    "Platforms will add bars.",
            fix="Recompose the background with crop/pad to 1080x1920."))
    if vs.get("sample_aspect_ratio", "1:1") not in ("1:1", "0:1", None):
        issues.append(QAIssue(
            check="sar", severity="erro",
            message=f"SAR {vs.get('sample_aspect_ratio')} distorts the picture.",
            fix="Add setsar=1 to the video filter."))

    # --- 2. duration ---
    if duration < settings.min_short_seconds:
        issues.append(QAIssue(
            check="duracao_minima", severity="erro",
            message=f"{duration:.1f}s is below the useful minimum of {settings.min_short_seconds}s.",
            fix="Lengthen the script or slow the narration down."))
    if duration > settings.max_short_seconds:
        issues.append(QAIssue(
            check="duracao_maxima", severity="erro",
            message=f"{duration:.1f}s is above the {settings.max_short_seconds}s limit "
                    "(Shorts cuts off past 180s; TikTok switches feeds).",
            fix="Shorten the script."))
    if expected_duration and abs(duration - expected_duration) > 1.5:
        issues.append(QAIssue(
            check="sincronia_duracao", severity="aviso",
            message=f"Video {duration:.1f}s vs narration {expected_duration:.1f}s.",
            fix="Check -shortest and the background trim."))

    # --- 3. codec / compatibility ---
    if vs.get("codec_name") != "h264":
        issues.append(QAIssue(
            check="codec_video", severity="erro",
            message=f"Codec {vs.get('codec_name')} is not H.264.",
            fix="Render with -c:v libx264."))
    if vs.get("pix_fmt") != "yuv420p":
        issues.append(QAIssue(
            check="pix_fmt", severity="erro",
            message=f"pix_fmt {vs.get('pix_fmt')} breaks on mobile players.",
            fix="Add -pix_fmt yuv420p."))
    if fps < 24:
        issues.append(QAIssue(
            check="fps", severity="aviso",
            message=f"{fps:.1f} fps is low for fluid motion.",
            fix="Render at 30 fps."))
    if not _faststart(video):
        issues.append(QAIssue(
            check="faststart", severity="aviso",
            message="moov atom at the end of the file; upload and preview are slow.",
            fix="Add -movflags +faststart."))

    # --- 4. audio ---
    if not audio_streams:
        issues.append(QAIssue(
            check="stream_audio", severity="fatal",
            message="Video has no audio — the algorithm deprioritizes silent shorts.",
            fix="Map the narration in the render."))
    else:
        audio = audio_streams[0]
        metrics["audio_codec"] = audio.get("codec_name")
        metrics["sample_rate"] = audio.get("sample_rate")
        if audio.get("codec_name") != "aac":
            issues.append(QAIssue(
                check="codec_audio", severity="aviso",
                message=f"Audio in {audio.get('codec_name')}; AAC is the accepted standard.",
                fix="Use -c:a aac -b:a 192k."))
        loud = _loudness(video)
        metrics.update(loud)
        lufs = loud.get("integrated_lufs")
        if lufs is not None:
            if lufs > -11:
                issues.append(QAIssue(
                    check="loudness", severity="aviso",
                    message=f"{lufs:.1f} LUFS: too loud, platforms will attenuate it.",
                    fix="Normalize with loudnorm=I=-14."))
            elif lufs < -19:
                issues.append(QAIssue(
                    check="loudness", severity="erro",
                    message=f"{lufs:.1f} LUFS: too quiet, weak sound on a phone.",
                    fix="Normalize with loudnorm=I=-14."))
        peak = loud.get("true_peak_dbfs")
        if peak is not None and peak > -0.5:
            issues.append(QAIssue(
                check="true_peak", severity="aviso",
                message=f"True peak {peak:.1f} dBFS: risk of clipping after re-encoding.",
                fix="Limit to -1.5 dBTP."))

        silences = _silence(video)
        metrics["silence_blocks"] = len(silences)
        for start, end in silences:
            if start <= 0.4:
                issues.append(QAIssue(
                    check="silencio_inicial", severity="erro",
                    message=f"Silence over the first {end:.1f}s: kills the hook's retention.",
                    fix="Trim the leading silence from the narration."))
                break
        for start, end in silences:
            if duration and end >= duration - 0.3 and (end - start) > 1.2:
                issues.append(QAIssue(
                    check="silencio_final", severity="aviso",
                    message=f"{end - start:.1f}s of silence at the end.",
                    fix="Trim the tail or shorten the background."))
                break

    # --- 5. black screen / visual hole ---
    blacks = _black_frames(video)
    black_total = sum(end - start for start, end in blacks)
    if blacks:
        metrics["black_seconds"] = round(black_total, 2)
        metrics["black_ratio"] = round(black_total / duration, 3) if duration else 0
    longest = max((end - start for start, end in blacks), default=0.0)
    if duration and black_total / duration > 0.15:
        issues.append(QAIssue(
            check="tela_preta", severity="erro",
            message=f"{black_total:.1f}s of black screen ({black_total / duration:.0%} "
                    "of the video): there is a visual hole in the edit.",
            fix="Extend the video clips or shorten the audio on the timeline."))
    elif longest > 1.5:
        issues.append(QAIssue(
            check="tela_preta", severity="aviso",
            message=f"A {longest:.1f}s stretch with a black screen.",
            fix="Fill the gap on the timeline."))

    # --- black bars / letterbox ---
    crop = _black_bars(video, duration)
    if crop:
        cw, ch, cx, cy = crop
        metrics["cropdetect"] = f"{cw}x{ch}+{cx}+{cy}"
        if cw < width * 0.97 or ch < height * 0.97:
            issues.append(QAIssue(
                check="barras_pretas", severity="erro",
                message=f"Usable area {cw}x{ch} is smaller than the {width}x{height} "
                        "frame: there is letterboxing/pillarboxing.",
                fix="Fill with a blurred background instead of pad."))

    # --- 6. subtitles / safe area ---
    if ass_path:
        issues.extend(_ass_safe_area(Path(ass_path)))
    manifest = video.parent / "overlays.json"
    if manifest.exists():
        overlay_issues, overlay_metrics = _overlay_safe_area(manifest, duration)
        issues.extend(overlay_issues)
        metrics.update(overlay_metrics)

    # --- 7. file weight ---
    size_mb = metrics["size_mb"]
    if size_mb > 280:
        issues.append(QAIssue(
            check="tamanho_arquivo", severity="aviso",
            message=f"{size_mb} MB is large for a mobile upload.",
            fix="Raise the CRF to 22-23."))

    penalty = sum(SEVERITY_WEIGHT[i.severity] for i in issues)
    score = max(0, 100 - penalty)
    passed = not any(i.severity in ("fatal", "erro") for i in issues)
    return QAReport(passed=passed, score=score, issues=issues, metrics=metrics)


def suggest_fix(report: QAReport, job: JobInput) -> tuple[str, JobInput, str] | None:
    """Turn a blocking QA error into a concrete action for the self-adjust loop.

    Returns (description, adjusted job, root stage to redo) or None when the
    problem has no known automatic fix — in that case the self-adjust loop stops
    and leaves the error visible to the user.

    The stage returned is only the ROOT: the caller expands it into the cascade
    of dependent stages (redoing the script forces voice, subtitles, background
    and render to be redone too, otherwise the adjustment would have no effect
    whatsoever on the final file).
    """
    codes = {i.check for i in report.issues if i.severity in ("fatal", "erro")}
    updated = job.model_copy(deep=True)

    if "duracao_minima" in codes:
        if updated.duration >= settings.max_short_seconds:
            return None
        updated.duration = min(updated.duration + 15, settings.max_short_seconds)
        return (f"script too short — raising the target duration to {updated.duration}s",
                updated, "script")

    if "duracao_maxima" in codes:
        if updated.duration <= settings.min_short_seconds:
            return None
        updated.duration = max(updated.duration - 15, settings.min_short_seconds)
        return (f"script too long — lowering the target duration to {updated.duration}s",
                updated, "script")

    if {"safe_area_inferior", "safe_area_lateral"} & codes and updated.caption_position != "centro":
        updated.caption_position = "centro"
        return ("subtitle reaching into the app UI area — moving it to the center",
                updated, "legendas")

    if {"loudness", "true_peak"} & codes and updated.music and updated.music_volume > 0.02:
        updated.music_volume = round(updated.music_volume * 0.5, 3)
        return (f"audio outside the loudness range — lowering the music to {updated.music_volume}",
                updated, "render")

    if "barras_pretas" in codes:
        # Re-frame before giving up on the footage. Zooming until the frame is
        # covered cannot leave a bar, and it keeps the video the user asked
        # for; replacing it with a gradient passes the audit by throwing the
        # content away, which is not a fix.
        if updated.background_fill != "preencher":
            updated.background_fill = "preencher"
            return ("background still has black bars — reframing to fill the "
                    "frame instead of fitting inside it", updated, "fundo")
        # Filling did not help, so the bars are not a framing problem: the
        # source itself is mostly black. Only now is the gradient the better
        # picture, and the log says the footage was dropped.
        if updated.background != "gradiente":
            updated.background = "gradiente"
            return ("reframing did not clear the black bars — the source is "
                    "too dark to use; falling back to a gradient",
                    updated, "fundo")

    return None


def _parse_fps(value: str) -> float:
    try:
        num, den = value.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except Exception:
        return 0.0
