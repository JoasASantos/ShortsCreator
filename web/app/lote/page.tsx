"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { api, PLATFORM_LABEL, type Account, type ClipPlan, type UploadResult, type Voice } from "@/lib/api";
import { formatSeconds } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { Chips, Field, StatusTag, Topbar, useToast } from "@/components/ui";

// The key is what the API understands; the label is what the chosen language shows.
const NICHE_KEYS = [
  "tecnologia", "ciberseguranca", "programacao", "cinema", "historia",
  "ciencia", "curiosidades", "negocios", "generico",
] as const;

// One long video becomes N shorts: it transcribes, the LLM picks the moments,
// each one becomes a regular job — and, if you want, goes out scheduled one a day.
export default function Lote() {
  const { t, f, date } = useI18n();
  const { toast, node } = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const [upload, setUpload] = useState<UploadResult | null>(null);
  const [uploading, setUploading] = useState(false);
  const [count, setCount] = useState(5);
  const [target, setTarget] = useState(45);
  const [niche, setNiche] = useState("tecnologia");
  const [plans, setPlans] = useState<ClipPlan[]>([]);
  const [active, setActive] = useState<string>("");
  const [busy, setBusy] = useState(false);

  const niches: { value: string; label: string }[] =
    NICHE_KEYS.map((value) => ({ value, label: t.niches[value] }));

  const pull = () => api.clipPlans().then(setPlans).catch(() => setPlans([]));

  useEffect(() => {
    pull();
    const id = setInterval(pull, 5000);
    // coming from /novo with the video already uploaded
    const params = new URLSearchParams(window.location.search);
    const attachment = params.get("attachment");
    if (attachment) {
      setUpload({ id: attachment, kind: "video", filename: params.get("name") || attachment, size_bytes: 0 });
      window.history.replaceState({}, "", "/lote");
    }
    return () => clearInterval(id);
  }, []);

  const handleFile = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    try {
      setUpload(await api.upload(files[0]));
    } catch (e) { toast(f(t.newJob.uploadFailed, { message: (e as Error).message })); }
    finally { setUploading(false); if (fileInput.current) fileInput.current.value = ""; }
  };

  const analyze = async () => {
    if (!upload) { toast(t.batch.needVideo); return; }
    setBusy(true);
    try {
      const { plan_id } = await api.createClipPlan({
        attachment_id: upload.id, count, target_seconds: target, niche,
      });
      setActive(plan_id);
      toast(t.batch.queued);
      pull();
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };

  const current = plans.find((p) => p.id === active) ?? plans[0];

  return (
    <>
      <Topbar title={t.batch.title}>
        <span className="label">{t.batch.subtitle}</span>
      </Topbar>

      <div className="content grid" style={{ gap: 18 }}>
        <div className="side">
          <div className="grid" style={{ gap: 14 }}>
            <section className="panel">
              <div className="panel-head"><span className="label">{t.batch.stepSource}</span></div>
              <div className="panel-body grid" style={{ gap: 12 }}>
                <input ref={fileInput} className="input" type="file" accept="video/*"
                       onChange={(e) => handleFile(e.target.files)} />
                {uploading ? <span className="label">{t.newJob.uploading}</span> : null}
                {upload ? (
                  <div className="row spread wrap" style={{ gap: 8 }}>
                    <span className="mono" style={{ fontSize: 12 }}>
                      {upload.filename}
                      {upload.size_bytes ? <span className="dimmer"> ({(upload.size_bytes / 1048576).toFixed(0)} MB)</span> : null}
                    </span>
                    <button className="btn sm ghost" onClick={() => setUpload(null)}>{t.batch.swap}</button>
                  </div>
                ) : (
                  <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                    {t.batch.sourceHint}
                  </p>
                )}
                <div className="two">
                  <Field label={t.batch.howMany} hint={`${count}`}>
                    <input type="range" min={1} max={12} step={1} value={count}
                           onChange={(e) => setCount(Number(e.target.value))} />
                  </Field>
                  <Field label={t.batch.eachDuration} hint={`${target}${t.common.seconds}`}>
                    <input type="range" min={20} max={90} step={5} value={target}
                           onChange={(e) => setTarget(Number(e.target.value))} />
                  </Field>
                </div>
                <Field label={t.newJob.niche}>
                  <Chips value={niche} onChange={setNiche} options={niches} />
                </Field>
                <button className="btn primary" onClick={analyze} disabled={busy || uploading || !upload}>
                  {busy ? t.batch.analyzing : t.batch.analyze}
                </button>
              </div>
            </section>

            {current ? <PlanPanel plan={current} toast={toast} onChanged={pull} /> : null}
          </div>

          <aside className="grid" style={{ gap: 10 }}>
            <span className="label">{t.batch.previous}</span>
            {plans.length === 0 ? (
              <div className="empty" style={{ padding: 22 }}>{t.batch.noPrevious}</div>
            ) : plans.map((p) => (
              <button key={p.id} className="panel" style={{ textAlign: "left", cursor: "pointer",
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
                    {f(t.batch.planSummary, {
                      count: p.requested,
                      seconds: p.target_seconds,
                      niche: nicheLabel(t.niches, p.options?.niche),
                    })}
                    {p.jobs?.length ? ` · ${f(t.batch.planShorts, { n: p.jobs.length })}` : ""}
                  </span>
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

/** Niche of the saved plan: translated when the key is known, shown raw otherwise. */
function nicheLabel(niches: Record<string, string>, value?: string): string {
  if (!value) return "";
  return niches[value] ?? value;
}

function PlanPanel({ plan, toast, onChanged }: {
  plan: ClipPlan; toast: (m: string) => void; onChanged: () => void;
}) {
  const { t, f } = useI18n();
  const [selected, setSelected] = useState<number[]>([]);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [voiceId, setVoiceId] = useState("");
  const [schedule, setSchedule] = useState(false);
  const [accountId, setAccountId] = useState("");
  const [startAt, setStartAt] = useState("");
  const [everyHours, setEveryHours] = useState(24);
  const [privacy, setPrivacy] = useState("public");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.voices().then(setVoices).catch(() => setVoices([]));
    api.accounts().then(setAccounts).catch(() => setAccounts([]));
  }, []);
  useEffect(() => {
    setSelected(plan.clips ? plan.clips.map((_, i) => i) : []);
  }, [plan.id, plan.clips?.length]);

  const toggle = (i: number) =>
    setSelected((prev) => prev.includes(i) ? prev.filter((x) => x !== i) : [...prev, i].sort());

  const render = async () => {
    if (!selected.length) { toast(t.batch.pickClip); return; }
    if (schedule && (!accountId || !startAt)) { toast(t.batch.pickAccountAndDate); return; }
    setBusy(true);
    try {
      const r = await api.renderClips(plan.id, {
        selected, voice_id: voiceId || null, caption_style: "karaoke",
        caption_position: "centro", music: true, watermark: "", qa_autofix: true,
        schedule: schedule ? {
          account_id: accountId, start_at: new Date(startAt).toISOString(),
          every_hours: everyHours, privacy,
        } : null,
      });
      toast(r.schedules.length
        ? f(t.batch.doneScheduled, { n: r.jobs.length, s: r.schedules.length })
        : f(t.batch.done, { n: r.jobs.length }));
      onChanged();
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.batch.stepClips}</span>
        <div className="grow" />
        <StatusTag status={plan.status} />
        <button className="btn sm ghost" onClick={() => api.deleteClipPlan(plan.id).then(onChanged)}>
          {t.common.remove}
        </button>
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
        {plan.status === "queued" || plan.status === "analisando" ? (
          <div className="empty" style={{ border: 0 }}>
            {t.batch.working}
          </div>
        ) : plan.status === "error" ? (
          <div className="issue" data-sev="fatal">
            <span className="label" style={{ minWidth: 52 }}>{t.job.failure}</span>
            <div className="mono" style={{ fontSize: 12 }}>{plan.error}</div>
          </div>
        ) : plan.clips?.length ? (
          <>
            {plan.clips.map((clip, i) => (
              <label key={i} className="issue" data-sev={selected.includes(i) ? "aviso" : "info"}
                     style={{ cursor: "pointer", alignItems: "start" }}>
                <input type="checkbox" checked={selected.includes(i)} onChange={() => toggle(i)}
                       style={{ marginTop: 3 }} />
                <div className="grow">
                  <div className="row wrap" style={{ gap: 8, marginBottom: 3 }}>
                    <span className="tag" data-tone="amber">
                      {formatSeconds(clip.inicio)} → {formatSeconds(clip.fim)}
                    </span>
                    <span className="mono dimmer" style={{ fontSize: 11 }}>
                      {Math.round(clip.fim - clip.inicio)}{t.common.seconds}
                    </span>
                  </div>
                  <b style={{ fontSize: 14 }}>{clip.titulo}</b>
                  <p className="dim" style={{ margin: "3px 0 0", fontSize: 12.5, lineHeight: 1.5 }}>{clip.motivo}</p>
                </div>
              </label>
            ))}

            {plan.jobs?.length ? (
              <div className="row wrap" style={{ gap: 6 }}>
                <span className="label" style={{ alignSelf: "center" }}>{t.batch.generatedShorts}</span>
                {plan.jobs.map((j) => (
                  <Link key={j} href={`/job/${j}`} className="tag" style={{ color: "var(--amber)" }}>
                    {j.slice(4, 12)}
                  </Link>
                ))}
              </div>
            ) : null}

            <hr className="rule" />
            <div className="two">
              <Field label={t.batch.voice}>
                <select className="select" value={voiceId} onChange={(e) => setVoiceId(e.target.value)}>
                  <option value="">{t.batch.voiceDefault}</option>
                  {voices.map((v) => <option key={v.id} value={v.id}>{v.name} · {v.provider}</option>)}
                </select>
              </Field>
              <Field label={t.batch.publishing}>
                <Chips value={schedule ? "sim" : "nao"} onChange={(v) => setSchedule(v === "sim")}
                       options={[{ value: "nao", label: t.batch.onlyGenerate },
                                 { value: "sim", label: t.batch.scheduleSequence }]} />
              </Field>
            </div>

            {schedule ? (
              accounts.length === 0 ? (
                <p className="dimmer" style={{ margin: 0, fontSize: 12.5 }}
                   dangerouslySetInnerHTML={{ __html: t.batch.noAccountsForSchedule }} />
              ) : (
                <div className="grid" style={{ gap: 10 }}>
                  <div className="two">
                    <Field label={t.batch.account}>
                      <select className="select" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                        <option value="">{t.publish.select}</option>
                        {accounts.map((a) => (
                          <option key={a.id} value={a.id}>{PLATFORM_LABEL[a.platform] ?? a.platform} · {a.display_name}</option>
                        ))}
                      </select>
                    </Field>
                    <Field label={t.batch.firstAt}>
                      <input className="input" type="datetime-local" value={startAt}
                             onChange={(e) => setStartAt(e.target.value)} />
                    </Field>
                  </div>
                  <div className="two">
                    <Field label={t.batch.interval}
                           hint={everyHours >= 24
                             ? f(t.batch.intervalDays, { n: everyHours / 24 })
                             : f(t.batch.intervalHours, { n: everyHours })}>
                      <input type="range" min={2} max={72} step={2} value={everyHours}
                             onChange={(e) => setEveryHours(Number(e.target.value))} />
                    </Field>
                    <Field label={t.batch.privacy}>
                      <select className="select" value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
                        <option value="public">{t.publish.public}</option>
                        <option value="unlisted">{t.publish.unlisted}</option>
                        <option value="private">{t.publish.private}</option>
                      </select>
                    </Field>
                  </div>
                  <p className="dimmer" style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5 }}>
                    {t.batch.scheduleExplain}
                  </p>
                </div>
              )
            ) : null}

            <button className="btn primary" onClick={render} disabled={busy}>
              {busy
                ? t.batch.submitting
                : f(schedule ? t.batch.submitAndSchedule : t.batch.submit, { n: selected.length })}
            </button>
          </>
        ) : (
          <div className="empty" style={{ border: 0 }}>{t.batch.noClips}</div>
        )}
      </div>
    </section>
  );
}
