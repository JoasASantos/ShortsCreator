"""ComfyUI client — the local, free generator. No key, no bill, your own GPU.

Routes, verified against ComfyUI's own server.py / execution.py:
  POST /prompt            {"prompt": <workflow in API format>, "client_id": str}
                          -> {"prompt_id", "number", "node_errors"}
                          -> 400 with {"error", "node_errors"} when the graph
                             does not validate
  GET  /history/{id}      -> {"<id>": {"prompt": [...], "outputs": {...},
                                "status": {"status_str": "success"|"error",
                                           "completed": bool, "messages": []}}}
                          -> {} while the job is still queued or running
  GET  /view?filename=&subfolder=&type=output   -> the raw file bytes
  GET  /system_stats      -> {"system": {...}, "devices": [{"name", "vram_free"}]}

The workflow is the user's, not ours: ComfyUI has no fixed model list, so
anything hardcoded here would be wrong on the next machine. What we own is the
substitution of the prompt into a workflow the user exported with
"Save (API format)".
"""
from __future__ import annotations

import json
import random
import time
import uuid
from pathlib import Path

import httpx

from .registry import LocalServerDown, ProviderRefused

# A local server either answers immediately or is not running at all, so the
# connect timeout is what turns "nothing on this port" into a fast, honest
# answer instead of a 30 s hang on the generators screen.
CONNECT_TIMEOUT = 3.0
# Queueing, polling and downloading are all quick calls against localhost.
TIMEOUT = httpx.Timeout(60.0, connect=CONNECT_TIMEOUT)
PROBE_TIMEOUT = httpx.Timeout(5.0, connect=CONNECT_TIMEOUT)

POLL_INTERVAL = 2.0
# A diffusion video workflow on a consumer GPU legitimately runs for minutes:
# ~5 s of WAN-class footage at 480p is 4-8 minutes, and the first run also
# loads well over 10 GB of weights from disk. Anything under half an hour would
# abandon jobs that were going to succeed — the opposite of what a timeout is
# for. The user still sees progress through the poll log.
POLL_TIMEOUT = 1800.0

PROMPT_PLACEHOLDER = "%prompt%"
SEED_PLACEHOLDER = "%seed%"

VIDEO_SUFFIXES = (".mp4", ".webm", ".mkv", ".mov", ".gif")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

# Where a node publishes its saved files. Core ComfyUI puts video under
# "images" with animated=True; the widely installed VideoHelperSuite uses
# "gifs" and some custom nodes use "videos", so all three are read.
OUTPUT_KEYS = ("images", "gifs", "videos")


def _down(base_url: str, exc: Exception | None = None) -> LocalServerDown:
    return LocalServerDown(
        f"ComfyUI is not answering at {base_url}. Start it — `python main.py "
        f"--listen 127.0.0.1 --port 8188` in the ComfyUI folder — or point "
        f"COMFYUI_URL at the machine that runs it."
        + (f" ({type(exc).__name__})" if exc else "")
    )


def probe(base_url: str, workflow_path: Path | None = None) -> str:
    """Reachability plus the workflow, because either one missing makes the
    provider unable to render. A local generator that is not running is not
    configured, however many settings point at it — and a running one with no
    workflow to queue is just as unable, so the state has to say both.
    """
    from ...config import settings

    url = base_url.rstrip("/")
    try:
        r = httpx.get(f"{url}/system_stats", timeout=PROBE_TIMEOUT)
    except httpx.HTTPError as exc:
        raise _down(url, exc) from exc
    if r.status_code == 404:
        raise LocalServerDown(
            f"Something is answering at {url} but it is not ComfyUI "
            f"(/system_stats is unknown to it). Check COMFYUI_URL."
        )
    r.raise_for_status()
    info = r.json()
    devices = info.get("devices") or []
    device = devices[0].get("name", "?") if devices else "no GPU reported"
    free = devices[0].get("vram_free") if devices else None
    free_gb = f", {free / 1024 ** 3:.1f} GB VRAM free" if isinstance(free, (int, float)) else ""

    workflow = Path(workflow_path or settings.comfyui_workflow)
    if not workflow.exists():
        raise ProviderRefused(
            f"ComfyUI is running at {url}, but there is no workflow to queue at "
            f"{workflow}. In ComfyUI use Workflow > Export (API), save it there "
            f"(or set COMFYUI_WORKFLOW) and put %prompt% where the text goes."
        )
    return f"ComfyUI responding at {url} — {device}{free_gb}, workflow {workflow.name}"


# ------------------------------------------------------------------ workflow

def load_workflow(path: Path, prompt: str, seed: int | None = None) -> dict:
    """The user's exported graph with this render's prompt in it.

    Two ways in, in this order: the placeholders %prompt% / %seed% anywhere in
    the graph, or — when the file has none, which is what "Save (API format)"
    produces — the text input of the first CLIPTextEncode node. Without the
    fallback the provider would be useless to anyone who simply exported a
    working workflow, which is everyone.
    """
    if not path.exists():
        raise ProviderRefused(
            f"No ComfyUI workflow at {path}. In ComfyUI use Workflow > Export "
            f"(API) and save it there, or set COMFYUI_WORKFLOW to the file you "
            f"already have. Put %prompt% where the text should go."
        )
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProviderRefused(f"The ComfyUI workflow at {path} is not valid JSON: {exc}") from exc
    if not isinstance(graph, dict) or not graph:
        raise ProviderRefused(
            f"{path} is not a workflow in API format — the export has to be "
            f"'Save (API format)', a flat object of node id -> node."
        )
    # ComfyUI's UI export wraps the graph in {"nodes": [...], "links": [...]};
    # only the API format is queueable, and saying so beats a 400 from /prompt.
    if "nodes" in graph and "last_node_id" in graph:
        raise ProviderRefused(
            f"{path} is a UI workflow, not the API format. Re-export it with "
            f"'Save (API format)' — the queue endpoint only accepts that one."
        )

    seed = random.randint(1, 2 ** 31 - 1) if seed is None else seed
    filled, hits = _substitute(graph, prompt, seed)
    if not hits:
        filled = _fill_first_text_node(filled, prompt)
    return filled


def _substitute(node, prompt: str, seed: int) -> tuple[object, int]:
    hits = 0
    if isinstance(node, dict):
        out_dict = {}
        for key, value in node.items():
            out_dict[key], found = _substitute(value, prompt, seed)
            hits += found
        return out_dict, hits
    if isinstance(node, list):
        out_list = []
        for value in node:
            replaced, found = _substitute(value, prompt, seed)
            out_list.append(replaced)
            hits += found
        return out_list, hits
    if isinstance(node, str):
        if PROMPT_PLACEHOLDER in node:
            hits += 1
            node = node.replace(PROMPT_PLACEHOLDER, prompt)
        if SEED_PLACEHOLDER in node:
            hits += 1
            node = node.replace(SEED_PLACEHOLDER, str(seed))
        return node, hits
    return node, 0


def _fill_first_text_node(graph: dict, prompt: str) -> dict:
    """The positive prompt is the CLIPTextEncode that comes first by node id —
    the same order ComfyUI itself assigns when the graph is built, so it is the
    positive one in every stock template."""
    encoders = sorted(
        (node_id for node_id, node in graph.items()
         if isinstance(node, dict) and "CLIPTextEncode" in str(node.get("class_type", ""))),
        key=lambda node_id: (len(node_id), node_id),
    )
    if not encoders:
        raise ProviderRefused(
            "The ComfyUI workflow has neither a %prompt% placeholder nor a "
            "CLIPTextEncode node, so there is nowhere to put the prompt. Add "
            "%prompt% to the text field the workflow uses."
        )
    graph[encoders[0]].setdefault("inputs", {})["text"] = prompt
    return graph


# ----------------------------------------------------------------- generate

def generate(prompt: str, out_path: Path, want: str = "video",
             base_url: str = "", workflow_path: Path | None = None,
             log=lambda m, *_: None) -> Path:
    from ...config import settings

    url = (base_url or settings.comfyui_url).rstrip("/")
    path = Path(workflow_path or settings.comfyui_workflow)
    graph = load_workflow(path, prompt)
    client_id = uuid.uuid4().hex

    log(f"comfyui: queueing {path.name} — {prompt[:60]}")
    try:
        r = httpx.post(f"{url}/prompt", json={"prompt": graph, "client_id": client_id},
                       timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        raise _down(url, exc) from exc
    if r.status_code == 400:
        # A graph ComfyUI refuses to run will never run: a missing checkpoint,
        # a node from an uninstalled pack. Retrying is pointless, so this is a
        # refusal and the next provider may take over.
        raise ProviderRefused(f"ComfyUI rejected the workflow: {_validation_error(r)}")
    r.raise_for_status()
    prompt_id = r.json().get("prompt_id")
    if not prompt_id:
        raise RuntimeError("ComfyUI accepted the workflow but returned no prompt_id")

    files = _wait(url, prompt_id, log)
    chosen = _pick(files, want)
    if chosen is None:
        made = ", ".join(sorted({Path(f["filename"]).suffix or "?" for f in files})) or "nothing"
        raise ProviderRefused(
            f"The ComfyUI workflow at {path} produced {made}, not {want}. "
            f"Point COMFYUI_WORKFLOW at a {want} workflow — for video, one of "
            f"the WAN / LTXV / AnimateDiff templates with a Save Video node."
        )
    _download(url, chosen, out_path)
    log(f"comfyui: {chosen['filename']} downloaded")
    return out_path


def _validation_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    error = payload.get("error") or {}
    message = error.get("message") if isinstance(error, dict) else str(error)
    nodes = payload.get("node_errors") or {}
    if nodes:
        first = next(iter(nodes.values()))
        details = (first or {}).get("errors") or []
        if details:
            message = f"{message or 'invalid graph'} — {details[0].get('message', '')}"
    return message or "invalid graph"


def _wait(url: str, prompt_id: str, log) -> list[dict]:
    deadline = time.monotonic() + POLL_TIMEOUT
    announced = False
    while time.monotonic() < deadline:
        try:
            s = httpx.get(f"{url}/history/{prompt_id}", timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            # The server went away mid-render. That is a network error, not a
            # refusal: it propagates instead of quietly handing the render to
            # another model.
            raise _down(url, exc) from exc
        s.raise_for_status()
        entry = (s.json() or {}).get(prompt_id)
        if entry:
            # A history entry only exists once the job left the queue, so
            # reaching here means it is finished — well or badly.
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                raise RuntimeError(
                    f"ComfyUI: the workflow failed — {_status_message(status)}")
            files = _collect(entry.get("outputs") or {})
            if files:
                return files
            raise RuntimeError("ComfyUI finished but the workflow saved no file "
                               "(the graph needs a Save Image / Save Video node)")
        if not announced:
            log("comfyui: rendering locally, this can take several minutes")
            announced = True
        time.sleep(POLL_INTERVAL)
    raise RuntimeError(f"ComfyUI: gave up after {POLL_TIMEOUT / 60:.0f} min waiting "
                       f"for prompt {prompt_id}")


def _status_message(status: dict) -> str:
    messages = status.get("messages") or []
    for message in reversed(messages):
        # messages are ["event_name", {...}] pairs; the failure carries a
        # human-readable exception_message.
        if isinstance(message, (list, tuple)) and len(message) == 2:
            payload = message[1] or {}
            if isinstance(payload, dict) and payload.get("exception_message"):
                return str(payload["exception_message"])[:300]
    return status.get("status_str") or "no detail"


def _collect(outputs: dict) -> list[dict]:
    files: list[dict] = []
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        for key in OUTPUT_KEYS:
            for item in node_output.get(key) or []:
                if isinstance(item, dict) and item.get("filename"):
                    files.append(item)
    return files


def _pick(files: list[dict], want: str) -> dict | None:
    suffixes = VIDEO_SUFFIXES if want == "video" else IMAGE_SUFFIXES
    for item in files:
        if Path(item["filename"]).suffix.lower() in suffixes:
            return item
    return None


def _download(url: str, item: dict, out_path: Path) -> None:
    params = {"filename": item["filename"],
              "subfolder": item.get("subfolder", ""),
              "type": item.get("type", "output")}
    try:
        with httpx.stream("GET", f"{url}/view", params=params, timeout=TIMEOUT) as resp:
            resp.raise_for_status()
            with out_path.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
    except httpx.HTTPError as exc:
        raise _down(url, exc) from exc
