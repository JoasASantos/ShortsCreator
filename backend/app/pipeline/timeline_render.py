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

# A crossfade between two shots, and the shortest still that gets to move.
#
# Both exist because a documentary made of stills and archive cuts reads as a
# slideshow without them: 27 of the 37 shots in a seven-minute mini-doc are
# stills, and a still that does not move is a photograph on screen.
TRANSITION = 0.4
MOTION_MIN_SECONDS = 1.5


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


def _crossfades(timeline: Timeline) -> set[int]:
    """Which clip indexes dissolve into the next one.

    Only between clips that carry no speech. `mute` already says that: an
    interview cut is unmuted because its own audio is on the timeline, and
    dissolving over someone's sentence smears the words. So archive footage
    and stills flow into each other, quotes cut hard, and the film stops
    reading as a slideshow without anyone's voice being mangled.

    A clip shorter than two transitions is left alone — a 0.4 s dissolve on a
    0.5 s shot is the whole shot.
    """
    out: set[int] = set()
    clips = timeline.video
    for index, clip in enumerate(clips[:-1]):
        nxt = clips[index + 1]
        # Adjacent on the timeline: a real hole between them becomes black, and
        # dissolving into black is not a transition, it is a fade-out.
        if abs((clip.start + clip.duration) - nxt.start) > 0.04:
            continue
        if not (clip.mute and nxt.mute):
            continue
        if min(clip.duration, nxt.duration) < TRANSITION * 2:
            continue
        out.add(index)
    return out


def _ken_burns(index: int, length: float, fmt) -> str:
    """A slow push or pull across a still.

    zoompan is fed a frame larger than the output so the crop has room to move
    without softening: at 1.15x zoom on a 1920-wide render, the pixels come
    from a 2880-wide scale rather than being stretched up from 1920.

    Direction alternates so consecutive stills do not perform the same move.
    """
    frames = max(int(round(length * FPS)) + 1, 2)
    big_w, big_h = fmt.width * 3 // 2, fmt.height * 3 // 2
    step = 0.15 / max(frames, 1)
    if index % 2 == 0:
        zoom = f"min(zoom+{step:.6f},1.15)"          # push in
    else:
        zoom = f"if(lte(zoom,1.0),1.15,max(1.001,zoom-{step:.6f}))"   # pull out
    return (
        f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,"
        f"crop={big_w}:{big_h},"
        f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={fmt.width}x{fmt.height}:fps={FPS},"
        f"scale=in_range=full:out_range=tv,setsar=1,format=yuv420p"
    )


def _build_video_track(job_dir: Path, timeline: Timeline, log) -> Path:
    """Cut and position each clip; gaps between clips become black.

    Clips that dissolve into the next are rendered TRANSITION longer than
    their slot, because xfade eats that much: the extra is exactly what the
    dissolve consumes, so the assembled track still lands on the timeline's
    own clock and the subtitles and audio stay in sync.
    """
    fmt = timeline.fmt
    W, H = fmt.width, fmt.height   # noqa: N806 — the frame is the timeline's, not the module's
    work = job_dir / "tl"
    work.mkdir(exist_ok=True)
    fading = _crossfades(timeline)
    # (path, rendered length, dissolves into the next part)
    parts: list[tuple[Path, float, bool]] = []
    cursor = 0.0

    for index, clip in enumerate(timeline.video):
        if clip.start > cursor + 0.04:
            gap = clip.start - cursor
            parts.append((_black(work, index, gap, fmt), gap, False))
            cursor += gap

        source = (job_dir / clip.source).resolve()
        if not source.exists():
            raise RuntimeError(f"Clip file not found: {clip.source}")

        dest = work / f"part_{index:03d}.mp4"
        length = max(clip.duration, 0.1)
        dissolves = index in fading
        rendered = length + TRANSITION if dissolves else length
        if clip.kind == "image":
            if length >= MOTION_MIN_SECONDS:
                still = _ken_burns(index, rendered, fmt)
            else:
                still = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                         f"crop={W}:{H},scale=in_range=full:out_range=tv,"
                         f"setsar=1,fps={FPS},format=yuv420p")
            cmd = [
                "ffmpeg", "-y", "-loop", "1", "-i", str(source),
                "-t", f"{rendered:.3f}", "-vf", still,
                "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "21", "-pix_fmt", "yuv420p", "-color_range", "tv", str(dest)]
        else:
            # -stream_loop covers the case where the user stretched the clip
            # beyond the material available in the source file
            cmd = [
                "ffmpeg", "-y", "-stream_loop", "-1",
                "-ss", f"{clip.in_point:.3f}", "-i", str(source),
                "-t", f"{rendered:.3f}", "-an", "-vf", render.fit_filter(fmt),
                "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "21", "-pix_fmt", "yuv420p", str(dest)]
        _render_part(cmd, dest, rendered, log)
        parts.append((dest, rendered, dissolves))
        cursor += length

    if cursor < timeline.duration - 0.04:
        gap = timeline.duration - cursor
        parts.append((_black(work, len(parts) + 900, gap, fmt), gap, False))

    if not parts:
        raise RuntimeError("No video clip on the timeline.")

    if any(dissolves for _, _, dissolves in parts):
        log(f"{sum(1 for _, _, d in parts if d)} dissolve(s) of {TRANSITION}s "
            f"between shots that carry no speech")
        return _join_with_dissolves(work, parts, timeline.duration)

    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p, _, _ in parts),
                       encoding="utf-8")
    background = work / "video_track.mp4"
    render._run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing.name,
                 "-c", "copy", background.name], cwd=work)
    return background


def _render_part(cmd: list[str], dest: Path, expected: float, log) -> Path:
    """Render one part and prove it can be read back.

    An MP4's moov atom is written last, so a write that was interrupted leaves
    a file that exists, has a plausible size, and cannot be opened. ffmpeg then
    fails at the *join* — after every other part was rendered — with "moov atom
    not found" naming one file out of forty. That cost two renders of the same
    seven-minute film, so the part is verified where it is written, and one bad
    part costs one part instead of the whole film.
    """
    for attempt in (1, 2):
        render._run(cmd)  # noqa: SLF001 — the project's one ffmpeg runner
        if dest.exists() and abs((render.probe_duration(dest) or 0.0) - expected) < 0.5:
            return dest
        if attempt == 1:
            log(f"{dest.name} came out unreadable or the wrong length; "
                f"rendering it again", "warn")
    raise RuntimeError(
        f"{dest.name} was written twice and cannot be read back (expected "
        f"{expected:.1f}s of video). Something is interrupting the render.")


def _join_with_dissolves(work: Path, parts: list[tuple[Path, float, bool]],
                         duration: float) -> Path:
    """One filter pass: xfade where a shot dissolves, concat where it cuts.

    The chain is walked left to right carrying its own accumulated length,
    because xfade's `offset` is measured on the chain built so far and not on
    the part being added. Getting that wrong does not fail — it silently
    slides every later shot, which is the kind of bug that only shows up as
    subtitles drifting out of sync near the end.
    """
    background = work / "video_track.mp4"
    cmd: list[str] = ["ffmpeg", "-y"]
    for path, _, _ in parts:
        cmd += ["-i", path.name]

    # `settb=AVTB` on every input and after every step, because concat and
    # xfade disagree about time: concat hands on 1/1000000 and xfade hands on
    # 1/15360, so the first xfade after a concat dies with "First input link
    # main timebase do not match the corresponding second input link". It is a
    # hard failure at the end of a seven-minute render, and it only appears
    # once a chain mixes the two.
    steps = [f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS[v{index}]"
             for index in range(len(parts))]
    current = "v0"
    accumulated = parts[0][1]
    for index in range(1, len(parts)):
        label = f"j{index}"
        if parts[index - 1][2]:
            offset = max(accumulated - TRANSITION, 0.0)
            steps.append(f"[{current}][v{index}]xfade=transition=fade"
                         f":duration={TRANSITION:.3f}:offset={offset:.3f},"
                         f"settb=AVTB[{label}]")
            accumulated = offset + parts[index][1]
        else:
            steps.append(f"[{current}][v{index}]concat=n=2:v=1:a=0,"
                         f"settb=AVTB[{label}]")
            accumulated += parts[index][1]
        current = label

    cmd += ["-filter_complex", ";".join(steps), "-map", f"[{current}]",
            "-t", f"{duration:.3f}", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", background.name]
    render._run(cmd, cwd=work)
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
