"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api, LIVECUT_MAX_CUTS, LIVECUT_MAX_WINDOW_MINUTES, LIVECUT_MIN_WINDOW_MINUTES,
  LIVECUT_MODE, type ClipPlan, type Voice,
} from "@/lib/api";
import { LOCALES, LOCALE_NAMES, NARRATION_LANGUAGE, useI18n } from "@/lib/i18n";
import { readDefaultWatermark } from "@/lib/watermark";
import {
  EMPTY_SOURCE, hasSource, SourcePicker, sourceBody, type SourceValue,
} from "@/components/SourcePicker";
import { Chips, Field, StatusTag, Topbar, useToast } from "@/components/ui";

// The key is what the API understands; the label is what the chosen language
// shows — same split as /novo and /lote.
const NICHE_KEYS = [
  "tecnologia", "ciberseguranca", "programacao", "cinema", "historia",
  "ciencia", "curiosidades", "negocios", "games", "saude", "politica", "generico",
] as const;

const CAPTION_STYLE_KEYS = ["karaoke", "bloco", "palavra"] as const;

/** A plan is still moving while it is queued or being transcribed. */
const isWorking = (status: ClipPlan["status"]) =>
  status === "queued" || status === "analisando";

/** A livestream row keeps the window it was analysed with in `options_json`. */
const windowMinutes = (plan: ClipPlan) =>
  Math.round((plan.options?.window_seconds ?? 40 * 60) / 60);

/** Timestamp inside the live. `formatSeconds` only ever counts minutes, which
 *  reads as "190:00" three hours into a stream — on this screen hours are the
 *  rule, so they get their own field. */
function stamp(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = String(total % 60).padStart(2, "0");
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}`
    : `${minutes}:${rest}`;
}

// One saved live becomes many vertical cuts: it downloads (when a link came
// in), transcribes, picks the moments window by window so they land across the
// whole stream, and each chosen cut becomes a regular job.
export default function LiveCuts() {
  const { t, f, date, narrationLanguage } = useI18n();
  const { toast, node } = useToast();

  const [source, setSource] = useState<SourceValue>(EMPTY_SOURCE);

  const [count, setCount] = useState(12);
  const [target, setTarget] = useState(45);
  const [niche, setNiche] = useState("generico");
  const [language, setLanguage] = useState(narrationLanguage);
  const [window_, setWindow] = useState(40);

  const [plans, setPlans] = useState<ClipPlan[]>([]);
  const [active, setActive] = useState("");
  const [busy, setBusy] = useState(false);

  // the interval below closes over the first render, so the id it has to
  // follow travels in a ref instead of in the closure
  const activeRef = useRef("");
  useEffect(() => { activeRef.current = active; }, [active]);

  const niches = useMemo(
    () => NICHE_KEYS.map((value) => ({ value, label: t.niches[value] })), [t]);
  const languages = useMemo(
    () => LOCALES.map((l) => ({ value: NARRATION_LANGUAGE[l], label: LOCALE_NAMES[l] })),
    []);

  // Only /api/clips lists plans, and it lists every kind, so the livestream
  // ones are picked out by the mark the livecuts router writes.
  const pull = useCallback(async () => {
    let live: ClipPlan[];
    try {
      live = (await api.clipPlans()).filter((p) => p.options?.mode === LIVECUT_MODE);
    } catch {
      return;   // a failed poll keeps the list it already had on screen
    }
    setPlans(live);

    // Following one plan goes through its own route: it is the documented
    // contract and the only thing that says "this id is not a live plan".
    const id = activeRef.current;
    if (!id || !live.some((p) => p.id === id)) return;
    try {
      const fresh = await api.liveCutPlan(id);
      setPlans((prev) => prev.map((p) => (p.id === id ? fresh : p)));
    } catch { /* the row from the list is good enough until the next tick */ }
  }, []);

  useEffect(() => {
    pull();
    const id = setInterval(pull, 5000);
    return () => clearInterval(id);
  }, [pull]);

  // narration follows the interface until the user picks something else
  const languageTouched = useRef(false);
  useEffect(() => {
    if (!languageTouched.current) setLanguage(narrationLanguage);
  }, [narrationLanguage]);

  const analyze = async () => {
    if (!hasSource(source)) { toast(t.liveCuts.needSource); return; }
    setBusy(true);
    try {
      const { plan_id } = await api.createLiveCutPlan({
        ...sourceBody(source),
        count, target_seconds: target, niche, language, window_minutes: window_,
      });
      setActive(plan_id);
      toast(t.batch.queued);
      pull();
    } catch (e) {
      toast((e as Error).message);   // the server's own wording, as elsewhere
    } finally { setBusy(false); }
  };

  const current = plans.find((p) => p.id === active) ?? plans[0];

  return (
    <>
      <Topbar title={t.nav.liveCuts}>
        <span className="label">{t.liveCuts.subtitle}</span>
      </Topbar>

      <div className="content grid" style={{ gap: 18 }}>
        <div className="side">
          <div className="grid" style={{ gap: 14 }}>
            <section className="panel">
              <div className="panel-head">
                <span className="label">{t.liveCuts.stepSource}</span>
              </div>
              <div className="panel-body grid" style={{ gap: 14 }}>
                <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                  {t.liveCuts.intro}
                </p>

                <SourcePicker
                  value={source}
                  onChange={setSource}
                  toast={toast}
                  labels={{
                    origin: t.liveCuts.origin,
                    exclusive: t.liveCuts.exclusive,
                    fromFile: t.liveCuts.fromFile,
                    fromUrl: t.liveCuts.fromUrl,
                    drop: t.liveCuts.drop,
                    dropping: t.liveCuts.dropping,
                    uploading: t.newJob.uploading,
                    uploadFailed: t.newJob.uploadFailed,
                    swap: t.batch.swap,
                    urlLabel: t.liveCuts.urlLabel,
                    urlHint: t.liveCuts.urlHint,
                    urlPlaceholder: t.liveCuts.urlPlaceholder,
                  }}
                />

                <div className="two">
                  <Field label={t.liveCuts.howMany} hint={`${count}`}>
                    <input type="range" min={1} max={LIVECUT_MAX_CUTS} step={1}
                           value={count}
                           onChange={(e) => setCount(Number(e.target.value))} />
                  </Field>
                  <Field label={t.batch.eachDuration} hint={`${target}${t.common.seconds}`}>
                    <input type="range" min={20} max={90} step={5} value={target}
                           onChange={(e) => setTarget(Number(e.target.value))} />
                  </Field>
                </div>

                <Field label={t.liveCuts.window}
                       hint={f(t.liveCuts.windowMinutes, { n: window_ })}>
                  <input type="range"
                         min={LIVECUT_MIN_WINDOW_MINUTES}
                         max={LIVECUT_MAX_WINDOW_MINUTES}
                         step={5} value={window_}
                         onChange={(e) => setWindow(Number(e.target.value))} />
                </Field>
                <p className="dimmer" style={{ margin: "-4px 0 0", fontSize: 11.5, lineHeight: 1.6 }}>
                  {t.liveCuts.windowHint}
                </p>

                <Field label={t.newJob.niche}>
                  <Chips value={niche} onChange={setNiche} options={niches} />
                </Field>

                <Field label={t.common.language}>
                  <select className="select" value={language}
                          onChange={(e) => {
                            languageTouched.current = true;
                            setLanguage(e.target.value);
                          }}>
                    {languages.map((l) => (
                      <option key={l.value} value={l.value}>{l.label}</option>
                    ))}
                  </select>
                </Field>

                {/* An upload in flight leaves `upload` null, so this covers
                    the uploading state too. */}
                <button className="btn primary" onClick={analyze}
                        disabled={busy || !hasSource(source)}>
                  {busy ? t.batch.analyzing : t.batch.analyze}
                </button>
              </div>
            </section>

            {current ? <PlanPanel plan={current} toast={toast} onChanged={pull} /> : null}
          </div>

          <aside className="grid" style={{ gap: 10 }}>
            <span className="label">{t.liveCuts.previous}</span>
            {plans.length === 0 ? (
              <div className="empty" style={{ padding: 22 }}>{t.liveCuts.noPrevious}</div>
            ) : plans.map((p) => (
              <button key={p.id} className="panel"
                      style={{ textAlign: "left", cursor: "pointer",
                               borderColor: p.id === current?.id ? "var(--amber)" : undefined }}
                      onClick={() => setActive(p.id)}>
                <div className="panel-body grid" style={{ gap: 6 }}>
                  <div className="row spread wrap" style={{ gap: 8 }}>
                    <StatusTag status={p.status} />
                    <span className="mono dimmer" style={{ fontSize: 11 }}>
                      {date(p.created_at)}
                    </span>
                  </div>
                  <span className="mono" style={{ fontSize: 12 }}>
                    {f(t.liveCuts.planSummary, {
                      count: p.requested, seconds: p.target_seconds,
                      window: windowMinutes(p),
                    })}
                  </span>
                  <div className="row wrap" style={{ gap: 6 }}>
                    <span className="tag">
                      {p.options?.url ? t.liveCuts.fromUrlTag : t.liveCuts.fromFileTag}
                    </span>
                    {p.clips?.length ? (
                      <span className="tag" data-tone="amber">
                        {f(t.liveCuts.cutsCount, { n: p.clips.length })}
                      </span>
                    ) : null}
                    {p.jobs?.length ? (
                      <span className="tag">{f(t.batch.planShorts, { n: p.jobs.length })}</span>
                    ) : null}
                  </div>
                </div>
              </button>
            ))}
          </aside>
        </div>
      </div>
      {node}
    </>
  );
}

function PlanPanel({ plan, toast, onChanged }: {
  plan: ClipPlan; toast: (m: string) => void; onChanged: () => void;
}) {
  const { t, f } = useI18n();
  const [selected, setSelected] = useState<number[]>([]);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voiceId, setVoiceId] = useState("");
  const [captionStyle, setCaptionStyle] = useState<string>("karaoke");
  const [music, setMusic] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.voices().then(setVoices).catch(() => setVoices([])); }, []);
  useEffect(() => {
    setSelected(plan.clips ? plan.clips.map((_, i) => i) : []);
  }, [plan.id, plan.clips?.length]);

  const clips = plan.clips ?? [];

  const toggle = (i: number) =>
    setSelected((prev) => prev.includes(i)
      ? prev.filter((x) => x !== i)
      : [...prev, i].sort((a, b) => a - b));

  const render = async () => {
    if (!selected.length) { toast(t.batch.pickClip); return; }
    setBusy(true);
    try {
      const r = await api.renderClips(plan.id, {
        selected, voice_id: voiceId || null, caption_style: captionStyle,
        caption_position: "centro", music, watermark: readDefaultWatermark(),
        qa_autofix: true, schedule: null,
      });
      toast(f(t.batch.done, { n: r.jobs.length }));
      onChanged();
    } catch (e) {
      toast((e as Error).message);
    } finally { setBusy(false); }
  };

  // Real elapsed time off the row, not a made-up percentage: the analysis has
  // no progress value to report, so what is shown is when it started.
  const startedMinutes = Math.max(
    0, Math.round((Date.now() - new Date(plan.created_at).getTime()) / 60000));

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.liveCuts.stepCuts}</span>
        <div className="grow" />
        <StatusTag status={plan.status} />
        <button className="btn sm ghost"
                onClick={() => api.deleteClipPlan(plan.id).then(onChanged)}>
          {t.common.remove}
        </button>
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
        {isWorking(plan.status) ? (
          <div className="grid" style={{ gap: 10 }}>
            <div className="row spread wrap" style={{ gap: 8 }}>
              <span className="label">{t.batch.working}</span>
              <span className="mono dimmer" style={{ fontSize: 11 }}>
                {f(t.liveCuts.running, { n: startedMinutes })}
              </span>
            </div>
            {/* indeterminate on purpose: the row carries no percentage */}
            <div className="bar"><i className="indeterminate" /></div>
            <p className="dimmer" style={{ margin: 0, fontSize: 12, lineHeight: 1.6 }}>
              {t.liveCuts.leave}
            </p>
          </div>
        ) : plan.status === "error" ? (
          <div className="issue" data-sev="fatal">
            <span className="label" style={{ minWidth: 52 }}>{t.job.failure}</span>
            <div className="mono" style={{ fontSize: 12, overflowWrap: "anywhere" }}>
              {plan.error}
            </div>
          </div>
        ) : clips.length ? (
          <>
            <div className="row spread wrap" style={{ gap: 8 }}>
              <span className="label">
                {f(t.liveCuts.found, { n: clips.length, sel: selected.length })}
              </span>
              <div className="row wrap" style={{ gap: 6 }}>
                <button className="btn sm ghost"
                        onClick={() => setSelected(clips.map((_, i) => i))}>
                  {t.liveCuts.selectAll}
                </button>
                <button className="btn sm ghost" onClick={() => setSelected([])}>
                  {t.liveCuts.clear}
                </button>
              </div>
            </div>

            {clips.map((clip, i) => (
              <label key={i} className="issue"
                     data-sev={selected.includes(i) ? "aviso" : "info"}
                     style={{ cursor: "pointer", alignItems: "start" }}>
                <input type="checkbox" checked={selected.includes(i)}
                       onChange={() => toggle(i)} style={{ marginTop: 3 }} />
                <div className="grow" style={{ minWidth: 0 }}>
                  <div className="row wrap" style={{ gap: 8, marginBottom: 3 }}>
                    <span className="tag" data-tone="amber">
                      {stamp(clip.inicio)} → {stamp(clip.fim)}
                    </span>
                    <span className="mono dimmer" style={{ fontSize: 11 }}>
                      {Math.round(clip.fim - clip.inicio)}{t.common.seconds}
                    </span>
                    {typeof clip.score === "number" ? (
                      <span className="tag">{t.liveCuts.score} {clip.score}</span>
                    ) : null}
                    {clip.assunto ? <span className="tag">{clip.assunto}</span> : null}
                  </div>
                  <b style={{ fontSize: 14 }}>{clip.titulo}</b>
                  {clip.motivo ? (
                    <p className="dim" style={{ margin: "3px 0 0", fontSize: 12.5, lineHeight: 1.5 }}>
                      {clip.motivo}
                    </p>
                  ) : null}
                </div>
              </label>
            ))}

            {plan.jobs?.length ? (
              <div className="row wrap" style={{ gap: 6 }}>
                <span className="label" style={{ alignSelf: "center" }}>
                  {t.batch.generatedShorts}
                </span>
                {plan.jobs.map((j) => (
                  <Link key={j} href={`/job/${j}`} className="tag"
                        style={{ color: "var(--amber)" }}>
                    {j.slice(4, 12)}
                  </Link>
                ))}
              </div>
            ) : null}

            <hr className="rule" />
            <div className="two">
              <Field label={t.batch.voice}>
                <select className="select" value={voiceId}
                        onChange={(e) => setVoiceId(e.target.value)}>
                  <option value="">{t.batch.voiceDefault}</option>
                  {voices.map((v) => (
                    <option key={v.id} value={v.id}>{v.name} · {v.provider}</option>
                  ))}
                </select>
              </Field>
              <Field label={t.newJob.captionStyle}>
                <Chips value={captionStyle} onChange={setCaptionStyle}
                       options={CAPTION_STYLE_KEYS.map((value) => ({
                         value, label: t.captionStyles[value],
                       }))} />
              </Field>
            </div>
            <div className="row wrap" style={{ gap: 18 }}>
              <button type="button" className="chip" data-on={music}
                      onClick={() => setMusic(!music)}>
                {music ? "✓ " : "○ "}{t.newJob.musicToggle}
              </button>
            </div>

            <button className="btn primary" onClick={render} disabled={busy}>
              {busy ? t.batch.submitting : f(t.batch.submit, { n: selected.length })}
            </button>
          </>
        ) : (
          <div className="empty" style={{ border: 0 }}>{t.batch.noClips}</div>
        )}
      </div>
    </section>
  );
}
