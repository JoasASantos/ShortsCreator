"""Multi-provider media generation: the choice is inspectable, a missing
provider is a normal state, and nobody's video silently changes model."""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.config import settings
from app.pipeline import connectors, videogen
from app.pipeline.generators import a1111, comfyui, nanobanana, registry

COMFY_URL = "http://127.0.0.1:18188"
A1111_URL = "http://127.0.0.1:17860"


@pytest.fixture(autouse=True)
def no_ambient_config(monkeypatch):
    """A developer's own .env must not decide whether these pass."""
    for var in ("HIGGSFIELD_KEY_ID", "HIGGSFIELD_KEY_SECRET", "GEMINI_API_KEY",
                "COMFYUI_URL", "COMFYUI_WORKFLOW", "A1111_URL",
                "VIDEOGEN_PROVIDER", "VIDEOGEN_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(settings, "comfyui_url", COMFY_URL)
    monkeypatch.setattr(settings, "a1111_url", A1111_URL)
    monkeypatch.setattr(settings, "videogen_provider", "")
    monkeypatch.setattr(settings, "videogen_model", "")
    monkeypatch.setattr(settings, "generator_prefer_local", False)


def _response(status: int, url: str = "http://x", method: str = "POST", **kwargs):
    """A response that can raise_for_status — which needs the request on it."""
    return httpx.Response(status, request=httpx.Request(method, url), **kwargs)


class _Stream:
    """httpx.stream() is used as a context manager, so a fake has to be one."""

    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self._response

    def __exit__(self, *exc):
        return False


def _local_server_down(monkeypatch):
    def refuse(*_a, **_k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(comfyui.httpx, "get", refuse)
    monkeypatch.setattr(a1111.httpx, "get", refuse)


def _comfyui_up(monkeypatch, tmp_path):
    """A reachable ComfyUI with a queueable workflow — both halves of its
    state, since either one missing makes it unable to render."""
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps({
        "3": {"class_type": "KSampler", "inputs": {"seed": "%seed%"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "%prompt%"}},
    }), encoding="utf-8")
    monkeypatch.setattr(settings, "comfyui_workflow", str(workflow))
    monkeypatch.setattr(comfyui.httpx, "get", lambda *a, **k: _response(
        200, method="GET", json={"system": {}, "devices": [
            {"name": "RTX 4090", "vram_free": 20 * 1024 ** 3}]}))
    return workflow


# ------------------------------------------------------- registry selection

def test_selection_picks_the_configured_provider_and_reports_why(monkeypatch):
    connectors.save("higgsfield", {"key_id": "a", "key_secret": "b"})
    _local_server_down(monkeypatch)

    chosen = registry.select(registry.TEXT_TO_VIDEO)
    assert chosen.provider_id == "higgsfield"
    assert chosen.eligible is True
    assert "credential in place" in chosen.reason


def test_selection_skips_a_provider_that_cannot_do_the_capability(monkeypatch):
    _local_server_down(monkeypatch)
    plan = {c.provider_id: c for c in registry.candidates(registry.TEXT_TO_VIDEO)}

    assert plan["nanobanana"].state == registry.INCAPABLE
    assert plan["nanobanana"].reason == "does not do text_to_video"
    assert plan["a1111"].state == registry.INCAPABLE


def test_selection_skips_an_unconfigured_provider_saying_what_to_configure(monkeypatch):
    _local_server_down(monkeypatch)
    plan = {c.provider_id: c for c in registry.candidates(registry.TEXT_TO_VIDEO)}

    assert plan["higgsfield"].state == registry.NOT_CONFIGURED
    assert plan["higgsfield"].eligible is False
    assert "HIGGSFIELD_KEY_ID" in plan["higgsfield"].reason


def test_a_key_that_merely_exists_does_not_make_a_local_server_ready(monkeypatch, tmp_path):
    """Reachability is part of the state: ComfyUI has a URL by default, and
    that alone must never read as 'works'."""
    _local_server_down(monkeypatch)
    monkeypatch.setattr(settings, "comfyui_workflow", str(tmp_path / "wf.json"))

    state, reason = registry.state("comfyui")
    assert state == registry.UNREACHABLE
    assert COMFY_URL in reason


def test_a_running_comfyui_without_a_workflow_is_not_configured(monkeypatch, tmp_path):
    _comfyui_up(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "comfyui_workflow", str(tmp_path / "missing.json"))

    state, reason = registry.state("comfyui")
    assert state == registry.NOT_CONFIGURED
    assert "Export (API)" in reason


def test_an_explicit_choice_goes_first_and_the_reason_says_so(monkeypatch, tmp_path):
    connectors.save("higgsfield", {"key_id": "a", "key_secret": "b"})
    _comfyui_up(monkeypatch, tmp_path)

    plan = registry.candidates(registry.TEXT_TO_VIDEO, prefer="comfyui")
    assert plan[0].provider_id == "comfyui"
    assert plan[0].reason.startswith("asked for explicitly")
    assert registry.select(registry.TEXT_TO_VIDEO, prefer="comfyui").provider_id == "comfyui"


def test_prefer_local_puts_the_free_servers_ahead_of_the_paid_api(monkeypatch, tmp_path):
    connectors.save("higgsfield", {"key_id": "a", "key_secret": "b"})
    _comfyui_up(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "generator_prefer_local", True)

    chosen = registry.select(registry.TEXT_TO_VIDEO)
    assert chosen.provider_id == "comfyui"
    assert "GENERATOR_PREFER_LOCAL" in chosen.reason


def test_nothing_available_explains_every_blocker_and_the_way_out(monkeypatch, tmp_path):
    _local_server_down(monkeypatch)
    monkeypatch.setattr(settings, "comfyui_workflow", str(tmp_path / "wf.json"))

    with pytest.raises(videogen.VideoGenNotConfigured) as exc:
        videogen.generate_clip("a city at night", 5.0, tmp_path / "bg.mp4")
    message = str(exc.value)
    assert "HIGGSFIELD_KEY_ID" in message          # what to configure
    assert COMFY_URL in message                     # and the other option
    assert "background='broll'" in message          # what to do meanwhile
    assert "Traceback" not in message


def test_the_reported_plan_matches_the_provider_that_ran(monkeypatch, tmp_path):
    """The reason shown and the model used come from the same call, so they
    cannot describe different worlds."""
    connectors.save("higgsfield", {"key_id": "a", "key_secret": "b"})
    _local_server_down(monkeypatch)
    ran: list[str] = []
    monkeypatch.setattr(registry, "run_text_to_video",
                        lambda provider_id, *a, **k: ran.append(provider_id) or (a[2]))

    plan = videogen.plan()
    videogen.generate_clip("x", 5.0, tmp_path / "bg.mp4")
    assert ran == [next(c.provider_id for c in plan if c.eligible)]


# ------------------------------------------- Higgsfield: the model is a choice

def test_model_names_resolve_to_the_provider_ids():
    assert registry.resolve_model("higgsfield", "seedance") == "seedance-lite"
    assert registry.resolve_model("higgsfield", "seedance-pro") == "seedance-pro"
    assert registry.resolve_model("higgsfield", "sora") == "sora-2"
    # A typo becomes the default instead of a 404 halfway into a render.
    assert registry.resolve_model("higgsfield", "seedanse") == "seedance-lite"
    assert registry.resolve_model("higgsfield", "") == "seedance-lite"


def test_seedance_pro_is_selectable_by_name_through_higgsfield(monkeypatch, tmp_path):
    """Higgsfield already fronts Seedance, so asking for it must not mean a
    second client behind its back."""
    connectors.save("higgsfield", {"key_id": "a", "key_secret": "b"})
    _local_server_down(monkeypatch)
    from app.pipeline.generators import higgsfield

    posted: list[str] = []

    def fake_post(url, **kwargs):
        posted.append(url)
        return _response(200, url, json={"request_id": "r1",
                                        "status_url": "http://x/status"})

    monkeypatch.setattr(higgsfield.httpx, "post", fake_post)
    monkeypatch.setattr(higgsfield.httpx, "get", lambda url, **k: _response(
        200, url, method="GET", json={"status": "completed",
                                      "video": {"url": "http://x/v.mp4"}}))
    monkeypatch.setattr(higgsfield.httpx, "stream", lambda *a, **k: _Stream(
        _response(200, method="GET", content=b"mp4")))

    out = tmp_path / "bg.mp4"
    videogen.generate_clip("a city", 5.0, out, model="seedance-pro")
    assert posted[0].endswith("/bytedance/seedance/v1/pro/fast/text-to-video")
    assert out.read_bytes() == b"mp4"


# ------------------------------------------------------------------- ComfyUI

def test_comfyui_queues_the_workflow_with_the_prompt_substituted(monkeypatch, tmp_path):
    workflow = _comfyui_up(monkeypatch, tmp_path)
    sent: dict = {}

    def fake_post(url, **kwargs):
        sent["url"] = url
        sent["json"] = kwargs["json"]
        return _response(200, url, json={"prompt_id": "p1", "number": 1,
                                         "node_errors": {}})

    history = _response(200, method="GET", json={"p1": {
        "status": {"status_str": "success", "completed": True, "messages": []},
        "outputs": {"9": {"gifs": [{"filename": "out.mp4", "subfolder": "",
                                    "type": "output"}]}},
    }})
    monkeypatch.setattr(comfyui.httpx, "post", fake_post)
    monkeypatch.setattr(comfyui.httpx, "get", lambda url, **k: history)
    viewed: dict = {}

    def fake_stream(method, url, **kwargs):
        viewed["url"] = url
        viewed["params"] = kwargs["params"]
        return _Stream(_response(200, method="GET", content=b"movie"))

    monkeypatch.setattr(comfyui.httpx, "stream", fake_stream)

    out = tmp_path / "bg.mp4"
    comfyui.generate("neon skyline", out, want="video", base_url=COMFY_URL,
                     workflow_path=workflow)

    assert sent["url"] == f"{COMFY_URL}/prompt"
    assert sent["json"]["prompt"]["6"]["inputs"]["text"] == "neon skyline"
    assert sent["json"]["prompt"]["3"]["inputs"]["seed"].isdigit()
    assert sent["json"]["client_id"]
    assert viewed["url"] == f"{COMFY_URL}/view"
    assert viewed["params"] == {"filename": "out.mp4", "subfolder": "",
                                "type": "output"}
    assert out.read_bytes() == b"movie"


def test_comfyui_fills_the_first_text_node_when_there_is_no_placeholder(tmp_path):
    """"Save (API format)" exports have no placeholders — refusing them would
    make the provider useless to everybody."""
    path = tmp_path / "wf.json"
    path.write_text(json.dumps({
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "negative"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "old prompt"}},
    }), encoding="utf-8")

    graph = comfyui.load_workflow(path, "a red car")
    assert graph["6"]["inputs"]["text"] == "a red car"
    assert graph["7"]["inputs"]["text"] == "negative"


def test_comfyui_refuses_a_ui_export_and_names_the_right_one(tmp_path):
    path = tmp_path / "wf.json"
    path.write_text(json.dumps({"last_node_id": 9, "nodes": [], "links": []}),
                    encoding="utf-8")

    with pytest.raises(registry.ProviderRefused, match="Save \\(API format\\)"):
        comfyui.load_workflow(path, "x")


def test_comfyui_that_is_not_running_says_so_and_how_to_start_it(monkeypatch):
    _local_server_down(monkeypatch)

    with pytest.raises(registry.LocalServerDown) as exc:
        comfyui.probe(COMFY_URL)
    message = str(exc.value)
    assert COMFY_URL in message
    assert "python main.py" in message
    assert "ConnectError" in message   # the cause, named, not a traceback


def test_something_else_on_the_comfyui_port_is_not_mistaken_for_it(monkeypatch):
    monkeypatch.setattr(comfyui.httpx, "get",
                        lambda *a, **k: _response(404, method="GET", json={}))

    with pytest.raises(registry.LocalServerDown, match="not ComfyUI"):
        comfyui.probe(COMFY_URL)


def test_an_image_workflow_cannot_make_the_video_background(monkeypatch, tmp_path):
    workflow = _comfyui_up(monkeypatch, tmp_path)
    monkeypatch.setattr(comfyui.httpx, "post", lambda url, **k: _response(
        200, url, json={"prompt_id": "p1"}))
    monkeypatch.setattr(comfyui.httpx, "get", lambda url, **k: _response(
        200, method="GET", json={"p1": {
            "status": {"status_str": "success", "completed": True},
            "outputs": {"9": {"images": [{"filename": "out.png",
                                          "subfolder": "", "type": "output"}]}},
        }}))

    with pytest.raises(registry.ProviderRefused) as exc:
        comfyui.generate("x", tmp_path / "bg.mp4", want="video",
                         base_url=COMFY_URL, workflow_path=workflow)
    assert ".png" in str(exc.value)
    assert "WAN" in str(exc.value)


def test_comfyui_reports_the_node_that_failed_validation(monkeypatch, tmp_path):
    workflow = _comfyui_up(monkeypatch, tmp_path)
    monkeypatch.setattr(comfyui.httpx, "post", lambda url, **k: _response(
        400, url, json={"error": {"message": "Prompt outputs failed validation"},
                        "node_errors": {"4": {"errors": [
                            {"message": "value not in list: ckpt_name"}]}}}))

    with pytest.raises(registry.ProviderRefused, match="ckpt_name"):
        comfyui.generate("x", tmp_path / "bg.mp4", want="video",
                         base_url=COMFY_URL, workflow_path=workflow)


# ------------------------------------------------------- Automatic1111 WebUI

def test_a1111_txt2img_shape_and_base64_response(monkeypatch, tmp_path):
    sent: dict = {}

    def fake_post(url, **kwargs):
        sent["url"] = url
        sent["json"] = kwargs["json"]
        return _response(200, url, json={
            "images": [base64.b64encode(b"png-bytes").decode()],
            "parameters": {}, "info": "{}"})

    monkeypatch.setattr(a1111.httpx, "post", fake_post)

    out = tmp_path / "still.png"
    a1111.generate_image("a lighthouse", out, base_url=A1111_URL)

    assert sent["url"] == f"{A1111_URL}/sdapi/v1/txt2img"
    assert sent["json"]["prompt"] == "a lighthouse"
    assert (sent["json"]["width"], sent["json"]["height"]) == a1111.ASPECT_SIZE["9:16"]
    assert sent["json"]["save_images"] is False
    assert out.read_bytes() == b"png-bytes"


def test_a1111_started_without_the_api_flag_says_to_restart_with_it(monkeypatch):
    monkeypatch.setattr(a1111.httpx, "get",
                        lambda *a, **k: _response(404, method="GET", json={}))

    with pytest.raises(registry.LocalServerDown) as exc:
        a1111.probe(A1111_URL)
    assert "--api" in str(exc.value)
    assert A1111_URL in str(exc.value)


def test_a1111_that_is_not_running_names_the_url_and_the_command(monkeypatch):
    _local_server_down(monkeypatch)

    with pytest.raises(registry.LocalServerDown) as exc:
        a1111.probe(A1111_URL)
    assert A1111_URL in str(exc.value)
    assert "webui.sh --api" in str(exc.value)


def test_a1111_with_no_checkpoint_installed_is_not_ready(monkeypatch):
    monkeypatch.setattr(a1111.httpx, "get",
                        lambda *a, **k: _response(200, method="GET", json=[]))

    with pytest.raises(registry.LocalServerDown, match="no checkpoint"):
        a1111.probe(A1111_URL)


def test_a1111_probe_lists_the_checkpoints_it_found(monkeypatch):
    monkeypatch.setattr(a1111.httpx, "get", lambda *a, **k: _response(
        200, method="GET", json=[{"title": "sdxl.safetensors [abc]",
                                  "model_name": "sdxl", "filename": "sdxl.safetensors"}]))

    assert "sdxl" in a1111.probe(A1111_URL)


# ---------------------------------------------------------- Nano Banana Pro

def _nano_ok(data: bytes = b"image-bytes", mime: str = "image/png") -> dict:
    return {"candidates": [{"content": {"parts": [
        {"text": "Here is the image."},
        {"inlineData": {"mimeType": mime,
                        "data": base64.b64encode(data).decode()}},
    ]}, "finishReason": "STOP"}]}


def test_nanobanana_request_shape_and_inline_image_parsing(monkeypatch, tmp_path):
    sent: dict = {}

    def fake_post(url, **kwargs):
        sent["url"] = url
        sent["json"] = kwargs["json"]
        sent["headers"] = kwargs["headers"]
        return _response(200, url, json=_nano_ok())

    monkeypatch.setattr(nanobanana.httpx, "post", fake_post)

    out = tmp_path / "cover.png"
    result = nanobanana.generate_image("a cover", out, {"api_key": "secret-key"})

    assert sent["url"].endswith("/models/gemini-3-pro-image-preview:generateContent")
    assert sent["headers"]["x-goog-api-key"] == "secret-key"
    assert sent["json"]["contents"][0]["parts"] == [{"text": "a cover"}]
    config = sent["json"]["generationConfig"]
    assert config["responseModalities"] == ["TEXT", "IMAGE"]
    assert config["imageConfig"]["aspectRatio"] == "9:16"
    assert result.read_bytes() == b"image-bytes"


def test_nanobanana_edits_an_image_by_sending_it_inline(monkeypatch, tmp_path):
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"original")
    sent: dict = {}
    monkeypatch.setattr(nanobanana.httpx, "post", lambda url, **k: (
        sent.update(k["json"]) or _response(200, url, json=_nano_ok())))

    nanobanana.generate_image("make it night", tmp_path / "out.png",
                              {"api_key": "k"}, reference=reference)
    inline = sent["contents"][0]["parts"][1]["inline_data"]
    assert base64.b64decode(inline["data"]) == b"original"
    assert inline["mime_type"] == "image/png"


def test_nanobanana_honours_the_mime_type_it_actually_returned(monkeypatch, tmp_path):
    """A JPEG written under a .png name only confuses ffmpeg later."""
    monkeypatch.setattr(nanobanana.httpx, "post", lambda url, **k: _response(
        200, url, json=_nano_ok(b"jpg", mime="image/jpeg")))

    result = nanobanana.generate_image("x", tmp_path / "out.png", {"api_key": "k"})
    assert result.suffix == ".jpg"


def test_nanobanana_a_blocked_prompt_is_a_refusal(monkeypatch, tmp_path):
    monkeypatch.setattr(nanobanana.httpx, "post", lambda url, **k: _response(
        200, url, json={"promptFeedback": {"blockReason": "SAFETY"}}))

    with pytest.raises(registry.ProviderRefused, match="SAFETY"):
        nanobanana.generate_image("x", tmp_path / "o.png", {"api_key": "k"})


def test_nanobanana_a_spent_quota_is_a_refusal(monkeypatch, tmp_path):
    monkeypatch.setattr(nanobanana.httpx, "post",
                        lambda url, **k: _response(429, url, json={}))

    with pytest.raises(registry.ProviderRefused, match="quota"):
        nanobanana.generate_image("x", tmp_path / "o.png", {"api_key": "k"})


def test_nanobanana_a_server_error_is_not_a_refusal(monkeypatch, tmp_path):
    """A 503 is transient: it must propagate instead of moving the render to
    another model."""
    monkeypatch.setattr(nanobanana.httpx, "post",
                        lambda url, **k: _response(503, url, json={}))

    with pytest.raises(httpx.HTTPStatusError):
        nanobanana.generate_image("x", tmp_path / "o.png", {"api_key": "k"})


def test_no_error_message_ever_carries_the_key(monkeypatch, tmp_path):
    monkeypatch.setattr(nanobanana.httpx, "post", lambda url, **k: _response(
        400, url, json={"error": {"message": "API key not valid: sk-live-123"}}))

    with pytest.raises(registry.ProviderRefused) as exc:
        nanobanana.generate_image("x", tmp_path / "o.png",
                                  {"api_key": "sk-live-123"})
    assert "sk-live-123" not in str(exc.value)
    assert "***" in str(exc.value)


# ------------------------------------------------- fallback, only on refusal

def _two_video_providers(monkeypatch, tmp_path):
    connectors.save("higgsfield", {"key_id": "a", "key_secret": "b"})
    _comfyui_up(monkeypatch, tmp_path)
    plan = registry.candidates(registry.TEXT_TO_VIDEO)
    assert [c.provider_id for c in plan if c.eligible] == ["higgsfield", "comfyui"]


def test_a_refusal_falls_back_and_says_loudly_which_model_took_over(monkeypatch, tmp_path):
    _two_video_providers(monkeypatch, tmp_path)
    from app.pipeline.generators import higgsfield

    def refuse(*_a, **_k):
        raise registry.ProviderRefused("out of credit")

    def make(prompt, out_path, **kwargs):
        out_path.write_bytes(b"local")
        return out_path

    monkeypatch.setattr(higgsfield, "generate_clip", refuse)
    monkeypatch.setattr(comfyui, "generate", make)
    lines: list[tuple[str, str]] = []

    out = tmp_path / "bg.mp4"
    videogen.generate_clip("a city", 5.0, out,
                           log=lambda m, level="info": lines.append((m, level)))

    assert out.read_bytes() == b"local"
    warning = next(m for m, level in lines if level == "warn")
    assert "out of credit" in warning
    assert "ComfyUI (local)" in warning
    assert "different model" in warning


def test_a_network_error_does_not_fall_back(monkeypatch, tmp_path):
    """The precedent is tts._synthesize_with: only a refusal falls back, a
    network error propagates rather than silently changing the model."""
    _two_video_providers(monkeypatch, tmp_path)
    from app.pipeline.generators import higgsfield

    def blip(*_a, **_k):
        raise httpx.ReadTimeout("timed out")

    used: list[str] = []
    monkeypatch.setattr(higgsfield, "generate_clip", blip)
    monkeypatch.setattr(comfyui, "generate",
                        lambda *a, **k: used.append("comfyui"))

    with pytest.raises(httpx.ReadTimeout):
        videogen.generate_clip("a city", 5.0, tmp_path / "bg.mp4")
    assert used == []


def test_the_last_provider_refusing_ends_as_a_normal_state(monkeypatch, tmp_path):
    connectors.save("higgsfield", {"key_id": "a", "key_secret": "b"})
    _local_server_down(monkeypatch)
    from app.pipeline.generators import higgsfield

    monkeypatch.setattr(higgsfield, "generate_clip", lambda *a, **k: (
        (_ for _ in ()).throw(registry.ProviderRefused("no credit left"))))

    with pytest.raises(videogen.VideoGenNotConfigured, match="no credit left"):
        videogen.generate_clip("a city", 5.0, tmp_path / "bg.mp4")


# -------------------------------------------------------------------- route

def test_the_route_never_claims_a_provider_works_because_a_key_exists(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app

    connectors.save("higgsfield", {"key_id": "a", "key_secret": "b"})
    _local_server_down(monkeypatch)
    monkeypatch.setattr(settings, "comfyui_workflow", str(tmp_path / "wf.json"))

    payload = TestClient(app).get("/api/generators").json()
    states = {p["id"]: p["state"] for p in payload["providers"]}
    assert states["higgsfield"] == registry.READY
    assert states["comfyui"] == registry.UNREACHABLE
    assert states["a1111"] == registry.UNREACHABLE
    # Every provider says what it unlocks, so the screen is not a list of ids.
    assert all(p["unlocks"] for p in payload["providers"])
    assert payload["selection"]["text_to_video"][0]["provider"] == "higgsfield"


def test_the_route_test_answers_with_the_actionable_message(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    _local_server_down(monkeypatch)
    client = TestClient(app)

    failed = client.post("/api/generators/test", json={"provider": "comfyui"})
    assert failed.status_code == 400
    assert "python main.py" in failed.json()["detail"]

    assert client.post("/api/generators/test", json={"provider": "nope"}).status_code == 404
