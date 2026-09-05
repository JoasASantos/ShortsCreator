"""Final 9:16 video composition through ffmpeg.

Pipeline: background (1080x1920) -> ASS subtitles -> audio mix -> H.264 faststart.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from ..config import settings
from ..schemas import JobInput
from . import broll

W, H, FPS = settings.width, settings.height, settings.fps

_FILTERS: set[str] | None = None

# Fills 9:16 without bars: blurred background + original video centered.
FIT_919 = (
    f"split=2[a][b];"
    f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
    f"gblur=sigma=28,eq=brightness=-0.06[bg];"
    f"[b]scale={W}:-2:force_original_aspect_ratio=decrease,"
    f"scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
    f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS},format=yuv420p"
)

# Zooms the footage until it covers 9:16 and cuts the sides. Nothing is left
# over to fill, so no bars are possible — at the cost of whatever falls outside
# the frame. This is the treatment for footage that is very wide, where the
# blurred fit leaves more blur than picture.
FILL_919 = (
    f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
    f"setsar=1,fps={FPS},format=yuv420p"
)


def content_crop(source: Path, seconds: float = 12.0) -> str:
    """The `crop=` filter that strips a source's own baked-in black bars.

    A cinema trailer is 2.39:1 delivered inside a 16:9 file: the bars are part
    of the picture, not of the container. Fitting that into 9:16 carries them
    along, and the result is correctly flagged as letterboxed — the bars really
    are there. Every downstream framing choice is wrong until they are gone, so
    this runs first and adapts to whatever the source turns out to be:
    letterboxed cinema, pillarboxed 4:3, or already-clean footage.

    Returns "" when there is nothing to strip, which is the common case and
    must cost nothing.
    """
    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-t", f"{seconds:.1f}",
         "-i", str(source), "-vf", "cropdetect=limit=24:round=2:reset=0",
         "-f", "null", "-"],
        capture_output=True, text=True)
    matches = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", probe.stderr)
    if not matches:
        return ""

    # The last reading is cropdetect's accumulated verdict over the sample.
    width, height, x, y = (int(v) for v in matches[-1])
    size = _dimensions(source)
    if size is None or width <= 0 or height <= 0:
        return ""
    full_w, full_h = size

    # Ignore a crop that shaves only a few pixels: that is compression noise at
    # the edge, and cropping on it would make the frame drift between renders.
    trimmed = (full_w - width) + (full_h - height)
    if trimmed < 0.04 * (full_w + full_h):
        return ""
    # A reading that would throw away most of the picture is cropdetect losing
    # its footing on a dark or fading shot, not a bar.
    if width * height < 0.25 * full_w * full_h:
        return ""
    return f"crop={width}:{height}:{x}:{y},"


def _dimensions(video: Path) -> tuple[int, int] | None:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=s=x:p=0", str(video)],
        capture_output=True, text=True)
    try:
        width, height = out.stdout.strip().split("x")
        return int(width), int(height)
    except ValueError:
        return None


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-12:])
        raise RuntimeError(f"ffmpeg failed: {tail}")


def ensure_ffmpeg() -> None:
    for binary in ("ffmpeg", "ffprobe"):
        if not shutil.which(binary):
            raise RuntimeError(f"{binary} not found on PATH. Install FFmpeg.")


# ------------------------- backgrounds -------------------------

def background_gradient(duration: float, niche: str, out: Path,
                        scroll: str = "nenhum") -> Path:
    c0, c1 = broll.palette(niche)
    src = (f"gradients=s={W}x{H}:c0={c0}:c1={c1}:x0=0:y0=0:x1={W}:y1={H}"
           f":d={duration:.2f}:speed=0.012:rate={FPS}")
    vf = "noise=alls=6:allf=t+u,format=yuv420p"
    if scroll == "pan":
        vf = f"scale={int(W*1.25)}:-2,crop={W}:{H}:'(iw-{W})*t/{duration:.2f}':0," + vf
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-t", f"{duration:.2f}",
          "-vf", vf, "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
          "-crf", "22", "-pix_fmt", "yuv420p", str(out)])
    return out


def background_from_video(source: Path, duration: float, out: Path,
                          scroll: str = "nenhum", start: float = 0.0,
                          fill: str = "desfoque", log=lambda m: None) -> Path:
    """Frame a source video as a 9:16 background.

    `fill` decides what happens to the space a landscape frame does not cover:
    "desfoque" keeps the whole picture over a blurred copy of itself, and
    "preencher" zooms until the frame is covered and cuts the sides. The
    source's own baked-in bars are stripped first either way — carrying them
    into the composition is what makes a correctly framed short look
    letterboxed.
    """
    debar = content_crop(source)
    if debar:
        log(f"Source has baked-in bars; cropping to {debar.rstrip(',')[5:]}")

    if scroll == "pan":
        vf = (f"{debar}scale={W}:{int(H*1.2)}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H}:0:'(ih-{H})*t/{duration:.2f}',setsar=1,"
              f"fps={FPS},format=yuv420p")
    elif fill == "preencher":
        vf = debar + FILL_919
    else:
        # FIT_919 opens with split, which takes no input prefix — the crop has
        # to run before the graph forks, not inside one of its branches.
        vf = debar + FIT_919

    _run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(source),
          "-t", f"{duration:.2f}", "-an", "-vf", vf, "-r", str(FPS),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
          "-pix_fmt", "yuv420p", str(out)])
    return out


def background_from_clips(clips: list[Path], duration: float, out: Path,
                          work_dir: Path) -> Path:
    """Normalize each clip to 9:16 and concatenate until the duration is covered."""
    per_clip = max(duration / max(len(clips), 1), 2.0)
    normalized: list[Path] = []
    for index, clip in enumerate(clips):
        dest = work_dir / f"bgpart_{index}.mp4"
        _run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(clip),
              "-t", f"{per_clip:.2f}", "-an", "-vf", FIT_919, "-r", str(FPS),
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
              "-pix_fmt", "yuv420p", str(dest)])
        normalized.append(dest)

    listing = work_dir / "bgparts.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in normalized), encoding="utf-8")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing.name,
          "-t", f"{duration:.2f}", "-c", "copy", out.name], cwd=work_dir)
    return out


def background_from_multi_highlights(
        windows: list[tuple[Path, float, float]], out: Path, work_dir: Path,
        durations: list[float] | None = None) -> Path:
    """Like background_from_highlights, but each excerpt may come from a different
    file — this is the path taken when the user uploads more than one video."""
    parts: list[Path] = []
    for index, (source, start, end) in enumerate(windows):
        dest = work_dir / f"hl_{index}.mp4"
        target = (durations[index] if durations and index < len(durations)
                  else max(end - start, 0.5))
        _run(["ffmpeg", "-y", "-stream_loop", "-1", "-ss", f"{start:.2f}",
              "-i", str(source), "-t", f"{target:.2f}", "-an", "-vf", FIT_919,
              "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
              "-crf", "21", "-pix_fmt", "yuv420p", str(dest)])
        parts.append(dest)

    listing = work_dir / "hlparts.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing.name,
          "-c", "copy", out.name], cwd=work_dir)
    return out


def background_from_highlights(source: Path, windows: list[tuple[float, float]],
                               out: Path, work_dir: Path,
                               durations: list[float] | None = None) -> Path:
    """Cut N windows out of a single long video (an episode) and concatenate them —
    used by 'recap' mode: each window comes from
    highlights.highlight_windows_for_script.

    `durations` defines how LONG each excerpt must last in the short. Since the
    source video may run out before the end of the window, each excerpt is
    looped until it fills the requested time — without that the background ends
    up shorter than the narration and the composition's `-shortest` truncates
    the tail of the audio.
    """
    parts: list[Path] = []
    for index, (start, end) in enumerate(windows):
        dest = work_dir / f"hl_{index}.mp4"
        target = (durations[index] if durations and index < len(durations)
                  else max(end - start, 0.5))
        _run(["ffmpeg", "-y", "-stream_loop", "-1", "-ss", f"{start:.2f}",
              "-i", str(source), "-t", f"{target:.2f}", "-an", "-vf", FIT_919,
              "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
              "-crf", "21", "-pix_fmt", "yuv420p", str(dest)])
        parts.append(dest)

    listing = work_dir / "hlparts.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing.name,
          "-c", "copy", out.name], cwd=work_dir)
    return out


# Alternating pan directions so the Ken Burns effect doesn't always repeat the same move.
_KENBURNS_PANS = [
    ("iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),   # center, zoom only
    ("0", "0"),                                  # top-left corner
    ("iw-iw/zoom", "ih-ih/zoom"),                # bottom-right corner
    ("iw/2-(iw/zoom/2)", "0"),                   # top, centered
]


def background_from_images_kenburns(images: list[Path], durations: list[float],
                                    out: Path, work_dir: Path) -> Path:
    """One still image per segment, with slow zoom/pan (the Ken Burns effect)."""
    canvas_w, canvas_h = int(W * 1.5), int(H * 1.5)
    parts: list[Path] = []
    for index, (image, duration) in enumerate(zip(images, durations)):
        dest = work_dir / f"kb_{index}.mp4"
        frames = max(int(round(FPS * duration)), 1)
        x_expr, y_expr = _KENBURNS_PANS[index % len(_KENBURNS_PANS)]
        # JPEG decodes as yuvj420p (full range). Without the explicit conversion
        # to TV range the encoder propagates yuvj420p, which QA rejects and some
        # mobile players render with blown-out colors.
        vf = (
            f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,"
            f"crop={canvas_w}:{canvas_h},"
            f"zoompan=z='min(zoom+0.0012,1.28)':d={frames}:x='{x_expr}':y='{y_expr}'"
            f":s={W}x{H}:fps={FPS},"
            f"scale=in_range=full:out_range=tv,format=yuv420p"
        )
        _run(["ffmpeg", "-y", "-loop", "1", "-i", str(image), "-t", f"{duration:.2f}",
              "-vf", vf, "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
              "-crf", "21", "-pix_fmt", "yuv420p", "-color_range", "tv", str(dest)])
        parts.append(dest)

    listing = work_dir / "kbparts.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing.name,
          "-c", "copy", out.name], cwd=work_dir)
    return out


def has_filter(name: str) -> bool:
    """Some FFmpeg builds (Homebrew's, for one) ship without libass/libfreetype."""
    global _FILTERS
    if _FILTERS is None:
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                              capture_output=True, text=True)
        _FILTERS = {line.split()[1] for line in proc.stdout.splitlines()
                    if len(line.split()) > 2 and line.startswith(" ")}
    return name in _FILTERS


def add_scroll_panel(background: Path, panel: Path, duration: float, out: Path,
                     speed: int = 95) -> Path:
    """Overlay a text panel scrolling from bottom to top (the 'scroll' effect)."""
    filt = (f"[0:v][1:v]overlay=x=80:y='H-mod(t*{speed}\\,H+h)':eval=frame"
            f":shortest=0[v]")
    _run(["ffmpeg", "-y", "-i", background.name, "-i", panel.name,
          "-filter_complex", filt, "-map", "[v]", "-t", f"{duration:.2f}",
          "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
          "-pix_fmt", "yuv420p", out.name], cwd=background.parent)
    return out


def pick_music(track_id: str = "") -> Path | None:
    """The track the user picked; with no pick, the first one in the folder."""
    from ..routers import music as music_router

    if track_id:
        chosen = music_router.resolve(track_id)
        if chosen is not None:
            return chosen

    folder = settings.assets_dir / "music"
    tracks = sorted(p for p in folder.glob("*")
                    if p.suffix.lower() in music_router.AUDIO_EXT)
    return tracks[0] if tracks else None


# ------------------------- final composition -------------------------

def compose(job_dir: Path, background: Path, narration: Path, out: Path,
            job: JobInput, duration: float, overlays: list = (),
            subtitles: Path | None = None) -> Path:
    """Burn in subtitles + mix audio + export an MP4 ready for upload.

    Uses the `ass` filter when FFmpeg has libass; otherwise it overlays the PNGs
    produced by the `overlays` module.
    """
    music = pick_music(job.music_track) if job.music else None

    cmd = ["ffmpeg", "-y", "-i", background.name, "-i", narration.name]
    music_index = None
    if music:
        music_local = job_dir / f"music{music.suffix}"
        if not music_local.exists() or music_local.stat().st_mtime < music.stat().st_mtime:
            shutil.copy(music, music_local)
        cmd += ["-stream_loop", "-1", "-i", music_local.name]
        music_index = 2

    use_ass = bool(subtitles) and has_filter("ass")
    image_start = (music_index + 1) if music_index is not None else 2

    if not use_ass:
        for overlay in overlays:
            cmd += ["-i", os.path.relpath(overlay.path, job_dir)]

    if use_ass:
        video_chain = f"[0:v]ass=filename={subtitles.name}[v]"
    elif overlays:
        steps = []
        current = "0:v"
        for i, overlay in enumerate(overlays):
            label = f"ov{i}"
            steps.append(
                f"[{current}][{image_start + i}:v]overlay="
                f"{overlay.x}:{overlay.y}:enable='between(t\\,{overlay.start:.3f}"
                f"\\,{overlay.end:.3f})':eof_action=repeat[{label}]"
            )
            current = label
        steps.append(f"[{current}]format=yuv420p[v]")
        video_chain = ";".join(steps)
    else:
        video_chain = "[0:v]format=yuv420p[v]"

    if music:
        audio_chain = (
            f"[{music_index}:a]volume={job.music_volume:.3f}[m];"
            f"[1:a]asplit=2[n][sc];"
            f"[m][sc]sidechaincompress=threshold=0.02:ratio=12:attack=15:release=350[md];"
            f"[n][md]amix=inputs=2:duration=first:dropout_transition=0,"
            f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]"
        )
    else:
        audio_chain = "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]"

    cmd += [
        "-filter_complex", f"{video_chain};{audio_chain}",
        "-map", "[v]", "-map", "[a]",
        "-t", f"{duration:.2f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-g", str(FPS * 2),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", "-shortest", out.name,
    ]
    _run(cmd, cwd=job_dir)
    return out


def probe_duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def ensure_min_duration(video: Path, target: float, work_dir: Path,
                        tolerance: float = 0.15) -> Path:
    """Safety net: if the background came out shorter than the narration, freeze
    the last frame until the time is filled. Without this the composition's
    `-shortest` truncates the end of the audio — the video stops before the last
    sentence has been spoken."""
    current = probe_duration(video)
    if current <= 0 or current >= target - tolerance:
        return video

    padded = work_dir / f"{video.stem}_padded.mp4"
    missing = target - current
    _run(["ffmpeg", "-y", "-i", str(video),
          "-vf", f"tpad=stop_mode=clone:stop_duration={missing:.2f},fps={FPS}",
          "-t", f"{target:.2f}", "-r", str(FPS), "-c:v", "libx264",
          "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
          str(padded)])
    return padded


def trim_video(source: Path, start: float, end: float, out: Path) -> Path:
    """Cut an excerpt out of the video, keeping the audio — used by the clipper to
    turn one long video into several files, one per clip."""
    duration = max(end - start, 0.5)
    _run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(source),
          "-t", f"{duration:.2f}", "-c:v", "libx264", "-preset", "veryfast",
          "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
          str(out)])
    return out


def make_thumbnail(video: Path, out: Path, at: float = 1.0) -> Path:
    _run(["ffmpeg", "-y", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1",
          "-vf", f"scale={W}:{H}", str(out)])
    return out


def make_preview_gif(video: Path, out: Path, seconds: float = 3.0,
                     width: int = 270, fps: int = 10) -> Path:
    """GIF of the first few seconds — the hook in motion in the dashboard list.
    Two-pass palette so gradients don't come out with banding."""
    filters = (f"fps={fps},scale={width}:-2:flags=lanczos,"
               f"split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];"
               f"[b][p]paletteuse=dither=bayer:bayer_scale=4")
    _run(["ffmpeg", "-y", "-t", f"{seconds:.2f}", "-i", str(video),
          "-vf", filters, "-loop", "0", str(out)])
    return out
