# ShortsCreator

Turns a link, a topic, a repository, photos or a long video into 9:16 vertical
videos ready for YouTube Shorts, TikTok, Instagram Reels and LinkedIn — with
narration, synced karaoke captions, automatic format auditing, scheduled
publishing and performance feedback that loops back into the scriptwriter.

![output](https://img.shields.io/badge/output-1080%C3%971920%20%C2%B7%209%3A16-ffc400)
![tests](https://img.shields.io/badge/tests-353%20passing-35d67f)
![i18n](https://img.shields.io/badge/i18n-PT%20%C2%B7%20EN%20%C2%B7%20ES%20%C2%B7%20RU%20%C2%B7%20ZH-35d6e8)

**🇧🇷 [Leia em português](README-PT-BR.md)**

## What it does

**Feed it** one of these:

| Source | What happens |
|---|---|
| Topic | Writes the script from scratch |
| Article link | Extracts the page text and turns it into a script |
| Video link or file | Downloads, transcribes and narrates over it — or condenses a whole episode into 60s |
| Several videos | Spreads the segments across them, proportional to each one's length |
| GitHub repository | Clones it, reads the README and the code, and narrates the architecture with the code scrolling on screen |
| Images | Narration over the photos with slow zoom and pan (Ken Burns) |
| Finished script | Narrates your text without going through AI |

**You get** an audited 1080×1920 MP4 with burned-in captions, mixed music, a
composed cover and post copy ready for each platform.

One long video can become **several** shorts at once: the **Batch** screen
transcribes it, the model picks the strongest moments and each segment becomes
an independent short — scheduled in sequence if you want one a day.

## The loop closes

What gets published comes back as data, and the data comes back as script:

1. **Trends** — Google Trends, Reddit rising, Hacker News and YouTube trending,
   by niche and region. One click turns a topic into a short.
2. **A/B hooks** — three alternatives for the same script, each using a
   different mechanism (question, number, contradiction, promise), with an
   audio preview in the video's own voice. Swap in place or publish both.
3. **Performance** — views, likes, comments, shares and average retention
   arrive on their own every 6 hours (YouTube Analytics, TikTok, Instagram).
4. **Feedback** — the hooks that retained best on *your* channel go into the
   next script's prompt as a reference for the *mechanism*, never the wording.

## Automatic QA

Every render is audited against the **final file**, not against what the
pipeline believes it produced:

- 1080×1920 resolution, 9:16 aspect ratio and 1:1 SAR
- No black bars (`cropdetect`) and no visual gaps (`blackdetect`)
- H.264 in `yuv420p`, 30 fps, `moov` atom at the front
- AAC audio normalized to −14 LUFS with true peak below −1 dBTP
- Silence at the start (which kills the hook) and dead air at the end
- Captions outside the bottom 340 px covered by the app's interface
- Caption coverage and duration inside the usable window

If it fails and auto-fix is on, the system **corrects and re-renders itself** —
stretching the script, moving the caption, trimming the silence, lowering the
music — until it passes or runs out of attempts. Every correction is logged.

## Editing after the fact

- **Script editor** — rewrite segments, reorder them, change voice and music
- **Prompt** — "make the hook more aggressive", "add a figure about the price":
  the AI rewrites without losing the rest
- **Timeline** — cut, move and stretch clips, audio and captions, with a video
  monitor synced to the playhead. Recompiles with FFmpeg without going through
  the LLM or the TTS again
- **Cover** — picks the frame with the most visual detail on its own and
  composes the title over it; the frame and the text can be changed by hand
- **Resume from a stage** — restart from `voice`, `captions`, `background` or
  `render` reusing what is already on disk, spending no LLM or TTS credit. A
  server restart in the middle of a job also resumes on its own
- **Drafts survive the screen** — what you type is saved server-side every few
  seconds, so closing the tab (or switching machines) does not lose the text

## Caption sync

Timings come from the speech synthesizer itself, not from a later transcription:

- **edge-tts** emits `WordBoundary` — per-word timing, measured drift of 0.085 s
- **ElevenLabs** returns per-character timestamps
- **fish.audio / XTTS** return no timing: narration is synthesized sentence by
  sentence and each file is measured, so the error stays confined to the
  sentence (measured drift of 0.131 s) instead of accumulating across the video

## Interface languages

The whole interface ships in **Portuguese, English, Spanish, Russian and
Chinese**. It follows the browser language on first load and remembers the
choice after that; the picker sits at the bottom of the sidebar.

Switching the interface also switches the **language of the output**: the
scriptwriter prompt, the hook generator, the post copy and the CTA all follow
`job.language`, so a Spanish UI produces a script written *as a native speaker
would write it* — not a translation of a Portuguese one. Nine languages are
recognized for output (the five UI ones plus French, German, Italian and
Japanese) and an unknown tag is passed through to the model as-is.

Layout adapts from desktop down to phones (Android and iOS), including notch
safe areas, 44 px touch targets and a slide-in navigation drawer.

## Install

Requires **Python 3.11+**, **Node 20+** and **FFmpeg**. One command per
platform installs all three, plus the Python and web dependencies, and writes
a `.env` from `.env.example`:

```bash
git clone https://github.com/JoasASantos/ShortsCreator.git
cd ShortsCreator
```

| Platform | Command |
|---|---|
| Linux (apt / dnf / pacman) | `make setup` |
| macOS (Homebrew) | `make setup` |
| Windows (PowerShell) | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` then `.\scripts\setup.ps1` |

`make setup` runs `scripts/setup.sh`; on Windows the equivalent is
`scripts/setup.ps1`, which uses winget and falls back to Chocolatey. The
`-Scope Process` form of the execution policy lasts only for that window —
there is no reason to loosen it machine-wide.

Both scripts are re-runnable: fix one thing, run again. Anything they could
not install is printed at the end with the command to do it by hand (on macOS
as root, that is everything Homebrew touches — Homebrew refuses to run as
root).

Then fill in `.env` and start both services:

```bash
make dev            # API on :8000, interface on :3000
```

### What is missing and why it matters

```bash
make doctor
```

Reports every dependency as **required** or **optional**, with the version
found, what each missing piece would unlock and the exact install command for
your OS. Optional gaps are not failures — it tells you what each one costs
(no `faster-whisper`, no captions on your own recordings; no libass in your
FFmpeg build, and captions fall back to PNG overlays: slower render, same
finished video). The same report is served at `GET /api/system/requirements`,
and its verdict rides along in `GET /api/health`.

### Language model

The cheapest path uses a subscription you already pay for instead of a
per-token key — the backend calls the local CLI that is already signed in.

Choosing a model is one name:

```env
LLM_MODEL=astra
```

| name | model | subscription |
|---|---|---|
| `astra` | GPT-6 Astra via Codex | ChatGPT Plus/Pro |
| `sol` | GPT-5.6 Sol via Codex | ChatGPT Plus/Pro |
| `fable` | Claude Fable 5.1 | Claude Pro/Max |
| `opus` | Claude Opus 5 | Claude Pro/Max |
| `sonnet` | Claude Sonnet 5 | Claude Pro/Max |
| `web` | ChatGPT Web via [codex-chatgpt-web](https://github.com/miuuyy/codex-chatgpt-web) | ChatGPT |

The fallback chain is derived from that name: the chosen model goes first and
every other one lines up behind it, alternating between the two subscriptions —
a quota runs out per account, so the link after a spent one should be on the
other. Choosing a model therefore never leaves you with a single point of
failure, and there is no configuration where the chain is one link long.

The backend moves to the next link when one fails (quota spent, CLI missing,
model not enrolled, invalid answer) and **says so in the job log**. A short
written by the fallback rather than by the model you picked is worth knowing
about.

It also asks the CLI which models it has before sending one, so a model your
account is not enrolled in is skipped with a reason instead of being quietly
swapped for the CLI's default (`LLM_VERIFY_MODEL=0` turns that off).

The model can also be changed from **Geradores** in the interface, without a
restart — that choice wins over `.env`. `GET /api/models` reports the whole
chain and why each link can or cannot be used.

#### GPT-6 Astra

Astra is a staged rollout. Install and sign in to Codex, then check whether
your account is enrolled:

```bash
npm install -g @openai/codex && codex login
codex models          # gpt-6-astra appears here once you have access
```

If it is not listed, ShortsCreator skips it with that reason and falls to the
next model — nothing breaks, and `POST /api/models/refresh` re-reads the
listing once access arrives, without a restart.

#### ChatGPT Web through Codex

[codex-chatgpt-web](https://github.com/miuuyy/codex-chatgpt-web) is an
unofficial launcher that registers ChatGPT Web models inside Codex's own model
picker. Because it works through the same `codex` binary, ShortsCreator needs
no extra provider for it — only the model id the launcher installed:

```bash
curl -fsSL https://github.com/miuuyy/codex-chatgpt-web/releases/latest/download/install-launcher.sh | sh
# sign in inside the launcher, run its smoke test, press "Install models",
# then restart Codex and check the name it registered:
codex models
```

```env
LLM_MODEL=web
CODEX_WEB_MODEL=chatgpt-web   # whatever `codex models` shows
```

To pin an explicit chain and bypass all of the above, `LLM_CHAIN` still wins:

```env
LLM_CHAIN=claude_cli:claude-fable-5-1,codex_cli:gpt-5.6-sol
```

To pin a single model, use the provider directly:

```env
LLM_PROVIDER=claude_cli
CLAUDE_CLI_MODEL=claude-fable-5-1
```

It also accepts `anthropic`, `openai` (per-token key) and `ollama` (local).

Every call records which link answered and how long it took — visible on the job
and aggregated under **Performance**, so you can tell whether the primary is
holding up or the chain keeps falling through to Codex.

### Voice

`edge-tts` is the default and it is free, with neural voices in several
languages. For character voices: **ElevenLabs** (API cloning), **fish.audio**
(catalog) or a local **XTTS** from a 6 to 30 second sample.

> Only use voices you have the right to use. Cloning a real person's voice
> without permission may violate personality rights and the platforms' terms.

## Publishing

| Platform | How | Note |
|---|---|---|
| YouTube Shorts | Data API v3, resumable upload | Native scheduling (`publishAt`). Custom thumbnails require a phone-verified channel |
| TikTok | Content Posting API v2 | Without app audit approval, uploads land in the drafts inbox — a platform limitation |
| Instagram Reels | Graph API (container → publish) | Professional account linked to a page. Meta **downloads** the MP4, so `PUBLIC_API_URL` must be reachable from the internet (ngrok, cloudflared) |
| LinkedIn | Posts API (native video) | Token with `w_member_social`. No post analytics for members |

One-off, scheduled or sequential (batch) publishing. Scheduled uploads wait for
each short to finish rendering and pass QA before going up.

### Notifications

Telegram, Discord or a generic webhook when a short finishes, fails or gets
published. Optional — with no credential configured, nothing is sent.

Each notice follows the language the job was made in, so a Spanish production
sends a Spanish alert. Set `PUBLIC_WEB_URL` if you run behind a tunnel:
otherwise the link in the message points at `localhost` and is useless on a
phone.

## Tests

```bash
make test        # whole backend
make test-fast   # skips what needs FFmpeg
make typecheck   # frontend types
make i18n        # audits the five dictionaries
make check       # all of the above
```

`make i18n` catches what the compiler cannot: a key copied without being
translated, a `{placeholder}` lost in translation (which would render
literally), an empty value. The keys themselves are already guaranteed —
`Dictionary` is inferred from the Portuguese dictionary, so a missing key
fails the build.

The QA and sync tests generate real MP4 and MP3 files with FFmpeg and audit the
result — the only way to exercise what the QA actually does. Without FFmpeg on
PATH those cases are skipped instead of failing.

## Architecture

```
backend/app/
  pipeline/
    ingest.py        article, video, repository, images, script
    script.py        script, refinement and alternative hooks via LLM
    llm.py           model chain with fallback + telemetry
    tts.py           synthesis with per-word timing
    captions.py      ASS captions with karaoke highlight
    overlays.py      PNG captions when FFmpeg has no libass
    highlights.py    scene detection and segment picking
    clipper.py       one long video → N shorts
    render.py        9:16 composition with FFmpeg
    cover.py         best-frame pick + title on the cover
    qa.py            final-file audit
    metrics.py       performance collection and feedback briefing
    trends.py        trending-topic radar
    notify.py        Telegram, Discord, webhook
    timeline.py      editor EDL model
    orchestrator.py  pipeline + auto-fix loop + resume
    publishers/      youtube, tiktok, instagram, linkedin
  routers/           HTTP API
  tests/             pytest (128 cases)
web/
  app/               Next.js screens
  components/        shared UI
  lib/i18n/          5 language dictionaries
```

SQLite with no ORM. In-thread job queue, no external broker.

Code, comments and commit history are in English. One thing stays in
Portuguese on purpose: the **LLM prompts** in `script.py` and `clipper.py`.
They are calibrated in Portuguese and their JSON responses use Portuguese
keys, which the pipeline reads by name — the *output* language is a separate
concern, controlled by `job.language`.

## License

MIT
