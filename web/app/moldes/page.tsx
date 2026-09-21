"use client";

import { useEffect, useState } from "react";

import { api, type Molde, type MoldeSummary, type MoldeVariant } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Field, Topbar, useToast } from "@/components/ui";

export default function Moldes() {
  const { t, f, narrationLanguage } = useI18n();
  const { toast, node } = useToast();

  const [list, setList] = useState<MoldeSummary[]>([]);
  const [current, setCurrent] = useState<Molde | null>(null);
  const [reference, setReference] = useState("");
  const [name, setName] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [subjects, setSubjects] = useState("");
  const [instruction, setInstruction] = useState("");
  const [variants, setVariants] = useState<MoldeVariant[]>([]);
  const [working, setWorking] = useState(false);

  const pull = () => api.moldes().then(setList).catch(() => setList([]));
  useEffect(() => { pull(); }, []);

  const analyze = async () => {
    if (!reference.trim()) return toast(t.moldes.needReference);
    setAnalyzing(true);
    try {
      const made = await api.analyzeMolde(reference.trim(), name.trim());
      setCurrent(made);
      setVariants([]);
      setReference("");
      setName("");
      pull();
      toast(f(t.moldes.analyzed, { beats: made.beats.length }));
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setAnalyzing(false);
    }
  };

  const open = async (id: string) => {
    try {
      setCurrent(await api.molde(id));
      setVariants([]);
    } catch (error) {
      toast((error as Error).message);
    }
  };

  const write = async (dryRun: boolean) => {
    if (!current) return;
    const list = subjects.split("\n").map((s) => s.trim()).filter(Boolean);
    if (!list.length) return toast(t.moldes.needSubjects);
    setWorking(true);
    try {
      const answer = await api.moldeVariants(current.id, {
        subjects: list, instruction, language: narrationLanguage,
        dry_run: dryRun,
      });
      setVariants(answer.variants);
      if (answer.failed.length) {
        toast(f(t.moldes.someFailed, { count: answer.failed.length }));
      } else if (!dryRun) {
        toast(f(t.moldes.queued, { count: answer.variants.length }));
      }
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setWorking(false);
    }
  };

  const remove = async (id: string) => {
    try {
      await api.deleteMolde(id);
      if (current?.id === id) setCurrent(null);
      pull();
    } catch (error) {
      toast((error as Error).message);
    }
  };

  return (
    <>
      <Topbar title={t.moldes.title}>
        <span className="dim" style={{ fontSize: 12 }}>{t.moldes.subtitle}</span>
      </Topbar>

      <div className="grid" style={{ gap: 14 }}>
        <section className="panel">
          <div className="panel-head">
            <span className="label">{t.moldes.analyzeTitle}</span>
          </div>
          <div className="panel-body grid" style={{ gap: 13 }}>
            {/* What is measured, said where it is decided: the shape, not the
                content. Someone who expects the words copied over should find
                out here and not from the first render. */}
            <p className="dim" style={{ fontSize: 12.5, lineHeight: 1.6,
                                        margin: 0 }}>
              {t.moldes.explain}
            </p>
            <Field label={t.moldes.reference} hint={t.moldes.referenceHint}>
              <div className="row" style={{ gap: 8 }}>
                <input
                  className="input"
                  placeholder="https://www.tiktok.com/@alguem/video/7123456"
                  value={reference}
                  onChange={(e) => setReference(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") analyze(); }}
                />
                <button className="btn primary" onClick={analyze}
                        disabled={analyzing}>
                  {analyzing ? t.moldes.analyzing : t.moldes.analyze}
                </button>
              </div>
            </Field>
            <Field label={t.moldes.name} hint={t.common.optional}>
              <input className="input" placeholder={t.moldes.namePlaceholder}
                     value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
          </div>
        </section>

        {list.length ? (
          <section className="panel">
            <div className="panel-head">
              <span className="label">{t.moldes.saved}</span>
            </div>
            <div className="panel-body grid" style={{ gap: 8 }}>
              {list.map((item) => (
                <div key={item.id} className="row spread wrap"
                     style={{ gap: 8, alignItems: "center" }}>
                  <button className="btn sm ghost" onClick={() => open(item.id)}>
                    {item.name}
                  </button>
                  <span className="dimmer mono" style={{ fontSize: 11.5 }}>
                    {item.seconds.toFixed(0)}s · {item.words} {t.moldes.words}
                  </span>
                  <button className="btn sm ghost" onClick={() => remove(item.id)}>
                    {t.common.remove}
                  </button>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {current ? (
          <section className="panel">
            <div className="panel-head row spread">
              <span className="label">{current.name}</span>
              <span className="dimmer mono" style={{ fontSize: 11.5 }}>
                {current.seconds.toFixed(0)}s · {current.pace} {t.moldes.pace} ·{" "}
                {current.cuts_per_minute} {t.moldes.cutsPerMin}
              </span>
            </div>
            <div className="panel-body grid" style={{ gap: 12 }}>
              {/* The shape itself, readable: this is what a new script has to
                  fit into, so it is worth seeing before writing one. */}
              <div className="grid" style={{ gap: 4 }}>
                {current.beats.map((beat, index) => (
                  <div key={index} className="row" style={{ gap: 10,
                                                            alignItems: "center" }}>
                    <span className="tag"
                          data-tone={beat.kind === "gancho" ? "ok"
                                     : beat.kind === "virada" ? "amber" : ""}>
                      {(t.moldes.beats as Record<string, string>)[beat.kind]
                       ?? beat.kind}
                    </span>
                    <span className="mono dimmer" style={{ fontSize: 11.5 }}>
                      {beat.seconds.toFixed(1)}s · {beat.words} {t.moldes.words}
                      {beat.cuts ? ` · ${beat.cuts} ${t.moldes.cuts}` : ""}
                    </span>
                    {/* the bar is the beat's share of the video */}
                    <div style={{ flex: 1, height: 6, minWidth: 40,
                                  background: "var(--void)", borderRadius: 3 }}>
                      <div style={{
                        width: `${(beat.seconds / current.seconds) * 100}%`,
                        height: "100%", borderRadius: 3,
                        background: beat.kind === "virada" ? "var(--amber)"
                                    : "var(--line-2, #2a2a2a)",
                      }} />
                    </div>
                  </div>
                ))}
              </div>

              <Field label={t.moldes.subjects} hint={t.moldes.subjectsHint}>
                <textarea className="textarea" style={{ minHeight: 76 }}
                          placeholder={t.moldes.subjectsPlaceholder}
                          value={subjects}
                          onChange={(e) => setSubjects(e.target.value)} />
              </Field>
              <Field label={t.moldes.instruction} hint={t.common.optional}>
                <input className="input" value={instruction}
                       onChange={(e) => setInstruction(e.target.value)} />
              </Field>

              <div className="row wrap" style={{ gap: 8 }}>
                <button className="btn" onClick={() => write(true)}
                        disabled={working}>
                  {working ? t.common.loading : t.moldes.preview}
                </button>
                <button className="btn primary" onClick={() => write(false)}
                        disabled={working}>
                  {t.moldes.generate}
                </button>
              </div>

              {variants.map((variant) => (
                <div key={variant.subject} className="issue" data-sev="info"
                     style={{ gridTemplateColumns: "minmax(0, 1fr)", gap: 6 }}>
                  <b style={{ fontSize: 13 }}>{variant.title}</b>
                  <span className="dim" style={{ fontSize: 12.5,
                                                 lineHeight: 1.6,
                                                 whiteSpace: "pre-wrap" }}>
                    {variant.text}
                  </span>
                  {variant.job_id ? (
                    <a className="btn sm ghost" href={`/job/${variant.job_id}`}
                       style={{ textDecoration: "none" }}>
                      {t.moldes.openJob} →
                    </a>
                  ) : null}
                </div>
              ))}
            </div>
          </section>
        ) : null}
      </div>
      {node}
    </>
  );
}
