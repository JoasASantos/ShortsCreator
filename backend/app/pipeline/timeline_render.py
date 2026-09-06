"""Compiles a Timeline (EDL) into an MP4 with FFmpeg.

Strategy: build the video track first (each clip cut, normalized to 9:16 and
positioned; holes become black), then composite the subtitles as PNGs and mix
the audio. It targets the same format as the main pipeline, so QA still applies
to the result.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..config import settings
from . import overlays as overlay_mod, render
from .timeline import Timeline, words_from_captions

W, H, FPS = settings.width, settings.height, settings.fps


def render_timeline(job_dir: Path, timeline: Timeline, out: Path,
                    log=lambda m: None) -> Path:
    timeline.normalize()
    if timeline.duration <= 0:
        raise RuntimeError("The timeline is empty.")

    log(f"Compiling timeline ({timeline.fmt.size}): {len(timeline.video)} video "
        f"clip(s), {len(timeline.audio)} audio, {len(timeline.captions)} "
        f"subtitle(s), {len(timeline.media)} media overlay(s)")

    background = _build_video_track(job_dir, timeline, log)
    overlays = _build_caption_overlays(job_dir, timeline, log)
    return _mux(job_dir, timeline, background, overlays, out)


def _build_video_track(job_dir: Path, timeline: Timeline, log) -> Path:
    """Cut and position each clip; gaps between clips become black."""
    fmt = timeline.fmt
    W, H = fmt.width, fmt.height   # noqa: N806 — the frame is the timeline's, not the module's
    work = job_dir / "tl"
    work.mkdir(exist_ok=True)
    parts: list[Path] = []
    cursor = 0.0

    for index, clip in enumerate(timeline.video):
        if clip.start > cursor + 0.04:
            gap = clip.start - cursor
            parts.append(_black(work, index, gap, fmt))
            cursor += gap

        source = (job_dir / clip.source).resolve()
        if not source.exists():
            raise RuntimeError(f"Clip file not found: {clip.source}")

        dest = work / f"part_{index:03d}.mp4"
        length = max(clip.duration, 0.1)
        if clip.kind == "image":
            render._run([
                "ffmpeg", "-y", "-loop", "1", "-i", str(source),
                "-t", f"{length:.3f}", "-vf",
                f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"scale=in_range=full:out_range=tv,setsar=1,fps={FPS},format=yuv420p",
                "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "21", "-pix_fmt", "yuv420p", "-color_range", "tv", str(dest)])
        else:
            # -stream_loop covers the case where the user stretched the clip
            # beyond the material available in the source file
            render._run([
                "ffmpeg", "-y", "-stream_loop", "-1",
                "-ss", f"{clip.in_point:.3f}", "-i", str(source),
                "-t", f"{length:.3f}", "-an", "-vf", render.fit_filter(fmt),
                "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "21", "-pix_fmt", "yuv420p", str(dest)])
        parts.append(dest)
        cursor += length

    if cursor < timeline.duration - 0.04:
        parts.append(_black(work, len(parts) + 900, timeline.duration - cursor, fmt))

    if not parts:
        raise RuntimeError("No video clip on the timeline.")

    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    background = work / "video_track.mp4"
    render._run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing.name,
                 "-c", "copy", background.name], cwd=work)
    return background


def _black(work: Path, index: int, duration: float, fmt) -> Path:
    dest = work / f"gap_{index:03d}.mp4"
    render._run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c=black:s={fmt.size}:d={max(duration, 0.05):.3f}:r={FPS}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-pix_fmt", "yuv420p", str(dest)])
    return dest


def _build_caption_overlays(job_dir: Path, timeline: Timeline, log) -> list:
    if not timeline.captions:
        return []
    words = words_from_captions(timeline.captions)
    overlay_dir = job_dir / "tl_overlays"
    items = overlay_mod.render_captions(
        words, overlay_dir,
        style=timeline.caption_style, position=timeline.caption_position,
        fmt=timeline.fmt)
    mark = overlay_mod.render_watermark(
        timeline.watermark, overlay_dir, timeline.duration,
        position=timeline.watermark_position, size=timeline.watermark_size,
        opacity=timeline.watermark_opacity, fmt=timeline.fmt)
    if mark:
        items = [mark] + items
    log(f"{len(items)} subtitle overlay(s)")
    return items


def _mux(job_dir: Path, timeline: Timeline, background: Path,
         overlays: list, out: Path) -> Path:
    cmd = ["ffmpeg", "-y", "-i", os.path.relpath(background, job_dir)]

    audio_inputs: list = []
    for clip in timeline.audio:
        source = job_dir / clip.source
        if not source.exists():
            continue
        # -ss before -i cuts at the source; the timeline offset is applied
        # afterwards with adelay
        cmd += ["-ss", f"{clip.in_point:.3f}", "-t", f"{clip.duration:.3f}",
                "-i", clip.source]
        audio_inputs.append(clip)

    # Media overlays come before the caption PNGs so captions always end up on
    # top — a picture-in-picture covering the subtitle would be a regression.
    media_inputs = [m for m in timeline.media if (job_dir / m.source).exists()]
    media_start = 1 + len(audio_inputs)
    for item in media_inputs:
        if item.kind == "video":
            cmd += ["-ss", f"{item.in_point:.3f}", "-t", f"{item.duration:.3f}",
                    "-i", item.source]
        else:
            # a still has no duration of its own; loop it for as long as it shows
            cmd += ["-loop", "1", "-t", f"{item.duration:.3f}", "-i", item.source]

    image_start = media_start + len(media_inputs)
    for overlay in overlays:
        cmd += ["-i", os.path.relpath(overlay.path, job_dir)]

    steps: list[str] = []
    current = "0:v"

    for index, item in enumerate(media_inputs):
        src = f"{media_start + index}:v"
        scaled, label = f"pip{index}", f"mv{index}"
        target_w = max(int(round(item.width * timeline.fmt.width)) // 2 * 2, 2)
        # Only the width is set: -2 keeps the source's aspect ratio, so nothing
        # gets stretched no matter what the user drops in.
        chain = f"[{src}]scale={target_w}:-2"
        if item.opacity < 1.0:
            # colorchannelmixer needs an alpha channel to write into
            chain += f",format=rgba,colorchannelmixer=aa={item.opacity:.3f}"
        # The cut input's frames start at its own zero, while `enable` below is
        # gated on the *timeline* clock. Without this pad the input has already
        # run out by the time its window opens and nothing is drawn — the
        # overlay silently never appears. tpad pushes it to its slot; the black
        # it pads with is never drawn, because `enable` is false there.
        if item.start > 0:
            chain += f",tpad=start_duration={item.start:.3f}"
        steps.append(f"{chain}[{scaled}]")
        # x/y are the overlay's centre as a fraction of the frame; ffmpeg wants
        # the top-left corner, and only knows the scaled size at filter time
        steps.append(
            f"[{current}][{scaled}]overlay="
            f"x='{item.x:.4f}*W-w/2':y='{item.y:.4f}*H-h/2'"
            f":enable='between(t\\,{item.start:.3f}\\,{item.end:.3f})'"
            f":eof_action=pass[{label}]")
        current = label

    for index, overlay in enumerate(overlays):
        label = f"ov{index}"
        steps.append(
            f"[{current}][{image_start + index}:v]overlay="
            f"{overlay.x}:{overlay.y}:enable='between(t\\,{overlay.start:.3f}"
            f"\\,{overlay.end:.3f})':eof_action=repeat[{label}]")
        current = label
    steps.append(f"[{current}]format=yuv420p[v]")

    if audio_inputs:
        labels = []
        for index, clip in enumerate(audio_inputs, start=1):
            label = f"a{index}"
            delay = int(clip.start * 1000)
            steps.append(
                f"[{index}:a]volume={clip.gain:.3f},"
                f"adelay={delay}|{delay},aresample=48000[{label}]")
            labels.append(f"[{label}]")
        joined = "".join(labels)
        steps.append(
            f"{joined}amix=inputs={len(labels)}:duration=longest:"
            f"dropout_transition=0,loudnorm=I=-14:TP=-1.5:LRA=11,"
            f"aresample=48000[a]")
    else:
        cmd += ["-f", "lavfi", "-t", f"{timeline.duration:.3f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        steps.append(f"[{image_start + len(overlays)}:a]anull[a]")

    cmd += [
        "-filter_complex", ";".join(steps),
        "-map", "[v]", "-map", "[a]",
        "-t", f"{timeline.duration:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-g", str(FPS * 2),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", out.name,
    ]
    render._run(cmd, cwd=job_dir)
    return out
