"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api, type Job, type ReelAssist, type Timeline, type UploadResult,
} from "@/lib/api";
import { LOCALES, LOCALE_NAMES, NARRATION_LANGUAGE, useI18n } from "@/lib/i18n";
import { readDefaultWatermark, saveDefaultWatermark } from "@/lib/watermark";
import {
  EMPTY_SOURCE, hasSource, SourcePicker, sourceBody, type SourceValue,
} from "@/components/SourcePicker";
import { Chips, Field, StatusTag, Topbar, useToast } from "@/components/ui";

const NICHE_KEYS = [
  "tecnologia", "ciberseguranca", "programacao", "cinema", "historia",
  "ciencia", "curiosidades", "negocios", "generico",
] as const;

const CAPTION_STYLE_KEYS = ["karaoke", "bloco", "palavra"] as const;
const CAPTION_POSITION_KEYS = ["centro", "baixo", "topo"] as const;

/** A reel becomes an ordinary job, marked by its edit mode. That is what
 *  gives it the timeline editor, QA, the cover and publishing for free — and
 *  it is also the only way to tell one apart in the job list. */
const isReel = (job: Job) => job.input?.edit_mode === "meu_video";

const isWorking = (status: Job["status"]) =>
  status === "queued" || status === "running";

const seconds = (value: number) => `${value.toFixed(1)}s`;

// You record; this edits. The speech is transcribed into captions timed to the
// actual words, and from there you lay media over yourself, ask for edits in
// your own words, and finish it on the timeline.
export default function ReelsEditor() {
  const { t, f, narrationLanguage } = useI18n();
  const { toast, node } = useToast();

  const [source, setSource] = useState<SourceValue>(EMPTY_SOURCE);
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [niche, setNiche] = useState("generico");
  const [language, setLanguage] = useState(narrationLanguage);
  const [captionStyle, setCaptionStyle] = useState<string>("karaoke");
  const [captionPosition, setCaptionPosition] = useState<string>("centro");
  const [watermark, setWatermark] = useState("");
  const [keepAudio, setKeepAudio] = useState(true);
  const [busy, setBusy] = useState(false);

  const [reels, setReels] = useState<Job[]>([]);
  const [active, setActive] = useState("");

  // the poll below closes over the first render, so the id it follows travels
  // in a ref instead of in the closure
  const activeRef = useRef("");
  useEffect(() => { activeRef.current = active; }, [active]);

  const languageTouched = useRef(false);
  useEffect(() => {
    if (!languageTouched.current) setLanguage(narrationLanguage);
  }, [narrationLanguage]);

  useEffect(() => { setWatermark(readDefaultWatermark()); }, []);

  const niches = useMemo(
    () => NICHE_KEYS.map((value) => ({ value, label: t.niches[value] })), [t]);
  const languages = useMemo(
    () => LOCALES.map((l) => ({ value: NARRATION_LANGUAGE[l], label: LOCALE_NAMES[l] })),
    []);

  const pull = useCallback(async () => {
    try {
      setReels((await api.jobs()).filter(isReel));
    } catch { /* a failed poll keeps what is already on screen */ }
  }, []);

  useEffect(() => {
    pull();
    const id = setInterval(pull, 5000);
    return () => clearInterval(id);
  }, [pull]);

  const prepare = async () => {
    if (!hasSource(source)) { toast(t.reels.needSource); return; }
    setBusy(true);
    try {
      const { job_id } = await api.createReel({
        ...sourceBody(source),
        title: title.trim(), niche, language, instruction: goal.trim(),
        caption_style: captionStyle, caption_position: captionPosition,
        watermark: watermark.trim(), keep_audio: keepAudio,
      });
      saveDefaultWatermark(watermark);
      setActive(job_id);
      setSource(EMPTY_SOURCE);
      toast(t.newJob.submitting);
      pull();
    } catch (e) {
      toast((e as Error).message);   // the server's own wording, as elsewhere
    } finally { setBusy(false); }
  };

  const current = reels.find((r) => r.id === active) ?? reels[0];

  return (
    <>
      <Topbar title={t.nav.reelsEditor}>
        <span className="label">{t.reels.subtitle}</span>
      </Topbar>

      <div className="content grid" style={{ gap: 18 }}>
        <div className="side">
          <div className="grid" style={{ gap: 14 }}>
            <section className="panel">
              <div className="panel-head">
                <span className="label">{t.reels.stepSource}</span>
              </div>
              <div className="panel-body grid" style={{ gap: 14 }}>
                <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                  {t.reels.intro}
                </p>

                <SourcePicker
                  value={source}
                  onChange={setSource}
                  toast={toast}
                  labels={{
                    origin: t.reels.origin,
                    exclusive: t.reels.exclusive,
                    fromFile: t.reels.fromFile,
                    fromUrl: t.reels.fromUrl,
                    drop: t.reels.drop,
                    dropping: t.reels.dropping,
                    uploading: t.newJob.uploading,
                    uploadFailed: t.newJob.uploadFailed,
                    swap: t.batch.swap,
                    urlLabel: t.reels.urlLabel,
                    urlHint: t.reels.urlHint,
                    urlPlaceholder: t.reels.urlPlaceholder,
                  }}
                />

                <Field label={t.reels.titleLabel} hint={t.common.optional}>
                  <input className="input" value={title}
                         placeholder={t.reels.titlePlaceholder}
                         onChange={(e) => setTitle(e.target.value)} />
                </Field>

                <Field label={t.reels.goal} hint={t.reels.goalHint}>
                  <textarea className="textarea" style={{ minHeight: 72 }}
                            value={goal} placeholder={t.reels.goalPlaceholder}
                            onChange={(e) => setGoal(e.target.value)} />
                </Field>
              </div>
            </section>

            <section className="panel">
              <div className="panel-head">
                <span className="label">{t.reels.stepStyle}</span>
              </div>
              <div className="panel-body grid" style={{ gap: 14 }}>
                <Field label={t.newJob.captionStyle}>
                  <Chips
                    value={captionStyle}
                    onChange={setCaptionStyle}
                    options={CAPTION_STYLE_KEYS.map((value) => ({
                      value, label: t.captionStyles[`${value}Short`],
                    }))}
                  />
                </Field>

                <Field label={t.newJob.captionPosition}>
                  <Chips
                    value={captionPosition}
                    onChange={setCaptionPosition}
                    options={CAPTION_POSITION_KEYS.map((value) => ({
                      value, label: t.positions[`${value}Short`],
                    }))}
                  />
                </Field>

                <Field label={t.newJob.watermark} hint={t.common.optional}>
                  <input className="input" value={watermark}
                         placeholder={t.newJob.watermarkPlaceholder}
                         onChange={(e) => setWatermark(e.target.value)} />
                </Field>

                {/* There is no TTS in this mode at all — the voice is yours.
                    So the only audio question left is whether to keep it. */}
                <Field label={t.reels.keepAudio} hint={t.reels.keepAudioHint}>
                  <Chips
                    value={keepAudio ? "on" : "off"}
                    onChange={(value) => setKeepAudio(value === "on")}
                    options={[{ value: "on", label: t.common.on },
                              { value: "off", label: t.common.off }]}
                  />
                </Field>

                <button className="btn primary" onClick={prepare}
                        disabled={busy || !hasSource(source)}>
                  {busy ? t.reels.submitting : t.reels.submit}
                </button>
              </div>
            </section>

            <ReelList reels={reels} active={current?.id ?? ""}
                      onPick={setActive} />
          </div>

          <div className="grid" style={{ gap: 14 }}>
            {current ? (
              <>
                <ReelStatus job={current} />
                <AssistPanel job={current} goal={goal} toast={toast}
                             onDone={pull} />
                <MediaPanel job={current} toast={toast} />
              </>
            ) : (
              <div className="empty">{t.reels.noMine}</div>
            )}
          </div>
        </div>
      </div>
      {node}
    </>
  );
}

// ------------------------------------------------------------------- the list

function ReelList({ reels, active, onPick }: {
  reels: Job[]; active: string; onPick: (id: string) => void;
}) {
  const { t } = useI18n();
  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.reels.mine}</span>
        <div className="grow" />
        <span className="tag">{reels.length}</span>
      </div>
      <div className="panel-body grid" style={{ gap: 6 }}>
        {reels.length === 0 ? (
          <span className="dimmer" style={{ fontSize: 12.5 }}>{t.reels.noMine}</span>
        ) : reels.map((reel) => (
          <button key={reel.id} className="btn sm ghost"
                  data-on={reel.id === active}
                  style={{ justifyContent: "space-between", width: "100%" }}
                  onClick={() => onPick(reel.id)}>
            <span style={{ minWidth: 0, overflow: "hidden",
                           textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {reel.title || t.dashboard.untitled}
            </span>
            <StatusTag status={reel.status} />
          </button>
        ))}
      </div>
    </section>
  );
}

function ReelStatus({ job }: { job: Job }) {
  const { t, f } = useI18n();
  const working = isWorking(job.status);

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{job.title || t.dashboard.untitled}</span>
        <div className="grow" />
        <StatusTag status={job.status} />
      </div>
      <div className="panel-body grid" style={{ gap: 10 }}>
        {working ? (
          <>
            <div className="bar"><i className="indeterminate" /></div>
            <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
              {t.reels.transcribing}
            </p>
          </>
        ) : null}

        {job.error ? (
          <div className="issue" data-sev="erro">
            <span className="label" style={{ minWidth: 52, paddingTop: 2 }}>
              {t.job.failure}
            </span>
            <div>{job.error}</div>
          </div>
        ) : null}

        {job.status === "done" ? (
          <>
            <video
              src={`/api/jobs/${job.id}/file/short.mp4?v=${job.updated_at}`}
              controls playsInline
              style={{ width: "100%", maxWidth: 260, aspectRatio: "9/16",
                       borderRadius: "var(--r)", border: "1px solid var(--line)",
                       background: "#000" }}
            />
            <div className="row wrap" style={{ gap: 6 }}>
              <Link className="btn sm" href={`/job/${job.id}`}>
                {t.reels.openEditor}
              </Link>
              {/* `download=1` is the server-side attachment; the HTML
                  attribute alone is ignored across origins. */}
              <a className="btn sm ghost"
                 href={`/api/jobs/${job.id}/file/short.mp4?download=1`}>
                {t.job.downloadMp4}
              </a>
              <a className="btn sm ghost"
                 href={`/api/jobs/${job.id}/file/captions.srt?download=1`}>
                {t.job.downloadSrt}
              </a>
            </div>
            {job.result?.words?.length ? (
              <span className="label">
                {job.result.words.length} {t.common.words}
              </span>
            ) : null}
          </>
        ) : null}
      </div>
    </section>
  );
}

// ----------------------------------------------------------------- the assist

function AssistPanel({ job, goal, toast, onDone }: {
  job: Job; goal: string; toast: (m: string) => void; onDone: () => void;
}) {
  const { t, f } = useI18n();
  const [instruction, setInstruction] = useState(goal);
  const [busy, setBusy] = useState(false);
  // What the last run found, kept on the job so reopening this screen shows it
  // instead of spending another LLM call to show the same thing.
  const stored = (job.result as { assist?: ReelAssist } | null)?.assist ?? null;
  const [assist, setAssist] = useState<ReelAssist | null>(stored);

  useEffect(() => { setAssist(stored); }, [stored]);

  const ask = async () => {
    setBusy(true);
    try {
      setAssist(await api.assistReel(job.id, instruction.trim()));
      onDone();
    } catch (e) {
      toast((e as Error).message);
    } finally { setBusy(false); }
  };

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast(t.reels.copied);
    } catch {
      toast(t.publish.copyFailed);
    }
  };

  const ready = job.status === "done";

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.reels.stepAssist}</span>
        <div className="grow" />
        {assist ? (
          <span className="tag" data-tone={assist.virality.score >= 70 ? "ok" : "amber"}>
            {assist.virality.score}/100
          </span>
        ) : null}
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
        <Field label={t.reels.goal} hint={t.reels.assistHint}>
          <textarea className="textarea" style={{ minHeight: 64 }}
                    value={instruction} placeholder={t.reels.goalPlaceholder}
                    onChange={(e) => setInstruction(e.target.value)} />
        </Field>

        <button className="btn" onClick={ask} disabled={busy || !ready}>
          {busy ? t.reels.assistAsking : t.reels.assistAsk}
        </button>

        {!ready ? (
          <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
            {t.reels.notReady}
          </p>
        ) : !assist ? (
          <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
            {t.reels.assistEmpty}
          </p>
        ) : (
          <div className="grid" style={{ gap: 12 }}>
            <div className="grid" style={{ gap: 4 }}>
              <span className="label">{t.reels.virality}</span>
              <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                {assist.virality.why}
              </p>
              {assist.virality.biggest_risk ? (
                <div className="issue" data-sev="aviso">
                  <span className="label" style={{ minWidth: 52, paddingTop: 2 }}>
                    {t.reels.viralityRisk}
                  </span>
                  <div>{assist.virality.biggest_risk}</div>
                </div>
              ) : null}
            </div>

            <Suggestions title={t.reels.hooksTitle} empty={t.reels.nothingHere}
                         items={assist.hooks.map((hook) => ({
                           key: hook.text,
                           head: hook.text,
                           body: hook.why,
                         }))} />

            <Suggestions title={t.reels.cutsTitle} empty={t.reels.nothingHere}
                         items={assist.cuts.map((cut) => ({
                           key: `${cut.start}-${cut.end}`,
                           head: `${seconds(cut.start)} → ${seconds(cut.end)}`,
                           body: cut.why,
                         }))} />

            <Suggestions title={t.reels.mediaTitle} empty={t.reels.nothingHere}
                         items={assist.media.map((item) => ({
                           key: `${item.start}-${item.what}`,
                           head: `${seconds(item.start)} → ${seconds(item.end)} · ${item.what}`,
                           body: item.stock_query
                             ? `${t.reels.stockQuery}: ${item.stock_query}`
                             : "",
                           // The prompt is ready to paste into whichever image
                           // generator is configured, so copying is the action.
                           action: item.image_prompt
                             ? { label: t.reels.copyPrompt,
                                 run: () => copy(item.image_prompt) }
                             : undefined,
                         }))} />

            <Suggestions title={t.reels.captionsTitle} empty={t.reels.nothingHere}
                         items={assist.captions.map((cue) => ({
                           key: `${cue.start}-${cue.text}`,
                           head: `${seconds(cue.start)} · ${cue.text}`,
                           body: cue.why,
                         }))} />

            {assist.hashtags.length ? (
              <div className="grid" style={{ gap: 6 }}>
                <div className="row spread wrap">
                  <span className="label">{t.publish.captionLabel}</span>
                  <button className="btn sm ghost"
                          onClick={() => copy([assist.title, assist.hashtags.join(" ")]
                            .filter(Boolean).join("\n\n"))}>
                    {t.publish.copy}
                  </button>
                </div>
                {assist.title ? (
                  <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                    {assist.title}
                  </p>
                ) : null}
                <div className="chips">
                  {assist.hashtags.map((tag) => (
                    <span className="tag" key={tag}>{tag}</span>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        )}
      </div>
    </section>
  );
}

/** One group of suggestions. An empty group says so rather than disappearing:
 *  "nothing to change here" is itself an answer, and a section that vanishes
 *  reads as a failure to produce one. */
function Suggestions({ title, empty, items }: {
  title: string;
  empty: string;
  items: {
    key: string; head: string; body: string;
    action?: { label: string; run: () => void };
  }[];
}) {
  return (
    <div className="grid" style={{ gap: 6 }}>
      <span className="label">{title}</span>
      {items.length === 0 ? (
        <span className="dimmer" style={{ fontSize: 12 }}>{empty}</span>
      ) : items.map((item) => (
        <div key={item.key} className="row spread wrap"
             style={{ gap: 8, alignItems: "start",
                      borderTop: "1px solid var(--line)", paddingTop: 6 }}>
          <div style={{ minWidth: 0 }}>
            <div className="mono" style={{ fontSize: 12, overflowWrap: "anywhere" }}>
              {item.head}
            </div>
            {item.body ? (
              <div className="dimmer" style={{ fontSize: 12, lineHeight: 1.6 }}>
                {item.body}
              </div>
            ) : null}
          </div>
          {item.action ? (
            <button className="btn sm ghost" onClick={item.action.run}>
              {item.action.label}
            </button>
          ) : null}
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ the media

/** Laying an image or clip over the recording — the picture-in-picture.
 *
 *  Position and size are set on a 9:16 preview rather than in pixels, because
 *  that is what the API stores: fractions of the frame. Dragging on the
 *  preview and the number that reaches the backend are then the same thing. */
function MediaPanel({ job, toast }: { job: Job; toast: (m: string) => void }) {
  const { t, f } = useI18n();
  const [upload, setUpload] = useState<UploadResult | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const preview = useRef<HTMLDivElement>(null);

  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(3);
  const [x, setX] = useState(0.5);
  const [y, setY] = useState(0.3);
  const [width, setWidth] = useState(0.6);
  const [opacity, setOpacity] = useState(1);
  const [busy, setBusy] = useState(false);

  const duration = timeline?.duration ?? job.result?.duration ?? 0;

  useEffect(() => {
    if (job.status !== "done") { setTimeline(null); return; }
    api.timeline(job.id).then(setTimeline).catch(() => setTimeline(null));
  }, [job.id, job.status, job.updated_at]);

  // A window that runs past the end would be clipped by the backend anyway;
  // clamping here keeps the sliders honest about what will happen.
  useEffect(() => {
    if (duration <= 0) return;
    setEnd((value) => Math.min(value || Math.min(3, duration), duration));
    setStart((value) => Math.min(value, Math.max(duration - 0.5, 0)));
  }, [duration]);

  const sendFile = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    try {
      setUpload(await api.upload(files[0]));
    } catch (e) {
      toast(f(t.newJob.uploadFailed, { message: (e as Error).message }));
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  /** Dragging the box moves its centre, which is exactly what x/y are. */
  const dragTo = (event: React.PointerEvent) => {
    const box = preview.current?.getBoundingClientRect();
    if (!box) return;
    setX(Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1));
    setY(Math.min(Math.max((event.clientY - box.top) / box.height, 0), 1));
  };

  const place = async () => {
    if (!upload) { toast(t.reels.mediaNeedFile); return; }
    setBusy(true);
    try {
      const fresh = await api.addReelMedia(job.id, {
        attachment_id: upload.id, start, end, x, y, width, opacity,
      });
      setTimeline(fresh);
      setUpload(null);
      toast(f(t.reels.mediaAdded, { start: start.toFixed(1), end: end.toFixed(1) }));
    } catch (e) {
      toast((e as Error).message);
    } finally { setBusy(false); }
  };

  if (job.status !== "done") return null;

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.reels.stepMedia}</span>
        <div className="grow" />
        <span className="tag">
          {timeline?.media?.length
            ? f(t.reels.mediaList, { n: timeline.media.length })
            : t.reels.mediaNone}
        </span>
      </div>
      <div className="panel-body grid" style={{ gap: 14 }}>
        <div className="grid" style={{ gap: 8 }}>
          <span className="label">{t.reels.mediaFile}</span>
          <label
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              sendFile(e.dataTransfer.files);
            }}
            style={{
              display: "grid", placeItems: "center", gap: 6, minHeight: 76,
              padding: "14px", textAlign: "center", cursor: "pointer",
              borderRadius: 3,
              border: `1px dashed ${dragging ? "var(--amber)" : "var(--line)"}`,
              background: dragging ? "#191300" : "var(--void)",
              color: "var(--ink-2)", fontSize: 12.5, lineHeight: 1.6,
            }}
          >
            <span>{dragging ? t.reels.dropping : t.reels.mediaDrop}</span>
            <input ref={fileInput} type="file" accept="image/*,video/*"
                   onChange={(e) => sendFile(e.target.files)}
                   style={{ position: "absolute", width: 1, height: 1,
                            opacity: 0, pointerEvents: "none" }} />
          </label>
          {uploading ? <span className="label">{t.newJob.uploading}</span> : null}
          {upload ? (
            <span className="mono" style={{ fontSize: 12, overflowWrap: "anywhere" }}>
              {upload.filename}
            </span>
          ) : null}
        </div>

        <div className="two">
          <div className="grid" style={{ gap: 10 }}>
            <Field label={t.reels.mediaWhen.replace("{start}", start.toFixed(1))
                            .replace("{end}", end.toFixed(1))}>
              <input type="range" min={0} max={Math.max(duration - 0.2, 0.2)}
                     step={0.1} value={start}
                     onChange={(e) => {
                       const next = Number(e.target.value);
                       setStart(next);
                       if (end <= next) setEnd(Math.min(next + 0.5, duration));
                     }} />
              <input type="range" min={0.2} max={Math.max(duration, 0.5)}
                     step={0.1} value={end}
                     onChange={(e) => setEnd(Math.max(Number(e.target.value),
                                                      start + 0.2))} />
            </Field>

            <Field label={t.reels.mediaSize}
                   hint={f(t.reels.mediaSizeValue, { n: Math.round(width * 100) })}>
              <input type="range" min={0.05} max={1} step={0.01} value={width}
                     onChange={(e) => setWidth(Number(e.target.value))} />
            </Field>

            <Field label={t.reels.mediaOpacity}
                   hint={`${Math.round(opacity * 100)}%`}>
              <input type="range" min={0.05} max={1} step={0.05} value={opacity}
                     onChange={(e) => setOpacity(Number(e.target.value))} />
            </Field>
          </div>

          <Field label={t.reels.mediaPreview} hint={t.reels.mediaPositionHint}>
            <div
              ref={preview}
              onPointerDown={(e) => {
                (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
                dragTo(e);
              }}
              onPointerMove={(e) => { if (e.buttons) dragTo(e); }}
              style={{
                position: "relative", width: "100%", maxWidth: 160,
                aspectRatio: "9/16", borderRadius: "var(--r)",
                border: "1px solid var(--line)", overflow: "hidden",
                background: "#000", cursor: "crosshair", touchAction: "none",
              }}
            >
              <img alt="" src={`/api/jobs/${job.id}/file/thumb.jpg`}
                   style={{ width: "100%", height: "100%", objectFit: "cover",
                            opacity: 0.55, pointerEvents: "none" }} />
              <div style={{
                position: "absolute",
                left: `${x * 100}%`, top: `${y * 100}%`,
                width: `${width * 100}%`,
                // square stand-in: the real height follows the media's own
                // aspect ratio, which the browser does not know until FFmpeg
                // scales it
                aspectRatio: "1/1",
                transform: "translate(-50%, -50%)",
                border: "1px solid var(--amber)",
                background: `rgba(255,176,0,${0.18 * opacity})`,
                pointerEvents: "none",
              }} />
            </div>
          </Field>
        </div>

        <button className="btn primary" onClick={place}
                disabled={busy || !upload || duration <= 0}>
          {busy ? t.reels.mediaAdding : t.reels.mediaAdd}
        </button>

        {timeline?.media?.length ? (
          <>
            <div className="grid" style={{ gap: 4 }}>
              {timeline.media.map((item) => (
                <div key={item.id} className="row spread wrap" style={{ gap: 8 }}>
                  <span className="mono" style={{ fontSize: 12,
                               overflowWrap: "anywhere" }}>
                    {item.source}
                  </span>
                  <span className="label">
                    {seconds(item.start)} → {seconds(item.end)} ·{" "}
                    {Math.round(item.width * 100)}%
                  </span>
                </div>
              ))}
            </div>
            <p className="dimmer" style={{ margin: 0, fontSize: 12, lineHeight: 1.6 }}>
              {t.reels.renderNeeded}
            </p>
          </>
        ) : null}
      </div>
    </section>
  );
}
