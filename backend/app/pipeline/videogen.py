"""Pluggable interface for the AI video generators used as the short's synthetic
background (background='ia_video').

The credential comes from connectors.credentials("higgsfield") — configurable
from the Accounts screen or through HIGGSFIELD_KEY_ID/HIGGSFIELD_KEY_SECRET in
.env.
"""
from __future__ import annotations

from pathlib import Path

from . import connectors


class VideoGenNotConfigured(RuntimeError):
    pass


def generate_clip(prompt: str, duration: float, out_path: Path,
                  aspect: str = "9:16", log=lambda m: None) -> Path:
    """The single entry point called by the orchestrator when
    background='ia_video'. Today it routes to Higgsfield (Sora 2, Veo 3.1,
    Kling 2.5, Seedance, Hailuo — all behind the same API). To swap providers,
    implement another module under generators/ and replace the call below with
    the equivalent connectors.credentials("<id>").
    """
    if not connectors.is_configured("higgsfield"):
        raise VideoGenNotConfigured(
            "No AI video generator is configured. Register the Higgsfield key "
            "on the Accounts screen (or HIGGSFIELD_KEY_ID/"
            "HIGGSFIELD_KEY_SECRET in .env), or use background="
            "'broll' / 'gradiente' / 'video_fonte' / 'imagem_kenburns' in the meantime."
        )
    from .generators import higgsfield

    creds = connectors.credentials("higgsfield")
    return higgsfield.generate_clip(prompt, duration, out_path, creds, log=log)
