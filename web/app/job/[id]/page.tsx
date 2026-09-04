"use client";

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { api, PLATFORM_LABEL, type Account, type HookOption, type Job,
         type LLMCall, type MetricRow } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { Editor } from "@/components/Editor";
import { TimelineEditor } from "@/components/TimelineEditor";
import { LogStream } from "@/components/LogStream";
import { PhonePreview } from "@/components/PhonePreview";
import { QAPanel } from "@/components/QAPanel";
import { Chips, Field, StatusTag, TableWrap, Topbar, useToast } from "@/components/ui";

// Technical stage names: they are compared against job.stage and sent to the
// backend, so they are never translated — only the on-screen label is.
const STAGES = ["ingest", "roteiro", "voz", "legendas", "fundo", "render", "qa"];
// same order as RESUME_STAGES in the backend: the menu slices from here onward
const RESUMABLE = ["voz", "fundo", "legendas", "render"];

/** Translated label for a stage, keeping the technical key as the fallback. */
function useStageLabel() {
  const { t } = useI18n();
  return (stage: string) =>
    (t.job.stages as Record<string, string>)[stage] ?? stage;
}

export default function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { t, f } = useI18n();
  const stageLabel = useStageLabel();
  const { toast, node } = useToast();
  const [job, setJob] = useState<Job | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [tab, setTab] = useState<"preview" | "editor" | "timeline">("preview");

  useEffect(() => {
    const pull = () => api.job(id).then(setJob).catch(() => undefined);
    pull();
    const timer = setInterval(pull, 2500);
    api.accounts().then(setAccounts).catch(() => setAccounts([]));
    return () => clearInterval(timer);
  }, [id]);

  if (!job) {
    return (
      <>
        <Topbar title={t.job.loadingTitle} />
        <div className="content">
          <div className="empty">{t.job.loadingBody}</div>
        </div>
      </>
    );
  }

  const live = job.status === "running" || job.status === "queued";

  return (
    <>
      <Topbar title={job.title || t.job.untitled}>
        <StatusTag status={job.status} />
        {job.status === "error" || job.status === "done" ? (
          <RetryMenu job={job} onRetry={(from) =>
            api.retryJob(id, from)
              .then(() => toast(from
                ? f(t.job.resuming, { stage: stageLabel(from) })
                : t.job.requeued))
              .catch((e) => toast((e as Error).message))} />
        ) : null}
        <button
          className="btn sm danger"
          onClick={async () => {
            await api.deleteJob(id);
            router.push("/");
          }}
        >
          {t.common.delete}
        </button>
      </Topbar>

      <div className="content grid" style={{ gap: 18 }}>
        <div className="steps">
          {STAGES.map((stage) => (
            <div key={stage} data-on={STAGES.indexOf(job.stage ?? "") >= STAGES.indexOf(stage)}>
              {stageLabel(stage)}
            </div>
          ))}
        </div>

        {live ? (
          <div className="panel panel-body grid" style={{ gap: 10 }}>
            <div className="row spread">
              <span className="label">
                {t.job.currentStage} · {stageLabel(job.stage ?? "")}
              </span>
              <span className="mono dim" style={{ fontSize: 12 }}>
                {(job.progress * 100).toFixed(0)}%
              </span>
            </div>
            <div className="bar">
              <i style={{ width: `${Math.max(job.progress * 100, 3)}%` }} />
            </div>
          </div>
        ) : null}

        {job.error ? (
          <div className="issue" data-sev="fatal">
            <span className="label" style={{ minWidth: 52 }}>{t.job.failure}</span>
            <div className="mono" style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>{job.error}</div>
          </div>
        ) : null}

        <div className="side">
          <div className="grid" style={{ gap: 16 }}>
            {job.result ? (
              <div className="steps">
                <div data-on={tab === "preview"} onClick={() => setTab("preview")}
                     style={{ cursor: "pointer" }}>{t.job.tabPreview}</div>
                <div data-on={tab === "editor"} onClick={() => setTab("editor")}
                     style={{ cursor: "pointer" }}>{t.job.tabEditor}</div>
                <div data-on={tab === "timeline"} onClick={() => setTab("timeline")}
                     style={{ cursor: "pointer" }}>{t.job.tabTimeline}</div>
              </div>
            ) : null}

            {tab === "timeline" && job.result ? (
              <TimelineEditor
                jobId={id}
                version={job.updated_at}
                toast={toast}
                onRendered={() => {
                  setTab("preview");
                  api.job(id).then(setJob).catch(() => undefined);
                }}
              />
            ) : tab === "editor" && job.result ? (
              <>
                <Editor
                  job={job}
                  toast={toast}
                  onApplied={() => {
                    setTab("preview");
                    api.job(id).then(setJob).catch(() => undefined);
                  }}
                />
                <div className="row spread wrap">
                  <span className="label">{t.job.timelineHint}</span>
                  <button className="btn" onClick={() => setTab("timeline")}>
                    {t.job.openTimeline}
                  </button>
                </div>
              </>
            ) : job.result ? (
              <>
                <PhonePreview
                  src={`/api/jobs/${id}/file/short.mp4?v=${job.updated_at}`}
                  poster={`/api/jobs/${id}/file/thumb.jpg?v=${job.updated_at}`}
                />
                <div className="row wrap" style={{ gap: 10 }}>
                  <button className="btn grow" onClick={() => setTab("editor")}>
                    {t.job.editScript}
                  </button>
                  <button className="btn grow" onClick={() => setTab("timeline")}>
                    {t.job.editTimeline}
                  </button>
                </div>
              </>
            ) : (
              <div className="stage">
                <div className="phone" style={{ display: "grid", placeItems: "center" }}>
                  <span className="label">{t.job.rendering}</span>
                </div>
              </div>
            )}

            {job.result && tab === "preview" && !live ? (
              <HooksPanel jobId={id} job={job} toast={toast}
                          onChanged={() => api.job(id).then(setJob).catch(() => undefined)} />
            ) : null}

            {job.qa && tab === "preview" ? (
              <QAPanel
                report={job.qa}
                onRerun={() => api.rerunQA(id).then(() => toast(t.job.qaRerun))}
              />
            ) : null}

            {job.result?.qa_attempts?.length && tab === "preview" ? (
              <section className="panel">
                <div className="panel-head">
                  <span className="label">{t.job.qaAutofixTitle}</span>
                  <div className="grow" />
                  <span className="tag" data-tone="amber">
                    {f(t.job.qaAutofixCount, { n: job.result.qa_attempts.length })}
                  </span>
                </div>
                <div className="panel-body grid" style={{ gap: 8 }}>
                  <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                    {t.job.qaAutofixExplain}
                  </p>
                  {job.result.qa_attempts.map((attempt) => (
                    <div className="issue" data-sev="aviso" key={attempt.attempt}>
                      <span className="label" style={{ minWidth: 52, paddingTop: 2 }}>
                        #{attempt.attempt}
                      </span>
                      <div>
                        <div style={{ marginBottom: 4 }}>{attempt.action}</div>
                        <div className="mono dimmer" style={{ fontSize: 11.5 }}>
                          {f(t.job.qaScoreBefore, { score: attempt.report.score })}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}

            {job.result && tab === "preview" ? (
              <section className="panel">
                <div className="panel-head">
                  <span className="label">{t.job.scriptTitle}</span>
                  <div className="grow" />
                  <a className="btn sm ghost" href={`/api/jobs/${id}/file/captions.srt`}>
                    {t.job.downloadSrt}
                  </a>
                  <a className="btn sm ghost" href={`/api/jobs/${id}/file/short.mp4`} download>
                    {t.job.downloadMp4}
                  </a>
                </div>
                <div className="panel-body grid" style={{ gap: 10 }}>
                  {job.result.script.segments.map((segment, index) => (
                    <div className="row" style={{ gap: 12, alignItems: "start" }} key={index}>
                      <span className="tag" data-tone={segment.kind === "hook" ? "amber" : ""}>
                        {(t.segmentKinds as Record<string, string>)[segment.kind]
                          ?? segment.kind}
                      </span>
                      <p style={{ margin: 0, lineHeight: 1.6 }} className="grow">
                        {segment.text}
                      </p>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}
          </div>

          <div className="grid" style={{ gap: 16 }}>
            {job.result?.cover && !live ? (
              <CoverPanel jobId={id} job={job} toast={toast}
                          onChanged={() => api.job(id).then(setJob).catch(() => undefined)} />
            ) : null}

            {job.result ? (
              <PublishBox jobId={id} job={job} accounts={accounts} toast={toast}
                          onCaption={() => api.job(id).then(setJob).catch(() => undefined)} />
            ) : null}

            {Array.isArray(job.metrics) && job.metrics.length ? (
              <MetricsPanel rows={job.metrics} />
            ) : null}

            <section className="panel">
              <div className="panel-head">
                <span className="label">{t.job.logTitle}</span>
                {live ? <i className="dot pulse" style={{ color: "var(--cyan)" }} /> : null}
              </div>
              <div className="panel-body">
                <LogStream jobId={id} live={live} />
              </div>
            </section>

            {job.llm_calls?.length ? <LLMPanel calls={job.llm_calls} /> : null}

            <section className="panel">
              <div className="panel-head">
                <span className="label">{t.job.paramsTitle}</span>
              </div>
              <div className="panel-body grid" style={{ gap: 9 }}>
                {Object.entries(job.input).map(([key, value]) => (
                  <div className="row spread" key={key}>
                    <span className="label">{key}</span>
                    <span className="mono dim" style={{ fontSize: 11.5, maxWidth: 190,
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {String(value)}
                    </span>
                  </div>
                ))}
              </div>
            </section>
          </div>
        </div>
      </div>
      {node}
    </>
  );
}

function RetryMenu({ job, onRetry }: { job: Job; onRetry: (from: string) => void }) {
  const { t } = useI18n();
  const stageLabel = useStageLabel();
  const [open, setOpen] = useState(false);
  const resumable = job.resumable_from;
  const allowed = resumable ? RESUMABLE.slice(RESUMABLE.indexOf(resumable)) : [];
  if (!allowed.length) {
    return (
      <button className="btn sm" onClick={() => onRetry("")}>{t.job.reprocess}</button>
    );
  }
  return (
    <div style={{ position: "relative" }}>
      <button className="btn sm" onClick={() => setOpen((v) => !v)}>
        {t.job.reprocess} {open ? "▴" : "▾"}
      </button>
      {open ? (
        <div className="panel" style={{ position: "absolute", right: 0, top: "110%", zIndex: 20,
                                        minWidth: 240, boxShadow: "var(--shadow)" }}>
          <div className="panel-body grid" style={{ gap: 4, padding: 8 }}>
            <span className="label" style={{ padding: "4px 6px" }}>{t.job.resumeHint}</span>
            {allowed.map((stage) => (
              <button key={stage} className="btn sm ghost" style={{ justifyContent: "flex-start" }}
                      onClick={() => { setOpen(false); onRetry(stage); }}>
                {t.job.resumeFrom} <b style={{ marginLeft: 4 }}>{stageLabel(stage)}</b>
              </button>
            ))}
            <hr className="rule" style={{ margin: "4px 0" }} />
            <button className="btn sm ghost" style={{ justifyContent: "flex-start" }}
                    onClick={() => { setOpen(false); onRetry(""); }}>
              {t.job.fromStart}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

// Alternative hooks: listen to each one in the job's voice, swap it in place or
// create the A/B pair as a new job to publish both and compare in Performance.
function HooksPanel({ jobId, job, toast, onChanged }: {
  jobId: string; job: Job; toast: (m: string) => void; onChanged: () => void;
}) {
  const { t, f } = useI18n();
  const [busy, setBusy] = useState(false);
  const [acting, setActing] = useState("");
  const variants = job.result?.hook_variants;

  const generate = async () => {
    setBusy(true);
    try {
      await api.buildHooks(jobId, 3);
      onChanged();
      toast(t.hooks.generated);
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };

  const apply = async (hook: HookOption) => {
    setActing(`apply-${hook.index}`);
    try {
      await api.applyHook(jobId, hook.text);
      toast(t.hooks.applied);
      onChanged();
    } catch (e) { toast((e as Error).message); } finally { setActing(""); }
  };

  const fork = async (hook: HookOption) => {
    setActing(`fork-${hook.index}`);
    try {
      const { job_id } = await api.forkHook(jobId, hook.text);
      toast(f(t.hooks.forked, { id: job_id.slice(0, 12) }));
    } catch (e) { toast((e as Error).message); } finally { setActing(""); }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.hooks.title}</span>
        <div className="grow" />
        <button className="btn sm ghost" onClick={generate} disabled={busy}>
          {busy ? t.common.generating
                : variants ? t.hooks.generateMore : t.hooks.generate}
        </button>
      </div>
      <div className="panel-body grid" style={{ gap: 10 }}>
        {!variants ? (
          <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
            {t.hooks.explain}
          </p>
        ) : (
          <>
            <div className="issue" data-sev="info">
              <span className="label" style={{ minWidth: 52, paddingTop: 2 }}>
                {t.hooks.current}
              </span>
              <div style={{ lineHeight: 1.5 }}>{variants.current}</div>
            </div>
            {variants.options.map((hook) => (
              <div className="issue" data-sev="aviso" key={hook.index} style={{ alignItems: "start" }}>
                <span className="label" style={{ minWidth: 52, paddingTop: 2 }}>
                  {String.fromCharCode(66 + hook.index)}
                </span>
                <div className="grow grid" style={{ gap: 6 }}>
                  <div style={{ lineHeight: 1.5 }}>{hook.text}</div>
                  <div className="mono dimmer" style={{ fontSize: 11 }}>
                    {t.hooks.mechanism}: {hook.mechanism} · {t.hooks.why}: {hook.why}
                  </div>
                  {hook.audio ? (
                    <audio controls preload="none" src={`${hook.audio}?v=${job.updated_at}`}
                           style={{ width: "100%", height: 30 }} />
                  ) : hook.audio_error ? (
                    <span className="mono" style={{ fontSize: 11, color: "var(--warn)" }}>
                      {f(t.hooks.previewUnavailable, { message: hook.audio_error })}
                    </span>
                  ) : null}
                  <div className="row wrap" style={{ gap: 6 }}>
                    <button className="btn sm" disabled={!!acting}
                            onClick={() => apply(hook)}>
                      {acting === `apply-${hook.index}` ? t.hooks.using : t.hooks.use}
                    </button>
                    <button className="btn sm ghost" disabled={!!acting}
                            onClick={() => fork(hook)}>
                      {acting === `fork-${hook.index}` ? t.hooks.forking : t.hooks.fork}
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </section>
  );
}

function CoverPanel({ jobId, job, toast, onChanged }: {
  jobId: string; job: Job; toast: (m: string) => void; onChanged: () => void;
}) {
  const { t, f } = useI18n();
  const [title, setTitle] = useState(job.result?.title ?? "");
  const [at, setAt] = useState<number>(job.result?.cover_at ?? 1);
  const [busy, setBusy] = useState(false);
  const [tocado, setTocado] = useState(false);
  const duration = job.result?.duration ?? 10;
  const coverAt = job.result?.cover_at ?? null;

  // The page reloads the job every 2.5s. Without syncing, the local state stays
  // frozen at the value from the first mount and the slider starts lying about
  // which frame the cover actually uses. It only syncs while the user has not
  // touched anything, so we don't overwrite what they are adjusting.
  useEffect(() => {
    if (!tocado && coverAt != null) setAt(coverAt);
  }, [coverAt, tocado]);
  useEffect(() => {
    if (!tocado && job.result?.title) setTitle(job.result.title);
  }, [job.result?.title, tocado]);

  const rebuild = async (auto: boolean) => {
    setBusy(true);
    try {
      const r = await api.rebuildCover(jobId, title, auto ? null : at);
      setAt(r.at);
      setTocado(false);   // back to reflecting what is on disk
      onChanged();
      toast(f(t.cover.done, { at: r.at.toFixed(1) }));
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.cover.title}</span>
        <div className="grow" />
        <span className="tag" data-tone={tocado ? "amber" : ""}>
          {f(t.cover.frame, { at: (coverAt ?? at).toFixed(1) })}
        </span>
      </div>
      <div className="panel-body" style={{ display: "grid", gridTemplateColumns: "96px 1fr", gap: 12 }}>
        <img src={`/api/jobs/${jobId}/file/cover.jpg?v=${job.updated_at}`} alt={t.cover.title}
             style={{ width: 96, aspectRatio: "9/16", objectFit: "cover",
                      borderRadius: "var(--r)", border: "1px solid var(--line)" }} />
        <div className="grid" style={{ gap: 8 }}>
          <Field label={t.cover.titleField}>
            <input className="input" value={title}
                   onChange={(e) => { setTocado(true); setTitle(e.target.value); }} />
          </Field>
          <Field label={t.cover.frameField}
                 hint={`${at.toFixed(1)}${t.common.seconds}${
                   tocado ? ` · ${t.cover.notApplied}` : ""}`}>
            <input type="range" min={0.3} max={Math.max(duration - 0.5, 1)} step={0.1}
                   value={at}
                   onChange={(e) => { setTocado(true); setAt(Number(e.target.value)); }} />
          </Field>
          <div className="row wrap" style={{ gap: 6 }}>
            <button className="btn sm" disabled={busy} onClick={() => rebuild(false)}>
              {busy ? t.common.generating : t.cover.rebuild}
            </button>
            <button className="btn sm ghost" disabled={busy} onClick={() => rebuild(true)}>
              {t.cover.auto}
            </button>
            {/* The cover is only useful outside the app — as the thumbnail you
                upload by hand. Without this it was generated and then trapped
                in the panel. `?v=` keeps a rebuilt cover from being served
                from the browser cache. */}
            <a className="btn sm ghost"
               href={`/api/jobs/${jobId}/file/cover.jpg?v=${job.updated_at}`}
               download={`cover-${jobId}.jpg`}>
              {t.cover.download}
            </a>
          </div>
          <p className="dimmer" style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5 }}>
            {t.cover.explain}
          </p>
        </div>
      </div>
    </section>
  );
}

function MetricsPanel({ rows }: { rows: MetricRow[] }) {
  const { t, f, dateTime } = useI18n();
  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.job.performanceTitle}</span>
        <div className="grow" />
        <span className="label">
          {f(t.job.performanceUpdated, { when: dateTime(rows[0].fetched_at) })}
        </span>
      </div>
      <TableWrap>
        <table className="table">
          <thead>
            <tr>
              <th>{t.job.metricsPlatform}</th>
              <th>{t.job.metricsViews}</th>
              <th>{t.job.metricsLikes}</th>
              <th>{t.job.metricsComments}</th>
              <th>{t.job.metricsRetention}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.schedule_id}>
                <td>
                  {r.url ? <a href={r.url} target="_blank" rel="noreferrer" style={{ color: "var(--amber)" }}>
                    {PLATFORM_LABEL[r.platform] ?? r.platform}</a> : PLATFORM_LABEL[r.platform] ?? r.platform}
                  {r.error ? <div className="mono" style={{ fontSize: 10.5, color: "var(--err)" }}>{r.error.slice(0, 60)}</div> : null}
                </td>
                <td className="mono">{formatCount(r.views)}</td>
                <td className="mono">{formatCount(r.likes)}</td>
                <td className="mono">{formatCount(r.comments)}</td>
                <td className="mono">{r.avg_view_pct != null ? `${r.avg_view_pct.toFixed(0)}%` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    </section>
  );
}

function LLMPanel({ calls }: { calls: LLMCall[] }) {
  const { t } = useI18n();
  const total = calls.reduce((s, c) => s + c.seconds, 0);
  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.job.aiCallsTitle}</span>
        <div className="grow" />
        <span className="tag">{calls.length} · {total.toFixed(0)}{t.common.seconds}</span>
      </div>
      <div className="panel-body grid" style={{ gap: 6 }}>
        {calls.map((c) => (
          <div className="row spread" key={c.id} style={{ gap: 8 }}>
            <span className="mono" style={{ fontSize: 11.5 }}>
              <i className="dot" style={{ color: c.ok ? "var(--ok)" : "var(--err)", marginRight: 6 }} />
              {c.purpose} · {c.provider}{c.model ? `:${c.model}` : ""}
            </span>
            <span className="mono dim" style={{ fontSize: 11.5 }} title={c.error ?? ""}>
              {c.ok ? `${c.seconds.toFixed(1)}${t.common.seconds}` : t.job.aiCallFailed}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function PublishBox({ jobId, job, accounts, toast, onCaption }: {
  jobId: string; job: Job; accounts: Account[];
  toast: (m: string) => void; onCaption: () => void;
}) {
  const { t, f } = useI18n();
  const [accountId, setAccountId] = useState("");
  const [privacy, setPrivacy] = useState("private");
  const [mode, setMode] = useState<"agora" | "agendar">("agora");
  const [when, setWhen] = useState("");
  const [busy, setBusy] = useState(false);
  const [generating, setGenerating] = useState(false);

  const caption = job.result?.caption;
  const account = accounts.find((a) => a.id === accountId);
  const platform = account?.platform;
  const blocked = job.qa ? !job.qa.passed : false;

  // each platform has its own text; the title only exists on YouTube
  const [title, setTitle] = useState(caption?.youtube_titulo ?? job.result?.title ?? "");
  const [body, setBody] = useState(caption?.youtube_descricao ?? job.result?.description ?? "");

  useEffect(() => {
    if (!caption) return;
    setTitle(caption.youtube_titulo);
    setBody(platform === "tiktok" ? caption.tiktok_legenda
      : platform === "instagram" ? caption.instagram_legenda
      : caption.youtube_descricao);
  }, [caption, platform]);

  const hashtags = caption?.hashtags ?? job.result?.hashtags ?? [];

  const generate = async () => {
    setGenerating(true);
    try {
      await api.buildCaption(jobId);
      onCaption();
      toast(t.publish.captionDone);
    } catch (error) {
      toast((error as Error).message);
    } finally { setGenerating(false); }
  };

  const copy = async () => {
    // What gets pasted into the app is title + text + hashtags, in that order —
    // the same shape the publishers assemble.
    const parts = [platform === "tiktok" || platform === "instagram" ? "" : title,
                   body, hashtags.join(" ")];
    try {
      await navigator.clipboard.writeText(
        parts.filter((p) => p.trim()).join("\n\n"));
      toast(t.publish.copied);
    } catch {
      // no clipboard permission (or a non-secure origin): the text is on
      // screen and selectable, so say that instead of failing silently
      toast(t.publish.copyFailed);
    }
  };

  const send = async () => {
    if (!account) { toast(t.publish.pickAccount); return; }
    if (mode === "agendar" && !when) { toast(t.publish.pickDate); return; }
    setBusy(true);
    try {
      await api.publish({
        job_id: jobId,
        account_id: accountId,
        platform: account.platform,
        title,
        description: body,
        tags: hashtags,
        privacy,
        publish_at: mode === "agendar" ? new Date(when).toISOString() : null,
      });
      toast(mode === "agendar" ? t.publish.scheduled : t.publish.queued);
    } catch (error) {
      toast((error as Error).message);
    } finally { setBusy(false); }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.publish.title}</span>
        <div className="grow" />
        {blocked ? (
          <span className="tag" data-tone="err">{t.publish.qaBlocked}</span>
        ) : (
          <span className="tag" data-tone="ok">{t.publish.qaApproved}</span>
        )}
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
        {blocked ? (
          <div className="issue" data-sev="erro">
            <span className="label" style={{ minWidth: 52, paddingTop: 2 }}>
              {t.publish.blockLabel}
            </span>
            <div>{t.publish.blockedExplain}</div>
          </div>
        ) : null}

        <div className="row spread wrap">
          <span className="label">{t.publish.captionLabel}</span>
          <div className="grow" />
          <button className="btn sm ghost" onClick={copy}>{t.publish.copy}</button>
          <button className="btn sm ghost" onClick={generate} disabled={generating}>
            {generating ? t.common.generating
                        : caption ? t.common.regenerate : t.common.generate}
          </button>
        </div>

        {/* The text is editable and copyable with no account connected: for
            anyone posting by hand, this panel *is* the deliverable. It used to
            live inside the account branch, so a caption was generated and then
            only its hashtags were visible. */}
        {platform !== "tiktok" && platform !== "instagram" ? (
          <Field label={t.publish.videoTitle} hint={`${title.length}/100`}>
            <input className="input" value={title} maxLength={100}
                   onChange={(e) => setTitle(e.target.value)} />
          </Field>
        ) : null}

        <Field
          label={platform === "tiktok" || platform === "instagram"
            ? t.publish.caption : t.publish.description}
          hint={f(t.publish.chars, { n: body.length })}
        >
          <textarea className="textarea" style={{ minHeight: 96 }} value={body}
                    onChange={(e) => setBody(e.target.value)} />
        </Field>

        {hashtags.length ? (
          <div className="chips">
            {hashtags.map((tag) => (
              <span className="tag" key={tag}>{tag}</span>
            ))}
          </div>
        ) : (
          <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
            {t.publish.noCaption}
          </p>
        )}

        {accounts.length === 0 ? (
          // The <b> comes from our own dictionary, not from user text.
          <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
            <span dangerouslySetInnerHTML={{ __html: t.publish.noAccounts }} />
          </p>
        ) : (
          <>
            <Field label={t.publish.account}>
              <select className="select" value={accountId}
                      onChange={(e) => setAccountId(e.target.value)}>
                <option value="">{t.publish.select}</option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {PLATFORM_LABEL[a.platform] ?? a.platform} · {a.display_name}
                  </option>
                ))}
              </select>
            </Field>

            <Field label={t.publish.when}>
              <Chips
                value={mode}
                onChange={setMode}
                options={[
                  { value: "agora", label: t.publish.now },
                  { value: "agendar", label: t.publish.later },
                ]}
              />
            </Field>

            <div className="two">
              <Field label={t.publish.privacy}>
                <select className="select" value={privacy}
                        onChange={(e) => setPrivacy(e.target.value)}>
                  <option value="private">{t.publish.private}</option>
                  <option value="unlisted">{t.publish.unlisted}</option>
                  <option value="public">{t.publish.public}</option>
                </select>
              </Field>
              {mode === "agendar" ? (
                <Field label={t.publish.datetime}>
                  <input className="input" type="datetime-local" value={when}
                         onChange={(e) => setWhen(e.target.value)} />
                </Field>
              ) : null}
            </div>

            <button className="btn primary" onClick={send} disabled={busy || blocked}>
              {busy ? t.publish.sending
                    : mode === "agendar" ? t.publish.submitLater : t.publish.submitNow}
            </button>
          </>
        )}
      </div>
    </section>
  );
}
