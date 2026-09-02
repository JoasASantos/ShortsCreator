# ShortsCreator

Gera vídeos verticais 9:16 prontos para YouTube Shorts e TikTok a partir de um
link, um tema, um repositório, fotos ou um vídeo longo — com narração, legenda
karaokê sincronizada, auditoria automática de formato e publicação agendada.

![formato](https://img.shields.io/badge/sa%C3%ADda-1080%C3%971920%20%C2%B7%209%3A16-ffc400)

## O que ele faz

**Entra** um destes:

| Origem | O que acontece |
|---|---|
| Tema | Escreve o roteiro do zero |
| Link de artigo | Extrai o texto da página e transforma em roteiro |
| Link ou arquivo de vídeo | Baixa, transcreve e narra por cima — ou resume um episódio inteiro em 60s |
| Vários vídeos | Distribui os trechos entre eles, proporcional à duração de cada um |
| Repositório GitHub | Clona, lê README e código, e narra sobre a arquitetura com o código rolando na tela |
| Imagens | Narração sobre as fotos com zoom e pan lento (Ken Burns) |
| Roteiro pronto | Narra o seu texto sem passar por IA |

**Sai** um MP4 1080×1920 auditado, com legenda queimada, trilha mixada e texto
de publicação pronto para cada plataforma.

## Auditoria automática (QA)

Toda renderização é auditada sobre o **arquivo final**, não sobre o que o
pipeline acha que produziu:

- Resolução 1080×1920, proporção 9:16 e SAR 1:1
- Ausência de barras pretas (`cropdetect`) e de buracos visuais (`blackdetect`)
- H.264 em `yuv420p`, 30 fps, átomo `moov` no início
- Áudio AAC normalizado em −14 LUFS com pico real abaixo de −1 dBTP
- Silêncio no início (mata o hook) e sobra no fim
- Legenda fora dos 340 px inferiores ocupados pela interface do app
- Cobertura de legenda e duração dentro da janela útil

Se reprovar e o autoajuste estiver ligado, o sistema **corrige e refaz sozinho**
— alonga o roteiro, move a legenda, corta o silêncio, baixa a trilha — repetindo
até passar ou esgotar as tentativas. Cada correção fica registrada.

## Edição depois de pronto

- **Editor de roteiro** — reescreve trechos, reordena, troca voz e trilha
- **Prompt** — "deixa o hook mais agressivo", "adiciona um dado sobre o preço":
  a IA reescreve sem perder o resto
- **Linha do tempo** — corta, move e estica clipes, áudio e legendas, com
  monitor de vídeo sincronizado ao cursor. Recompila com FFmpeg sem passar de
  novo pelo LLM nem pelo TTS

## Sincronia da legenda

Os tempos vêm do próprio sintetizador de voz, não de transcrição posterior:

- **edge-tts** expõe `WordBoundary` — timing por palavra, desvio medido de 0,085 s
- **ElevenLabs** devolve timestamps por caractere
- **fish.audio / XTTS** não devolvem timing: a narração é sintetizada frase a
  frase e cada arquivo é medido, então o erro fica confinado à frase
  (desvio medido de 0,131 s) em vez de acumular ao longo do vídeo

## Instalação

Requer **Python 3.11+**, **Node 20+** e **FFmpeg**.

```bash
git clone https://github.com/JoasASantos/ShortsCreator.git
cd ShortsCreator
make setup          # cria o venv, instala backend e frontend, copia o .env
```

Preencha o `.env` e suba os dois serviços:

```bash
make dev            # API em :8000, interface em :3000
```

`make doctor` confere as dependências do sistema.

### Modelo de linguagem

O caminho mais barato usa uma assinatura que você já paga, em vez de chave
cobrada por token — o backend chama a CLI local já autenticada. O padrão é a
cadeia: Fable como principal, Opus 5 como reserva e o Codex como último recurso.

```env
LLM_PROVIDER=chain
LLM_CHAIN=claude_cli:claude-fable-5-1,claude_cli:claude-opus-5,codex_cli:gpt-5.6-sol
```

Cada elo é `provider:modelo` e o backend só passa para o próximo se o anterior
falhar (limite da assinatura, CLI fora do PATH, resposta inválida). Os dois
primeiros usam Claude Pro/Max via Claude Code; o terceiro usa ChatGPT Plus/Pro
via `codex login`.

Para fixar um único modelo, use o provider direto:

```env
LLM_PROVIDER=claude_cli
CLAUDE_CLI_MODEL=claude-fable-5-1
```

Também aceita `anthropic`, `openai` (chave por token) e `ollama` (local).

### Voz

`edge-tts` é o padrão e é gratuito, com vozes neurais em pt-BR. Para voz de
personagem: **ElevenLabs** (clonagem por API), **fish.audio** (catálogo com
vozes em português) ou **XTTS** local a partir de um sample de 6 a 30 segundos.

> Use apenas vozes que você tem direito de usar. Clonar a voz de uma pessoa real
> sem autorização pode violar direitos de imagem e os termos das plataformas.

## Publicação

YouTube Shorts via Data API v3 (upload resumível, agendamento nativo) e TikTok
via Content Posting API v2. Enquanto o app do TikTok não passa pela auditoria
deles, os envios chegam na caixa de rascunhos — é limitação da plataforma.

## Arquitetura

```
backend/app/
  pipeline/
    ingest.py        artigo, vídeo, repositório, imagens, roteiro
    script.py        geração e refinamento de roteiro por LLM
    tts.py           síntese com timing por palavra
    captions.py      legenda ASS com destaque karaokê
    overlays.py      legenda em PNG quando o FFmpeg não tem libass
    highlights.py    detecção de cena e escolha de trechos
    render.py        composição 9:16 com FFmpeg
    qa.py            auditoria do arquivo final
    timeline.py      modelo EDL do editor
    orchestrator.py  pipeline + laço de autoajuste
  routers/           API HTTP
web/                 interface Next.js
```

Banco em SQLite puro, sem ORM. Fila de jobs em thread, sem broker externo.

## Licença

MIT
