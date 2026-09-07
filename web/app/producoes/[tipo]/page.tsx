"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";

import {
  api, LONGFORM_STAGES, LONGFORM_TYPES,
  type LongformBriefing, type LongformEstimate, type LongformPlan,
  type LongformProject, type LongformScript, type LongformStage,
  type LongformType, type LongformTypeInfo, type LongformVocabulary,
  type UploadResult,
} from "@/lib/api";
import { LOCALES, LOCALE_NAMES, NARRATION_LANGUAGE, useI18n } from "@/lib/i18n";
import { Chips, Field, StatusTag, Topbar, useToast } from "@/components/ui";

/** The chain, in order. `setup` is the form that creates the production and
 *  queues ingestion; `generate` is the end. The four in between are the stages
 *  the backend develops, each corrected before the next is written against
 *  it. */
const FLOW = ["setup", ...LONGFORM_STAGES, "generate"] as const;
type FlowStep = (typeof FLOW)[number];

/** States in which every mutating route answers 400 — the screen must not
 *  offer buttons that can only fail. */
const BUSY = new Set(["ingesting", "queued", "assembling"]);

const NICHE_KEYS = [
  "ciberseguranca", "tecnologia", "politica", "saude", "historia", "ciencia",
  "negocios", "cinema", "games", "curiosidades", "programacao", "generico",
] as const;

/** A stage document is a document once developed; the cheap listing sends a
 *  boolean in its place, and neither counts as "the user can read it". */
const hasDoc = (value: unknown) =>
  Boolean(value) && typeof value === "object";

function isDone(project: LongformProject | undefined, step: FlowStep): boolean {
  if (!project) return false;
  if (step === "setup") return true;              // the production exists
  if (step === "generate") return Boolean(project.jobs
    && Object.keys(project.jobs).length);
  if (step === "material") return hasDoc(project.material);
  return hasDoc(project[step as "briefing" | "roteiro" | "plano_de_edicao"]);
}

/** Open on the first unfinished step, so picking a production drops you where
 *  the work actually is. */
function firstOpenStep(project: LongformProject | undefined): number {
  if (!project) return 0;
  for (let i = 1; i < FLOW.length; i += 1) {
    if (!isDone(project, FLOW[i])) return i;
  }
  return FLOW.length - 1;
}

const seconds = (value: number) => `${Math.round(value)}s`;

// Documentaries, mini-docs, short films and mini-series: the user brings the
// material, the AI proposes the brief, writes the script and decides what is on
// screen in each block — with every stage inspectable and correctable.
export default function LongformStudio() {
  const params = useParams<{ tipo: string }>();
  const kind = (LONGFORM_TYPES as readonly string[]).includes(params.tipo)
    ? (params.tipo as LongformType) : "documentario";

  const { t, f, narrationLanguage } = useI18n();
  const { toast, node } = useToast();

  const [vocab, setVocab] = useState<LongformVocabulary | null>(null);
  const [projects, setProjects] = useState<LongformProject[]>([]);
  const [active, setActive] = useState("");
  const [step, setStep] = useState(0);

  const activeRef = useRef("");
  useEffect(() => { activeRef.current = active; }, [active]);

  useEffect(() => { api.longformTypes().then(setVocab).catch(() => undefined); }, []);

  const info: LongformTypeInfo | undefined =
    vocab?.types.find((entry) => entry.id === kind);

  const pull = useCallback(async () => {
    let rows: LongformProject[];
    try {
      rows = await api.longforms();
    } catch { return; }
    setProjects(rows);

    // Only the detail route carries the documents and the estimate, so the
    // production being looked at is fetched on its own.
    const id = activeRef.current;
    if (!id || !rows.some((r) => r.id === id)) return;
    try {
      const fresh = await api.longform(id);
      setProjects((prev) => prev.map((r) => (r.id === id ? fresh : r)));
    } catch { /* the list row holds until the next tick */ }
  }, []);

  useEffect(() => {
    pull();
    const timer = setInterval(pull, 5000);
    return () => clearInterval(timer);
  }, [pull]);

  const mine = useMemo(
    () => projects.filter((p) => p.type === kind), [projects, kind]);
  const current = mine.find((p) => p.id === active) ?? mine[0];

  // Follow the production being looked at, but a manual step wins until the
  // production changes — otherwise developing a stage would yank the view.
  const steppedFor = useRef("");
  useEffect(() => {
    const id = current?.id ?? "";
    if (steppedFor.current === id) return;
    steppedFor.current = id;
    setStep(firstOpenStep(current));
  }, [current]);

  // Switching type in the menu is a different studio; start at its form.
  useEffect(() => { setActive(""); steppedFor.current = ""; setStep(0); }, [kind]);

  const at = FLOW[step];
  const reachable = (index: number) =>
    index === 0 || isDone(current, FLOW[index - 1]);
  const busy = BUSY.has(current?.status ?? "");

  return (
    <>
      <Topbar title={t.nav[navKey(kind)]}>
        <span className="label">{t.longform.subtitle}</span>
      </Topbar>

      <div className="content grid" style={{ gap: 18 }}>
        <div className="steps chain">
          {FLOW.map((name, index) => {
            const open = reachable(index);
            return (
              <div key={name}
                   data-on={index === step}
                   data-done={index !== step && isDone(current, name)}
                   data-locked={!open}
                   style={{ cursor: open ? "pointer" : "not-allowed" }}
                   onClick={() => { if (open) setStep(index); }}>
                {t.longform.flow[name]}
              </div>
            );
          })}
        </div>

        <div className="side">
          <div className="grid" style={{ gap: 14 }}>
            {at === "setup" ? (
              <SetupPanel kind={kind} info={info} vocab={vocab} toast={toast}
                          onCreated={(project) => {
                            setProjects((prev) => [project, ...prev]);
                            setActive(project.id);
                            steppedFor.current = project.id;
                            setStep(1);
                          }} />
            ) : !current ? (
              <div className="empty">{t.longform.noMine}</div>
            ) : at === "generate" ? (
              <GeneratePanel project={current} toast={toast} onChanged={pull} />
            ) : (
              <StagePanel project={current} stage={at as LongformStage}
                          order={step} total={FLOW.length} busy={busy}
                          toast={toast} onChanged={pull}
                          onDeveloped={() => setStep((s) =>
                            Math.min(s + 1, FLOW.length - 1))} />
            )}

            {current ? (
              <div className="row spread wrap" style={{ gap: 8 }}>
                <button className="btn sm ghost" disabled={step === 0}
                        onClick={() => setStep((s) => Math.max(s - 1, 0))}>
                  {t.longform.back}
                </button>
                <button className="btn sm"
                        disabled={step >= FLOW.length - 1 || !reachable(step + 1)}
                        onClick={() => setStep((s) =>
                          Math.min(s + 1, FLOW.length - 1))}>
                  {t.longform.next}
                </button>
              </div>
            ) : null}
          </div>

          <div className="grid" style={{ gap: 14 }}>
            {current ? (
              <ProjectSummary project={current} toast={toast}
                              onDeleted={() => { setActive(""); pull(); }} />
            ) : null}

            <section className="panel">
              <div className="panel-head">
                <span className="label">{t.longform.mine}</span>
                <div className="grow" />
                <span className="tag">{mine.length}</span>
              </div>
              <div className="panel-body grid" style={{ gap: 6 }}>
                {mine.length === 0 ? (
                  <span className="dimmer" style={{ fontSize: 12.5 }}>
                    {t.longform.noMine}
                  </span>
                ) : mine.map((project) => (
                  <button key={project.id} className="btn sm ghost"
                          data-on={project.id === current?.id}
                          style={{ justifyContent: "space-between", width: "100%" }}
                          onClick={() => setActive(project.id)}>
                    <span style={{ minWidth: 0, overflow: "hidden",
                                   textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {project.title || project.prompt.slice(0, 42)}
                    </span>
                    <StatusTag status={project.status} />
                  </button>
                ))}
              </div>
            </section>

            {current ? (
              <button className="btn sm ghost" onClick={() => {
                setActive("");
                steppedFor.current = "";
                setStep(0);
              }}>
                {t.longform.stepNew}
              </button>
            ) : null}
          </div>
        </div>
      </div>
      {node}
    </>
  );
}

const navKey = (kind: LongformType) => ({
  documentario: "documentary", mini_documentario: "miniDoc",
  curta: "shortFilm", mini_serie: "miniSeries",
} as const)[kind];

// ------------------------------------------------------------------ setup

function SetupPanel({ kind, info, vocab, toast, onCreated }: {
  kind: LongformType;
  info?: LongformTypeInfo;
  vocab: LongformVocabulary | null;
  toast: (m: string) => void;
  onCreated: (project: LongformProject) => void;
}) {
  const { t, f, narrationLanguage } = useI18n();
  const fileInput = useRef<HTMLInputElement>(null);

  const [prompt, setPrompt] = useState("");
  const [instruction, setInstruction] = useState("");
  const [title, setTitle] = useState("");
  const [tone, setTone] = useState("investigativo");
  const [style, setStyle] = useState("");
  const [narrator, setNarrator] = useState("oculto");
  const [niche, setNiche] = useState("generico");
  const [language, setLanguage] = useState(narrationLanguage);
  const [minutes, setMinutes] = useState(0);
  const [episodes, setEpisodes] = useState(3);
  const [links, setLinks] = useState("");
  const [references, setReferences] = useState("");
  const [uploads, setUploads] = useState<UploadResult[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);

  // The target follows the type until the user moves it: a mini-doc and a
  // documentary are different lengths and the slider has to start sane.
  const touched = useRef(false);
  useEffect(() => {
    if (!touched.current && info) setMinutes(info.default_minutes);
  }, [info]);

  const niches = useMemo(
    () => NICHE_KEYS
      .filter((value) => value in t.niches)
      .map((value) => ({ value, label: t.niches[value] })), [t]);
  const languages = useMemo(
    () => LOCALES.map((l) => ({ value: NARRATION_LANGUAGE[l], label: LOCALE_NAMES[l] })),
    []);

  const send = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    try {
      const added: UploadResult[] = [];
      for (const file of Array.from(files)) added.push(await api.upload(file));
      setUploads((prev) => [...prev, ...added]);
    } catch (e) {
      toast(f(t.newJob.uploadFailed, { message: (e as Error).message }));
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const lines = (value: string) =>
    value.split(/\n+/).map((l) => l.trim()).filter(Boolean);

  const create = async () => {
    if (prompt.trim().length < 12) { toast(t.longform.needPrompt); return; }
    if (!lines(links).length && !uploads.length) {
      toast(t.longform.needMaterial); return;
    }
    setBusy(true);
    try {
      onCreated(await api.createLongform({
        type: kind, prompt: prompt.trim(), instruction: instruction.trim(),
        title: title.trim(), tone, style: style.trim(), narrator, niche, language,
        target_minutes: minutes, episodes,
        links: lines(links), references: lines(references),
        attachments: uploads.map((u) => u.id),
      }));
    } catch (e) {
      toast((e as Error).message);   // the server's own wording
    } finally { setBusy(false); }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.longform.stepNew}</span>
        <div className="grow" />
        {info ? (
          <span className="tag">{info.min_minutes}–{info.max_minutes} min</span>
        ) : null}
      </div>
      <div className="panel-body grid" style={{ gap: 14 }}>
        <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
          {t.longform.intro}
        </p>
        {info?.structure ? (
          <span className="dimmer mono" style={{ fontSize: 11.5, lineHeight: 1.6 }}>
            {info.structure}
          </span>
        ) : null}

        <Field label={t.longform.promptLabel} hint={t.longform.promptHint}>
          <textarea className="textarea" style={{ minHeight: 84 }} value={prompt}
                    placeholder={t.longform.promptPlaceholder}
                    onChange={(e) => setPrompt(e.target.value)} />
        </Field>

        <Field label={t.longform.instruction} hint={t.longform.instructionHint}>
          <textarea className="textarea" style={{ minHeight: 72 }} value={instruction}
                    placeholder={t.longform.instructionPlaceholder}
                    onChange={(e) => setInstruction(e.target.value)} />
        </Field>

        <Field label={t.longform.titleLabel} hint={t.common.optional}>
          <input className="input" value={title}
                 onChange={(e) => setTitle(e.target.value)} />
        </Field>

        {/* The material. Links and uploads are content; references are read
            for structure only, and the backend refuses references alone. */}
        <Field label={t.longform.links} hint={t.longform.linksHint}>
          <textarea className="textarea" style={{ minHeight: 72 }} value={links}
                    placeholder={t.longform.linksPlaceholder}
                    onChange={(e) => setLinks(e.target.value)} />
        </Field>

        <div className="grid" style={{ gap: 8 }}>
          <div className="row spread">
            <span className="label">{t.longform.uploads}</span>
            <span className="label">{t.longform.uploadsHint}</span>
          </div>
          <label
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault(); setDragging(false); send(e.dataTransfer.files);
            }}
            style={{
              display: "grid", placeItems: "center", gap: 6, minHeight: 84,
              padding: "16px 14px", textAlign: "center", cursor: "pointer",
              borderRadius: 3,
              border: `1px dashed ${dragging ? "var(--amber)" : "var(--line)"}`,
              background: dragging ? "#191300" : "var(--void)",
              color: "var(--ink-2)", fontSize: 12.5, lineHeight: 1.6,
            }}
          >
            <span>{t.longform.uploadsDrop}</span>
            <input ref={fileInput} type="file" accept="video/*,image/*" multiple
                   onChange={(e) => send(e.target.files)}
                   style={{ position: "absolute", width: 1, height: 1,
                            opacity: 0, pointerEvents: "none" }} />
          </label>
          {uploading ? <span className="label">{t.newJob.uploading}</span> : null}
          {uploads.map((upload) => (
            <div key={upload.id} className="row spread wrap" style={{ gap: 8 }}>
              <span className="mono" style={{ fontSize: 12, minWidth: 0,
                           overflowWrap: "anywhere" }}>{upload.filename}</span>
              <button className="btn sm ghost"
                      onClick={() => setUploads((prev) =>
                        prev.filter((u) => u.id !== upload.id))}>
                {t.common.remove}
              </button>
            </div>
          ))}
        </div>

        <Field label={t.longform.references} hint={t.longform.referencesHint}>
          <textarea className="textarea" style={{ minHeight: 56 }} value={references}
                    placeholder={t.longform.referencesPlaceholder}
                    onChange={(e) => setReferences(e.target.value)} />
        </Field>

        <Field label={t.longform.tone}>
          <Chips value={tone} onChange={setTone}
                 options={(vocab?.tones ?? []).map((entry) => ({
                   value: entry.id, label: entry.id }))} />
        </Field>

        <Field label={t.longform.style} hint={t.longform.styleHint}>
          <input className="input" value={style}
                 placeholder={t.longform.stylePlaceholder}
                 onChange={(e) => setStyle(e.target.value)} />
        </Field>

        <Field label={t.longform.narrator} hint={t.longform.narratorHint}>
          <Chips value={narrator} onChange={setNarrator}
                 options={(vocab?.narrators ?? []).map((entry) => ({
                   value: entry.id, label: entry.id }))} />
        </Field>

        <div className="two">
          <Field label={t.longform.minutes}
                 hint={f(t.longform.minutesValue, { n: minutes })}>
            <input type="range" min={info?.min_minutes ?? 5}
                   max={info?.max_minutes ?? 30} step={1} value={minutes}
                   onChange={(e) => {
                     touched.current = true;
                     setMinutes(Number(e.target.value));
                   }} />
          </Field>
          {kind === "mini_serie" ? (
            <Field label={t.longform.episodes} hint={`${episodes}`}>
              <input type="range" min={vocab?.min_episodes ?? 2}
                     max={vocab?.max_episodes ?? 12} step={1} value={episodes}
                     onChange={(e) => setEpisodes(Number(e.target.value))} />
            </Field>
          ) : <span />}
        </div>

        <div className="two">
          <Field label={t.newJob.niche}>
            <select className="select" value={niche}
                    onChange={(e) => setNiche(e.target.value)}>
              {niches.map((n) => (
                <option key={n.value} value={n.value}>{n.label}</option>
              ))}
            </select>
          </Field>
          <Field label={t.common.language}>
            <select className="select" value={language}
                    onChange={(e) => setLanguage(e.target.value)}>
              {languages.map((l) => (
                <option key={l.value} value={l.value}>{l.label}</option>
              ))}
            </select>
          </Field>
        </div>

        <button className="btn primary" onClick={create}
                disabled={busy || uploading || prompt.trim().length < 12}>
          {busy ? t.longform.creating : t.longform.create}
        </button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- summary

function ProjectSummary({ project, toast, onDeleted }: {
  project: LongformProject;
  toast: (m: string) => void;
  onDeleted: () => void;
}) {
  const { t, f } = useI18n();
  const busy = BUSY.has(project.status);
  const jobs = Object.entries(project.jobs ?? {});

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">
          {project.title || t.longform.untitled}
        </span>
        <div className="grow" />
        <StatusTag status={project.status} />
      </div>
      <div className="panel-body grid" style={{ gap: 10 }}>
        <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
          {project.prompt}
        </p>
        {project.instruction ? (
          <div className="row" style={{ gap: 10, alignItems: "start" }}>
            <span className="label" style={{ minWidth: 76, paddingTop: 2 }}>
              {t.longform.instruction}
            </span>
            <span className="dimmer" style={{ fontSize: 12.5, lineHeight: 1.6 }}>
              {project.instruction}
            </span>
          </div>
        ) : null}

        {busy ? (
          <>
            <div className="bar"><i className="indeterminate" /></div>
            <span className="dimmer" style={{ fontSize: 12.5, lineHeight: 1.6 }}>
              {project.status === "ingesting" ? t.longform.ingesting
                                              : t.longform.queued}
              {project.stage ? ` — ${project.stage}` : ""}
            </span>
          </>
        ) : null}

        {project.error ? (
          <div className="issue" data-sev="erro">
            <span className="label" style={{ minWidth: 52, paddingTop: 2 }}>
              {t.job.failure}
            </span>
            <div>{project.error}</div>
          </div>
        ) : null}

        <div className="row wrap" style={{ gap: 6 }}>
          {jobs.map(([episode, jobId]) => (
            <Link key={jobId} className="btn sm" href={`/job/${jobId}`}>
              {jobs.length > 1 ? f(t.longform.episodeJob, { n: episode })
                               : t.longform.openJob}
            </Link>
          ))}
          <button className="btn sm ghost" disabled={busy} onClick={async () => {
            try {
              await api.deleteLongform(project.id);
              toast(t.longform.deleted);
              onDeleted();
            } catch (e) { toast((e as Error).message); }
          }}>
            {t.longform.deleteProject}
          </button>
        </div>
      </div>
    </section>
  );
}

// ----------------------------------------------------------------- stages

function StagePanel({ project, stage, order, total, busy, toast, onChanged,
                      onDeveloped }: {
  project: LongformProject;
  stage: LongformStage;
  order: number;
  total: number;
  busy: boolean;
  toast: (m: string) => void;
  onChanged: () => void;
  onDeveloped: () => void;
}) {
  const { t, f } = useI18n();
  const [instruction, setInstruction] = useState("");
  const [working, setWorking] = useState(false);

  const doc = project[stage];
  const developed = hasDoc(doc);

  const develop = async () => {
    setWorking(true);
    try {
      const result = await api.developLongformStage(project.id, stage,
                                                    instruction.trim());
      setInstruction("");
      if (result.invalidated?.length) {
        toast(f(t.longform.invalidated, {
          stages: result.invalidated
            .map((s) => t.longform.stages[s as LongformStage] ?? s).join(", "),
        }));
      }
      onChanged();
      // Step forward only on a first development: a rewrite means the user is
      // working on this link and should stay on it.
      if (!developed) onDeveloped();
    } catch (e) {
      toast((e as Error).message);
    } finally { setWorking(false); }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.longform.stages[stage]}</span>
        <div className="grow" />
        <span className="tag" data-tone={developed ? "ok" : ""}>
          {f(t.longform.stepOf, { n: order + 1, total })}
        </span>
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
        {developed ? <StageBody project={project} stage={stage} /> : null}

        {stage === "material" ? (
          <button className="btn" onClick={develop} disabled={working || busy}>
            {working ? t.longform.developing : t.longform.reingest}
          </button>
        ) : (
          <>
            <Field label={t.longform.stageInstruction}
                   hint={t.longform.stageInstructionHint}>
              <textarea className="textarea" style={{ minHeight: 56 }}
                        value={instruction}
                        placeholder={t.longform.stageInstructionPlaceholder}
                        onChange={(e) => setInstruction(e.target.value)} />
            </Field>
            <button className={developed ? "btn" : "btn primary"}
                    onClick={develop} disabled={working || busy}>
              {working ? t.longform.developing
                       : developed ? t.longform.redevelop : t.longform.developNext}
            </button>
          </>
        )}
      </div>
    </section>
  );
}

function StageBody({ project, stage }: {
  project: LongformProject; stage: LongformStage;
}) {
  if (stage === "material") {
    return <MaterialView items={project.material?.items ?? []} />;
  }
  if (stage === "briefing") {
    return <BriefingView doc={project.briefing as LongformBriefing} />;
  }
  if (stage === "roteiro") {
    return <ScriptView doc={project.roteiro as LongformScript} />;
  }
  return <PlanView doc={project.plano_de_edicao as LongformPlan} />;
}

function MaterialView({ items }: { items: LongformProject["material"] extends null
  ? never : NonNullable<LongformProject["material"]>["items"] }) {
  const { t } = useI18n();
  return (
    <div className="grid" style={{ gap: 8 }}>
      {items.map((item) => (
        <div key={item.id} style={{ borderTop: "1px solid var(--line)",
                                    paddingTop: 8 }}>
          <div className="row spread wrap" style={{ gap: 8 }}>
            <span className="mono" style={{ fontSize: 12 }}>
              {item.id} · {item.kind}
              {item.duration ? ` · ${seconds(item.duration)}` : ""}
            </span>
            <span className="tag"
                  data-tone={item.status === "ok" ? "ok" : "err"}>
              {item.status === "ok" ? t.longform.transcript : t.longform.failed}
            </span>
          </div>
          <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>
            {item.title || item.url}
          </div>
          {/* A reference is read for structure and never copied — worth saying
              on screen, because it is the one item whose content is off limits. */}
          {item.reference ? (
            <span className="tag" data-tone="amber">{t.longform.reference}</span>
          ) : null}
          {item.error ? (
            <div className="dimmer" style={{ fontSize: 12 }}>{item.error}</div>
          ) : item.transcript ? (
            <div className="dimmer" style={{ fontSize: 12, lineHeight: 1.6 }}>
              {item.transcript.slice(0, 180)}…
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function BriefingView({ doc }: { doc: LongformBriefing }) {
  const { t } = useI18n();
  if (!doc) return null;
  return (
    <div className="grid" style={{ gap: 10 }}>
      <div>
        <div className="mono" style={{ fontSize: 13 }}>{doc.title}</div>
        <div className="dimmer" style={{ fontSize: 12.5, lineHeight: 1.6 }}>
          {doc.logline}
        </div>
      </div>
      <Line label={t.longform.angle} value={doc.angle} />

      <Group label={t.longform.facts}>
        {doc.facts.map((fact, i) => (
          <div key={i} style={{ fontSize: 12.5, lineHeight: 1.6 }}>
            {fact.fact}
            {fact.source ? (
              <span className="dimmer"> · {t.longform.source} {fact.source}</span>
            ) : null}
          </div>
        ))}
      </Group>

      <Group label={t.longform.moments}>
        {doc.moments.map((moment, i) => (
          <div key={i} style={{ fontSize: 12.5, lineHeight: 1.6 }}>
            <span className="mono dimmer">
              {moment.material} {seconds(moment.start)}–{seconds(moment.end)}
            </span>{" "}
            “{moment.quote}”
          </div>
        ))}
      </Group>

      <Group label={t.longform.stockQueries}>
        <div className="chips">
          {doc.stock_queries.map((query) => (
            <span className="tag" key={query}>{query}</span>
          ))}
        </div>
      </Group>

      <Group label={t.longform.structure}>
        {doc.structure.map((part, i) => (
          <div key={i} style={{ fontSize: 12.5, lineHeight: 1.6 }}>
            <span className="mono">{part.part}</span>
            <span className="dimmer"> · {part.minutes} min · {part.purpose}</span>
          </div>
        ))}
      </Group>

      <Rejected items={doc.rejected?.map((r) => ({
        what: r.fact ?? r.quote ?? r.what, reason: r.reason }))} />
    </div>
  );
}

function ScriptView({ doc }: { doc: LongformScript }) {
  const { t, f } = useI18n();
  if (!doc) return null;
  return (
    <div className="grid" style={{ gap: 12 }}>
      {doc.episodes.map((episode) => (
        <div key={episode.episode} className="grid" style={{ gap: 6 }}>
          <div className="row spread wrap">
            <span className="label">
              {doc.episodes.length > 1
                ? f(t.longform.episodeLabel, { n: episode.episode })
                : episode.title}
            </span>
            <span className="label">
              {f(t.longform.blocks, { n: episode.blocks.length })} ·{" "}
              {seconds(episode.total_seconds)}
            </span>
          </div>
          {episode.blocks.map((block) => (
            <div key={block.key} style={{ borderTop: "1px solid var(--line)",
                                          paddingTop: 6 }}>
              <div className="row spread wrap" style={{ gap: 8 }}>
                <span className="mono" style={{ fontSize: 12 }}>
                  {block.kind} · {block.title}
                </span>
                <span className="label">
                  {seconds(block.seconds)}
                  {block.adjusted ? ` · ${t.longform.adjusted}` : ""}
                </span>
              </div>
              <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>
                {block.narration || (
                  <span className="dimmer">{t.longform.narrationEmpty}</span>
                )}
              </div>
            </div>
          ))}
        </div>
      ))}
      <Rejected items={doc.rejected?.map((r) => ({
        what: r.block, reason: r.reason }))} />
    </div>
  );
}

function PlanView({ doc }: { doc: LongformPlan }) {
  const { t, f } = useI18n();
  if (!doc) return null;
  return (
    <div className="grid" style={{ gap: 12 }}>
      {doc.episodes.map((episode) => (
        <div key={episode.episode} className="grid" style={{ gap: 6 }}>
          {doc.episodes.length > 1 ? (
            <span className="label">
              {f(t.longform.episodeLabel, { n: episode.episode })}
            </span>
          ) : null}
          {episode.blocks.map((block) => (
            <div key={block.key} style={{ borderTop: "1px solid var(--line)",
                                          paddingTop: 6 }}>
              <div className="row spread wrap" style={{ gap: 8 }}>
                <span className="mono" style={{ fontSize: 12 }}>
                  {block.kind} · {block.title}
                </span>
                <span className="label">{seconds(block.seconds)}</span>
              </div>
              <div className="chips" style={{ marginTop: 4 }}>
                {block.shots.length === 0 ? (
                  <span className="dimmer" style={{ fontSize: 12 }}>
                    {t.longform.noShots}
                  </span>
                ) : block.shots.map((shot, i) => (
                  <span className="tag" key={i}>
                    {shot.kind}
                    {shot.material ? ` ${shot.material}` : ""}
                    {shot.query ? ` “${shot.query}”` : ""}
                    {shot.text ? ` “${shot.text.slice(0, 24)}”` : ""}
                    {" · "}{seconds(shot.seconds)}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      ))}
      <Rejected items={doc.rejected?.map((r) => ({
        what: r.block, reason: r.reason }))} />
    </div>
  );
}

/** What the validation refused. Shown as written, and never hidden when empty
 *  is not the case: a refusal nobody reads is a refusal that did not happen. */
function Rejected({ items }: { items?: { what: string; reason: string }[] }) {
  const { t } = useI18n();
  if (!items?.length) return null;
  return (
    <div className="issue" data-sev="aviso"
         style={{ gridTemplateColumns: "minmax(0, 1fr)" }}>
      <div className="grid" style={{ gap: 4 }}>
        <span className="label">{t.longform.rejected}</span>
        <span className="dimmer" style={{ fontSize: 11.5 }}>
          {t.longform.rejectedHint}
        </span>
        {items.map((item, i) => (
          <div key={i} style={{ fontSize: 12, lineHeight: 1.6 }}>
            <span className="mono">{item.what}</span> — {item.reason}
          </div>
        ))}
      </div>
    </div>
  );
}

function Line({ label, value }: { label: string; value?: string }) {
  if (!value) return null;
  return (
    <div className="grid" style={{ gap: 2 }}>
      <span className="label">{label}</span>
      <span style={{ fontSize: 12.5, lineHeight: 1.6 }}>{value}</span>
    </div>
  );
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  const list = Array.isArray(children) ? children.filter(Boolean) : children;
  if (Array.isArray(list) && list.length === 0) return null;
  return (
    <div className="grid" style={{ gap: 4 }}>
      <span className="label">{label}</span>
      {children}
    </div>
  );
}

// --------------------------------------------------------------- generate

function GeneratePanel({ project, toast, onChanged }: {
  project: LongformProject; toast: (m: string) => void; onChanged: () => void;
}) {
  const { t, f } = useI18n();
  const [working, setWorking] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const estimate: LongformEstimate | undefined = project.estimate;
  const ready = hasDoc(project.plano_de_edicao);
  const busy = BUSY.has(project.status);

  const start = async (placeholderOk: boolean) => {
    setWorking(true);
    try {
      const result = await api.generateLongform(project.id, true, placeholderOk);
      if (result.queued) {
        toast(t.longform.queued);
        setConfirming(false);
        onChanged();
      }
    } catch (e) {
      toast((e as Error).message);
    } finally { setWorking(false); }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.longform.stepGenerate}</span>
        <div className="grow" />
        {busy ? <StatusTag status={project.status} /> : null}
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
        {!ready ? (
          <span className="dimmer" style={{ fontSize: 12.5 }}>
            {t.longform.needPlan}
          </span>
        ) : (
          <>
            {estimate ? (
              <div className="grid" style={{ gap: 4 }}>
                <span className="label">{t.longform.estimateTitle}</span>
                <span style={{ fontSize: 12.5 }}>
                  {f(t.longform.estBlocks, { n: estimate.blocks,
                     seconds: estimate.target_seconds })}
                </span>
                <span style={{ fontSize: 12.5 }}>
                  {f(t.longform.estNarration, {
                    words: estimate.narration.words,
                    minutes: estimate.narration.minutes })}
                </span>
                <span style={{ fontSize: 12.5 }}>
                  {f(t.longform.estStock, { n: estimate.stock.clips })} ·{" "}
                  {f(t.longform.estInterview, { n: estimate.interview.cuts })} ·{" "}
                  {f(t.longform.estCards, { n: estimate.cards })}
                </span>
                <span style={{ fontSize: 12.5 }}>
                  {f(t.longform.estToDo, {
                    narration: estimate.narration_to_synthesize,
                    shots: estimate.shots_to_prepare })}
                </span>
                {estimate.reused ? (
                  <span className="dimmer" style={{ fontSize: 12 }}>
                    {f(t.longform.estReused, { n: estimate.reused })}
                  </span>
                ) : null}

                {/* The backend's own warnings, verbatim: they name the key to
                    register and the count that would become placeholders. */}
                {estimate.warnings.map((warning, i) => (
                  <div key={i} className="issue" data-sev="aviso"
                       style={{ gridTemplateColumns: "minmax(0, 1fr)" }}>
                    <div style={{ fontSize: 12, lineHeight: 1.6 }}>{warning}</div>
                  </div>
                ))}
              </div>
            ) : null}

            {busy ? (
              <>
                <div className="bar"><i className="indeterminate" /></div>
                <span className="dimmer" style={{ fontSize: 12.5 }}>
                  {t.longform.queued}
                  {project.stage ? ` — ${project.stage}` : ""}
                </span>
              </>
            ) : confirming ? (
              <div className="issue" data-sev="aviso">
                <span className="label" style={{ minWidth: 62, paddingTop: 2 }}>
                  {t.longform.confirmTitle}
                </span>
                <div className="grid" style={{ gap: 8 }}>
                  <span>
                    {f(t.longform.confirmBody, {
                      blocks: estimate?.blocks ?? 0,
                      seconds: estimate?.target_seconds ?? 0 })}
                  </span>
                  <div className="row wrap" style={{ gap: 6 }}>
                    <button className="btn primary" disabled={working}
                            onClick={() => start(false)}>
                      {working ? t.longform.generating : t.longform.confirmYes}
                    </button>
                    {/* Assembling with cards is a real delivery when no stock
                        bank is configured — it shows the edit before paying. */}
                    {estimate && !estimate.stock.provider.configured
                      && estimate.stock.clips > 0 ? (
                      <button className="btn" disabled={working}
                              onClick={() => start(true)}>
                        {t.longform.placeholder}
                      </button>
                    ) : null}
                    <button className="btn sm ghost"
                            onClick={() => setConfirming(false)}>
                      {t.common.cancel}
                    </button>
                  </div>
                  {estimate && !estimate.stock.provider.configured ? (
                    <span className="dimmer" style={{ fontSize: 11.5, lineHeight: 1.6 }}>
                      {t.longform.placeholderHint}
                    </span>
                  ) : null}
                </div>
              </div>
            ) : (
              <button className="btn primary" onClick={() => setConfirming(true)}
                      disabled={working}>
                {t.longform.generate}
              </button>
            )}
          </>
        )}
      </div>
    </section>
  );
}
