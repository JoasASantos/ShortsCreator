"""Interface plugável para geradores de vídeo por IA (Runway, Luma, Pika,
Higgsfield etc.) usados como fundo sintético do short.

Nenhum provider vem configurado por padrão — implemente um abaixo e troque
`background="ia_video"` no job quando tiver credenciais. Segue o mesmo padrão
dos providers de TTS/LLM: uma função por serviço, escolhida em runtime.
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings


class VideoGenNotConfigured(RuntimeError):
    pass


def generate_clip(prompt: str, duration: float, out_path: Path,
                  aspect: str = "9:16", log=lambda m: None) -> Path:
    """Ponto de entrada único chamado pelo orchestrator quando
    background='ia_video'. Roteia para o provider em settings.videogen_provider.

    Providers a implementar (todos com geração de vídeo por texto):
      - Runway Gen-3/Gen-4 — https://docs.dev.runwayml.com
      - Luma Dream Machine — https://docs.lumalabs.ai
      - Pika Labs          — https://pika.art/api
      - Higgsfield         — via MCP, quando disponível na sessão

    Cada implementação deve: (1) chamar a API com prompt + duração + aspect
    9:16, (2) fazer polling até o job terminar, (3) baixar o clipe final para
    `out_path`, (4) retornar `out_path`. Sem provider configurado, levanta
    VideoGenNotConfigured explicando o que falta.
    """
    provider = settings.videogen_provider
    if provider == "runway":
        raise VideoGenNotConfigured(
            "Provider 'runway' declarado mas não implementado — adicione a "
            "chamada à Runway API em videogen.py e defina RUNWAY_API_KEY."
        )
    if provider == "luma":
        raise VideoGenNotConfigured(
            "Provider 'luma' declarado mas não implementado — adicione a "
            "chamada à Luma Dream Machine API em videogen.py."
        )
    if provider == "higgsfield":
        raise VideoGenNotConfigured(
            "Provider 'higgsfield' declarado mas não implementado — esta "
            "função roda no processo do backend, sem acesso ao MCP da sessão "
            "de chat. Gere o clipe manualmente e importe como vídeo de "
            "origem (source_type='video' com upload), ou implemente aqui "
            "uma chamada HTTP direta à API do Higgsfield."
        )
    raise VideoGenNotConfigured(
        "Nenhum gerador de vídeo por IA está configurado (VIDEOGEN_PROVIDER "
        "vazio no .env). Implemente um provider em "
        "backend/app/pipeline/videogen.py, ou use background="
        "'broll' / 'gradiente' / 'video_fonte' / 'imagem_kenburns' enquanto isso."
    )
