"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api, FILM_MAX_SCENES, FILM_MAX_SHOT_SECONDS, FILM_MIN_SCENES,
  FILM_MIN_SHOT_SECONDS, FILM_STAGES,
  type Film, type FilmBible, type FilmCharacter, type FilmEstimate,
  type FilmScene, type FilmStage,
} from "@/lib/api";
import { LOCALES, LOCALE_NAMES, NARRATION_LANGUAGE, useI18n } from "@/lib/i18n";
import { readDefaultWatermark, saveDefaultWatermark } from "@/lib/watermark";
import { Chips, Field, StatusTag, Topbar, useToast } from "@/components/ui";

const NICHE_KEYS = [
  "cinema", "historia", "ciencia", "curiosidades", "tecnologia",
  "ciberseguranca", "programacao", "negocios", "generico",
] as const;

/** 9:16 is the house format, but a film is the one thing here someone might
 *  actually want wide. */
const ASPECTS = ["9:16", "16:9", "1:1"] as const;

/** The chain, in order. `premise` is the form that creates the film (and
 *  develops the story in the same call); `generate` is the end of the flow.
 *  The four in between are the stages the backend develops. */
const FLOW = ["premise", ...FILM_STAGES, "generate"] as const;
type FlowStep = (typeof FLOW)[number];

/** How far a film has got. Each stage is written against the one before it, so
 *  a later step is not reachable until the earlier one exists — the backend
 *  answers 400 otherwise, and a step that can only fail is worse than a locked
 *  one that says why. */
function isDone(film: Film | undefined, step: FlowStep): boolean {
  if (!film) return false;
  if (step === "premise") return true;          // the film exists
  if (step === "generate") return Boolean(film.job_id);
  return Boolean(film[step]);
}

/** The step to open on: the first one not finished yet, so picking a film in
 *  the list drops you where the work actually is. */
function firstOpenStep(film: Film | undefined): number {
  if (!film) return 0;
  for (let i = 1; i < FLOW.length; i += 1) {
    if (!isDone(film, FLOW[i])) return i;
  }
  return FLOW.length - 1;
}

const isWorking = (status: string) =>
  status === "queued" || status === "generating" || status === "gerando";

/** Which model will actually make the footage. Naming it matters: the same
 *  provider fronts several models, and the user should know whether their film
 *  came out of Seedance or Sora. */
const providerName = (p: FilmEstimate["provider"]) =>
  p.model ? `${p.name} · ${p.model}` : p.name || p.id;

// A film is developed, not generated in one shot: premise → story → characters
// → screenplay → shots, each stage inspectable and correctable before the next.
// Nothing is generated until the cost has been shown and confirmed.
export default function FilmStudio() {
  const { t, f, narrationLanguage } = useI18n();
  const { toast, node } = useToast();

  const [premise, setPremise] = useState("");
  const [instruction, setInstruction] = useState("");
  const [title, setTitle] = useState("");
  const [niche, setNiche] = useState("cinema");
  const [language, setLanguage] = useState(narrationLanguage);
  const [aspect, setAspect] = useState<string>("9:16");
  const [scenes, setScenes] = useState(6);
  const [shotSeconds, setShotSeconds] = useState(8);
  const [watermark, setWatermark] = useState("");
  const [busy, setBusy] = useState(false);

  const [films, setFilms] = useState<Film[]>([]);
  const [active, setActive] = useState("");

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
    let rows: Film[];
    try {
      rows = await api.films();
    } catch { return; }
    setFilms(rows);

    // The detail route is the only one that carries the estimate, so the film
    // being looked at is fetched on its own.
    const id = activeRef.current;
    if (!id || !rows.some((r) => r.id === id)) return;
    try {
      const fresh = await api.film(id);
      setFilms((prev) => prev.map((r) => (r.id === id ? fresh : r)));
    } catch { /* the list row holds until the next tick */ }
  }, []);

  useEffect(() => {
    pull();
    const id = setInterval(pull, 5000);
    return () => clearInterval(id);
  }, [pull]);

  const create = async () => {
    if (premise.trim().length < 12) { toast(t.films.needPremise); return; }
    setBusy(true);
    try {
      const film = await api.createFilm({
        premise: premise.trim(), instruction: instruction.trim(),
        title: title.trim(), language, niche, aspect, scenes,
        shot_seconds: shotSeconds, watermark: watermark.trim(),
      });
      saveDefaultWatermark(watermark);
      setActive(film.id);
      setPremise("");
      pull();
    } catch (e) {
      toast((e as Error).message);   // the server's own wording
    } finally { setBusy(false); }
  };

  const current = films.find((r) => r.id === active) ?? films[0];

  // Where in the chain we are. It follows the film that is open, but a manual
  // step wins until the film changes — otherwise developing a stage would
  // yank the view somewhere else while you are reading it.
  const [step, setStep] = useState(0);
  const steppedFor = useRef("");
  useEffect(() => {
    const id = current?.id ?? "";
    if (steppedFor.current === id) return;
    steppedFor.current = id;
    setStep(firstOpenStep(current));
  }, [current]);

  const at = FLOW[step];
  const reachable = (index: number) =>
    index === 0 || isDone(current, FLOW[index - 1]);

  return (
    <>
      <Topbar title={t.nav.filmStudio}>
        <span className="label">{t.films.subtitle}</span>
      </Topbar>

      <div className="content grid" style={{ gap: 18 }}>
        {/* The chain. Sequential on purpose: a finished step is ticked, an
            unreachable one is dimmed and not clickable, and going back to a
            finished step to rewrite it is one click. */}
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
                {t.films.flow[name]}
              </div>
            );
          })}
        </div>

        <div className="side">
          <div className="grid" style={{ gap: 14 }}>
            {at === "premise" ? (
            <section className="panel">
              <div className="panel-head">
                <span className="label">{t.films.stepNew}</span>
                <div className="grow" />
                <span className="tag">
                  {f(t.films.stepOf, { n: step + 1, total: FLOW.length })}
                </span>
              </div>
              <div className="panel-body grid" style={{ gap: 14 }}>
                <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                  {t.films.intro}
                </p>

                <Field label={t.films.premise} hint={t.films.premiseHint}>
                  <textarea className="textarea" style={{ minHeight: 84 }}
                            value={premise} placeholder={t.films.premisePlaceholder}
                            onChange={(e) => setPremise(e.target.value)} />
                </Field>

                <Field label={t.films.instruction} hint={t.films.instructionHint}>
                  <textarea className="textarea" style={{ minHeight: 72 }}
                            value={instruction}
                            placeholder={t.films.instructionPlaceholder}
                            onChange={(e) => setInstruction(e.target.value)} />
                </Field>

                <Field label={t.films.titleLabel} hint={t.common.optional}>
                  <input className="input" value={title}
                         onChange={(e) => setTitle(e.target.value)} />
                </Field>

                <div className="two">
                  <Field label={t.films.scenes} hint={`${scenes}`}>
                    <input type="range" min={FILM_MIN_SCENES} max={FILM_MAX_SCENES}
                           step={1} value={scenes}
                           onChange={(e) => setScenes(Number(e.target.value))} />
                  </Field>
                  <Field label={t.films.shotSeconds}
                         hint={`${shotSeconds}${t.common.seconds} · ${t.films.shotSecondsHint}`}>
                    <input type="range" min={FILM_MIN_SHOT_SECONDS}
                           max={FILM_MAX_SHOT_SECONDS} step={1} value={shotSeconds}
                           onChange={(e) => setShotSeconds(Number(e.target.value))} />
                  </Field>
                </div>

                <Field label={t.films.aspect}>
                  <Chips value={aspect} onChange={setAspect}
                         options={ASPECTS.map((value) => ({ value, label: value }))} />
                </Field>

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
                            onChange={(e) => {
                              languageTouched.current = true;
                              setLanguage(e.target.value);
                            }}>
                      {languages.map((l) => (
                        <option key={l.value} value={l.value}>{l.label}</option>
                      ))}
                    </select>
                  </Field>
                </div>

                <Field label={t.newJob.watermark} hint={t.common.optional}>
                  <input className="input" value={watermark}
                         placeholder={t.newJob.watermarkPlaceholder}
                         onChange={(e) => setWatermark(e.target.value)} />
                </Field>

                {/* Creating a film already develops the story bible, so this
                    button is an LLM call, not just an insert. */}
                <button className="btn primary" onClick={create}
                        disabled={busy || premise.trim().length < 12}>
                  {busy ? t.films.creating : t.films.create}
                </button>
              </div>
            </section>
            ) : !current ? (
              <div className="empty">{t.films.pickFirst}</div>
            ) : at === "generate" ? (
              <GeneratePanel film={current} toast={toast} onChanged={pull} />
            ) : (
              <StagePanel film={current} stage={at} order={step}
                          total={FLOW.length} toast={toast} onChanged={pull}
                          onDeveloped={() => setStep((s) =>
                            Math.min(s + 1, FLOW.length - 1))} />
            )}

            {current ? (
              <FlowNav step={step} total={FLOW.length}
                       canGoNext={reachable(step + 1)}
                       onBack={() => setStep((s) => Math.max(s - 1, 0))}
                       onNext={() => setStep((s) =>
                         Math.min(s + 1, FLOW.length - 1))} />
            ) : null}
          </div>

          <div className="grid" style={{ gap: 14 }}>
            {current ? (
              <FilmSummary film={current} toast={toast}
                           onDeleted={() => { setActive(""); pull(); }} />
            ) : null}

            <section className="panel">
              <div className="panel-head">
                <span className="label">{t.films.mine}</span>
                <div className="grow" />
                <span className="tag">{films.length}</span>
              </div>
              <div className="panel-body grid" style={{ gap: 6 }}>
                {films.length === 0 ? (
                  <span className="dimmer" style={{ fontSize: 12.5 }}>
                    {t.films.noMine}
                  </span>
                ) : films.map((film) => (
                  <button key={film.id} className="btn sm ghost"
                          data-on={film.id === current?.id}
                          style={{ justifyContent: "space-between", width: "100%" }}
                          onClick={() => setActive(film.id)}>
                    <span style={{ minWidth: 0, overflow: "hidden",
                                   textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {film.title || film.bible?.title || t.films.untitled}
                    </span>
                    <StatusTag status={film.status} />
                  </button>
                ))}
              </div>
            </section>

            {/* Starting another production is a step back to the first link of
                the chain, not a separate screen. */}
            {current ? (
              <button className="btn sm ghost" onClick={() => {
                setActive("");
                steppedFor.current = "";
                setStep(0);
              }}>
                {t.films.stepNew}
              </button>
            ) : null}
          </div>
        </div>
      </div>
      {node}
    </>
  );
}

/** Back and forward through the chain. Forward is only offered once the
 *  current link is finished — the next stage is written against it. */
function FlowNav({ step, total, canGoNext, onBack, onNext }: {
  step: number; total: number; canGoNext: boolean;
  onBack: () => void; onNext: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="row spread wrap" style={{ gap: 8 }}>
      <button className="btn sm ghost" onClick={onBack} disabled={step === 0}>
        {t.films.back}
      </button>
      <button className="btn sm" onClick={onNext}
              disabled={step >= total - 1 || !canGoNext}>
        {t.films.next}
      </button>
    </div>
  );
}

// ----------------------------------------------------------------- the stages

/** The production itself, kept on screen through every step: what you asked
 *  for is the thing every stage is judged against. */
function FilmSummary({ film, toast, onDeleted }: {
  film: Film;
  toast: (m: string) => void;
  onDeleted: () => void;
}) {
  const { t } = useI18n();

  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <span className="label">
            {film.title || film.bible?.title || t.films.untitled}
          </span>
          <div className="grow" />
          <StatusTag status={film.status} />
        </div>
        <div className="panel-body grid" style={{ gap: 10 }}>
          <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
            {film.premise}
          </p>
          {film.instruction ? (
            <div className="row" style={{ gap: 10, alignItems: "start" }}>
              <span className="label" style={{ minWidth: 76, paddingTop: 2 }}>
                {t.films.instruction}
              </span>
              <span className="dimmer" style={{ fontSize: 12.5, lineHeight: 1.6 }}>
                {film.instruction}
              </span>
            </div>
          ) : null}
          {film.error ? (
            <div className="issue" data-sev="erro">
              <span className="label" style={{ minWidth: 52, paddingTop: 2 }}>
                {t.job.failure}
              </span>
              <div>{film.error}</div>
            </div>
          ) : null}
          <div className="row wrap" style={{ gap: 6 }}>
            {film.job_id ? (
              <Link className="btn sm" href={`/job/${film.job_id}`}>
                {t.films.openJob}
              </Link>
            ) : null}
            <button className="btn sm ghost" onClick={async () => {
              try {
                await api.deleteFilm(film.id);
                toast(t.films.deleted);
                onDeleted();
              } catch (e) { toast((e as Error).message); }
            }}>
              {t.films.deleteFilm}
            </button>
          </div>
        </div>
      </section>
    </>
  );
}

/** One link of the chain: what this stage produced, an instruction to redo it,
 *  and — where hand editing actually matters — editable fields.
 *
 *  A stage that has not been developed yet leads with the instruction box and a
 *  single button that develops it and moves on, so the flow reads forward. Once
 *  it exists, the content is what you see first and the button becomes a
 *  rewrite. */
function StagePanel({ film, stage, order, total, toast, onChanged, onDeveloped }: {
  film: Film;
  stage: FilmStage;
  order: number;
  total: number;
  toast: (m: string) => void;
  onChanged: () => void;
  onDeveloped: () => void;
}) {
  const { t, f } = useI18n();
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);

  const doc = film[stage];

  const develop = async () => {
    setBusy(true);
    try {
      const result = await api.developStage(film.id, stage, instruction.trim());
      setInstruction("");
      if (result.invalidated.length) {
        // Rewriting a stage drops what was written against it. Saying which,
        // rather than letting the ticks quietly disappear from the chain.
        toast(f(t.films.invalidated, {
          stages: result.invalidated
            .map((s) => t.films.stages[s]).join(", "),
        }));
      }
      onChanged();
      // Only step forward on a first development — a rewrite means the user is
      // working on this link and should stay on it.
      if (!doc) onDeveloped();
    } catch (e) {
      toast((e as Error).message);
    } finally { setBusy(false); }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.films.stages[stage]}</span>
        <div className="grow" />
        <span className="tag" data-tone={doc ? "ok" : ""}>
          {f(t.films.stepOf, { n: order + 1, total })}
        </span>
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
        {doc ? (
          <StageBody film={film} stage={stage} toast={toast}
                     onChanged={onChanged} />
        ) : null}

        <Field label={t.films.stageInstruction}
               hint={t.films.stageInstructionHint}>
          <textarea className="textarea" style={{ minHeight: 56 }}
                    value={instruction}
                    placeholder={t.films.stageInstructionPlaceholder}
                    onChange={(e) => setInstruction(e.target.value)} />
        </Field>

        <button className={doc ? "btn" : "btn primary"} onClick={develop}
                disabled={busy}>
          {busy ? t.films.developing
                : doc ? t.films.redevelop : t.films.developNext}
        </button>
      </div>
    </section>
  );
}

function StageBody({ film, stage, toast, onChanged }: {
  film: Film; stage: FilmStage;
  toast: (m: string) => void; onChanged: () => void;
}) {
  if (stage === "bible" && film.bible) {
    return <BibleEditor film={film} bible={film.bible} toast={toast}
                        onChanged={onChanged} />;
  }
  if (stage === "characters" && film.characters) {
    return <CharacterEditor film={film} cast={film.characters.characters}
                            toast={toast} onChanged={onChanged} />;
  }
  if (stage === "screenplay" && film.screenplay) {
    return <ScreenplayView scenes={film.screenplay.scenes} />;
  }
  if (stage === "shots" && film.shots) {
    return <ShotsView shots={film.shots.shots} />;
  }
  return null;
}

function BibleEditor({ film, bible, toast, onChanged }: {
  film: Film; bible: FilmBible;
  toast: (m: string) => void; onChanged: () => void;
}) {
  const { t, f } = useI18n();
  const [draft, setDraft] = useState(bible);
  const [busy, setBusy] = useState(false);

  // The server's version wins whenever it changes — a redevelop must not be
  // hidden behind a stale draft.
  const key = JSON.stringify(bible);
  useEffect(() => { setDraft(JSON.parse(key)); }, [key]);

  const set = (field: keyof FilmBible) => (value: string) =>
    setDraft((d) => ({ ...d, [field]: value }));

  const save = async () => {
    setBusy(true);
    try {
      await api.saveFilmStage(film.id, "bible", draft);
      toast(f(t.films.saved, { stage: t.films.stages.bible }));
      onChanged();
    } catch (e) {
      toast((e as Error).message);
    } finally { setBusy(false); }
  };

  return (
    <div className="grid" style={{ gap: 10 }}>
      <Field label={t.films.titleLabel}>
        <input className="input" value={draft.title}
               onChange={(e) => set("title")(e.target.value)} />
      </Field>
      <Field label={t.films.logline}>
        <textarea className="textarea" style={{ minHeight: 56 }} value={draft.logline}
                  onChange={(e) => set("logline")(e.target.value)} />
      </Field>
      <div className="two">
        <Field label={t.films.theme}>
          <input className="input" value={draft.theme}
                 onChange={(e) => set("theme")(e.target.value)} />
        </Field>
        <Field label={t.films.tone}>
          <input className="input" value={draft.tone}
                 onChange={(e) => set("tone")(e.target.value)} />
        </Field>
      </div>
      {/* Deliberately in English: it is pasted into the video model's prompt,
          which is trained on English descriptions. */}
      <Field label={t.films.look} hint={t.films.lookHint}>
        <textarea className="textarea" style={{ minHeight: 56 }} value={draft.look}
                  onChange={(e) => set("look")(e.target.value)} />
      </Field>
      <Field label={t.films.setting}>
        <textarea className="textarea" style={{ minHeight: 56 }} value={draft.setting}
                  onChange={(e) => set("setting")(e.target.value)} />
      </Field>

      <div className="grid" style={{ gap: 6 }}>
        <span className="label">{t.films.acts}</span>
        {bible.acts.map((act) => (
          <div key={act.act} style={{ borderTop: "1px solid var(--line)",
                                      paddingTop: 6 }}>
            <div className="mono" style={{ fontSize: 12 }}>
              {t.films.act} {act.act} · {act.name}
            </div>
            <div className="dimmer" style={{ fontSize: 12, lineHeight: 1.6 }}>
              {act.summary}
            </div>
            <div style={{ fontSize: 12, lineHeight: 1.6 }}>
              <span className="label">{t.films.turningPoint}</span> {act.turning_point}
            </div>
          </div>
        ))}
      </div>

      <button className="btn sm" onClick={save} disabled={busy}>
        {busy ? t.films.savingEdits : t.films.saveEdits}
      </button>
    </div>
  );
}

function CharacterEditor({ film, cast, toast, onChanged }: {
  film: Film; cast: FilmCharacter[];
  toast: (m: string) => void; onChanged: () => void;
}) {
  const { t, f } = useI18n();
  const [draft, setDraft] = useState(cast);
  const [busy, setBusy] = useState(false);

  const key = JSON.stringify(cast);
  useEffect(() => { setDraft(JSON.parse(key)); }, [key]);

  const set = (index: number, field: keyof FilmCharacter) => (value: string) =>
    setDraft((rows) => rows.map((row, i) =>
      (i === index ? { ...row, [field]: value } : row)));

  const save = async () => {
    setBusy(true);
    try {
      await api.saveFilmStage(film.id, "characters", { characters: draft });
      toast(f(t.films.saved, { stage: t.films.stages.characters }));
      onChanged();
    } catch (e) {
      toast((e as Error).message);
    } finally { setBusy(false); }
  };

  return (
    <div className="grid" style={{ gap: 12 }}>
      {draft.map((person, index) => (
        <div key={index} className="grid"
             style={{ gap: 8, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
          <div className="two">
            <Field label={t.films.characterName}>
              <input className="input" value={person.name}
                     onChange={(e) => set(index, "name")(e.target.value)} />
            </Field>
            <Field label={t.films.role}>
              <input className="input" value={person.role}
                     onChange={(e) => set(index, "role")(e.target.value)} />
            </Field>
          </div>
          <Field label={t.films.personality}>
            <textarea className="textarea" style={{ minHeight: 48 }}
                      value={person.personality}
                      onChange={(e) => set(index, "personality")(e.target.value)} />
          </Field>
          {/* This one field is the consistency anchor: it is pasted verbatim
              into every shot prompt featuring the character, which is the only
              thing keeping their face the same from shot to shot. */}
          <Field label={t.films.visual} hint={t.films.visualHint}>
            <textarea className="textarea" style={{ minHeight: 56 }}
                      value={person.visual}
                      onChange={(e) => set(index, "visual")(e.target.value)} />
          </Field>
          <Field label={t.films.voice}>
            <input className="input" value={person.voice}
                   onChange={(e) => set(index, "voice")(e.target.value)} />
          </Field>
        </div>
      ))}
      <button className="btn sm" onClick={save} disabled={busy}>
        {busy ? t.films.savingEdits : t.films.saveEdits}
      </button>
    </div>
  );
}

function ScreenplayView({ scenes }: { scenes: FilmScene[] }) {
  const { t } = useI18n();
  return (
    <div className="grid" style={{ gap: 10 }}>
      {scenes.map((scene) => (
        <div key={scene.scene} style={{ borderTop: "1px solid var(--line)",
                                        paddingTop: 8 }}>
          <div className="row spread wrap" style={{ gap: 8 }}>
            <span className="mono" style={{ fontSize: 12 }}>
              {t.films.scene} {scene.scene} · {scene.location} · {scene.time_of_day}
            </span>
            <span className="label">{scene.seconds}{t.common.seconds}</span>
          </div>
          <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>{scene.beat}</div>
          {scene.narration ? (
            <div style={{ fontSize: 12, lineHeight: 1.6 }}>
              <span className="label">{t.films.narration}</span> {scene.narration}
            </div>
          ) : null}
          {scene.dialogue?.length ? (
            <div className="grid" style={{ gap: 2, marginTop: 4 }}>
              {scene.dialogue.map((line, i) => (
                <div key={i} style={{ fontSize: 12, lineHeight: 1.6 }}>
                  <span className="mono">{line.character}:</span> {line.line}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function ShotsView({ shots }: { shots: NonNullable<Film["shots"]>["shots"] }) {
  const { t } = useI18n();
  return (
    <div className="grid" style={{ gap: 6 }}>
      {shots.map((shot, index) => (
        <div key={index} style={{ borderTop: "1px solid var(--line)", paddingTop: 6 }}>
          <div className="row spread wrap" style={{ gap: 8 }}>
            <span className="mono" style={{ fontSize: 12 }}>
              {t.films.scene} {shot.scene} · {t.films.shot} {shot.shot}
            </span>
            <span className="label">{shot.seconds}{t.common.seconds}</span>
          </div>
          <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>{shot.action}</div>
          <div className="dimmer" style={{ fontSize: 12, lineHeight: 1.6 }}>
            <span className="label">{t.films.camera}</span> {shot.camera}
            {shot.characters?.length ? ` · ${shot.characters.join(", ")}` : ""}
          </div>
        </div>
      ))}
    </div>
  );
}

// --------------------------------------------------------------- generating

/** The cost, then the confirmation. A run is minutes of paid generation, so
 *  the size of it is on screen before anything starts — the backend enforces
 *  the same thing by answering with the estimate unless `confirm` is set. */
function GeneratePanel({ film, toast, onChanged }: {
  film: Film; toast: (m: string) => void; onChanged: () => void;
}) {
  const { t, f } = useI18n();
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const estimate = film.estimate;
  const ready = Boolean(film.shots?.shots.length);
  const configured = Boolean(estimate?.provider.configured);

  const start = async (placeholderOk: boolean) => {
    setBusy(true);
    try {
      const result = await api.generateFilm(film.id, true, placeholderOk);
      if (result.queued) {
        toast(t.films.queued);
        setConfirming(false);
        onChanged();
      }
    } catch (e) {
      toast((e as Error).message);
    } finally { setBusy(false); }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.films.stepGenerate}</span>
        <div className="grow" />
        {isWorking(film.status) ? <StatusTag status={film.status} /> : null}
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
        {!ready ? (
          <span className="dimmer" style={{ fontSize: 12.5 }}>{t.films.needShots}</span>
        ) : (
          <>
            {estimate ? (
              <div className="grid" style={{ gap: 4 }}>
                <span className="label">{t.films.estimateTitle}</span>
                <span style={{ fontSize: 12.5 }}>
                  {f(t.films.estShots, { n: estimate.shots })} ·{" "}
                  {f(t.films.estSeconds, { n: estimate.seconds })} ·{" "}
                  {f(t.films.estScenes, { n: estimate.scenes })} ·{" "}
                  {f(t.films.estLines, { n: estimate.narration_lines })}
                </span>
                <span style={{ fontSize: 12.5 }}>
                  {f(t.films.estToGenerate, {
                    n: estimate.shots_to_generate,
                    seconds: estimate.seconds_to_generate,
                  })}
                </span>
                {estimate.reused_shots ? (
                  <span className="dimmer" style={{ fontSize: 12 }}>
                    {f(t.films.estReused, { n: estimate.reused_shots })}
                  </span>
                ) : null}
                <div className="row spread wrap" style={{ gap: 8, marginTop: 4 }}>
                  <span className="label">{t.films.provider}</span>
                  <span className="tag" data-tone={configured ? "ok" : "amber"}>
                    {configured ? providerName(estimate.provider)
                                : t.films.providerNone}
                  </span>
                </div>
                {!configured ? (
                  <>
                    {estimate.provider.reason ? (
                      <span className="dimmer" style={{ fontSize: 12, lineHeight: 1.6 }}>
                        {estimate.provider.reason}
                      </span>
                    ) : null}
                    <Link className="btn sm ghost" href="/contas">
                      {t.films.providerSetup}
                    </Link>
                  </>
                ) : null}
              </div>
            ) : null}

            {isWorking(film.status) ? (
              <>
                <div className="bar"><i className="indeterminate" /></div>
                <span className="dimmer" style={{ fontSize: 12.5 }}>
                  {t.films.queued}
                </span>
              </>
            ) : confirming ? (
              <div className="issue" data-sev="aviso">
                <span className="label" style={{ minWidth: 62, paddingTop: 2 }}>
                  {t.films.confirmTitle}
                </span>
                <div className="grid" style={{ gap: 8 }}>
                  <span>
                    {f(t.films.confirmBody, {
                      shots: estimate?.shots_to_generate ?? 0,
                      seconds: estimate?.seconds_to_generate ?? 0,
                      provider: estimate && configured
                        ? providerName(estimate.provider)
                        : t.films.providerNone,
                    })}
                  </span>
                  <div className="row wrap" style={{ gap: 6 }}>
                    <button className="btn primary" disabled={busy}
                            onClick={() => start(!configured)}>
                      {busy ? t.films.generating : t.films.confirmYes}
                    </button>
                    <button className="btn sm ghost" onClick={() => setConfirming(false)}>
                      {t.common.cancel}
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <div className="grid" style={{ gap: 6 }}>
                <button className="btn primary" onClick={() => setConfirming(true)}
                        disabled={busy}>
                  {configured ? t.films.generate : t.films.animatic}
                </button>
                {!configured ? (
                  <span className="dimmer" style={{ fontSize: 12, lineHeight: 1.6 }}>
                    {t.films.animaticHint}
                  </span>
                ) : null}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
