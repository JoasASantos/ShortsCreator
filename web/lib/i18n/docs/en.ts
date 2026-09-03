import type { DocsPage } from "./types";

export const docsEn: DocsPage = {
  title: "Install",
  intro: "quick guide",
  sections: [
    {
      title: "Requirements",
      blocks: [
        {
          kind: "ul",
          items: [
            "**Python 3.11+** — backend and media pipeline",
            "**Node 20+** — web interface",
            "**FFmpeg** — video composition (required)",
            "An LLM: the **Claude Code** or **Codex** CLI already signed in (uses your subscription, no key), or an Anthropic/OpenAI API key, or a local Ollama",
          ],
        },
      ],
    },
    {
      title: "1. Install FFmpeg",
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
          text: "If your FFmpeg comes without `libass`, everything still works: the pipeline detects the absence and burns the captions in as PNG images generated locally.",
        },
      ],
    },
    {
      title: "2. Backend",
      blocks: [
        {
          kind: "code",
          text: `git clone <your-repo> ShortsCreator && cd ShortsCreator
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r backend/requirements.txt
cp .env.example .env             # fill in the keys`,
        },
      ],
    },
    {
      title: "3. Configure .env",
      blocks: [
        {
          kind: "p",
          text: "The cheapest path is to use a subscription you already pay for instead of a per-token API key. The backend calls the local CLI that is already signed in:",
        },
        {
          kind: "code",
          text: `# chain: falls through to the next model when the previous one fails
LLM_PROVIDER=chain
LLM_CHAIN=claude_cli:claude-fable-5-1,claude_cli:claude-opus-5,codex_cli:gpt-5.6-sol

TTS_PROVIDER=edge                # free, neural pt-BR voices
EDGE_VOICE=pt-BR-AntonioNeural`,
        },
        {
          kind: "p",
          text: "Each link is `provider:model`. Fable is the primary, Opus 5 is the fallback and Codex (GPT-5.6 Sol) comes last. The first two use Claude Pro/Max through Claude Code; the third uses ChatGPT Plus/Pro through `codex login`. To pin a single model, switch to `LLM_PROVIDER=claude_cli` with `CLAUDE_CLI_MODEL=claude-fable-5-1`.",
        },
        {
          kind: "p",
          text: "No key goes into `.env` in this mode. Confirm the login with `claude --version` or `codex login status` — the **Auth** indicator in the sidebar shows `subscription`, and each link of the chain appears right below it with its model and whether it is available.",
        },
        {
          kind: "p",
          text: "Each call takes 25 to 40 seconds because it runs a full agent underneath. If you prefer lower latency and do not mind paying per token, use key mode:",
        },
        {
          kind: "code",
          text: `LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-5`,
        },
        {
          kind: "p",
          text: "You can also run fully offline with `LLM_PROVIDER=ollama` pointing at a local model.",
        },
        { kind: "p", text: "Optionals that improve the result a lot:" },
        {
          kind: "ul",
          items: [
            "`PEXELS_API_KEY` — real vertical B-roll instead of a gradient",
            "`ELEVENLABS_API_KEY` — cloned character voice with exact timings",
            "`YOUTUBE_CLIENT_SECRETS` and `TIKTOK_CLIENT_KEY` — publishing",
            "`TELEGRAM_BOT_TOKEN` or `DISCORD_WEBHOOK_URL` — a notice when a short finishes or fails",
          ],
        },
        {
          kind: "p",
          text: "All of this can also be registered from the **Accounts** screen, which writes to the database and overrides `.env` — handy for keeping keys out of a file.",
        },
      ],
    },
    {
      title: "4. Start both services",
      blocks: [
        {
          kind: "code",
          text: `# terminal 1 — API
make api        # or: uvicorn app.main:app --app-dir backend --reload

# terminal 2 — interface
make web        # or: cd web && npm install && npm run dev`,
        },
        {
          kind: "p",
          text: "Interface on `http://localhost:3000`, API on `http://localhost:8000` (interactive documentation on `/docs`).",
        },
      ],
    },
    {
      title: "5. Connect the publishing accounts",
      blocks: [
        {
          kind: "p",
          text: "**YouTube:** in the Google Cloud Console, enable the **YouTube Data API v3**, create an OAuth credential of type **Web application** with the redirect URI `http://localhost:8000/api/publish/youtube/callback` and save the JSON to `data/secrets/youtube_client_secret.json`.",
        },
        {
          kind: "p",
          text: "**TikTok:** register an app on TikTok for Developers with the `video.upload`, `video.publish` and `video.list` scopes (the last one unlocks the metrics). Until the app passes the audit, uploads land in the app's drafts inbox — a platform limitation, not a project one.",
        },
        {
          kind: "p",
          text: "**Instagram Reels:** needs a professional account linked to a Facebook page, and a long-lived token with `instagram_content_publish`. One detail that usually blocks people: the Graph API does not accept an upload, it **downloads** the MP4 from a public URL. On a local machine, bring up a tunnel and point `PUBLIC_API_URL` at it:",
        },
        {
          kind: "code",
          text: `cloudflared tunnel --url http://localhost:8000
# or: ngrok http 8000
# then, in .env:
PUBLIC_API_URL=https://your-public-address`,
        },
        {
          kind: "p",
          text: "**LinkedIn:** a token with `w_member_social`. The author URN can be left empty if the token also has `openid` and `profile` — in that case it is discovered on its own.",
        },
      ],
    },
    {
      title: "6. Close the loop",
      blocks: [
        {
          kind: "p",
          text: "After the first publish, the system starts learning from your own channel:",
        },
        {
          kind: "ul",
          items: [
            "**Trends** — trending topics by niche and region, from Google Trends, Reddit, Hacker News and YouTube. No key needed. One click opens the form already filled in.",
            "**Batch** — a podcast or a long lecture becomes several shorts: it transcribes, the model picks the strongest moments and each segment goes through the full pipeline. You can schedule one a day in a single go.",
            "**Hook test** — on a finished short, generate three hooks with different mechanisms, listen to each one in the video's own voice and swap it in. Or create variant B as a short of its own and publish both.",
            "**Performance** — views, likes, comments and average retention arrive on their own every 6 hours. The hooks that retained best go into the next script's prompt as a reference for the mechanism — never as copied wording.",
          ],
        },
      ],
    },
    {
      title: "Character voice",
      blocks: [
        {
          kind: "p",
          text: "Two routes for narrating with a character's voice. On **ElevenLabs**, clone the voice in their dashboard and paste the `voice_id` into the Voices screen. On **local XTTS**, bring up the server and send a 6 to 30 second sample — it runs offline with no per-character cost.",
        },
        {
          kind: "code",
          text: `# local XTTS via Docker
docker run -d --name xtts -p 8020:80 \\
  ghcr.io/coqui-ai/xtts-streaming-server:latest`,
        },
      ],
    },
    {
      title: "What the QA checks",
      blocks: [
        {
          kind: "p",
          text: "Every render goes through an automatic audit against the final file — not against what the pipeline believes it produced:",
        },
        {
          kind: "ul",
          items: [
            "Exact 1080×1920 resolution and 9:16 aspect ratio with 1:1 SAR",
            "No black bars (`cropdetect` over samples of the video)",
            "H.264 in `yuv420p`, 30 fps, `moov` atom at the front",
            "AAC audio normalized to −14 LUFS with true peak below −1 dBTP",
            "Silence at the start (which kills the hook) and dead air at the end",
            "Captions outside the bottom 340 px covered by the app's interface",
            "Caption coverage across the video and duration inside the usable window",
          ],
        },
      ],
    },
    {
      title: "Redo without spending subscription",
      blocks: [
        {
          kind: "p",
          text: "The script and the narration timings stay saved in the job's directory. The **Reprocess** button opens a menu with the stages you can resume from — picking `captions` reuses the script and the voice and redoes only what comes after. If the server goes down in the middle of a render, the job resumes on its own when it comes back.",
        },
      ],
    },
    {
      title: "Tests",
      blocks: [
        {
          kind: "code",
          text: `make test        # whole backend
make test-fast   # skips what needs FFmpeg
make typecheck   # frontend types
make check       # both`,
        },
        {
          kind: "p",
          text: "The QA and sync tests generate real MP4 and MP3 files and audit the resulting file. Without FFmpeg on PATH, those cases are skipped instead of failing.",
        },
      ],
    },
    {
      title: "Common problems",
      blocks: [
        {
          kind: "ul",
          items: [
            "**“yt-dlp not found”** — install it with `pip install yt-dlp` inside the same virtual environment as the API.",
            "**Video comes out with no captions** — check that there is some TrueType font in `assets/fonts/` or on the system.",
            "**No soundtrack** — put royalty-free `.mp3` files in `assets/music/`.",
            "**QA fails on duration** — adjust the target duration in the form; the script is sized from it.",
            "**“No space left on device”** — rendering uses a lot of temporary disk. `make clean` clears old jobs, cache and outputs.",
            "**Instagram rejects with error 2207026** — the video has to be 9:16 and the public URL has to answer from outside. Test it by opening `PUBLIC_API_URL/api/outputs/<job_id>.mp4` on your phone, off Wi-Fi.",
            "**Retention does not show up on YouTube** — accounts connected before the `yt-analytics.readonly` scope existed only bring the public statistics. Disconnect and connect again under Accounts.",
            "**Empty TikTok metrics** — uploads that land in the drafts inbox have no public video id, so there is nothing to measure.",
          ],
        },
      ],
    },
  ],
};
