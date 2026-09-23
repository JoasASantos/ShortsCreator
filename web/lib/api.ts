export type JobStatus = "queued" | "running" | "done" | "error";

export type Platform = "youtube" | "tiktok" | "instagram" | "linkedin";

export const PLATFORM_LABEL: Record<string, string> = {
  youtube: "YouTube Shorts",
  tiktok: "TikTok",
  instagram: "Instagram Reels",
  linkedin: "LinkedIn",
};

export interface JobMetricsSummary {
  job_id: string;
  views: number;
  likes: number;
  avg_view_pct: number | null;
  platforms: number;
}

export interface MetricRow {
  schedule_id: string;
  job_id: string;
  platform: string;
  video_id: string;
  url: string | null;
  views: number;
  likes: number;
  comments: number;
  shares: number;
  avg_view_seconds: number | null;
  avg_view_pct: number | null;
  published_at: string | null;
  fetched_at: string;
  error: string | null;
  title?: string | null;
  niche?: string | null;
}

export interface LLMCall {
  id: number;
  purpose: string;
  provider: string;
  model: string;
  seconds: number;
  ok: number;
  error: string | null;
  created_at: string;
}

export interface LLMSummary {
  provider: string;
  model: string;
  calls: number;
  ok: number;
  avg_seconds: number;
  total_seconds: number;
}

export interface HookOption {
  index: number;
  text: string;
  mechanism: string;
  why: string;
  audio: string | null;
  seconds?: number;
  audio_error?: string;
}

export interface HookVariants {
  current: string;
  options: HookOption[];
}

export interface Job {
  id: string;
  title: string | null;
  status: JobStatus;
  stage: string | null;
  progress: number;
  error: string | null;
  created_at: string;
  updated_at: string;
  input: JobInput;
  result: JobResult | null;
  qa: QAReport | null;
  // list: aggregated summary; detail: one row per platform
  metrics?: JobMetricsSummary | MetricRow[] | null;
  llm_calls?: LLMCall[];
  resumable_from?: string | null;
}

export type SourceType =
  | "url" | "tema" | "texto" | "video" | "github" | "imagem" | "roteiro";

export interface JobInput {
  source_type: SourceType;
  source: string;
  attachments: string[];
  /** Material that INFORMS the script without appearing in it: links to
   *  reviews and articles, or loose notes. Transcribed and read, never shown. */
  /** Quem conversa, quando o modo é dialogo. */
  cast?: string[];
  /** Footage sua por baixo da narração: id de /api/fundos ou link. */
  fundo?: string;
  research: string;
  research_attachments: string[];
  // meu_video: a recording of your own — no script and no TTS, the captions
  // come from transcribing what was actually said.
  // avatar: a talking presenter reads the script — the provider renders both
  // the picture and the voice, so there is no TTS stage either.
  edit_mode: "narrar_por_cima" | "resumo" | "meu_video" | "avatar" | "dublar" | "dialogo";
  angle: string;
  instruction: string;
  niche: string;
  language: string;
  voice_id?: string | null;
  duration: number;
  caption_style: "karaoke" | "bloco" | "palavra";
  caption_position: "centro" | "baixo" | "topo";
  scroll: "nenhum" | "texto" | "pan" | "codigo";
  background:
    | "auto" | "broll" | "gradiente" | "video_fonte"
    | "imagem_kenburns" | "codigo_scroll" | "ia_video" | "ia_imagem"
    | "site_scroll" | "video_fundo" | "upload";
  background_query: string;
  music: boolean;
  // Only meaningful in meu_video mode: keep the voice on the recording, or
  // deliver it silent with captions only. Optional because this type doubles
  // as the request body, and the other flows have no reason to send it.
  keep_audio?: boolean;
  /** Dubs only: put the original's link in the description. */
  credit_source?: boolean;
  music_track: string;
  music_volume: number;
  caption_offset: number;
  hook_hard: boolean;
  cta: string;
  title_overlay: boolean;
  watermark: string;
  watermark_position: WatermarkPosition;
  watermark_size: WatermarkSize;
  watermark_opacity: number;
  variants: number;
  qa_autofix: boolean;
  qa_max_attempts: number;
  // Only meaningful in avatar mode: the provider's own ids for the presenter
  // and the voice it speaks in. Optional because this type doubles as the
  // request body and no other flow has a reason to send them.
  avatar_id?: string;
  avatar_voice_id?: string;
}

/** Where the channel handle sits, how big and how visible. Values are the
 *  technical keys the backend expects — only the label is translated. */
export type WatermarkPosition =
  | "baixo_centro" | "baixo_esquerda" | "baixo_direita"
  | "topo_centro" | "topo_esquerda" | "topo_direita";
export type WatermarkSize = "pequeno" | "medio" | "grande";

export interface ScriptSegment {
  kind: string;
  text: string;
  broll_query?: string;
  on_screen?: string;
}

export interface MusicTrack {
  id: string;
  name: string;
  duration: number;
  size_bytes: number;
}

export interface CatalogVoice {
  id: string;
  name: string;
  languages: string[];
  likes: number;
  author: string;
  description: string;
  sample_text: string;
  has_sample: boolean;
}

export interface VoicePreset {
  id: string;
  name: string;
  note: string;
  likes: number;
  category: string;
  category_label: string;
  category_hint: string;
  installed: boolean;
}

export interface ScriptEdit {
  segments?: ScriptSegment[];
  title?: string;
  voice_id?: string | null;
  caption_style?: string;
  caption_position?: string;
  caption_offset?: number;
  music?: boolean;
  music_track?: string;
  music_volume?: number;
  watermark?: string;
  watermark_position?: WatermarkPosition;
  watermark_size?: WatermarkSize;
  watermark_opacity?: number;
  background?: string;
  scroll?: string;
}

/** What the script editor keeps without rendering, so it survives closing the
 *  tab. It becomes the official version when the user applies it. */
export interface ScriptDraft {
  segments: ScriptSegment[];
  title: string;
  voice_id: string;
  caption_style: string;
  caption_position: string;
  caption_offset: number;
  music: boolean;
  music_track: string;
  music_volume: number;
  watermark: string;
  watermark_position: WatermarkPosition;
  watermark_size: WatermarkSize;
  watermark_opacity: number;
  saved_at?: string;
}

export interface TimelineVideoClip {
  id: string;
  source: string;
  in_point: number;
  out_point: number;
  start: number;
  kind: string;
  mute: boolean;
}

export interface TimelineAudioClip {
  id: string;
  source: string;
  in_point: number;
  out_point: number;
  start: number;
  gain: number;
  role: string;
}

export interface TimelineCue {
  id: string;
  text: string;
  start: number;
  end: number;
}

/** An image or clip laid over the main track — the picture-in-picture.
 *
 *  Geometry is in fractions of the frame, not pixels: `x`/`y` are the
 *  overlay's centre and `width` its share of the frame width, with the height
 *  following the media's own aspect ratio. That is what lets the editor show a
 *  9:16 preview at whatever size the browser gives it without knowing the
 *  output resolution. */
export interface TimelineMedia {
  id: string;
  source: string;
  start: number;
  end: number;
  x: number;
  y: number;
  width: number;
  kind: "image" | "video";
  opacity: number;
  in_point: number;
  mute: boolean;
}

export interface Timeline {
  duration: number;
  video: TimelineVideoClip[];
  audio: TimelineAudioClip[];
  captions: TimelineCue[];
  media: TimelineMedia[];
  caption_style: string;
  caption_position: string;
  watermark: string;
  watermark_position: string;
  watermark_size: string;
  watermark_opacity: number;
  /** The frame this is composed into — "vertical" (1080x1920, the short),
   *  "horizontal" (1920x1080, a documentary) or "quadrado". Older timelines
   *  carry none and are the vertical short they always were. */
  format?: TimelineFormat;
}

export type TimelineFormat = "vertical" | "horizontal" | "quadrado";

// ------------------------------------------------------------- long form

/** Documentary, mini-doc, short film, mini-series — assembled from material
 *  the user brings, over stages they correct before the next one runs. */
export const LONGFORM_TYPES = [
  "documentario", "mini_documentario", "curta", "mini_serie",
] as const;
export type LongformType = (typeof LONGFORM_TYPES)[number];

/** The stages, in the order they are developed. `material` is ingestion; the
 *  other three are the model's, each rewritable. */
export const LONGFORM_STAGES = [
  "material", "briefing", "roteiro", "plano_de_edicao",
] as const;
export type LongformStage = (typeof LONGFORM_STAGES)[number];

export interface LongformTypeInfo {
  id: LongformType;
  label: string;
  fiction: boolean;
  min_minutes: number;
  max_minutes: number;
  default_minutes: number;
  structure: string;
}

export interface LongformVocabulary {
  types: LongformTypeInfo[];
  narrators: { id: string; description: string }[];
  tones: { id: string; description: string }[];
  stages: LongformStage[];
  block_kinds: string[];
  shot_kinds: string[];
  min_episodes: number;
  max_episodes: number;
}

export interface LongformMaterialItem {
  id: string;
  kind: "entrevista" | "link" | "artigo" | "imagem";
  /** A reference example: read for structure and rhythm, never for content. */
  reference: boolean;
  title: string;
  url?: string;
  duration: number;
  transcript?: string;
  description?: string;
  status: "ok" | "failed";
  error?: string;
}

export interface LongformBriefing {
  title: string;
  logline: string;
  angle: string;
  facts: { fact: string; source: string; why: string }[];
  themes: string[];
  /** A quote worth using, and where in which material it actually is. */
  moments: { material: string; start: number; end: number; quote: string; why: string }[];
  gaps: string[];
  stock_queries: string[];
  structure: { part: string; purpose: string; minutes: number }[];
  episodes: { episode: number; title: string; focus: string }[];
  rejected: { what: string; reason: string; fact?: string; quote?: string }[];
}

export interface LongformBlock {
  key: string;
  kind: string;
  title: string;
  narration: string;
  words: number;
  seconds: number;
  budget_seconds: number;
  /** The block was lengthened: its narration needs more time than was asked. */
  adjusted: boolean;
  visual: string;
  moment: { material: string; start: number; end: number } | null;
}

export interface LongformScript {
  episodes: {
    episode: number; title: string; total_seconds: number;
    narration_words: number; blocks: LongformBlock[];
  }[];
  rejected: { what: string; block: string; reason: string }[];
  notes: string[];
}

export interface LongformShot {
  kind: "entrevista" | "link" | "stock" | "imagem" | "avatar" | "cartela";
  material?: string;
  start?: number;
  end?: number;
  query?: string;
  text?: string;
  seconds: number;
  lower_third?: string;
}

export interface LongformPlan {
  episodes: {
    episode: number; title: string;
    blocks: {
      key: string; kind: string; title: string; narration: string;
      narrator: "oculto" | "avatar" | "nenhum";
      seconds: number; shots: LongformShot[];
    }[];
  }[];
  /** What the model asked for that the catalog could not honour. Shown as
   *  written: a refusal nobody reads is a refusal that did not happen. */
  rejected: { block: string; reason: string; shot: LongformShot | null }[];
}

export interface LongformEstimate {
  episodes: number;
  blocks: number;
  target_seconds: number;
  narration: {
    blocks: number; words: number; seconds: number; minutes: number;
    mode: string;
    provider: { id: string; name: string; configured: boolean; reason: string };
  };
  avatar: { seconds: number; configured: boolean | null };
  stock: {
    clips: number; queries: number;
    provider: { id: string; name: string; configured: boolean; reason: string };
  };
  interview: { cuts: number; seconds: number; minutes: number };
  images: number;
  cards: number;
  narration_to_synthesize: number;
  shots_to_prepare: number;
  reused: number;
  warnings: string[];
}

export interface LongformProject {
  id: string;
  title: string | null;
  status: string;
  stage: string | null;
  type: LongformType;
  prompt: string;
  instruction: string;
  error: string | null;
  created_at: string;
  updated_at: string;
  options: Record<string, unknown>;
  sources: { links: string[]; attachments: string[]; references: string[] };
  material: { items: LongformMaterialItem[] } | null;
  // The listing sends booleans here instead of the documents, so it stays cheap.
  briefing: LongformBriefing | boolean | null;
  roteiro: LongformScript | boolean | null;
  plano_de_edicao: LongformPlan | boolean | null;
  progress: unknown[] | boolean | null;
  jobs: Record<string, string> | null;
  estimate?: LongformEstimate;
}

export interface LongformInput {
  type: LongformType;
  prompt: string;
  instruction?: string;
  title?: string;
  tone?: string;
  style?: string;
  narrator?: string;
  language?: string;
  niche?: string;
  target_minutes?: number;
  episodes?: number;
  links?: string[];
  attachments?: string[];
  references?: string[];
  caption_style?: string;
  caption_position?: string;
  watermark?: string;
}

export interface LongformStageResult {
  project_id: string;
  stage: string;
  invalidated: string[];
  rejected?: unknown[];
  queued?: boolean;
  will_invalidate?: string[];
  detail?: string;
  briefing?: LongformBriefing;
  roteiro?: LongformScript;
  plano_de_edicao?: LongformPlan;
}

export interface LongformGenerateResult {
  project_id: string;
  queued: boolean;
  status?: string;
  estimate: LongformEstimate;
  detail?: string;
}

export interface LongformEvent {
  id: number;
  job_id: string;
  level: "info" | "warn" | "error";
  message: string;
  created_at: string;
}

/** CSS aspect ratio for each frame, for previews that must show the shape
 *  the file actually has instead of a phone every time. */
export const FORMAT_ASPECT: Record<TimelineFormat, string> = {
  vertical: "9 / 16",
  horizontal: "16 / 9",
  quadrado: "1 / 1",
};

export const FORMAT_SIZE: Record<TimelineFormat, string> = {
  vertical: "1080×1920",
  horizontal: "1920×1080",
  quadrado: "1080×1080",
};

/** A reel you recorded yourself: exactly one of `attachment_id` or `url` —
 *  the backend answers 400 when both, or neither, come filled in. */
export interface ReelInput {
  attachment_id?: string;
  url?: string;
  title?: string;
  niche?: string;
  language?: string;
  caption_style?: string;
  caption_position?: string;
  watermark?: string;
  watermark_position?: string;
  watermark_size?: string;
  watermark_opacity?: number;
  keep_audio?: boolean;
  instruction?: string;
}

export interface ReelMediaInput {
  attachment_id: string;
  start: number;
  end: number;
  x?: number;
  y?: number;
  width?: number;
  opacity?: number;
  in_point?: number;
}

// ------------------------------------------------------------------- films

/** The four stages a film is developed through, in order. Each one is written
 *  against the ones before it, so rewriting an early stage drops the later
 *  ones — the API says which in `invalidated`. */
export const FILM_STAGES = ["bible", "characters", "screenplay", "shots"] as const;
export type FilmStage = (typeof FILM_STAGES)[number];

/** Limits `routers/films.py` enforces. */
export const FILM_MIN_SCENES = 1;
export const FILM_MAX_SCENES = 24;
export const FILM_MIN_SHOT_SECONDS = 3;
export const FILM_MAX_SHOT_SECONDS = 12;

export interface FilmInput {
  premise: string;
  instruction?: string;
  title?: string;
  language?: string;
  niche?: string;
  aspect?: string;
  scenes?: number;
  shot_seconds?: number;
  voice_id?: string | null;
  caption_style?: string;
  caption_position?: string;
  watermark?: string;
}

export interface FilmBible {
  title: string;
  logline: string;
  theme: string;
  tone: string;
  /** Written in English on purpose: it is fed to the video model. */
  look: string;
  setting: string;
  acts: { act: number; name: string; summary: string; turning_point: string }[];
}

export interface FilmCharacter {
  name: string;
  role: string;
  personality: string;
  /** Pasted verbatim into every shot prompt featuring this character — that
   *  literal reuse is the only thing keeping a face the same between shots. */
  visual: string;
  voice: string;
}

export interface FilmScene {
  scene: number;
  act: number;
  location: string;
  time_of_day: string;
  beat: string;
  characters: string[];
  narration: string;
  dialogue: { character: string; line: string }[];
  seconds: number;
}

export interface FilmShot {
  scene: number;
  shot: number;
  action: string;
  camera: string;
  characters: string[];
  seconds: number;
  key?: string;
  prompt?: string;
}

export interface FilmEstimate {
  shots: number;
  seconds: number;
  scenes: number;
  narration_lines: number;
  shots_to_generate: number;
  seconds_to_generate: number;
  reused_shots: number;
  /** `reason` is the backend's own explanation of what to do about an
   *  unconfigured provider — shown verbatim rather than reworded. */
  provider: {
    id: string;
    name: string;
    model: string;
    cost: string;
    configured: boolean;
    reason?: string;
  };
}

export interface Film {
  id: string;
  premise: string;
  instruction: string;
  title: string | null;
  status: string;
  error: string | null;
  job_id?: string | null;
  created_at: string;
  updated_at?: string;
  options: Record<string, unknown> | null;
  bible: FilmBible | null;
  characters: { characters: FilmCharacter[] } | null;
  screenplay: { scenes: FilmScene[] } | null;
  shots: { shots: FilmShot[] } | null;
  progress?: unknown;
  estimate?: FilmEstimate;
}

export interface StageResult {
  film_id: string;
  stage: FilmStage;
  invalidated: FilmStage[];
  bible?: FilmBible;
  characters?: { characters: FilmCharacter[] };
  screenplay?: { scenes: FilmScene[] };
  shots?: { shots: FilmShot[] };
}

export interface FilmGenerateResult {
  film_id: string;
  queued: boolean;
  status?: string;
  estimate: FilmEstimate;
  detail?: string;
}

/** Suggestions for a recording, anchored to timestamps that exist in its
 *  transcript — a suggestion the editor cannot place is worse than none, so
 *  the backend drops those before they get here. */
export interface ReelAssist {
  instruction: string;
  virality: { score: number; why: string; biggest_risk: string };
  hooks: { text: string; why: string }[];
  cuts: { start: number; end: number; why: string }[];
  media: {
    start: number; end: number; what: string;
    image_prompt: string; stock_query: string;
  }[];
  captions: { start: number; end: number; text: string; why: string }[];
  title: string;
  hashtags: string[];
}

export interface UploadResult {
  id: string;
  kind: "imagem" | "video";
  filename: string;
  size_bytes: number;
}

export interface QAAttempt {
  attempt: number;
  action: string;
  report: QAReport;
}

export interface PostCaption {
  youtube_titulo: string;
  youtube_descricao: string;
  tiktok_legenda: string;
  instagram_legenda: string;
  hashtags: string[];
}

export interface JobResult {
  title: string;
  description: string;
  hashtags: string[];
  duration: number;
  video: string;
  thumbnail: string;
  captions_srt: string;
  // Absent for a recording of your own: nothing wrote a script for it, and
  // the words below are the transcript of what was actually said. This being
  // non-optional is what let the job page crash on the first reel.
  script?: { segments: { kind: string; text: string; on_screen: string }[] };
  words: { word: string; start: number; end: number }[];
  source_kind?: string;
  edit_mode?: string;
  /** Only on a recording of your own — the language whisper detected. */
  transcript_language?: string;
  /** The frame the file was composed in. Absent on anything made before
   *  formats existed, which is the vertical short. */
  format?: TimelineFormat;
  /** Only on a recording of your own — the last suggestions asked for. */
  assist?: ReelAssist;
  qa_attempts?: QAAttempt[];
  caption?: PostCaption;
  cover?: string | null;
  cover_at?: number | null;
  preview_gif?: string | null;
  hook_variants?: HookVariants;
}

export interface TrendItem {
  source: string;
  title: string;
  snippet: string;
  url: string;
  heat: number;
  /** The backend sends numbers, not a ready-made sentence: the wording is
   *  assembled in the UI so it follows the chosen language. */
  heat_kind: "searches" | "rising" | "reddit_rising" | "points_comments" | "views"
    | "feed" | "web_search";
  heat_data: Record<string, string | number>;
  /** Niche keys the item belongs to — the first one prefills /novo. */
  niches: string[];
  /** Suggested opening hook, only on the LLM-curated web search items. */
  angle?: string;
}

export interface TrendSource {
  source: string;
  items: number;
  age_seconds: number | null;
}

export interface ClipInfo {
  inicio: number;
  fim: number;
  titulo: string;
  motivo: string;
  assunto: string;
  /** Only some analyses rate the stretch; shown when it comes. */
  score?: number;
}

export interface ClipPlan {
  id: string;
  attachment_id: string;
  status: "queued" | "analisando" | "ready" | "rendered" | "error";
  requested: number;
  target_seconds: number;
  /** `options_json` is free-form on the backend: a livestream plan carries
   *  `mode: "livestream"` plus the VOD link and the window it was cut with. */
  options: {
    niche: string; language: string;
    mode?: string; url?: string; window_seconds?: number;
  } | null;
  clips: ClipInfo[] | null;
  jobs: string[] | null;
  error: string | null;
  created_at: string;
  updated_at?: string;
}

/** Livestream plan: exactly one of `attachment_id` or `url` — the backend
 *  answers 400 when both, or neither, come filled in. */
export interface LiveCutInput {
  attachment_id?: string;
  url?: string;
  count: number;
  target_seconds: number;
  niche: string;
  language: string;
  window_minutes: number;
}

/** Livestream plan limits, the same ones `routers/livecuts.py` enforces. */
export const LIVECUT_MAX_CUTS = 40;
export const LIVECUT_MIN_WINDOW_MINUTES = 10;
export const LIVECUT_MAX_WINDOW_MINUTES = 90;
export const LIVECUT_MODE = "livestream";

export interface QAIssue {
  check: string;
  severity: "fatal" | "erro" | "aviso" | "info";
  message: string;
  fix: string;
}

export interface QAReport {
  passed: boolean;
  score: number;
  issues: QAIssue[];
  metrics: Record<string, string | number>;
}

export interface Voice {
  id: string;
  name: string;
  provider: string;
  provider_voice_id: string;
  sample_path: string;
  settings_json: string;
}

export interface Account {
  id: string;
  platform: Platform;
  display_name: string;
  created_at: string;
}

export interface ConnectorField {
  key: string;
  label: string;
  secret: boolean;
  env: string;
  hint: string;
  filled: boolean;
}

export interface Connector {
  id: string;
  name: string;
  category: "publicacao" | "video" | "avatar" | "voz" | "broll" | "notificacao";
  auth: "oauth" | "api_key";
  detail: string;
  requirement: string;
  docs: string;
  status: "pronto" | "beta" | "planejado";
  configured: boolean;
  source: "painel" | "env" | "";
  testable: boolean;
  accounts: number;
  fields: ConnectorField[];
}

export interface Schedule {
  id: string;
  job_id: string;
  account_id: string;
  platform: string;
  publish_at: string;
  status: string;
  error: string | null;
}

export interface ChainStep {
  provider: string;
  model: string;
  ready: boolean;
  /** The backend's own sentence about why this link cannot be used, empty when
   *  it can. Shown as written: it names the binary or the model id to fix. */
  reason: string;
}

/** One model the chain knows how to reach. `ready` false is not a failure —
 *  the chain simply moves on to the next link. */
export interface ModelOption {
  name: string;
  provider: string;
  model: string;
  note: string;
  ready: boolean;
  reason: string;
}

export interface ModelsReport {
  chosen: string;
  /** "interface" when it was picked on screen, "env" when it still comes from
   *  LLM_MODEL — the difference is what makes an ignored setting traceable. */
  source: string;
  chain: ChainStep[];
  models: ModelOption[];
  /** LLM_CHAIN in .env silently outranks the choice; the screen has to say so. */
  chain_override: boolean;
  any_ready: boolean;
}

/** One line of the requirements doctor. `level` mirrors `required` and is what
 *  the install screen groups by — a missing optional item is not a failure. */
export interface Requirement {
  id: string;
  label: string;
  required: boolean;
  found: boolean;
  version: string;
  unlocks: string;
  install: string;
  /** What is lost without it, or how the pipeline copes. Only worth showing
   *  when the item is missing. */
  note: string;
  level: "required" | "optional";
}

export interface RequirementsReport {
  ok: boolean;
  platform: { os: string; machine: string; package_manager: string };
  checks: Requirement[];
  missing_required: string[];
  missing_optional: string[];
  min_free_gb: number;
}

export type GeneratorCapability =
  | "text_to_video" | "image_to_video" | "text_to_image" | "image_to_image";

/** `unreachable` only ever happens to a local server: a key that exists proves
 *  nothing about a server that is not running. `incapable` is not a problem —
 *  it only means this provider does not do that one capability. */
export type GeneratorState =
  | "ready" | "not_configured" | "unreachable" | "incapable";

export interface Generator {
  id: string;
  label: string;
  capabilities: GeneratorCapability[];
  hosting: "hosted" | "local";
  cost: "paid" | "free";
  speed: string;
  state: GeneratorState;
  /** The backend's own sentence about why it is in this state. */
  reason: string;
  setup: string;
  docs: string;
  connector: string;
  /** Local servers only — the URL that was probed. */
  base_url: string;
  models: { id: string; label: string }[];
  default_model: string;
  unlocks: string[];
}

/** A provider weighed for one capability, with the reason it was kept or
 *  skipped. The first eligible entry is the one that runs. */
export interface GeneratorCandidate {
  provider: string;
  label: string;
  state: GeneratorState;
  eligible: boolean;
  reason: string;
  hosting: string;
  cost: string;
  speed: string;
  model: string;
}

export interface GeneratorsReport {
  providers: Generator[];
  selection: Record<GeneratorCapability, GeneratorCandidate[]>;
}

/* ---------------------------------------- your own avatar, your own voice */

/** One path of the avatar feature, in the same shape the generator registry
 *  uses: a state, the backend's own reason for it, and what it unlocks.
 *  `kind` says which half it belongs to — the talking presenter, or cloning
 *  your voice from a sample. */
export interface AvatarProvider {
  id: string;
  label: string;
  kind: "avatar_video" | "voice_clone";
  hosting: "hosted" | "local";
  cost: "paid" | "free";
  speed: string;
  state: GeneratorState;
  reason: string;
  setup: string;
  docs: string;
  connector: string;
  /** Local servers only — the URL that was probed. */
  base_url: string;
  unlocks: string[];
}

export interface AvatarReport {
  avatar: AvatarProvider;
  voice_clone: AvatarProvider[];
  providers: AvatarProvider[];
  /** The numbers the screen has to state before someone records a sample or
   *  writes a script, rather than after being refused. */
  limits: {
    min_sample_seconds: number;
    recommended_sample_seconds: number;
    max_sample_seconds: number;
    min_script_chars: number;
    max_script_chars: number;
  };
}

export interface AvatarChoice {
  id: string;
  name: string;
  gender: string;
  preview_image: string;
  preview_video: string;
}

export interface AvatarVoiceChoice {
  id: string;
  name: string;
  language: string;
  gender: string;
  preview_audio: string;
}

/** A voice row plus the two things only the cloning answer knows: which path
 *  ran, and what the sample turned out to be. */
export interface ClonedVoice extends Voice {
  cloned_with: string;
  note: string;
  sample_seconds: number;
  sample_dbfs: number;
}

export interface AvatarVideoInput {
  script: string;
  avatar_id: string;
  voice_id: string;
  title?: string;
  language?: string;
  caption_style?: string;
  caption_position?: string;
  watermark?: string;
  watermark_position?: WatermarkPosition;
  watermark_size?: WatermarkSize;
  watermark_opacity?: number;
}

export interface Health {
  status: string;
  ffmpeg: boolean;
  ytdlp: boolean;
  llm_provider: string;
  llm_key_set: boolean;
  llm_auth: string;
  llm_chain: ChainStep[];
  tts_provider: string;
  broll_ready: boolean;
  queue: number;
  format: string;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: init?.body instanceof FormData ? init?.headers
      : { "Content-Type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const raw = await res.text();
    // FastAPI returns {"detail": "..."} — without this the toast showed raw JSON.
    let message = raw;
    try {
      const parsed = JSON.parse(raw);
      if (typeof parsed?.detail === "string") message = parsed.detail;
    } catch { /* body was not JSON, use the plain text */ }
    throw new Error(message.slice(0, 400) || `HTTP ${res.status}`);
  }
  return res.status === 204 ? (null as T) : res.json();
}

/** A local server the panel can start with docker. `state` is the container's,
 *  not the service's: "running" says the container is up, which happens well
 *  before a model finishes loading. */
export type ServiceStatus = {
  id: string;
  label: string;
  state: "sem_docker" | "docker_parado" | "nao_criado" | "parado" | "rodando";
  reason: string;
  can_start: boolean;
  image: string;
  note: string;
  docs: string;
};

/** One video from a profile listing — metadata only, nothing downloaded. */
export type RecycleItem = {
  id: string;
  url: string;
  title: string;
  views: number | null;
  likes: number | null;
  duration: number | null;
  thumbnail: string;
  uploader: string;
  platform: string;
};

export type RecycleScan = {
  handle: string;
  platform: string;
  profile_url: string;
  author: string;
  items: RecycleItem[];
  note: string;
};

/** One beat of a cloned shape: the role it plays and the room it takes. */
export type MoldeBeat = {
  kind: string;
  seconds: number;
  words: number;
  cuts: number;
  starts_at: number;
};

export type Molde = {
  id: string;
  name: string;
  source_url: string;
  seconds: number;
  words: number;
  beats: MoldeBeat[];
  cuts: number;
  language: string;
  pace: number;
  cuts_per_minute: number;
  hook_seconds: number;
  created_at?: string;
};

export type MoldeSummary = {
  id: string;
  name: string;
  source_url: string;
  seconds: number;
  words: number;
  created_at: string;
};

export type MoldeVariant = {
  subject: string;
  title: string;
  text: string;
  job_id: string;
};

/** Footage sua, guardada uma vez e reusada por todos os shorts. */
export type Fundo = {
  id: string;
  name: string;
  seconds: number;
  source_url: string;
  size_mb: number;
};

/** Quem conversa nos vídeos de diálogo: nome, voz e cara, amarrados. */
export type Personagem = {
  id: string;
  name: string;
  voice_id: string;
  voice_name: string;
  side: string;
  note: string;
  has_image: boolean;
};

export const api = {
  health: () => req<Health>("/api/health"),
  config: () => req<{ niches: string[]; min_seconds: number; max_seconds: number }>("/api/config"),

  // The doctor probes binaries on every call, so this is asked for when the
  // install screen opens and on demand — never on a timer.
  requirements: () => req<RequirementsReport>("/api/system/requirements"),

  // Local servers started from the panel. The catalogue lives in the backend:
  // a request names a service, never a command.
  services: () => req<{ docker: { ok: boolean; state: string; reason: string };
                        services: ServiceStatus[] }>("/api/services"),
  service: (id: string) => req<ServiceStatus>(`/api/services/${id}`),

  // A profile's videos, most watched first. Metadata only: the download
  // happens later, for the one that gets picked.
  // The shape of a reference video, reused to write new ones.
  elenco: () => req<{ personagens: Personagem[]; lados: string[] }>("/api/elenco")
    .then((body) => body.personagens),
  createPersonagem: (form: FormData) =>
    req<Personagem>("/api/elenco", { method: "POST", body: form }),
  deletePersonagem: (id: string) =>
    req<{ deleted: boolean }>(`/api/elenco/${id}`, { method: "DELETE" }),
  personagemImage: (id: string) => `/api/elenco/${id}/imagem`,

  fundos: () => req<{ fundos: Fundo[] }>("/api/fundos")
    .then((body) => body.fundos),
  saveFundo: (source: string, name = "") =>
    req<Fundo>("/api/fundos", {
      method: "POST",
      body: JSON.stringify({ source, name }),
    }),
  deleteFundo: (id: string) =>
    req<{ deleted: boolean }>(`/api/fundos/${id}`, { method: "DELETE" }),

  moldes: () => req<{ moldes: MoldeSummary[] }>("/api/moldes")
    .then((body) => body.moldes),
  molde: (id: string) => req<Molde>(`/api/moldes/${id}`),
  analyzeMolde: (source: string, name = "") =>
    req<Molde>("/api/moldes/analisar", {
      method: "POST",
      body: JSON.stringify({ source, name }),
    }),
  deleteMolde: (id: string) =>
    req<{ deleted: boolean }>(`/api/moldes/${id}`, { method: "DELETE" }),
  moldeVariants: (id: string, body: {
    subjects: string[]; instruction?: string; language?: string;
    niche?: string; dry_run?: boolean;
  }) =>
    req<{ molde: string; variants: MoldeVariant[];
          failed: { subject: string; error: string }[] }>(
      `/api/moldes/${id}/variantes`,
      { method: "POST", body: JSON.stringify(body) }),

  recycleScan: (target: string, platform: string, limit = 12) =>
    req<RecycleScan>("/api/recycle/scan", {
      method: "POST",
      body: JSON.stringify({ target, platform, limit }),
    }),
  startService: (id: string) =>
    req<{ started: boolean; state: ServiceStatus["state"]; message: string }>(
      `/api/services/${id}/start`, { method: "POST" }),
  stopService: (id: string) =>
    req<{ stopped: boolean; state: ServiceStatus["state"]; message: string }>(
      `/api/services/${id}/stop`, { method: "POST" }),
  serviceLogs: (id: string) =>
    req<{ logs: string }>(`/api/services/${id}/logs`),

  // probe=true is what makes a local server's state truthful; it costs one
  // round trip per server.
  generators: (probe = true) =>
    req<GeneratorsReport>(`/api/generators?probe=${probe}`),
  testGenerator: (id: string) =>
    req<{ ok: boolean; provider: string; message: string }>("/api/generators/test",
      { method: "POST", body: JSON.stringify({ provider: id }) }),

  // Every one of these answers with the whole report, so the screen never has
  // to guess what the write did — it renders what the backend now holds.
  models: () => req<ModelsReport>("/api/models"),
  chooseModel: (model: string) =>
    req<ModelsReport>("/api/models", { method: "PUT", body: JSON.stringify({ model }) }),
  resetModel: () => req<ModelsReport>("/api/models", { method: "DELETE" }),
  // The CLI listing is cached for the life of the server process, so a model
  // enrolled after boot stays invisible until this is asked for.
  refreshModels: () => req<ModelsReport>("/api/models/refresh", { method: "POST" }),

  jobs: () => req<Job[]>("/api/jobs"),
  job: (id: string) => req<Job>(`/api/jobs/${id}`),
  createJob: (input: Partial<JobInput>) =>
    req<{ job_id: string }>("/api/jobs", { method: "POST", body: JSON.stringify(input) }),
  retryJob: (id: string, from = "") =>
    req(`/api/jobs/${id}/retry${from ? `?from=${from}` : ""}`, { method: "POST" }),

  buildHooks: (id: string, count = 3) =>
    req<HookVariants>(`/api/jobs/${id}/hooks`,
      { method: "POST", body: JSON.stringify({ count, preview_audio: true }) }),
  applyHook: (id: string, text: string) =>
    req<{ rendering: boolean }>(`/api/jobs/${id}/hooks/apply`,
      { method: "POST", body: JSON.stringify({ text, render: true }) }),
  forkHook: (id: string, text: string) =>
    req<{ job_id: string }>(`/api/jobs/${id}/hooks/fork`,
      { method: "POST", body: JSON.stringify({ text }) }),
  rebuildCover: (id: string, title = "", at: number | null = null) =>
    req<{ cover: string; at: number }>(`/api/jobs/${id}/cover`,
      { method: "POST", body: JSON.stringify({ title, at }) }),

  metrics: () =>
    req<{ summary: { published: number; views: number; likes: number;
                     avg_retention: number | null; last_fetch: string | null };
          items: MetricRow[]; llm: LLMSummary[] }>("/api/metrics"),
  refreshMetrics: () => req<{ updated: number }>("/api/metrics/refresh", { method: "POST" }),
  insights: (niche = "") =>
    req<{ briefing: string }>(`/api/metrics/insights?niche=${niche}`),

  trends: (niche: string, geo = "BR") =>
    req<{ items: TrendItem[]; sources: TrendSource[]; pending: string[] }>(
      `/api/trends?niche=${niche}&geo=${geo}`),

  clipPlans: () => req<ClipPlan[]>("/api/clips"),
  clipPlan: (id: string) => req<ClipPlan>(`/api/clips/${id}`),
  createClipPlan: (body: { attachment_id: string; count: number; target_seconds: number;
                           niche: string; language?: string }) =>
    req<{ plan_id: string }>("/api/clips", { method: "POST", body: JSON.stringify(body) }),
  deleteClipPlan: (id: string) => req(`/api/clips/${id}`, { method: "DELETE" }),

  // Livestream cuts: the plan is the same `clip_plans` row, so listing,
  // deleting and rendering stay on /api/clips — only creating and following
  // the windowed analysis have their own route.
  createLiveCutPlan: (body: LiveCutInput) =>
    req<{ plan_id: string; status: string }>("/api/livecuts",
      { method: "POST", body: JSON.stringify(body) }),
  liveCutPlan: (id: string) => req<ClipPlan>(`/api/livecuts/${id}`),

  // A reel you recorded yourself. It becomes an ordinary job, which is why
  // following it goes through /api/jobs like every other production — only
  // creating it, asking for suggestions and dropping media over it are its own.
  createReel: (body: ReelInput) =>
    req<{ job_id: string; status: string }>("/api/reels",
      { method: "POST", body: JSON.stringify(body) }),
  assistReel: (id: string, instruction: string) =>
    req<ReelAssist>(`/api/reels/${id}/assist`,
      { method: "POST", body: JSON.stringify({ instruction }) }),
  addReelMedia: (id: string, body: ReelMediaInput) =>
    req<Timeline>(`/api/reels/${id}/media`,
      { method: "POST", body: JSON.stringify(body) }),

  // Films. Creating one already develops the story bible, so the POST is the
  // slow call here; every later stage is asked for on its own.
  createFilm: (body: FilmInput) =>
    req<Film>("/api/films", { method: "POST", body: JSON.stringify(body) }),
  films: () => req<Film[]>("/api/films"),
  film: (id: string) => req<Film>(`/api/films/${id}`),
  deleteFilm: (id: string) => req(`/api/films/${id}`, { method: "DELETE" }),
  developStage: (id: string, stage: FilmStage, instruction = "") =>
    req<StageResult>(`/api/films/${id}/stage/${stage}`,
      { method: "POST", body: JSON.stringify({ instruction }) }),
  saveFilmStage: (id: string, stage: FilmStage, doc: unknown) =>
    req<StageResult>(`/api/films/${id}/${stage}`,
      { method: "PUT", body: JSON.stringify(doc) }),
  // confirm=false answers with the estimate and starts nothing — a run is
  // minutes of paid generation, so the size of it is shown first.
  generateFilm: (id: string, confirm: boolean, placeholderOk = false) =>
    req<FilmGenerateResult>(`/api/films/${id}/generate`,
      { method: "POST",
        body: JSON.stringify({ confirm, placeholder_ok: placeholderOk }) }),

  // Long form. Creating one only queues the ingestion of the material; the
  // three model stages are asked for one at a time, so each can be read and
  // corrected before the next is written against it.
  longformTypes: () => req<LongformVocabulary>("/api/longform/types"),
  longforms: () => req<LongformProject[]>("/api/longform"),
  longform: (id: string) => req<LongformProject>(`/api/longform/${id}`),
  createLongform: (body: LongformInput) =>
    req<LongformProject>("/api/longform",
      { method: "POST", body: JSON.stringify(body) }),
  deleteLongform: (id: string) =>
    req<{ deleted: string }>(`/api/longform/${id}`, { method: "DELETE" }),
  developLongformStage: (id: string, stage: LongformStage, instruction = "") =>
    req<LongformStageResult>(`/api/longform/${id}/stage/${stage}`,
      { method: "POST", body: JSON.stringify({ instruction }) }),
  saveLongformStage: (id: string, stage: LongformStage, doc: unknown) =>
    req<LongformStageResult>(`/api/longform/${id}/${stage}`,
      { method: "PUT", body: JSON.stringify(doc) }),
  // confirm=false answers with the estimate and starts nothing: a 30-minute
  // assembly is minutes of paid work and the size of it is shown first.
  generateLongform: (id: string, confirm: boolean, placeholderOk = false) =>
    req<LongformGenerateResult>(`/api/longform/${id}/generate`,
      { method: "POST",
        body: JSON.stringify({ confirm, placeholder_ok: placeholderOk }) }),
  longformEvents: (id: string, after = 0) =>
    req<LongformEvent[]>(`/api/longform/${id}/events?after=${after}`),

  renderClips: (id: string, body: Record<string, unknown>) =>
    req<{ jobs: string[]; schedules: { job_id: string; publish_at: string }[] }>(
      `/api/clips/${id}/render`, { method: "POST", body: JSON.stringify(body) }),
  deleteJob: (id: string) => req(`/api/jobs/${id}`, { method: "DELETE" }),
  rerunQA: (id: string) => req<QAReport>(`/api/jobs/${id}/qa`, { method: "POST" }),
  editJob: (id: string, edit: ScriptEdit) =>
    req<{ job_id: string; applied: string[] }>(`/api/jobs/${id}/edit`,
      { method: "POST", body: JSON.stringify(edit) }),
  resetEdit: (id: string) => req(`/api/jobs/${id}/edit`, { method: "DELETE" }),

  draft: (id: string) => req<{ draft: ScriptDraft | null }>(`/api/jobs/${id}/draft`),
  saveDraft: (id: string, draft: ScriptDraft) =>
    req<{ saved_at: string }>(`/api/jobs/${id}/draft`,
      { method: "PUT", body: JSON.stringify(draft) }),
  discardDraft: (id: string) => req(`/api/jobs/${id}/draft`, { method: "DELETE" }),

  refineScript: (id: string, instruction: string, render = true) =>
    req<{ script: { segments: ScriptSegment[]; title: string }; rendering: boolean }>(
      `/api/jobs/${id}/script/prompt`,
      { method: "POST", body: JSON.stringify({ instruction, render }) }),

  buildCaption: (id: string, instruction = "") =>
    req<PostCaption>(`/api/jobs/${id}/caption`,
      { method: "POST", body: JSON.stringify({ instruction }) }),

  timeline: (id: string) => req<Timeline>(`/api/jobs/${id}/timeline`),
  saveTimeline: (id: string, timeline: Timeline) =>
    req<Timeline>(`/api/jobs/${id}/timeline`,
      { method: "PUT", body: JSON.stringify(timeline) }),
  renderTimeline: (id: string, timeline: Timeline) =>
    req<{ duration: number; qa: QAReport }>(`/api/jobs/${id}/timeline/render`,
      { method: "POST", body: JSON.stringify(timeline) }),

  music: () => req<MusicTrack[]>("/api/music"),
  uploadMusic: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return req<MusicTrack>("/api/music", { method: "POST", body: form });
  },
  deleteMusic: (id: string) => req(`/api/music/${id}`, { method: "DELETE" }),
  events: (id: string, after = 0) =>
    req<{ id: number; level: string; message: string; created_at: string }[]>(
      `/api/jobs/${id}/events?after=${after}`),

  upload: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return req<UploadResult>("/api/uploads", { method: "POST", body: form });
  },

  voices: () => req<Voice[]>("/api/voices"),
  voicePresets: () => req<VoicePreset[]>("/api/voices/presets"),
  searchVoices: (query: string, language = "pt", pageSize = 20) =>
    req<CatalogVoice[]>(
      `/api/voices/catalog/fish?query=${encodeURIComponent(query)}` +
      `&language=${language}&page_size=${pageSize}`),
  addCatalogVoice: (form: FormData) =>
    req<Voice>("/api/voices", { method: "POST", body: form }),
  sampleUrl: (referenceId: string) =>
    `/api/voices/catalog/fish/${referenceId}/sample`,
  installPreset: (referenceId: string) =>
    req<Voice>(`/api/voices/presets/${referenceId}/install`, { method: "POST" }),
  edgeCatalog: (locale = "pt-BR") =>
    req<{ id: string; name: string; gender: string }[]>(`/api/voices/catalog/edge?locale=${locale}`),
  createVoice: (form: FormData) =>
    req<Voice>("/api/voices", { method: "POST", body: form }),
  deleteVoice: (id: string) => req(`/api/voices/${id}`, { method: "DELETE" }),

  accounts: () => req<Account[]>("/api/publish/accounts"),
  deleteAccount: (id: string) => req(`/api/publish/accounts/${id}`, { method: "DELETE" }),
  youtubeAuth: () => req<{ auth_url: string }>("/api/publish/youtube/auth"),
  tiktokAuth: () => req<{ auth_url: string }>("/api/publish/tiktok/auth"),

  connectors: () => req<Connector[]>("/api/connectors"),
  saveConnector: (id: string, values: Record<string, string>) =>
    req<Connector>(`/api/connectors/${id}`, { method: "PUT", body: JSON.stringify(values) }),
  clearConnector: (id: string) => req<Connector>(`/api/connectors/${id}`, { method: "DELETE" }),
  testConnector: (id: string) =>
    req<{ ok: boolean; message: string }>(`/api/connectors/${id}/test`, { method: "POST" }),

  publish: (body: Record<string, unknown>) =>
    req<{ schedule_id: string }>("/api/publish", { method: "POST", body: JSON.stringify(body) }),
  schedules: () => req<Schedule[]>("/api/publish/schedules"),
  deleteSchedule: (id: string) => req(`/api/publish/schedules/${id}`, { method: "DELETE" }),
  runSchedule: (id: string) => req(`/api/publish/schedules/${id}/run`, { method: "POST" }),

  // Your own avatar with your own voice. probe=true is what makes the local
  // XTTS server's state truthful; it costs one round trip.
  avatarCapabilities: (probe = true) =>
    req<AvatarReport>(`/api/avatar?probe=${probe}`),
  // Both answer 400 with the instruction when HeyGen is not configured — the
  // catalog belongs to the account, so there is nothing to show without it.
  avatarChoices: () => req<AvatarChoice[]>("/api/avatar/avatars"),
  avatarVoiceChoices: () => req<AvatarVoiceChoice[]>("/api/avatar/voices"),
  // An avatar video becomes an ordinary job, so following it goes through
  // /api/jobs like every other production.
  createAvatarVideo: (body: AvatarVideoInput) =>
    req<{ job_id: string; status: string }>("/api/avatar/videos",
      { method: "POST", body: JSON.stringify(body) }),
  // Multipart: the sample goes up with the name. Audio or video — the audio
  // is extracted from a video server-side.
  cloneVoice: (form: FormData) =>
    req<ClonedVoice>("/api/voices/clone", { method: "POST", body: form }),
  // The stored sample itself, playable even when nothing is configured —
  // unlike /preview, which has to synthesize.
  voiceSampleUrl: (voiceId: string) => `/api/voices/${voiceId}/sample`,
};
