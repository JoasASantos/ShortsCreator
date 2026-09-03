import type { DocsPage } from "./types";

export const docsPtBR: DocsPage = {
  title: "Instalação",
  intro: "guia rápido",
  sections: [
    {
      title: "Requisitos",
      blocks: [
        {
          kind: "ul",
          items: [
            "**Python 3.11+** — backend e pipeline de mídia",
            "**Node 20+** — interface web",
            "**FFmpeg** — composição do vídeo (obrigatório)",
            "Um LLM: a CLI do **Claude Code** ou do **Codex** já autenticada (usa a sua assinatura, sem chave), ou uma chave de API da Anthropic/OpenAI, ou Ollama local",
          ],
        },
      ],
    },
    {
      title: "1. Instalar o FFmpeg",
      blocks: [
        {
          kind: "code",
          text: `# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Windows (winget)
winget install Gyan.FFmpeg`,
        },
        {
          kind: "p",
          text: "Se o seu FFmpeg vier sem `libass`, tudo continua funcionando: o pipeline detecta a ausência e queima as legendas como imagens PNG geradas localmente.",
        },
      ],
    },
    {
      title: "2. Backend",
      blocks: [
        {
          kind: "code",
          text: `git clone <seu-repo> ShortsCreator && cd ShortsCreator
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r backend/requirements.txt
cp .env.example .env             # preencha as chaves`,
        },
      ],
    },
    {
      title: "3. Configurar o .env",
      blocks: [
        {
          kind: "p",
          text: "O caminho mais barato é usar uma assinatura que você já paga, em vez de uma chave de API cobrada por token. O backend chama a CLI local que já está autenticada:",
        },
        {
          kind: "code",
          text: `# cadeia: cai pro próximo modelo se o anterior falhar
LLM_PROVIDER=chain
LLM_CHAIN=claude_cli:claude-fable-5-1,claude_cli:claude-opus-5,codex_cli:gpt-5.6-sol

TTS_PROVIDER=edge                # gratuito, vozes neurais pt-BR
EDGE_VOICE=pt-BR-AntonioNeural`,
        },
        {
          kind: "p",
          text: "Cada elo é `provider:modelo`. Fable é o principal, Opus 5 é a reserva e o Codex (GPT-5.6 Sol) entra por último. Os dois primeiros usam Claude Pro/Max pelo Claude Code; o terceiro usa ChatGPT Plus/Pro por `codex login`. Para fixar um único modelo, troque para `LLM_PROVIDER=claude_cli` com `CLAUDE_CLI_MODEL=claude-fable-5-1`.",
        },
        {
          kind: "p",
          text: "Nenhuma chave entra no `.env` nesse modo. Confirme o login com `claude --version` ou `codex login status` — o indicador **Auth** na barra lateral mostra `assinatura`, e cada elo da cadeia aparece logo abaixo com o modelo e se está disponível.",
        },
        {
          kind: "p",
          text: "Cada chamada leva de 25 a 40 segundos porque roda um agente completo por baixo. Se preferir latência menor e não se importar em pagar por token, use o modo de chave:",
        },
        {
          kind: "code",
          text: `LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-5`,
        },
        {
          kind: "p",
          text: "Também dá para rodar totalmente offline com `LLM_PROVIDER=ollama` apontando para um modelo local.",
        },
        { kind: "p", text: "Opcionais que melhoram bastante o resultado:" },
        {
          kind: "ul",
          items: [
            "`PEXELS_API_KEY` — B-roll vertical real em vez de gradiente",
            "`ELEVENLABS_API_KEY` — voz clonada de personagem com timings exatos",
            "`YOUTUBE_CLIENT_SECRETS` e `TIKTOK_CLIENT_KEY` — publicação",
            "`TELEGRAM_BOT_TOKEN` ou `DISCORD_WEBHOOK_URL` — aviso quando um short termina ou falha",
          ],
        },
        {
          kind: "p",
          text: "Tudo isso também pode ser cadastrado pela tela **Contas**, que grava no banco e vence o `.env` — útil para não deixar chave em arquivo.",
        },
      ],
    },
    {
      title: "4. Subir os dois serviços",
      blocks: [
        {
          kind: "code",
          text: `# terminal 1 — API
make api        # ou: uvicorn app.main:app --app-dir backend --reload

# terminal 2 — interface
make web        # ou: cd web && npm install && npm run dev`,
        },
        {
          kind: "p",
          text: "Interface em `http://localhost:3000`, API em `http://localhost:8000` (documentação interativa em `/docs`).",
        },
      ],
    },
    {
      title: "5. Conectar as contas de publicação",
      blocks: [
        {
          kind: "p",
          text: "**YouTube:** no Google Cloud Console, ative a **YouTube Data API v3**, crie uma credencial OAuth do tipo **Aplicativo Web** com a URI de redirecionamento `http://localhost:8000/api/publish/youtube/callback` e salve o JSON em `data/secrets/youtube_client_secret.json`.",
        },
        {
          kind: "p",
          text: "**TikTok:** registre um app no TikTok for Developers com os escopos `video.upload`, `video.publish` e `video.list` (esse último libera as métricas). Enquanto o app não passar pela auditoria, os envios chegam na caixa de rascunhos do aplicativo — é uma limitação da plataforma, não do projeto.",
        },
        {
          kind: "p",
          text: "**Instagram Reels:** precisa de conta profissional ligada a uma página do Facebook, e de um token de longa duração com `instagram_content_publish`. Um detalhe que costuma travar: a Graph API não aceita upload, ela **baixa** o MP4 de uma URL pública. Em máquina local, suba um túnel e aponte o `PUBLIC_API_URL` para ele:",
        },
        {
          kind: "code",
          text: `cloudflared tunnel --url http://localhost:8000
# ou: ngrok http 8000
# depois, no .env:
PUBLIC_API_URL=https://seu-endereco-publico`,
        },
        {
          kind: "p",
          text: "**LinkedIn:** token com `w_member_social`. O URN do autor pode ficar vazio se o token também tiver `openid` e `profile` — nesse caso ele é descoberto sozinho.",
        },
      ],
    },
    {
      title: "6. Fechar o ciclo",
      blocks: [
        {
          kind: "p",
          text: "Depois da primeira publicação, o sistema começa a aprender com o próprio canal:",
        },
        {
          kind: "ul",
          items: [
            "**Tendências** — assuntos em alta por nicho e região, de Google Trends, Reddit, Hacker News e YouTube. Nenhuma chave necessária. Um clique já abre o formulário preenchido.",
            "**Lote** — um podcast ou aula longa vira vários shorts: transcreve, o modelo escolhe os melhores momentos e cada trecho segue o pipeline completo. Dá para agendar um por dia de uma vez.",
            "**Teste de gancho** — no short pronto, gere três ganchos com mecanismos diferentes, ouça cada um na voz do vídeo e troque. Ou crie a variante B como short próprio e publique as duas.",
            "**Desempenho** — views, likes, comentários e retenção média chegam sozinhos a cada 6 horas. Os ganchos que mais retiveram voltam para o prompt do próximo roteiro como referência de mecanismo — nunca de texto copiado.",
          ],
        },
      ],
    },
    {
      title: "Voz de personagem",
      blocks: [
        {
          kind: "p",
          text: "Duas rotas para narrar com a voz de um personagem. Na **ElevenLabs**, clone a voz no painel deles e cole o `voice_id` na tela de Vozes. No **XTTS local**, suba o servidor e envie um sample de 6 a 30 segundos — roda offline e sem custo por caractere.",
        },
        {
          kind: "code",
          text: `# XTTS local via Docker
docker run -d --name xtts -p 8020:80 \\
  ghcr.io/coqui-ai/xtts-streaming-server:latest`,
        },
      ],
    },
    {
      title: "O que o QA verifica",
      blocks: [
        {
          kind: "p",
          text: "Toda renderização passa por uma auditoria automática sobre o arquivo final — não sobre o que o pipeline acha que produziu:",
        },
        {
          kind: "ul",
          items: [
            "Resolução exata de 1080×1920 e proporção 9:16 com SAR 1:1",
            "Ausência de barras pretas (`cropdetect` sobre amostras do vídeo)",
            "H.264 em `yuv420p`, 30 fps, átomo `moov` no início",
            "Áudio AAC normalizado em −14 LUFS com pico real abaixo de −1 dBTP",
            "Silêncio no início (mata o hook) e sobra de silêncio no fim",
            "Legenda fora dos 340 px inferiores ocupados pela interface do app",
            "Cobertura de legenda ao longo do vídeo e duração dentro da janela útil",
          ],
        },
      ],
    },
    {
      title: "Refazer sem gastar assinatura",
      blocks: [
        {
          kind: "p",
          text: "O roteiro e os timings da narração ficam salvos no diretório do job. O botão **Reprocessar** abre um menu com as etapas de onde é possível retomar — escolher `legendas` reaproveita roteiro e voz e refaz só o que vem depois. Se o servidor cair no meio de uma renderização, o job retoma sozinho na volta.",
        },
      ],
    },
    {
      title: "Testes",
      blocks: [
        {
          kind: "code",
          text: `make test        # backend inteiro
make test-fast   # pula o que precisa de FFmpeg
make typecheck   # tipos do frontend
make check       # os dois`,
        },
        {
          kind: "p",
          text: "Os testes de QA e de sincronia geram MP4 e MP3 de verdade e auditam o arquivo resultante. Sem FFmpeg no PATH, esses casos são pulados em vez de falhar.",
        },
      ],
    },
    {
      title: "Problemas comuns",
      blocks: [
        {
          kind: "ul",
          items: [
            "**“yt-dlp não encontrado”** — instale com `pip install yt-dlp` dentro do mesmo ambiente virtual da API.",
            "**Vídeo sai sem legenda** — verifique se existe alguma fonte TrueType em `assets/fonts/` ou no sistema.",
            "**Sem trilha sonora** — coloque arquivos `.mp3` livres de direitos em `assets/music/`.",
            "**QA reprova por duração** — ajuste a duração alvo no formulário; o roteiro é dimensionado a partir dela.",
            "**“No space left on device”** — renderização usa bastante disco temporário. `make clean` limpa jobs, cache e saídas antigas.",
            "**Instagram recusa com erro 2207026** — o vídeo tem que estar em 9:16 e a URL pública precisa responder de fora. Teste abrindo `PUBLIC_API_URL/api/outputs/<job_id>.mp4` no celular, sem Wi-Fi.",
            "**Retenção não aparece no YouTube** — contas conectadas antes do escopo `yt-analytics.readonly` existir só trazem as estatísticas públicas. Desconecte e conecte de novo em Contas.",
            "**Métricas do TikTok vazias** — envios que caem na caixa de rascunhos não têm id de vídeo público, então não há o que medir.",
          ],
        },
      ],
    },
  ],
};
