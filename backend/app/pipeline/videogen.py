"""Interface plugável para geradores de vídeo por IA usados como fundo
sintético do short (background='ia_video').

Credencial vem de connectors.credentials("higgsfield") — configurável pela
tela de Contas ou por HIGGSFIELD_KEY_ID/HIGGSFIELD_KEY_SECRET no .env.
"""
from __future__ import annotations

from pathlib import Path

from . import connectors


class VideoGenNotConfigured(RuntimeError):
    pass


def generate_clip(prompt: str, duration: float, out_path: Path,
                  aspect: str = "9:16", log=lambda m: None) -> Path:
    """Ponto de entrada único chamado pelo orchestrator quando
    background='ia_video'. Hoje roteado para o Higgsfield (Sora 2, Veo 3.1,
    Kling 2.5, Seedance, Hailuo — todos por trás da mesma API). Para trocar
    de provider, implemente outro módulo em generators/ e troque a chamada
    abaixo por connectors.credentials("<id>") equivalente.
    """
    if not connectors.is_configured("higgsfield"):
        raise VideoGenNotConfigured(
            "Nenhum gerador de vídeo por IA está configurado. Cadastre a "
            "chave da Higgsfield na tela de Contas (ou HIGGSFIELD_KEY_ID/"
            "HIGGSFIELD_KEY_SECRET no .env), ou use background="
            "'broll' / 'gradiente' / 'video_fonte' / 'imagem_kenburns' enquanto isso."
        )
    from .generators import higgsfield

    creds = connectors.credentials("higgsfield")
    return higgsfield.generate_clip(prompt, duration, out_path, creds, log=log)
