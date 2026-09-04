# ShortsCreator

Gera vídeos verticais 9:16 prontos para YouTube Shorts, TikTok, Instagram Reels
e LinkedIn a partir de um link, um tema, um repositório, fotos ou um vídeo longo
— com narração, legenda karaokê sincronizada, auditoria automática de formato,
publicação agendada e retorno de desempenho que realimenta o roteirista.

![formato](https://img.shields.io/badge/sa%C3%ADda-1080%C3%971920%20%C2%B7%209%3A16-ffc400)
![testes](https://img.shields.io/badge/testes-128%20passando-35d67f)
![i18n](https://img.shields.io/badge/i18n-PT%20%C2%B7%20EN%20%C2%B7%20ES%20%C2%B7%20RU%20%C2%B7%20ZH-35d6e8)

**🇬🇧 [Read in English](README.md)**

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

**Sai** um MP4 1080×1920 auditado, com legenda queimada, trilha mixada, capa
composta e texto de publicação pronto para cada plataforma.

Um vídeo longo pode virar **vários** shorts de uma vez: a tela **Lote**
transcreve, o modelo escolhe os melhores momentos e cada trecho vira um short
independente — com agendamento em sequência, se você quiser um por dia.

## O ciclo fecha

O que é publicado volta como dado, e o dado volta como roteiro:

1. **Tendências** — Google Trends, Reddit rising, Hacker News e YouTube em alta,
   por nicho e região. Um clique transforma o assunto em short.
2. **Ganchos A/B** — três alternativas para o mesmo roteiro, cada uma com um
   mecanismo diferente (pergunta, número, contradição, promessa), com prévia em
   áudio na voz do vídeo. Troque no lugar ou publique as duas versões.
3. **Desempenho** — views, likes, comentários, compartilhamentos e retenção
   média chegam sozinhos a cada 6 horas (YouTube Analytics, TikTok, Instagram).
4. **Realimentação** — os ganchos que mais retiveram no seu canal entram no
   prompt do próximo roteiro como referência de *mecanismo*, não de texto.

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
- **Capa** — escolhe sozinha o frame com mais detalhe visual e compõe o título
  em cima; dá para trocar o frame e o texto à mão
- **Reprocessar de uma etapa** — retoma de `voz`, `legendas`, `fundo` ou
  `render` reaproveitando o que já está no disco, sem gastar LLM nem TTS de
  novo. Um restart do servidor no meio de um job também retoma sozinho
- **O rascunho sobrevive à tela** — o que você digita é guardado no servidor a
  cada poucos segundos, então fechar a aba (ou trocar de máquina) não perde o
  texto

## Sincronia da legenda

Os tempos vêm do próprio sintetizador de voz, não de transcrição posterior:

- **edge-tts** expõe `WordBoundary` — timing por palavra, desvio medido de 0,085 s
- **ElevenLabs** devolve timestamps por caractere
- **fish.audio / XTTS** não devolvem timing: a narração é sintetizada frase a
  frase e cada arquivo é medido, então o erro fica confinado à frase
  (desvio medido de 0,131 s) em vez de acumular ao longo do vídeo

## Idiomas da interface

A interface inteira existe em **português, inglês, espanhol, russo e chinês**.
Ela segue o idioma do navegador na primeira visita e lembra a escolha depois; o
seletor fica no pé da barra lateral.

Trocar o idioma da interface também troca o **idioma do que sai**: o prompt do
roteirista, o gerador de ganchos, o texto de publicação e o CTA acompanham o
`job.language`, então uma interface em espanhol produz um roteiro escrito *como
um nativo escreveria* — não a tradução de um roteiro em português. Nove idiomas
são reconhecidos na saída (os cinco da interface mais francês, alemão, italiano
e japonês), e uma tag desconhecida é repassada ao modelo como está.

O layout se adapta do desktop ao celular (Android e iOS), respeitando as áreas
seguras de notch, alvos de toque de 44 px e uma gaveta de navegação deslizante.

## Instalação

Requer **Python 3.11+**, **Node 20+** e **FFmpeg**. Um comando por plataforma
instala os três, mais as dependências Python e da web, e escreve um `.env` a
partir do `.env.example`:

```bash
git clone https://github.com/JoasASantos/ShortsCreator.git
cd ShortsCreator
```

| Plataforma | Comando |
|---|---|
| Linux (apt / dnf / pacman) | `make setup` |
| macOS (Homebrew) | `make setup` |
| Windows (PowerShell) | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` e depois `.\scripts\setup.ps1` |

O `make setup` roda o `scripts/setup.sh`; no Windows o equivalente é o
`scripts/setup.ps1`, que usa winget e cai para o Chocolatey. A forma
`-Scope Process` da política de execução vale só para aquela janela — não
existe motivo para afrouxá-la na máquina inteira.

Os dois scripts podem ser rodados de novo: corrija uma coisa e rode outra vez.
O que eles não conseguiram instalar sai no fim, com o comando para fazer à mão
(no macOS como root, isso é tudo que passa pelo Homebrew — ele se recusa a
rodar como root).

Depois preencha o `.env` e suba os dois serviços:

```bash
make dev            # API em :8000, interface em :3000
```

### O que está faltando e o que isso custa

```bash
make doctor
```

Reporta cada dependência como **obrigatória** ou **opcional**, com a versão
encontrada, o que cada peça faltando destravaria e o comando exato de
instalação para o seu sistema. Falta opcional não é falha — ele diz o que cada
uma custa (sem `faster-whisper`, não dá para legendar a sua própria gravação;
sem libass no seu FFmpeg, a legenda cai para o overlay em PNG: render mais
lento, vídeo final igual). O mesmo relatório é servido em
`GET /api/system/requirements`, e o veredito dele acompanha o
`GET /api/health`.

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

Cada chamada registra qual elo respondeu e quanto demorou — visível no job e
agregado em **Desempenho**, então dá para ver se o principal está sendo
suficiente ou se a cadeia está caindo para o Codex toda hora.

### Voz

`edge-tts` é o padrão e é gratuito, com vozes neurais em vários idiomas. Para
voz de personagem: **ElevenLabs** (clonagem por API), **fish.audio** (catálogo)
ou **XTTS** local a partir de um sample de 6 a 30 segundos.

> Use apenas vozes que você tem direito de usar. Clonar a voz de uma pessoa real
> sem autorização pode violar direitos de imagem e os termos das plataformas.

## Publicação

| Plataforma | Como | Observação |
|---|---|---|
| YouTube Shorts | Data API v3, upload resumível | Agendamento nativo (`publishAt`). Capa personalizada exige canal verificado por telefone |
| TikTok | Content Posting API v2 | Sem a auditoria do app aprovada, os envios chegam na caixa de rascunhos — limitação da plataforma |
| Instagram Reels | Graph API (container → publish) | Conta profissional ligada a uma página. A Meta **baixa** o MP4, então `PUBLIC_API_URL` precisa ser alcançável da internet (ngrok, cloudflared) |
| LinkedIn | Posts API (vídeo nativo) | Token com `w_member_social`. Sem analytics de post para membros |

Publicação avulsa, agendada ou em sequência (lote). O agendamento espera cada
short terminar de renderizar e passar no QA antes de subir.

### Avisos

Telegram, Discord ou webhook genérico quando um short termina, falha ou é
publicado. Opcional — sem credencial configurada, nada é enviado.

Cada aviso sai no idioma em que o short foi feito, então uma produção em
espanhol manda alerta em espanhol. Defina `PUBLIC_WEB_URL` se você roda atrás
de um túnel: sem isso o link da mensagem aponta para `localhost` e não serve no
celular.

## Testes

```bash
make test        # backend inteiro
make test-fast   # pula o que precisa de FFmpeg
make typecheck   # tipos do frontend
make i18n        # audita os dicionários dos cinco idiomas
make check       # tudo acima
```

O `make i18n` pega o que o compilador não vê: chave copiada sem traduzir,
`{placeholder}` perdido na tradução (que apareceria literal na tela), valor
vazio. As chaves em si já são garantidas — o tipo `Dictionary` é inferido do
dicionário português, então uma chave faltando quebra a compilação.

Os testes de QA e de sincronia geram MP4 e MP3 de verdade com FFmpeg e auditam
o arquivo resultante — é o único jeito de exercitar o que o QA realmente faz.
Sem FFmpeg no PATH, esses casos são pulados em vez de falhar.

## Arquitetura

```
backend/app/
  pipeline/
    ingest.py        artigo, vídeo, repositório, imagens, roteiro
    script.py        roteiro, refinamento e ganchos alternativos por LLM
    llm.py           cadeia de modelos com fallback + telemetria
    tts.py           síntese com timing por palavra
    captions.py      legenda ASS com destaque karaokê
    overlays.py      legenda em PNG quando o FFmpeg não tem libass
    highlights.py    detecção de cena e escolha de trechos
    clipper.py       um vídeo longo → N shorts
    render.py        composição 9:16 com FFmpeg
    cover.py         escolha do melhor frame + título na capa
    qa.py            auditoria do arquivo final
    metrics.py       coleta de desempenho e briefing de realimentação
    trends.py        radar de assuntos em alta
    notify.py        Telegram, Discord, webhook
    timeline.py      modelo EDL do editor
    orchestrator.py  pipeline + laço de autoajuste + retomada
    publishers/      youtube, tiktok, instagram, linkedin
  routers/           API HTTP
  tests/             pytest (128 casos)
web/
  app/               telas Next.js
  components/        interface compartilhada
  lib/i18n/          dicionários dos 5 idiomas
```

Banco em SQLite puro, sem ORM. Fila de jobs em thread, sem broker externo.

Código, comentários e histórico de commits estão em inglês. Uma coisa fica em
português de propósito: os **prompts do LLM** em `script.py` e `clipper.py`.
São calibrados em português e suas respostas JSON usam chaves portuguesas, que
o pipeline lê por nome — o idioma da *saída* é outro assunto, controlado pelo
`job.language`.

## Licença

MIT
