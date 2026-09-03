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
  edit_mode: "narrar_por_cima" | "resumo";
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
    | "imagem_kenburns" | "codigo_scroll" | "ia_video" | "upload";
  background_query: string;
  music: boolean;
  music_track: string;
  music_volume: number;
  caption_offset: number;
  hook_hard: boolean;
  cta: string;
  title_overlay: boolean;
  watermark: string;
  variants: number;
  qa_autofix: boolean;
  qa_max_attempts: number;
}

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

export interface Timeline {
  duration: number;
  video: TimelineVideoClip[];
  audio: TimelineAudioClip[];
  captions: TimelineCue[];
  caption_style: string;
  caption_position: string;
  watermark: string;
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
  script: { segments: { kind: string; text: string; on_screen: string }[] };
  words: { word: string; start: number; end: number }[];
  source_kind?: string;
  edit_mode?: string;
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
  heat_kind: "searches" | "rising" | "reddit_rising" | "points_comments" | "views";
  heat_data: Record<string, string | number>;
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
}

export interface ClipPlan {
  id: string;
  attachment_id: string;
  status: "queued" | "analisando" | "ready" | "rendered" | "error";
  requested: number;
  target_seconds: number;
  options: { niche: string; language: string } | null;
  clips: ClipInfo[] | null;
  jobs: string[] | null;
  error: string | null;
  created_at: string;
}

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

export const api = {
  health: () => req<Health>("/api/health"),
  config: () => req<{ niches: string[]; min_seconds: number; max_seconds: number }>("/api/config"),

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
    req<{ items: TrendItem[]; sources: TrendSource[] }>(
      `/api/trends?niche=${niche}&geo=${geo}`),

  clipPlans: () => req<ClipPlan[]>("/api/clips"),
  clipPlan: (id: string) => req<ClipPlan>(`/api/clips/${id}`),
  createClipPlan: (body: { attachment_id: string; count: number; target_seconds: number;
                           niche: string; language?: string }) =>
    req<{ plan_id: string }>("/api/clips", { method: "POST", body: JSON.stringify(body) }),
  deleteClipPlan: (id: string) => req(`/api/clips/${id}`, { method: "DELETE" }),
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
};
