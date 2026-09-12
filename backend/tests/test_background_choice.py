"""Which background a job ends up with, and whether it says so.

The gradient looks the same whether it was chosen or merely settled for. These
tests are about the difference: a short must never come out looking finished
with none of the footage the user asked for and nothing explaining why.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.pipeline import orchestrator
from app.pipeline.ingest import SourceMaterial
from app.schemas import JobInput, ScriptSegment, ShortScript


def _script() -> ShortScript:
    return ShortScript(
        title="t", description="d", hashtags=[],
        segments=[ScriptSegment(kind="corpo", text="uma frase narrada")],
        estimated_seconds=20)


class _Narration:
    duration = 20.0
    words = [{"word": "uma", "start": 0.0, "end": 0.4}]


def _run(job: JobInput, material: SourceMaterial, tmp_path: Path,
         monkeypatch, images: object = False) -> tuple[str, list[tuple[str, str]]]:
    """Run the background stage with the renderers stubbed out, and report
    which one was called plus everything that was logged.

    `images` is what the image generators do: False for none configured (the
    default — a developer's own OPENAI_API_KEY must not decide these), True to
    draw, or a callable to fail a specific way.
    """
    called: list[str] = []
    events: list[tuple[str, str]] = []

    from app.pipeline import broll, imagegen, render, webcast

    # No browser by default: `auto` films the page when there is a link and a
    # browser, and these tests are about the choice AFTER that — a machine with
    # Chrome installed must not send them to the network.
    monkeypatch.setattr(webcast, "available", lambda: (False, "no browser"))
    monkeypatch.setattr(imagegen, "providers_ready", lambda *a, **k: bool(images))
    monkeypatch.setattr(imagegen, "why_not",
                        lambda *a, **k: "No AI image generator is available.")
    if images is True:
        def draw(prompt, out_path, **kwargs):
            out_path.write_bytes(b"png")
            return out_path
        monkeypatch.setattr(imagegen, "generate_image", draw)
    elif callable(images):
        monkeypatch.setattr(imagegen, "generate_image", images)

    def record(name: str):
        return lambda *a, **k: called.append(name)

    for name in ("background_gradient", "background_from_video",
                 "background_from_clips", "background_from_images_kenburns",
                 "background_from_multi_highlights"):
        monkeypatch.setattr(render, name, record(name))
    monkeypatch.setattr(render, "ensure_min_duration", lambda p, d, w: p)
    monkeypatch.setattr(broll, "providers_ready", lambda: [])

    def log(message: str, level: str = "info") -> None:
        events.append((level, message))

    orchestrator._build_background(  # noqa: SLF001
        job, _script(), _Narration(), material, tmp_path, 20.0, log)
    return (called[0] if called else ""), events


# ------------------------------------------------------- the silent fallback

def test_a_gradient_settled_for_says_so(tmp_path, monkeypatch):
    """Nothing to show and no stock bank: the gradient is the only option left,
    but the user has to learn that from the job and not from staring at a
    finished-looking short wondering where their video went."""
    job = JobInput(source_type="tema", source="um tema qualquer", background="auto")
    material = SourceMaterial(kind="tema", title="x", text="x")

    used, events = _run(job, material, tmp_path, monkeypatch)

    assert used == "background_gradient"
    warnings = [m for level, m in events if level == "warn"]
    assert warnings, "settling for a gradient has to be reported"
    assert "gradient" in warnings[0].lower()
    # and it has to say what to do about it
    assert "link" in warnings[0].lower() or "upload" in warnings[0].lower()


def test_a_gradient_asked_for_is_not_a_warning(tmp_path, monkeypatch):
    """Choosing the gradient deliberately is a normal thing to do."""
    job = JobInput(source_type="tema", source="tema", background="gradiente")
    material = SourceMaterial(kind="tema", title="x", text="x")

    used, events = _run(job, material, tmp_path, monkeypatch)

    assert used == "background_gradient"
    assert not [m for level, m in events if level == "warn"]


# --------------------------------------------------- the source video branch

def test_the_source_video_is_used_when_there_is_one(tmp_path, monkeypatch):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"fake")
    job = JobInput(source_type="video", source="https://youtu.be/x",
                   background="auto")
    material = SourceMaterial(kind="video", title="x", video_path=video)

    used, _ = _run(job, material, tmp_path, monkeypatch)
    assert used == "background_from_video"


def test_asking_for_the_source_video_without_one_refuses(tmp_path, monkeypatch):
    """Regression: this used to skip the branch entirely and fall through to
    the gradient, so a job asked to use its own footage came out with none of
    it and reported success. The image branch has always refused here; this
    one only pretended to work."""
    job = JobInput(source_type="tema", source="um tema", background="video_fonte")
    material = SourceMaterial(kind="tema", title="x", text="x")

    with pytest.raises(RuntimeError, match="no video was downloaded"):
        _run(job, material, tmp_path, monkeypatch)


def test_the_refusal_says_what_to_do(tmp_path, monkeypatch):
    job = JobInput(source_type="tema", source="um tema", background="video_fonte")
    material = SourceMaterial(kind="tema", title="x", text="x")

    with pytest.raises(RuntimeError) as caught:
        _run(job, material, tmp_path, monkeypatch)

    message = str(caught.value)
    assert "upload" in message or "Paste" in message
    assert "background" in message


def test_asking_for_images_without_one_still_refuses(tmp_path, monkeypatch):
    """The behaviour the video branch now matches."""
    job = JobInput(source_type="imagem", background="imagem_kenburns")
    material = SourceMaterial(kind="imagem", title="x")

    with pytest.raises(RuntimeError, match="no image"):
        _run(job, material, tmp_path, monkeypatch)


# ------------------------------------------------------------- what auto does

@pytest.mark.parametrize("kind,expected", [
    ("video", "background_from_video"),
    ("tema", "background_gradient"),
    ("artigo", "background_gradient"),
])
def test_auto_follows_the_material(kind, expected, tmp_path, monkeypatch):
    """A downloaded video is always preferred over a gradient — that is the
    whole reason someone pastes a link."""
    video = tmp_path / "source.mp4"
    video.write_bytes(b"fake")
    material = SourceMaterial(kind=kind, title="x",
                              video_path=video if kind == "video" else None)
    job = JobInput(source_type="url", source="https://x.test/a", background="auto")

    used, _ = _run(job, material, tmp_path, monkeypatch)
    assert used == expected


# ------------------------------------------------------- generated stills

def test_generated_images_beat_the_gradient_when_a_generator_is_keyed(tmp_path, monkeypatch):
    """A drawn scene is not the thing being talked about, but it is a picture
    rather than a coloured rectangle — and the gradient was what 'nothing to
    show' used to look like."""
    job = JobInput(source_type="tema", source="um tema", background="auto")
    material = SourceMaterial(kind="tema", title="x", text="x")

    used, events = _run(job, material, tmp_path, monkeypatch, images=True)

    assert used == "background_from_images_kenburns"
    warnings = [m for level, m in events if level == "warn"]
    assert any("generated as images" in m for m in warnings), \
        "settling for drawings has to be reported too"


def test_real_footage_still_wins_over_generated_stills(tmp_path, monkeypatch):
    """The order matters: a recording of the subject beats a drawing of it."""
    video = tmp_path / "source.mp4"
    video.write_bytes(b"fake")
    job = JobInput(source_type="video", source="https://youtu.be/x", background="auto")
    material = SourceMaterial(kind="video", title="x", video_path=video)

    used, _ = _run(job, material, tmp_path, monkeypatch, images=True)
    assert used == "background_from_video"


def test_asking_for_generated_images_with_no_generator_refuses(tmp_path, monkeypatch):
    """The same rule as video_fonte and imagem_kenburns: asked for something
    specific and got nothing, so say so instead of drawing a gradient that
    looks finished."""
    job = JobInput(source_type="tema", source="um tema", background="ia_imagem")
    material = SourceMaterial(kind="tema", title="x", text="x")

    with pytest.raises(RuntimeError, match="no image could be generated"):
        _run(job, material, tmp_path, monkeypatch, images=False)


def test_a_generator_that_fails_mid_way_still_uses_what_it_made(tmp_path, monkeypatch):
    """Images are paid for one at a time. Throwing away the two that worked
    because the third refused would charge for nothing — and whatever refused
    the third prompt will refuse the fourth, so the loop stops there."""
    from app.pipeline import imagegen

    made: list[str] = []

    def flaky(prompt, out_path, **kwargs):
        if len(made) >= 2:
            raise RuntimeError("out of credit")
        made.append(out_path.name)
        out_path.write_bytes(b"png")
        return out_path

    monkeypatch.setattr(imagegen, "generate_image", flaky)
    script = ShortScript(
        title="t", description="d", hashtags=[],
        segments=[ScriptSegment(kind="corpo", text="frase", broll_query=f"q{i}")
                  for i in range(5)],
        estimated_seconds=60)
    job = JobInput(source_type="tema", source="um tema", background="ia_imagem")
    events: list[tuple[str, str]] = []

    images = orchestrator._generate_scene_images(  # noqa: SLF001
        job, script, 5, tmp_path,
        lambda m, level="info": events.append((level, m)))

    assert len(images) == 2 and len(made) == 2
    assert any("out of credit" in m for level, m in events if level == "warn")


def test_an_image_already_on_disk_is_not_bought_twice(tmp_path, monkeypatch):
    """A resumed job must not pay for the same picture again."""
    (tmp_path / "ia_img_00.png").write_bytes(b"already-here")
    calls: list[str] = []

    def draw(prompt, out_path, **kwargs):
        calls.append(out_path.name)
        out_path.write_bytes(b"png")
        return out_path

    job = JobInput(source_type="tema", source="um tema", background="ia_imagem")
    material = SourceMaterial(kind="tema", title="x", text="x")

    _run(job, material, tmp_path, monkeypatch, images=draw)
    assert "ia_img_00.png" not in calls


def test_the_scene_prompt_reuses_the_scripts_own_visual_intent(tmp_path):
    """`broll_query` is what the writer would have searched a stock bank for,
    so it is the drawing brief too — inventing a second one would put a
    different picture on screen than the script asked for."""
    script = ShortScript(
        title="t", description="d", hashtags=[],
        segments=[ScriptSegment(kind="corpo", text="frase",
                                broll_query="hacker digitando no terminal")],
        estimated_seconds=20)
    job = JobInput(source_type="tema", source="tema", background="ia_imagem")

    prompts = orchestrator._scene_prompts(job, script, 2)  # noqa: SLF001
    assert all("hacker digitando no terminal" in p for p in prompts)
    # and one style, so separate calls look like one video
    assert all("cinematográfica" in p for p in prompts)
    assert all("sem texto" in p for p in prompts)


def test_a_long_script_does_not_turn_into_forty_paid_calls(tmp_path):
    script = ShortScript(
        title="t", description="d", hashtags=[],
        segments=[ScriptSegment(kind="corpo", text="frase", broll_query=f"q{i}")
                  for i in range(40)],
        estimated_seconds=90)
    job = JobInput(source_type="tema", source="tema", background="ia_imagem")

    assert len(orchestrator._scene_prompts(job, script, 40)) \
        == orchestrator.MAX_SCENE_IMAGES  # noqa: SLF001
