# ShortsCreator

Turns a link, a topic, a repository, photos or a long video into 9:16 vertical
videos ready for YouTube Shorts, TikTok, Instagram Reels and LinkedIn — with
narration, synced karaoke captions, automatic format auditing, scheduled
publishing and performance feedback that loops back into the scriptwriter.

![output](https://img.shields.io/badge/output-1080%C3%971920%20%C2%B7%209%3A16-ffc400)
![tests](https://img.shields.io/badge/tests-128%20passing-35d67f)
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

Requires **Python 3.11+**, **Node 20+** and **FFmpeg**.

```bash
git clone https://github.com/JoasASantos/ShortsCreator.git
cd ShortsCreator
make setup          # creates the venv, installs backend and frontend, copies .env
```

Fill in `.env` and start both services:

```bash
make dev            # API on :8000, interface on :3000
```

`make doctor` checks the system dependencies.

### Language model

The cheapest path uses a subscription you already pay for instead of a
per-token key — the backend calls the local CLI that is already signed in. The
default is a chain: Fable as primary, Opus 5 as fallback and Codex as the last
resort.

```env
LLM_PROVIDER=chain
LLM_CHAIN=claude_cli:claude-fable-5-1,claude_cli:claude-opus-5,codex_cli:gpt-5.6-sol
```

Each link is `provider:model`, and the backend only moves to the next one when
the previous fails (subscription limit, CLI missing from PATH, invalid answer).
The first two use Claude Pro/Max through Claude Code; the third uses ChatGPT
Plus/Pro through `codex login`.

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

Code, comments and commit history are in English. Two things stay in
Portuguese on purpose: the **LLM prompts** in `script.py` and `clipper.py`
(they are calibrated in Portuguese and their JSON responses use Portuguese
keys — the *output* language is controlled separately by `job.language`), and
the **connector descriptions** in `connectors.py`, which the Accounts screen
renders directly and would need their own i18n pass.

## License

MIT
