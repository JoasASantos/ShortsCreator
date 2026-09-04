"""A talking presenter reading your script — the HeyGen path.

What this adds is only the *wiring*: `generators/heygen.py` already knows how
to list the account's avatars and voices, ask for a video, poll it and download
the result. What was missing was somewhere for that clip to land.

It lands as an ordinary job. `edit_mode = "avatar"` routes the orchestrator
here instead of through script + TTS, and what this writes on disk is
deliberately the same set of artifacts a generated short writes (`short.mp4`,
`captions.ass`, `timeline.json`, `thumb.jpg`, `words` in the result) — the same
choice `pipeline.reels` made, and for the same reason: the timeline editor, QA,
the cover builder, publishing and metrics then work on an avatar video with
nothing special-cased. The framing helpers are reels' own, so an avatar clip
and a recording of your own are the same shape of thing downstream.

There is no local path for the avatar itself. Cloning a *voice* is free and
local (see voice_clone.py); making a photoreal person speak is not something
this project ships a model for, so when HeyGen is not configured the answer is
a refusal that says what to do — never a stack trace, and never a silent
substitution of something else.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..config import settings
from . import captions as captions_mod
from . import connectors, notify, reels, render, timeline as timeline_mod
from . import timeline_render, tts
from .generators import heygen
from .generators.registry import NOT_CONFIGURED, READY

# What `JobInput.edit_mode` carries for an avatar video. The orchestrator reads
# it to route here.
MODE = "avatar"

CONNECTOR = "heygen"

# The script is spoken by the avatar in one take, so a very long one is not a
# short — it is a video that will be cut up anyway. Refusing it up front beats
# discovering the limit after a paid render.
MAX_SCRIPT_CHARS = 2000
MIN_SCRIPT_CHARS = 10

SETUP = (
    "Register the HeyGen API key on the Accounts screen (or HEYGEN_API_KEY in "
    ".env). The key is in the HeyGen account under Settings > API.")

DOCS = "https://docs.heygen.com/reference/create-an-avatar-video-v2"


class AvatarNotConfigured(RuntimeError):
    """No provider can make a talking avatar. A normal state, not a bug — the
    message says what to configure, mirroring registry.GeneratorNotConfigured.
    """


# ------------------------------------------------------------ availability

def is_configured() -> bool:
    return connectors.is_configured(CONNECTOR)


def credentials() -> dict:
    """The HeyGen credential, or a refusal explaining what to do instead."""
    if not is_configured():
        raise AvatarNotConfigured(
            "HeyGen is not configured, so there is nothing that can make a "
            "talking avatar. " + SETUP + " Meanwhile, a short narrated in "
            "your own cloned voice needs no key at all — register the voice "
            "from a sample and use the ordinary short flow.")
    return connectors.credentials(CONNECTOR)


def describe(probe: bool = True) -> dict:
    """Honest state of the whole feature, for the screen to render.

    Same shape as the generators registry: a state, the reason it is in that
    state, and what having it working unlocks. `probe` is passed through to
    the voice paths — only a round trip proves a local server is up.
    """
    from . import voice_clone

    ready = is_configured()
    avatar_provider = {
        "id": CONNECTOR, "label": "HeyGen", "kind": "avatar_video",
        "hosting": "hosted", "cost": "paid",
        "speed": "1-5 min per video",
        "state": READY if ready else NOT_CONFIGURED,
        "reason": (f"credential in place ({connectors.source(CONNECTOR) or 'env'})"
                   if ready else SETUP),
        "setup": SETUP, "docs": DOCS, "connector": CONNECTOR, "base_url": "",
        "unlocks": ["a presenter reading your script to camera, delivered as "
                    "an ordinary short you can edit, audit and publish"],
    }

    voice_providers = voice_clone.describe_providers(probe=probe)
    return {
        "avatar": avatar_provider,
        "voice_clone": voice_providers,
        "providers": [avatar_provider, *voice_providers],
        "limits": {
            "min_sample_seconds": voice_clone.MIN_SECONDS,
            "recommended_sample_seconds": voice_clone.RECOMMENDED_SECONDS,
            "max_sample_seconds": voice_clone.MAX_SECONDS,
            "min_script_chars": MIN_SCRIPT_CHARS,
            "max_script_chars": MAX_SCRIPT_CHARS,
        },
    }


# ------------------------------------------------- the account's own catalog

def list_avatars() -> list[dict]:
    """The avatars on the account, trimmed to what the picker shows.

    Only the fields the UI uses are passed on, so a change in their payload
    cannot reach the browser as an unrecognised blob.
    """
    raw = heygen.list_avatars(credentials())
    return [
        {
            "id": item.get("avatar_id") or item.get("id") or "",
            "name": item.get("avatar_name") or item.get("name") or "(unnamed)",
            "gender": item.get("gender") or "",
            "preview_image": item.get("preview_image_url") or "",
            "preview_video": item.get("preview_video_url") or "",
        }
        for item in raw
        if item.get("avatar_id") or item.get("id")
    ]


def list_voices() -> list[dict]:
    raw = heygen.list_voices(credentials())
    return [
        {
            "id": item.get("voice_id") or item.get("id") or "",
            "name": item.get("name") or item.get("display_name") or "(unnamed)",
            "language": item.get("language") or "",
            "gender": item.get("gender") or "",
            # `preview_audio_url` is the documented name; the older payload
            # called it `preview_audio`.
            "preview_audio": (item.get("preview_audio_url")
                              or item.get("preview_audio") or ""),
        }
        for item in raw
        if item.get("voice_id") or item.get("id")
    ]


def check_script(text: str) -> str:
    script = (text or "").strip()
    if len(script) < MIN_SCRIPT_CHARS:
        raise ValueError(
            f"Write the script the avatar should read — at least "
            f"{MIN_SCRIPT_CHARS} characters.")
    if len(script) > MAX_SCRIPT_CHARS:
        raise ValueError(
            f"This script is {len(script)} characters and the limit here is "
            f"{MAX_SCRIPT_CHARS}. An avatar reads it in one take, so past "
            f"this it is no longer a short — split it into several videos.")
    return script


# ------------------------------------------------------------------ the run

def _words(source: Path, script_text: str, duration: float, log) -> list[dict]:
    """Word timings for the avatar's speech.

    Transcribing the rendered video is the accurate answer, and it is the same
    call a recording of your own makes. But the script is already known here,
    so when transcription is not installed the timings can be spread over the
    real duration instead of the feature simply failing — captions a shade
    imprecise beat no captions at all. The log says which one happened.
    """
    try:
        words, _language = reels.transcribe_words(source, log)
    except RuntimeError as exc:
        log(f"{exc} Falling back to timings estimated from the script — the "
            f"captions may drift by a fraction of a second.", "warn")
        return tts.estimate_words(script_text, duration)
    if words:
        return words
    log("The transcription found no speech in the avatar's video; using "
        "timings estimated from the script.", "warn")
    return tts.estimate_words(script_text, duration)


def prepare(job_dir: Path, source: Path, script_text: str, log) -> reels.Reel:
    """The avatar clip turned into something the editor can work on: framed
    9:16, audio on its own track, every word timed. Same artifact names a
    recording of your own uses, so nothing downstream tells them apart."""
    job_dir.mkdir(parents=True, exist_ok=True)

    duration = render.probe_duration(source)
    if duration <= 0:
        raise RuntimeError(
            "The video HeyGen returned has no readable duration. It may have "
            "downloaded incomplete — run the job again.")

    words = _words(source, script_text, duration, log)
    reels.extract_audio(source, job_dir / "reel_audio.mp3")
    render.background_from_video(source, duration, job_dir / "reel_919.mp4")
    log(f"Avatar video framed to 9:16: {duration:.1f}s, {len(words)} timed words")

    return reels.Reel(source=source, duration=duration, words=words)


def generate(job_dir: Path, script_text: str, avatar_id: str, voice_id: str,
             log) -> Path:
    """The clip itself. Skips the call when the file is already on disk, so a
    retry of a job that failed later does not pay for the render twice."""
    source = job_dir / "avatar_source.mp4"
    if source.exists() and source.stat().st_size > 0:
        log(f"Reusing the avatar video already downloaded ({source.name}) "
            f"instead of paying for it again.")
        return source

    job_dir.mkdir(parents=True, exist_ok=True)
    creds = credentials()
    if not avatar_id:
        raise AvatarNotConfigured(
            "No avatar was chosen. Pick one from the account's avatars.")
    if not voice_id:
        raise AvatarNotConfigured(
            "No voice was chosen. Pick one from the account's voices.")

    return heygen.generate_avatar_video(
        script_text, avatar_id, voice_id, source, creds, aspect="9:16",
        log=lambda message: log(message))


def run(job_id: str, job, job_dir: Path, log, stage) -> dict:
    """Generate the avatar video, then finish it like any other short.

    Deliberately short, exactly as reels.run is: anything the user might want
    to change afterwards belongs in the timeline editor, not in a second pass
    through here.
    """
    from .. import db
    from . import qa as qa_mod

    render.ensure_ffmpeg()

    stage("ingest")
    script_text = check_script(job.source)
    source = generate(job_dir, script_text, job.avatar_id, job.avatar_voice_id, log)

    title = ((db.get_job(job_id) or {}).get("title") or "").strip()
    if not title:
        # The first sentence of the script is a better name than "Avatar".
        title = script_text.split(".")[0][:80].strip() or "Avatar"

    stage("legendas")
    reel = prepare(job_dir, source, script_text, log)

    ass_path = captions_mod.build_ass(
        reel.words, job_dir / "captions.ass",
        style=job.caption_style, position=job.caption_position,
        title="", watermark=job.watermark,
        watermark_position=job.watermark_position,
        watermark_size=job.watermark_size,
        watermark_opacity=job.watermark_opacity)
    captions_mod.build_srt(reel.words, job_dir / "captions.srt")

    edl = reels.build_timeline(
        job_dir, reel,
        caption_style=job.caption_style, caption_position=job.caption_position,
        watermark=job.watermark, watermark_position=job.watermark_position,
        watermark_size=job.watermark_size, watermark_opacity=job.watermark_opacity,
        keep_audio=True)
    timeline_mod.save(job_dir, edl)

    stage("render")
    final = job_dir / "short.mp4"
    timeline_render.render_timeline(job_dir, edl, final, log=lambda m: log(m))
    render.make_thumbnail(final, job_dir / "thumb.jpg",
                          at=min(1.0, edl.duration / 4))

    stage("qa")
    report = qa_mod.audit(final, ass_path, expected_duration=reel.duration)
    log(f"QA: score {report.score}/100 — "
        f"{'PASSED' if report.passed else 'FAILED'}")

    shutil.copy(final, settings.outputs_dir / f"{job_id}.mp4")

    result = {
        "mode": MODE,
        "title": title,
        "description": "",
        "hashtags": [],
        "duration": round(edl.duration, 2),
        "video": f"/api/jobs/{job_id}/file/short.mp4",
        "thumbnail": f"/api/jobs/{job_id}/file/thumb.jpg",
        "captions_srt": f"/api/jobs/{job_id}/file/captions.srt",
        # Same key the editor and the caption builder read on every other job.
        "words": reel.words,
        "source_kind": "video",
        "edit_mode": job.edit_mode,
        "avatar": {"provider": CONNECTOR, "avatar_id": job.avatar_id,
                   "voice_id": job.avatar_voice_id},
    }
    (job_dir / "avatar.json").write_text(
        json.dumps({"script": script_text, "duration": reel.duration,
                    "avatar_id": job.avatar_id,
                    "voice_id": job.avatar_voice_id}, ensure_ascii=False),
        encoding="utf-8")

    db.update_job(job_id, status="done", stage="qa", progress=1.0, title=title,
                  result_json=json.dumps(result, ensure_ascii=False),
                  qa_json=report.model_dump_json())
    notify.job_done(job_id, title, edl.duration, report.score, report.passed)
    return result
