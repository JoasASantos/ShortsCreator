from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _parse_chain(raw: str) -> list[tuple[str, str]]:
    """"claude_cli:claude-fable-5-1,codex_cli" -> [("claude_cli", "claude-fable-5-1"), ("codex_cli", "")]"""
    steps: list[tuple[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        provider, _, model = item.partition(":")
        provider = provider.strip()
        if provider:
            steps.append((provider, model.strip()))
    return steps


# The models this project knows how to reach, each under a short name so that
# choosing one is a single word instead of a provider:model pair nobody
# remembers. `LLM_MODEL=astra` is the whole configuration.
#
# Every one of these runs on a subscription already logged in on the machine —
# `claude` for the Claude models, `codex` for the OpenAI ones — so no API key
# is billed per token.
MODEL_PRESETS: dict[str, tuple[str, str, str]] = {
    # name: (provider, model id, what it is)
    "astra": ("codex_cli", "gpt-6-astra",
              "GPT-6 Astra via Codex — the most capable, staged rollout"),
    "sol": ("codex_cli", "gpt-5.6-sol", "GPT-5.6 Sol via Codex"),
    "fable": ("claude_cli", "claude-fable-5-1", "Claude Fable 5.1"),
    "opus": ("claude_cli", "claude-opus-5", "Claude Opus 5"),
    "sonnet": ("claude_cli", "claude-sonnet-5", "Claude Sonnet 5"),
    # Installed by codex-chatgpt-web, which registers ChatGPT Web models inside
    # Codex's own picker. It is reached through the same `codex` binary, so it
    # needs no provider of its own — only the model id the launcher installed.
    "web": ("codex_cli", os.getenv("CODEX_WEB_MODEL", "chatgpt-web"),
            "ChatGPT Web through Codex (codex-chatgpt-web)"),
}

# Order the chain falls through when the chosen model cannot answer. Claude and
# OpenAI alternate on purpose: a subscription runs out per account, so the next
# link should be on the other one rather than the same quota that just failed.
FALLBACK_ORDER = ["astra", "fable", "opus", "sol", "sonnet", "web"]


# What LLM_CHAIN used to be shipped as, before LLM_MODEL existed. A .env
# carrying exactly this was never a deliberate choice — it was the default
# written out by hand — so it must not silently outrank the model the user
# picks now. Anything else in LLM_CHAIN is a real customization and wins.
_LEGACY_CHAIN = "claude_cli:claude-fable-5-1,claude_cli:claude-opus-5,codex_cli:gpt-5.6-sol"


def is_legacy_chain(raw: str) -> bool:
    return ([s for s in _parse_chain(raw)]
            == [s for s in _parse_chain(_LEGACY_CHAIN)])


def build_chain(chosen: str, explicit: str) -> list[tuple[str, str]]:
    """The chain to try, in order: the chosen model first, then the rest.

    `explicit` (LLM_CHAIN) still wins when set — it is the escape hatch for a
    combination the presets do not cover. Otherwise the chain is derived from a
    single name, so picking a model never means hand-writing a fallback list
    and never leaves the chain one link long.
    """
    if explicit.strip() and not is_legacy_chain(explicit):
        return _parse_chain(explicit)

    key = chosen.strip().lower()
    # An explicit provider:model as the chosen one — still gets the presets
    # appended behind it, so it keeps a fallback.
    head: list[tuple[str, str]] = []
    if ":" in key:
        head = _parse_chain(key)
    elif key in MODEL_PRESETS:
        provider, model, _ = MODEL_PRESETS[key]
        head = [(provider, model)]

    rest = [MODEL_PRESETS[name][:2] for name in FALLBACK_ORDER
            if name != key and MODEL_PRESETS[name][:2] not in head]
    return head + rest


class Settings:
    """Configuration read from .env. No pydantic-settings, to avoid coupling."""

    def __init__(self) -> None:
        self.root = ROOT
        self.data_dir = Path(os.getenv("DATA_DIR", ROOT / "data")).resolve()
        self.assets_dir = ROOT / "assets"

        self.jobs_dir = self.data_dir / "jobs"
        self.cache_dir = self.data_dir / "cache"
        self.voices_dir = self.data_dir / "voices"
        self.outputs_dir = self.data_dir / "outputs"
        self.secrets_dir = self.data_dir / "secrets"
        self.uploads_dir = self.data_dir / "uploads"
        self.repos_dir = self.cache_dir / "repos"
        for d in (self.jobs_dir, self.cache_dir, self.voices_dir,
                  self.outputs_dir, self.secrets_dir, self.uploads_dir,
                  self.repos_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.db_path = self.data_dir / "shortscreator.db"

        # LLM
        self.llm_provider = os.getenv("LLM_PROVIDER", "chain")
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.anthropic_model = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        self.openai_model = os.getenv("OPENAI_MODEL", "gpt-4o")
        self.ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "llama3.1")

        # CLI providers: they use the subscription already logged in on this
        # machine, with no API key and no per-token billing.
        self.claude_cli_bin = os.getenv("CLAUDE_CLI_BIN", "claude")
        self.claude_cli_model = os.getenv("CLAUDE_CLI_MODEL", "")
        self.codex_cli_bin = os.getenv("CODEX_CLI_BIN", "codex")
        self.codex_cli_model = os.getenv("CODEX_CLI_MODEL", "")
        self.codex_reasoning_effort = os.getenv("CODEX_REASONING_EFFORT", "medium")
        self.llm_cli_timeout = int(os.getenv("LLM_CLI_TIMEOUT", "420"))

        # Which model to use, by short name — see MODEL_PRESETS. This is the
        # only setting most people touch: the fallback chain is derived from
        # it, so choosing a model never means writing one out.
        self.llm_model = os.getenv("LLM_MODEL", "astra")
        # Escape hatch: an explicit "provider:model,provider:model" chain that
        # overrides the derived one entirely.
        self.llm_chain_raw = os.getenv("LLM_CHAIN", "")
        self.llm_chain = build_chain(self.llm_model, self.llm_chain_raw)
        # Ask the CLI which models it actually has before sending one. A model
        # the binary does not know is either rejected or, worse, silently
        # swapped for its default — and a short would then be written by a
        # model nobody chose. Set to 0 to skip the check.
        self.llm_verify_model = os.getenv("LLM_VERIFY_MODEL", "1").lower() not in (
            "0", "false", "no")

        # TTS
        self.tts_provider = os.getenv("TTS_PROVIDER", "edge")
        self.edge_voice = os.getenv("EDGE_VOICE", "pt-BR-AntonioNeural")
        # How fast the narration is spoken. The neural voices read a sentence at
        # about 2.9 words a second at +0%, which is quick enough that a
        # clause-heavy line arrives as one breathless run. Backing off a little
        # is the difference between energetic and rushed; a voice registered
        # with its own `rate` still wins over this.
        self.narration_rate = os.getenv("NARRATION_RATE", "-8%")
        self.elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY", "")
        self.elevenlabs_model = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
        self.xtts_server = os.getenv("XTTS_SERVER", "http://localhost:8020")
        self.fishaudio_api_key = os.getenv("FISHAUDIO_API_KEY", "")
        # model family: s1 | s2-pro | s2.1-pro | s2.1-pro-free
        self.fishaudio_backend = os.getenv("FISHAUDIO_BACKEND", "s2.1-pro-free")
        self.fishaudio_model = os.getenv("FISHAUDIO_MODEL", "")
        # VoiceStudio: the local ElevenLabs-alternative. Clones a voice from a
        # sample and speaks with it on this machine, no key and no upload. Its
        # speech API is OpenAI-shaped, its profiles API is its own.
        self.voicestudio_url = os.getenv("VOICESTUDIO_URL", "http://127.0.0.1:3900")
        # Which of its engines synthesizes. Empty means whichever is active in
        # VoiceStudio itself — its own choice is the sane default, and naming
        # one here is how someone pins OmniVoice or CosyVoice for a project.
        self.voicestudio_engine = os.getenv("VOICESTUDIO_ENGINE", "")

        # B-roll
        # Some platforms only list a profile to a logged-in session. A cookie
        # file (or the browser to take cookies from) is what yt-dlp needs, and
        # it is a path on this machine — never a credential this app stores.
        self.ytdlp_cookies = os.getenv("YTDLP_COOKIES", "")
        self.ytdlp_cookies_browser = os.getenv("YTDLP_COOKIES_BROWSER", "")

        self.pexels_api_key = os.getenv("PEXELS_API_KEY", "")
        self.pixabay_api_key = os.getenv("PIXABAY_API_KEY", "")
        self.coverr_api_key = os.getenv("COVERR_API_KEY", "")
        # Stock clips are tens of MB each and a short uses several. They are
        # deleted after the render by default; keeping them only pays off
        # while iterating on the same short.
        self.broll_keep_cache = os.getenv("BROLL_KEEP_CACHE", "").lower() in (
            "1", "true", "yes")

        # Publish
        self.youtube_client_secrets = os.getenv(
            "YOUTUBE_CLIENT_SECRETS", str(self.secrets_dir / "youtube_client_secret.json")
        )
        self.tiktok_client_key = os.getenv("TIKTOK_CLIENT_KEY", "")
        self.tiktok_client_secret = os.getenv("TIKTOK_CLIENT_SECRET", "")
        self.tiktok_redirect_uri = os.getenv(
            "TIKTOK_REDIRECT_URI", "http://localhost:8000/api/publish/tiktok/callback"
        )

        # App
        self.public_api_url = os.getenv("PUBLIC_API_URL", "http://localhost:8000")
        # Where the interface answers, used in notification links. Behind a
        # tunnel the notice has to be clickable from a phone, which a
        # localhost link never is.
        self.public_web_url = os.getenv("PUBLIC_WEB_URL", "http://localhost:3000")
        self.whisper_model = os.getenv("WHISPER_MODEL", "base")
        # Captioning a recording of your own is held to a higher standard than
        # locating a moment in a two-hour video: the words end up burned into
        # the frame, so a wrong one is worse than no caption at all. `base`
        # measurably mishears ("lendo o cabeçalho" came back as "além do"), so
        # this path pays for a bigger model. Override if the machine is slow.
        self.reels_whisper_model = os.getenv("REELS_WHISPER_MODEL", "small")
        self.max_short_seconds = int(os.getenv("MAX_SHORT_SECONDS", "90"))
        self.min_short_seconds = int(os.getenv("MIN_SHORT_SECONDS", "15"))
        # AI media generation. Empty VIDEOGEN_PROVIDER/VIDEOGEN_MODEL means
        # "let generators/registry.py choose"; naming one forces it and the
        # reason shows up in the job log either way.
        self.videogen_provider = os.getenv("VIDEOGEN_PROVIDER", "")
        self.videogen_model = os.getenv("VIDEOGEN_MODEL", "")
        # Same contract for the still generators (GPT Image 2.5, Nano Banana,
        # ComfyUI, A1111): empty means the registry chooses.
        self.imagegen_provider = os.getenv("IMAGEGEN_PROVIDER", "")
        self.imagegen_model = os.getenv("IMAGEGEN_MODEL", "")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.comfyui_url = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")
        self.comfyui_workflow = os.getenv(
            "COMFYUI_WORKFLOW", str(self.data_dir / "comfyui" / "workflow.json"))
        self.a1111_url = os.getenv("A1111_URL", "http://127.0.0.1:7860")
        # Puts the free local servers ahead of the paid APIs when both can do
        # the job.
        self.generator_prefer_local = os.getenv(
            "GENERATOR_PREFER_LOCAL", "").lower() in ("1", "true", "yes")
        self.max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "300"))
        self.github_max_files = int(os.getenv("GITHUB_MAX_FILES", "12"))
        self.github_max_file_chars = int(os.getenv("GITHUB_MAX_FILE_CHARS", "6000"))

        # Fixed short format
        self.width = 1080
        self.height = 1920
        self.fps = 30

    def job_dir(self, job_id: str) -> Path:
        d = self.jobs_dir / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
