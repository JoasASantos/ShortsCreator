"""Cutting a livestream (3–8h) into many shorts.

The long-video clipper sends one transcript to the LLM and truncates it at
`clipper.TRANSCRIPT_CHAR_LIMIT` characters. That is fine for a 20min–2h upload
and useless for a live: six hours of transcript is several times the cap, so
every cut would come out of the first hour. Two more things break at that
length — a live is largely dead air (waiting, silence, "let me get some
water"), and whoever cuts a live wants 10–30 shorts, not 3.

So this module:

  * splits the live into ~40min windows and runs `clipper.pick_clips` once per
    window with that window's slice of the transcript, so the cuts land across
    the whole stream instead of piling up at the start;
  * asks ffmpeg's `silencedetect` where the long dead air is and drops any
    candidate whose stretch is mostly silence;
  * applies `clipper.overlaps` to the whole set, because two neighbouring
    windows can pick the same moment at the boundary they share and
    `pick_clips` can only see one window at a time.

Everything else is the flow that already exists: the plan is a row in
`clip_plans` (marked `options_json.mode = "livestream"`), the analysis runs on
the clip queue, and rendering goes through `POST /api/clips/{id}/render` ->
`clipper_jobs.spawn_jobs`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import db
from ..config import settings
from ..routers import uploads as uploads_router
from . import clipper, highlights, ingest

# Marks a `clip_plans` row as a livestream plan, so the clip queue knows to run
# the windowed analysis instead of the plain long-video one.
MODE = "livestream"

# One prompt per 40 minutes of live. Long enough that a window still holds a
# whole conversation (a moment needs its build-up to be picked well), short
# enough that its transcript fits in a prompt with room to spare.
WINDOW_SECONDS = 40 * 60
# A window is never split below this: the halving in `_fit_to_prompt` has to
# terminate, and a 2-minute window has no context left to judge a moment by.
MIN_WINDOW_SECONDS = 120.0

# Silence thresholds. `tts.trim_leading_silence` uses -45dB, which is right for
# a clean TTS file; a live carries room tone, fans, keyboards and a music bed,
# all of which sit above -45dB, so nothing would ever be detected. -35dB reads
# "nobody is talking" on real stream audio. It errs towards calling quiet
# speech silence, which is why the decision needs the ratio below as well.
SILENCE_NOISE_DB = "-35dB"
# Only blocks longer than this count. Pauses between sentences are under a
# second; four seconds of nothing is someone reading chat or fetching water.
SILENCE_MIN_SECONDS = 4.0
# A candidate is dropped when this much of its stretch is dead air. It has to
# be a majority (not, say, 25%) precisely because -35dB is a loose threshold:
# two loose criteria that both have to fire is safer than one strict one, and
# the cost of a wrong drop is a lost cut, in a live that has plenty more.
DEAD_AIR_RATIO = 0.6

# A start that falls outside its own window did not come from the text we sent,
# so it is a made-up timestamp. The tolerance absorbs rounding in the [MM:SS]
# stamps the model reads.
BOUNDARY_TOLERANCE = 5.0


@dataclass
class Window:
    """A stretch of the live analysed by one LLM call, and how many cuts it owes."""
    start: float
    end: float
    count: int


# ----------------------------- windowing -----------------------------

def plan_windows(total_duration: float, count: int, segments=(),
                 window_seconds: float = WINDOW_SECONDS) -> list[Window]:
    """Splits the live into windows that together cover it end to end."""
    total = max(float(total_duration or 0.0), 0.0)
    count = max(int(count), 1)
    if total <= 0:
        return [Window(0.0, 0.0, count)]

    # Round to the nearest whole number of windows so the last one is never a
    # sliver, and never make more windows than cuts asked for — a window is one
    # LLM call, and calling it to pick zero clips is a wasted minute.
    parts = min(max(int(total / max(window_seconds, MIN_WINDOW_SECONDS) + 0.5), 1), count)
    bounds = [(total * i / parts, total * (i + 1) / parts) for i in range(parts)]
    if segments:
        bounds = _fit_to_prompt(bounds, segments)
    return _allocate(bounds, count)


def _fit_to_prompt(bounds: list[tuple[float, float]],
                   segments) -> list[tuple[float, float]]:
    """Halves any window whose transcript would be truncated by `pick_clips`.

    Windows are measured in seconds but the prompt cap is in characters, and a
    fast talker fits far more transcript into 40 minutes than a slow one.
    Without this check the truncation this module exists to avoid would come
    back one window at a time — quietly, because nothing reports it.
    """
    pending = list(bounds)
    out: list[tuple[float, float]] = []
    while pending:
        start, end = pending.pop(0)
        too_long = len(window_transcript(segments, start, end)) > clipper.TRANSCRIPT_CHAR_LIMIT
        if too_long and (end - start) > MIN_WINDOW_SECONDS * 2:
            middle = (start + end) / 2
            pending[:0] = [(start, middle), (middle, end)]
            continue
        out.append((start, end))
    return out


def _allocate(bounds: list[tuple[float, float]], count: int) -> list[Window]:
    """Shares `count` cuts among the windows, in proportion to their length.

    Cumulative rounding: each window gets the running share up to its end minus
    the running share up to its start. That sums to exactly `count`, and when
    there are more windows than cuts the empty ones fall evenly across the live
    — largest-remainder, with windows of equal length and therefore equal
    remainders, hands every cut to the first few windows, which is the same
    "everything comes from the first hour" bug in a new place.
    """
    total = sum(end - start for start, end in bounds) or 1.0
    windows: list[Window] = []
    elapsed, given = 0.0, 0
    for start, end in bounds:
        elapsed += end - start
        cumulative = round(count * elapsed / total)
        windows.append(Window(start, end, cumulative - given))
        given = cumulative
    return windows


def window_transcript(segments, start: float, end: float) -> str:
    """The window's own slice of the transcript.

    The timestamps stay absolute rather than window-relative, so what the model
    returns can go straight into the clip list. A segment that straddles the
    boundary shows up in both windows on purpose: each side needs the context,
    and a moment picked twice is caught by the overlap check.
    """
    inside = [
        seg for seg in segments
        if float(seg.get("end", seg.get("start", 0.0))) > start
        and float(seg.get("start", 0.0)) < end
    ]
    return clipper.transcript_with_timestamps(inside)


# ----------------------------- dead air -----------------------------

def detect_silences(video: Path, noise_db: str = SILENCE_NOISE_DB,
                    min_seconds: float = SILENCE_MIN_SECONDS,
                    total_duration: float | None = None) -> list[tuple[float, float]]:
    """Blocks of silence longer than `min_seconds`, as (start, end) seconds.

    Silence is a hint, not a requirement: if ffmpeg is missing or the decode
    fails we return nothing and simply do not filter dead air, rather than
    failing an analysis that took hours to get here.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video),
             "-af", f"silencedetect=noise={noise_db}:d={min_seconds}",
             "-f", "null", "-"],
            capture_output=True, text=True,
        )
    except OSError:
        return []

    starts = [float(v) for v in re.findall(r"silence_start:\s*(-?\d+\.?\d*)", proc.stderr)]
    ends = [float(v) for v in re.findall(r"silence_end:\s*(-?\d+\.?\d*)", proc.stderr)]

    blocks: list[tuple[float, float]] = []
    for index, start in enumerate(starts):
        if index < len(ends):
            end = ends[index]
        elif total_duration:
            # a live that ends in silence gets a silence_start with no matching
            # end — the block runs to the end of the file
            end = total_duration
        else:
            continue
        if end - start >= min_seconds:
            blocks.append((max(start, 0.0), end))
    return blocks


def dead_air_ratio(start: float, end: float,
                   silences: list[tuple[float, float]]) -> float:
    """How much of [start, end) is covered by silence, from 0.0 to 1.0."""
    span = end - start
    if span <= 0:
        return 0.0
    quiet = sum(max(0.0, min(end, s_end) - max(start, s_start))
                for s_start, s_end in silences)
    return min(quiet / span, 1.0)


# ----------------------------- picking -----------------------------

def collect_clips(segments, total_duration: float, count: int, target_seconds: int,
                  silences: list[tuple[float, float]] | None = None,
                  window_seconds: float = WINDOW_SECONDS,
                  log=lambda m: None) -> list[dict]:
    """Runs `pick_clips` window by window and merges the results into one list.

    A short live ends up as a single window, which is the plain long-video flow
    with one extra dead-air filter on top.
    """
    silences = silences or []
    windows = plan_windows(total_duration, count, segments, window_seconds)
    log(f"live of {total_duration:.0f}s in {len(windows)} window(s) "
        f"for {count} cut(s)")

    accepted: list[dict] = []
    failures = 0

    for index, window in enumerate(windows, start=1):
        if window.count <= 0:
            continue  # more windows than cuts asked for; this one drew a blank
        transcript = window_transcript(segments, window.start, window.end)
        if not transcript.strip():
            log(f"window {index}: nobody speaks here, skipped")
            continue

        try:
            # total_duration is the whole live, not the window: `pick_clips`
            # clamps the end against it, and clamping to the window end would
            # cut off a moment that happens to straddle the boundary.
            candidates = clipper.pick_clips(transcript, total_duration,
                                            window.count, target_seconds, log)
        except Exception as exc:  # noqa: BLE001 — one window is not the live
            failures += 1
            log(f"window {index} failed and was skipped: {exc}")
            continue

        for clip in candidates:
            if not _inside_window(clip, window, target_seconds):
                log(f"window {index}: '{clip['titulo']}' is outside the window, dropped")
                continue
            if dead_air_ratio(clip["inicio"], clip["fim"], silences) >= DEAD_AIR_RATIO:
                log(f"window {index}: '{clip['titulo']}' is mostly silence, dropped")
                continue
            if clipper.overlaps(clip["inicio"], clip["fim"], accepted):
                log(f"window {index}: '{clip['titulo']}' repeats a cut already taken")
                continue
            accepted.append(clip)

    if not accepted and failures:
        raise RuntimeError(
            f"None of the {len(windows)} windows of the live could be analysed."
        )

    accepted.sort(key=lambda clip: clip["inicio"])
    log(f"{len(accepted)} cut(s) spread over the live")
    return accepted[:count]


def _inside_window(clip: dict, window: Window, target_seconds: int) -> bool:
    """The window's transcript only holds that window's lines, so a start
    outside it is a timestamp the model invented. The END may run past the
    boundary: the boundary is arbitrary and a good moment can straddle it."""
    return (window.start - BOUNDARY_TOLERANCE <= clip["inicio"]
            < window.end + target_seconds)


# ----------------------------- plan analysis -----------------------------

def analyze_plan(plan_id: str) -> None:
    """Turns a livestream plan into a list of cuts, ready to render."""
    row = db.get_clip_plan(plan_id)
    if row is None:
        raise RuntimeError(f"Plan {plan_id} does not exist")
    options = json.loads(row["options_json"] or "{}")

    db.update_clip_plan(plan_id, status="analisando")
    source = _source_video(plan_id, row, options)
    total = highlights.probe_duration(source)

    segments = ingest.whisper_segments(source)
    if not segments:
        raise RuntimeError(
            "Could not transcribe the live. Install faster-whisper "
            "(pip install faster-whisper) to cut a livestream."
        )

    clips = collect_clips(
        segments, total, row["requested"], row["target_seconds"],
        silences=detect_silences(source, total_duration=total),
        window_seconds=float(options.get("window_seconds") or WINDOW_SECONDS),
    )
    if not clips:
        raise RuntimeError("The model found no usable stretches in this live.")

    db.update_clip_plan(plan_id, status="ready", clips_json=json.dumps(clips))


def _source_video(plan_id: str, row: dict, options: dict) -> Path:
    """The live as a local file.

    A URL is downloaded once and then registered as an ordinary upload, so that
    everything downstream — `spawn_jobs`, the render route, the outputs — keeps
    working off `attachment_id` without knowing a live was involved.
    """
    if row["attachment_id"]:
        return uploads_router.resolve(row["attachment_id"])

    url = (options.get("url") or "").strip()
    if not url:
        raise RuntimeError("This plan has neither an attachment nor a URL.")

    work = settings.cache_dir / "livecuts" / plan_id
    work.mkdir(parents=True, exist_ok=True)
    video, _info = ingest.download_video(url, work)
    if video is None:
        raise RuntimeError(f"yt-dlp brought back no video file from {url}")

    upload_id = db.new_id("upl")
    dest = settings.uploads_dir / f"{upload_id}{video.suffix}"
    shutil.move(str(video), dest)
    db.update_clip_plan(plan_id, attachment_id=upload_id)
    return dest
