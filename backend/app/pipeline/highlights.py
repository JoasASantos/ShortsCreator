"""Extracting highlight excerpts from a long video (episode, trailer, VOD).

Uses FFmpeg's native scene-cut detection (`select='gt(scene,X)'`) — it depends
on neither libass nor any external library. The idea: instead of narrating over
a 40-minute video from start to finish, we pick N windows spread across the
timeline (one per script segment), each anchored to the nearest scene cut so it
doesn't open or close in the middle of an action.
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
    """Timestamps (seconds) where the picture changes abruptly from one frame to the next."""
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
    """Pick `count` windows of `window_len`s spread across the whole video.

    Each anchor is pulled to the nearest scene cut (if there is one less than 3s
    away), otherwise it stays at the evenly spaced point — a hard cut is better
    than losing coverage of a part of the video.
    """
    usable_start = min(skip_start, total_duration * 0.05)
    usable_end = max(total_duration - skip_end, usable_start + window_len)
    span = max(usable_end - usable_start - window_len, 0.1)

    anchors: list[float] = []
    for i in range(count):
        # centers spread evenly, not right at the extremes
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
            # not enough room left (short video / many segments); compress it
            start = max(usable_start, end - window_len * 0.4)
        windows.append((round(start, 2), round(end, 2)))
        last_end = end
    return windows


def highlight_windows_for_script(video: Path, segment_count: int,
                                 segment_durations: list[float],
                                 log=lambda m: None) -> list[tuple[float, float]]:
    """Entry point used by the orchestrator: one window per body segment."""
    total = probe_duration(video)
    if total <= 0:
        raise RuntimeError("Could not measure the duration of the source video.")

    avg_window = sum(segment_durations) / len(segment_durations) if segment_durations else 4.0
    log(f"Detecting scene cuts across {total:.0f}s of video…")
    cuts = detect_scene_cuts(video)
    log(f"{len(cuts)} scene cut(s) detected; assembling {segment_count} highlight(s)")

    windows = pick_windows(total, segment_count, avg_window, scene_cuts=cuts)
    return windows


def windows_across_sources(sources: list[Path], segment_count: int,
                           segment_durations: list[float],
                           log=lambda m: None) -> list[tuple[Path, float, float]]:
    """Distribute the excerpts across SEVERAL source videos.

    Each video gets a share proportional to its duration — a 2-minute video
    alongside a 30-second one cannot hand over the same number of excerpts.
    Within each video the points are still anchored to the scene cuts.
    """
    if len(sources) == 1:
        windows = highlight_windows_for_script(
            sources[0], segment_count, segment_durations, log)
        return [(sources[0], start, end) for start, end in windows]

    durations = [probe_duration(src) for src in sources]
    total = sum(durations) or 1.0
    log(f"Distributing {segment_count} excerpt(s) across {len(sources)} videos")

    # how many excerpts each video hands over, proportional to its duration
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
        log(f"  {source.name}: {quota} excerpt(s) out of {duration:.0f}s")

    return out[:segment_count]
