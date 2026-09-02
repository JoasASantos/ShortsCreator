import { Topbar } from "@/components/ui";

export default function Docs() {
  return (
    <>
      <Topbar title="Instalação">
        <span className="label">guia rápido</span>
      </Topbar>

      <div className="content">
        <div className="prose">
          <h3>Requisitos</h3>
          <ul>
            <li><b>Python 3.11+</b> — backend e pipeline de mídia</li>
            <li><b>Node 20+</b> — interface web</li>
            <li><b>FFmpeg</b> — composição do vídeo (obrigatório)</li>
            <li>
              Um LLM: a CLI do <b>Claude Code</b> ou do <b>Codex</b> já autenticada (usa a sua
              assinatura, sem chave), ou uma chave de API da Anthropic/OpenAI, ou Ollama local
            </li>
          </ul>

          <h3>1. Instalar o FFmpeg</h3>
          <pre><code>{`# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Windows (winget)
winget install Gyan.FFmpeg`}</code></pre>
          <p>
            Se o seu FFmpeg vier sem <code>libass</code>, tudo continua funcionando: o pipeline
            detecta a ausência e queima as legendas como imagens PNG geradas localmente.
          </p>

          <h3>2. Backend</h3>
          <pre><code>{`git clone <seu-repo> ShortsCreator && cd ShortsCreator
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r backend/requirements.txt
cp .env.example .env             # preencha as chaves`}</code></pre>

          <h3>3. Configurar o .env</h3>
          <p>
            O caminho mais barato é usar uma assinatura que você já paga, em vez de uma chave de
            API cobrada por token. O backend chama a CLI local que já está autenticada:
          </p>
          <pre><code>{`# cadeia: cai pro próximo modelo se o anterior falhar
LLM_PROVIDER=chain
LLM_CHAIN=claude_cli:claude-fable-5-1,claude_cli:claude-opus-5,codex_cli:gpt-5.6-sol

TTS_PROVIDER=edge                # gratuito, vozes neurais pt-BR
EDGE_VOICE=pt-BR-AntonioNeural`}</code></pre>
          <p>
            Cada elo é <code>provider:modelo</code>. Fable é o principal, Opus 5 é a reserva e o
            Codex (GPT-5.6 Sol) entra por último. Os dois primeiros usam Claude Pro/Max pelo Claude
            Code; o terceiro usa ChatGPT Plus/Pro por <code>codex login</code>. Para fixar um único
            modelo, troque para <code>LLM_PROVIDER=claude_cli</code> com{" "}
            <code>CLAUDE_CLI_MODEL=claude-fable-5-1</code>.
          </p>
          <p>
            Nenhuma chave entra no <code>.env</code> nesse modo. Confirme o login com{" "}
            <code>claude --version</code> ou <code>codex login status</code> — o indicador{" "}
            <b>Auth</b> na barra lateral mostra <code>assinatura</code>, e cada elo da cadeia
            aparece logo abaixo com o modelo e se está disponível.
          </p>
          <p>
            Cada chamada leva de 25 a 40 segundos porque roda um agente completo por baixo. Se
            preferir latência menor e não se importar em pagar por token, use o modo de chave:
          </p>
          <pre><code>{`LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-5`}</code></pre>
          <p>
            Também dá para rodar totalmente offline com <code>LLM_PROVIDER=ollama</code> apontando
            para um modelo local.
          </p>
          <p>Opcionais que melhoram bastante o resultado:</p>
          <ul>
            <li><code>PEXELS_API_KEY</code> — B-roll vertical real em vez de gradiente</li>
            <li><code>ELEVENLABS_API_KEY</code> — voz clonada de personagem com timings exatos</li>
            <li><code>YOUTUBE_CLIENT_SECRETS</code> e <code>TIKTOK_CLIENT_KEY</code> — publicação</li>
            <li><code>TELEGRAM_BOT_TOKEN</code> ou <code>DISCORD_WEBHOOK_URL</code> — aviso quando
              um short termina ou falha</li>
          </ul>
          <p>
            Tudo isso também pode ser cadastrado pela tela <b>Contas</b>, que grava no banco e
            vence o <code>.env</code> — útil para não deixar chave em arquivo.
          </p>

          <h3>4. Subir os dois serviços</h3>
          <pre><code>{`# terminal 1 — API
make api        # ou: uvicorn app.main:app --app-dir backend --reload

# terminal 2 — interface
make web        # ou: cd web && npm install && npm run dev`}</code></pre>
          <p>
            Interface em <code>http://localhost:3000</code>, API em <code>http://localhost:8000</code>{" "}
            (documentação interativa em <code>/docs</code>).
          </p>

          <h3>5. Conectar as contas de publicação</h3>
          <p><b>YouTube:</b> no Google Cloud Console, ative a <i>YouTube Data API v3</i>, crie uma
            credencial OAuth do tipo <i>Aplicativo Web</i> com a URI de redirecionamento{" "}
            <code>http://localhost:8000/api/publish/youtube/callback</code> e salve o JSON em{" "}
            <code>data/secrets/youtube_client_secret.json</code>.</p>
          <p><b>TikTok:</b> registre um app no TikTok for Developers com os escopos{" "}
            <code>video.upload</code>, <code>video.publish</code> e <code>video.list</code> (esse
            último libera as métricas). Enquanto o app não passar pela auditoria, os envios chegam
            na caixa de rascunhos do aplicativo — é uma limitação da plataforma, não do projeto.</p>
          <p><b>Instagram Reels:</b> precisa de conta profissional ligada a uma página do Facebook,
            e de um token de longa duração com <code>instagram_content_publish</code>. Um detalhe
            que costuma travar: a Graph API não aceita upload, ela <b>baixa</b> o MP4 de uma URL
            pública. Em máquina local, suba um túnel e aponte o <code>PUBLIC_API_URL</code> para
            ele:</p>
          <pre><code>{`cloudflared tunnel --url http://localhost:8000
# ou: ngrok http 8000
# depois, no .env:
PUBLIC_API_URL=https://seu-endereco-publico`}</code></pre>
          <p><b>LinkedIn:</b> token com <code>w_member_social</code>. O URN do autor pode ficar
            vazio se o token também tiver <code>openid</code> e <code>profile</code> — nesse caso
            ele é descoberto sozinho.</p>

          <h3>6. Fechar o ciclo</h3>
          <p>
            Depois da primeira publicação, o sistema começa a aprender com o próprio canal:
          </p>
          <ul>
            <li><b>Tendências</b> — assuntos em alta por nicho e região, de Google Trends, Reddit,
              Hacker News e YouTube. Nenhuma chave necessária. Um clique já abre o formulário
              preenchido.</li>
            <li><b>Lote</b> — um podcast ou aula longa vira vários shorts: transcreve, o modelo
              escolhe os melhores momentos e cada trecho segue o pipeline completo. Dá para
              agendar um por dia de uma vez.</li>
            <li><b>Teste de gancho</b> — no short pronto, gere três ganchos com mecanismos
              diferentes, ouça cada um na voz do vídeo e troque. Ou crie a variante B como short
              próprio e publique as duas.</li>
            <li><b>Desempenho</b> — views, likes, comentários e retenção média chegam sozinhos a
              cada 6 horas. Os ganchos que mais retiveram voltam para o prompt do próximo
              roteiro como referência de mecanismo — nunca de texto copiado.</li>
          </ul>

          <h3>Voz de personagem</h3>
          <p>
            Duas rotas para narrar com a voz de um personagem. Na <b>ElevenLabs</b>, clone a voz no
            painel deles e cole o <code>voice_id</code> na tela de Vozes. No <b>XTTS local</b>, suba o
            servidor e envie um sample de 6 a 30 segundos — roda offline e sem custo por caractere.
          </p>
          <pre><code>{`# XTTS local via Docker
docker run -d --name xtts -p 8020:80 \\
  ghcr.io/coqui-ai/xtts-streaming-server:latest`}</code></pre>

          <h3>O que o QA verifica</h3>
          <p>
            Toda renderização passa por uma auditoria automática sobre o arquivo final — não sobre o
            que o pipeline acha que produziu:
          </p>
          <ul>
            <li>Resolução exata de 1080×1920 e proporção 9:16 com SAR 1:1</li>
            <li>Ausência de barras pretas (<code>cropdetect</code> sobre amostras do vídeo)</li>
            <li>H.264 em <code>yuv420p</code>, 30 fps, átomo <code>moov</code> no início</li>
            <li>Áudio AAC normalizado em −14 LUFS com pico real abaixo de −1 dBTP</li>
            <li>Silêncio no início (mata o hook) e sobra de silêncio no fim</li>
            <li>Legenda fora dos 340 px inferiores ocupados pela interface do app</li>
            <li>Cobertura de legenda ao longo do vídeo e duração dentro da janela útil</li>
          </ul>

          <h3>Refazer sem gastar assinatura</h3>
          <p>
            O roteiro e os timings da narração ficam salvos no diretório do job. O botão{" "}
            <b>Reprocessar</b> abre um menu com as etapas de onde é possível retomar — escolher{" "}
            <code>legendas</code> reaproveita roteiro e voz e refaz só o que vem depois. Se o
            servidor cair no meio de uma renderização, o job retoma sozinho na volta.
          </p>

          <h3>Testes</h3>
          <pre><code>{`make test        # backend inteiro
make test-fast   # pula o que precisa de FFmpeg
make typecheck   # tipos do frontend
make check       # os dois`}</code></pre>
          <p>
            Os testes de QA e de sincronia geram MP4 e MP3 de verdade e auditam o arquivo
            resultante. Sem FFmpeg no PATH, esses casos são pulados em vez de falhar.
          </p>

          <h3>Problemas comuns</h3>
          <ul>
            <li>
              <b>&quot;yt-dlp não encontrado&quot;</b> — instale com <code>pip install yt-dlp</code>{" "}
              dentro do mesmo ambiente virtual da API.
            </li>
            <li>
              <b>Vídeo sai sem legenda</b> — verifique se existe alguma fonte TrueType em{" "}
              <code>assets/fonts/</code> ou no sistema.
            </li>
            <li>
              <b>Sem trilha sonora</b> — coloque arquivos <code>.mp3</code> livres de direitos em{" "}
              <code>assets/music/</code>.
            </li>
            <li>
              <b>QA reprova por duração</b> — ajuste a duração alvo no formulário; o roteiro é
              dimensionado a partir dela.
            </li>
            <li>
              <b>&quot;No space left on device&quot;</b> — renderização usa bastante disco
              temporário. <code>make clean</code> limpa jobs, cache e saídas antigas.
            </li>
            <li>
              <b>Instagram recusa com erro 2207026</b> — o vídeo tem que estar em 9:16 e a URL
              pública precisa responder de fora. Teste abrindo{" "}
              <code>PUBLIC_API_URL/api/outputs/&lt;job_id&gt;.mp4</code> no celular, sem Wi-Fi.
            </li>
            <li>
              <b>Retenção não aparece no YouTube</b> — contas conectadas antes do escopo{" "}
              <code>yt-analytics.readonly</code> existir só trazem as estatísticas públicas.
              Desconecte e conecte de novo em Contas.
            </li>
            <li>
              <b>Métricas do TikTok vazias</b> — envios que caem na caixa de rascunhos não têm id
              de vídeo público, então não há o que medir.
            </li>
          </ul>
        </div>
      </div>
    </>
  );
}
