import type { DocsPage } from "./types";

export const docsEs: DocsPage = {
  title: "Instalación",
  intro: "guía rápida",
  sections: [
    {
      title: "Requisitos",
      blocks: [
        {
          kind: "ul",
          items: [
            "**Python 3.11+** — backend y pipeline de medios",
            "**Node 20+** — interfaz web",
            "**FFmpeg** — composición del vídeo (obligatorio)",
            "Un LLM: la CLI de **Claude Code** o de **Codex** ya autenticada (usa tu suscripción, sin clave), o una clave de API de Anthropic/OpenAI, u Ollama local",
          ],
        },
      ],
    },
    {
      title: "1. Instalar FFmpeg",
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
          text: "Si tu FFmpeg viene sin `libass`, todo sigue funcionando: el pipeline detecta la ausencia y quema los subtítulos como imágenes PNG generadas en local.",
        },
      ],
    },
    {
      title: "2. Backend",
      blocks: [
        {
          kind: "code",
          text: `git clone <tu-repo> ShortsCreator && cd ShortsCreator
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r backend/requirements.txt
cp .env.example .env             # rellena las claves`,
        },
      ],
    },
    {
      title: "3. Configurar el .env",
      blocks: [
        {
          kind: "p",
          text: "El camino más barato es usar una suscripción que ya pagas, en vez de una clave de API cobrada por token. El backend llama a la CLI local que ya está autenticada:",
        },
        {
          kind: "code",
          text: `# cadena: pasa al modelo siguiente si el anterior falla
LLM_PROVIDER=chain
LLM_CHAIN=claude_cli:claude-fable-5-1,claude_cli:claude-opus-5,codex_cli:gpt-5.6-sol

TTS_PROVIDER=edge                # gratuito, voces neuronales pt-BR
EDGE_VOICE=pt-BR-AntonioNeural`,
        },
        {
          kind: "p",
          text: "Cada eslabón es `provider:modelo`. Fable es el principal, Opus 5 es el de reserva y Codex (GPT-5.6 Sol) entra al final. Los dos primeros usan Claude Pro/Max a través de Claude Code; el tercero usa ChatGPT Plus/Pro con `codex login`. Para fijar un único modelo, cambia a `LLM_PROVIDER=claude_cli` con `CLAUDE_CLI_MODEL=claude-fable-5-1`.",
        },
        {
          kind: "p",
          text: "En este modo no entra ninguna clave en el `.env`. Confirma la sesión con `claude --version` o `codex login status` — el indicador **Auth** de la barra lateral muestra `suscripción`, y cada eslabón de la cadena aparece justo debajo con su modelo y si está disponible.",
        },
        {
          kind: "p",
          text: "Cada llamada tarda entre 25 y 40 segundos porque por debajo ejecuta un agente completo. Si prefieres menos latencia y no te importa pagar por token, usa el modo de clave:",
        },
        {
          kind: "code",
          text: `LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-5`,
        },
        {
          kind: "p",
          text: "También se puede ejecutar totalmente offline con `LLM_PROVIDER=ollama` apuntando a un modelo local.",
        },
        { kind: "p", text: "Opcionales que mejoran bastante el resultado:" },
        {
          kind: "ul",
          items: [
            "`PEXELS_API_KEY` — B-roll vertical real en lugar de un degradado",
            "`ELEVENLABS_API_KEY` — voz clonada de personaje con timings exactos",
            "`YOUTUBE_CLIENT_SECRETS` y `TIKTOK_CLIENT_KEY` — publicación",
            "`TELEGRAM_BOT_TOKEN` o `DISCORD_WEBHOOK_URL` — aviso cuando un short termina o falla",
          ],
        },
        {
          kind: "p",
          text: "Todo esto también se puede registrar desde la pantalla **Cuentas**, que guarda en la base de datos y tiene prioridad sobre el `.env` — útil para no dejar claves en un archivo.",
        },
      ],
    },
    {
      title: "4. Levantar los dos servicios",
      blocks: [
        {
          kind: "code",
          text: `# terminal 1 — API
make api        # o: uvicorn app.main:app --app-dir backend --reload

# terminal 2 — interfaz
make web        # o: cd web && npm install && npm run dev`,
        },
        {
          kind: "p",
          text: "Interfaz en `http://localhost:3000`, API en `http://localhost:8000` (documentación interactiva en `/docs`).",
        },
      ],
    },
    {
      title: "5. Conectar las cuentas de publicación",
      blocks: [
        {
          kind: "p",
          text: "**YouTube:** en la Google Cloud Console, activa la **YouTube Data API v3**, crea una credencial OAuth de tipo **Aplicación web** con la URI de redirección `http://localhost:8000/api/publish/youtube/callback` y guarda el JSON en `data/secrets/youtube_client_secret.json`.",
        },
        {
          kind: "p",
          text: "**TikTok:** registra una app en TikTok for Developers con los permisos `video.upload`, `video.publish` y `video.list` (este último habilita las métricas). Mientras la app no pase la auditoría, los envíos llegan a la bandeja de borradores de la aplicación — es una limitación de la plataforma, no del proyecto.",
        },
        {
          kind: "p",
          text: "**Instagram Reels:** necesita una cuenta profesional vinculada a una página de Facebook y un token de larga duración con `instagram_content_publish`. Un detalle que suele atascar: la Graph API no acepta subidas, ella **descarga** el MP4 desde una URL pública. En una máquina local, levanta un túnel y apunta `PUBLIC_API_URL` hacia él:",
        },
        {
          kind: "code",
          text: `cloudflared tunnel --url http://localhost:8000
# o: ngrok http 8000
# después, en el .env:
PUBLIC_API_URL=https://tu-direccion-publica`,
        },
        {
          kind: "p",
          text: "**LinkedIn:** token con `w_member_social`. El URN del autor puede quedar vacío si el token también tiene `openid` y `profile` — en ese caso se descubre solo.",
        },
      ],
    },
    {
      title: "6. Cerrar el ciclo",
      blocks: [
        {
          kind: "p",
          text: "Después de la primera publicación, el sistema empieza a aprender de tu propio canal:",
        },
        {
          kind: "ul",
          items: [
            "**Tendencias** — temas al alza por nicho y región, de Google Trends, Reddit, Hacker News y YouTube. No hace falta ninguna clave. Un clic abre el formulario ya rellenado.",
            "**Lote** — un podcast o una clase larga se convierte en varios shorts: transcribe, el modelo elige los mejores momentos y cada fragmento pasa por el pipeline completo. Se puede programar uno por día de una sola vez.",
            "**Prueba de gancho** — en el short terminado, genera tres ganchos con mecanismos distintos, escucha cada uno con la voz del vídeo y cámbialo. O crea la variante B como short propio y publica las dos.",
            "**Rendimiento** — vistas, likes, comentarios y retención media llegan solos cada 6 horas. Los ganchos que más retuvieron vuelven al prompt del siguiente guion como referencia de mecanismo — nunca de texto copiado.",
          ],
        },
      ],
    },
    {
      title: "Voz de personaje",
      blocks: [
        {
          kind: "p",
          text: "Dos rutas para narrar con la voz de un personaje. En **ElevenLabs**, clona la voz en su panel y pega el `voice_id` en la pantalla de Voces. En **XTTS local**, levanta el servidor y envía una muestra de 6 a 30 segundos — funciona offline y sin coste por carácter.",
        },
        {
          kind: "code",
          text: `# XTTS local con Docker
docker run -d --name xtts -p 8020:80 \\
  ghcr.io/coqui-ai/xtts-streaming-server:latest`,
        },
      ],
    },
    {
      title: "Qué verifica el QA",
      blocks: [
        {
          kind: "p",
          text: "Cada renderizado pasa por una auditoría automática sobre el archivo final — no sobre lo que el pipeline cree que produjo:",
        },
        {
          kind: "ul",
          items: [
            "Resolución exacta de 1080×1920 y proporción 9:16 con SAR 1:1",
            "Ausencia de barras negras (`cropdetect` sobre muestras del vídeo)",
            "H.264 en `yuv420p`, 30 fps, átomo `moov` al principio",
            "Audio AAC normalizado a −14 LUFS con pico real por debajo de −1 dBTP",
            "Silencio al principio (mata el gancho) y silencio de sobra al final",
            "Subtítulos fuera de los 340 px inferiores que ocupa la interfaz de la app",
            "Cobertura de subtítulos a lo largo del vídeo y duración dentro de la ventana útil",
          ],
        },
      ],
    },
    {
      title: "Rehacer sin gastar suscripción",
      blocks: [
        {
          kind: "p",
          text: "El guion y los timings de la narración quedan guardados en el directorio del job. El botón **Reprocesar** abre un menú con las etapas desde las que se puede retomar — elegir `subtítulos` reaprovecha guion y voz y rehace solo lo que viene después. Si el servidor se cae en medio de un renderizado, el job se retoma solo al volver.",
        },
      ],
    },
    {
      title: "Pruebas",
      blocks: [
        {
          kind: "code",
          text: `make test        # backend completo
make test-fast   # salta lo que necesita FFmpeg
make typecheck   # tipos del frontend
make check       # los dos`,
        },
        {
          kind: "p",
          text: "Las pruebas de QA y de sincronía generan MP4 y MP3 reales y auditan el archivo resultante. Sin FFmpeg en el PATH, esos casos se saltan en vez de fallar.",
        },
      ],
    },
    {
      title: "Problemas comunes",
      blocks: [
        {
          kind: "ul",
          items: [
            "**«yt-dlp no encontrado»** — instálalo con `pip install yt-dlp` dentro del mismo entorno virtual de la API.",
            "**El vídeo sale sin subtítulos** — comprueba que exista alguna fuente TrueType en `assets/fonts/` o en el sistema.",
            "**Sin banda sonora** — pon archivos `.mp3` libres de derechos en `assets/music/`.",
            "**El QA suspende por duración** — ajusta la duración objetivo en el formulario; el guion se dimensiona a partir de ella.",
            "**«No space left on device»** — el renderizado usa bastante disco temporal. `make clean` limpia jobs, caché y salidas antiguas.",
            "**Instagram rechaza con el error 2207026** — el vídeo tiene que estar en 9:16 y la URL pública debe responder desde fuera. Pruébalo abriendo `PUBLIC_API_URL/api/outputs/<job_id>.mp4` en el móvil, sin Wi-Fi.",
            "**La retención no aparece en YouTube** — las cuentas conectadas antes de que existiera el permiso `yt-analytics.readonly` solo traen las estadísticas públicas. Desconéctala y conéctala de nuevo en Cuentas.",
            "**Métricas de TikTok vacías** — los envíos que caen en la bandeja de borradores no tienen id de vídeo público, así que no hay nada que medir.",
          ],
        },
      ],
    },
  ],
};
