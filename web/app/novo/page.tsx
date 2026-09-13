"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { api, type JobInput, type SourceType, type UploadResult, type Voice,
         type WatermarkPosition, type WatermarkSize } from "@/lib/api";
import { useI18n, type Dictionary } from "@/lib/i18n";
import { readDefaultWatermark, saveDefaultWatermark } from "@/lib/watermark";
import { Chips, Field, Topbar, useToast } from "@/components/ui";

// The `value`s below are the technical keys that go to the API: they don't
// change with the language. Only the label comes from the dictionary.
const NICHE_VALUES = [
  "tecnologia", "ciberseguranca", "programacao", "cinema", "historia",
  "ciencia", "curiosidades", "negocios", "games", "saude", "politica", "generico",
] as const;

const ANGLE_VALUES = [
  "auto", "critica", "analise", "contexto", "historia",
  "enredo", "curiosidade", "explicacao", "tutorial",
] as const;

const SOURCE_VALUES: SourceType[] = [
  "tema", "url", "video", "github", "imagem", "texto", "roteiro",
];

const WATERMARK_POSITION_VALUES: WatermarkPosition[] = [
  "baixo_centro", "baixo_esquerda", "baixo_direita",
  "topo_centro", "topo_esquerda", "topo_direita",
];
const WATERMARK_SIZE_VALUES: WatermarkSize[] = ["pequeno", "medio", "grande"];

const CAPTION_STYLE_VALUES = ["karaoke", "bloco", "palavra"] as const;
const SCROLL_VALUES = ["nenhum", "texto", "codigo", "pan"] as const;
const BACKGROUND_VALUES = [
  "auto", "broll", "video_fonte", "imagem_kenburns", "codigo_scroll", "gradiente",
  "ia_imagem", "ia_video", "site_scroll",
] as const;

// The nine the script prompt knows how to write in (backend: script.LANGUAGE_NAMES).
// Their labels are endonyms — someone picking Japanese for a short is not
// necessarily reading the panel in Portuguese.
const LANGUAGE_VALUES = [
  ["pt-BR", "Português (BR)"], ["en", "English"], ["es", "Español"],
  ["fr", "Français"], ["de", "Deutsch"], ["it", "Italiano"],
  ["ru", "Русский"], ["zh", "简体中文"], ["ja", "日本語"],
] as const;

const languages = () =>
  LANGUAGE_VALUES.map(([value, label]) => ({ value, label }));

const niches = (t: Dictionary) =>
  NICHE_VALUES.map((value) => ({ value, label: t.niches[value] }));

const angles = (t: Dictionary) =>
  ANGLE_VALUES.map((value) => ({ value, label: t.angles[value] }));

const sources = (t: Dictionary) =>
  SOURCE_VALUES.map((value) => ({
    value,
    label: t.sources[value],
    hint: t.sources[`${value}Hint`],
  }));

const captionStyles = (t: Dictionary) =>
  CAPTION_STYLE_VALUES.map((value) => ({ value, label: t.captionStyles[value] }));

const scrolls = (t: Dictionary) =>
  SCROLL_VALUES.map((value) => ({ value, label: t.scrolls[value] }));

const backgrounds = (t: Dictionary) =>
  BACKGROUND_VALUES.map((value) => ({ value, label: t.backgrounds[value] }));

/** Narration language and CTA come from the interface language: whoever uses
 *  it in Spanish gets Spanish narration by default. */
const defaults = (language: string, cta: string): JobInput => ({
  source_type: "tema",
  source: "",
  attachments: [],
  edit_mode: "narrar_por_cima",
  angle: "auto",
  instruction: "",
  niche: "tecnologia",
  language,
  voice_id: null,
  duration: 45,
  caption_style: "karaoke",
  caption_position: "centro",
  scroll: "nenhum",
  background: "auto",
  background_query: "",
  music: true,
  music_track: "",
  music_volume: 0.12,
  caption_offset: 0,
  hook_hard: true,
  cta,
  title_overlay: true,
  watermark: "",
  watermark_position: "baixo_centro",
  watermark_size: "medio",
  watermark_opacity: 0.6,
  variants: 1,
  qa_autofix: true,
  qa_max_attempts: 3,
});

export default function NovoShort() {
  const router = useRouter();
  const { t, f, narrationLanguage } = useI18n();
  const { toast, node } = useToast();
  const [form, setForm] = useState<JobInput>(() =>
    defaults(narrationLanguage, t.newJob.ctaDefault));
  const [voices, setVoices] = useState<Voice[]>([]);
  const [uploads, setUploads] = useState<UploadResult[]>([]);
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  // a hand-written CTA is not overwritten when the language changes
  const ctaTouched = useRef(false);
  const watermarkTouched = useRef(false);
  // a language picked for this short is not overwritten when the panel's own
  // language changes
  const languageTouched = useRef(false);

  const sourceOptions = useMemo(() => sources(t), [t]);
  const angleOptions = useMemo(() => angles(t), [t]);
  const nicheOptions = useMemo(() => niches(t), [t]);
  const languageOptions = useMemo(() => languages(), []);

  useEffect(() => {
    api.voices().then(setVoices).catch(() => setVoices([]));

    // the handle used on the last short comes pre-filled: it is the same on
    // practically every video, and retyping it was pure friction
    const savedWatermark = readDefaultWatermark();
    if (savedWatermark && !watermarkTouched.current) {
      setForm((prev) => (prev.watermark ? prev : { ...prev, watermark: savedWatermark }));
    }

    // coming from Trends: ?tema=...&niche=...&url=... already filled in
    const params = new URLSearchParams(window.location.search);
    const tema = params.get("tema");
    const url = params.get("url");
    const niche = params.get("niche");
    if (tema || url) {
      setForm((prev) => ({
        ...prev,
        source_type: url ? "url" : "tema",
        source: url || tema || "",
        instruction: url && tema
          ? f(t.newJob.trendInstruction, { topic: tema })
          : prev.instruction,
        niche: (niche as JobInput["niche"]) || prev.niche,
      }));
    }
  }, []);   // on mount only: the URL parameters are read once

  // The real language is only known after the provider's first effect (and can
  // change in the picker), so narration and CTA follow the interface.
  useEffect(() => {
    setForm((prev) => ({
      ...prev,
      // the interface's language is the default, never an override: picking
      // English for this short survives switching the panel back
      language: languageTouched.current ? prev.language : narrationLanguage,
      cta: ctaTouched.current ? prev.cta : t.newJob.ctaDefault,
    }));
  }, [narrationLanguage, t]);

  const set = <K extends keyof JobInput>(key: K, value: JobInput[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  // several links pasted at once become a montage; the hint says how many
  // the backend will actually pick up
  const linkCount = (form.source.match(/https?:\/\/\S+/g) ?? []).length;

  const acceptsFiles = form.source_type === "imagem" || form.source_type === "video";
  const isLongForm = form.source_type === "video";
  const hasVideoUpload = isLongForm && uploads.length > 0;

  const handleFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    try {
      const results: UploadResult[] = [];
      for (const file of Array.from(files)) {
        results.push(await api.upload(file));
      }
      // there is always a single video; images pile up into a sequence
      const merged = form.source_type === "video" ? results.slice(-1)
        : [...uploads, ...results];
      setUploads(merged);
      set("attachments", merged.map((u) => u.id));
      toast(f(t.newJob.uploaded, { count: results.length }));
    } catch (error) {
      toast(f(t.newJob.uploadFailed, { message: (error as Error).message }));
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const removeUpload = (id: string) => {
    const next = uploads.filter((u) => u.id !== id);
    setUploads(next);
    set("attachments", next.map((u) => u.id));
  };

  const submit = async () => {
    if (form.source_type === "imagem" && form.attachments.length === 0) {
      toast(t.newJob.errNoImage);
      return;
    }
    if (!form.source.trim() && form.attachments.length === 0) {
      toast(t.newJob.errNoSource);
      return;
    }
    setBusy(true);
    try {
      const { job_id } = await api.createJob(form);
      router.push(`/job/${job_id}`);
    } catch (error) {
      toast(f(t.newJob.errQueue, { message: (error as Error).message }));
      setBusy(false);
    }
  };

  const activeSource = sourceOptions.find((s) => s.value === form.source_type);

  return (
    <>
      <Topbar title={t.newJob.title}>
        <span className="label">{t.newJob.outputFixed}</span>
      </Topbar>

      <div className="content grid" style={{ gap: 18 }}>
        <div className="steps">
          {t.newJob.stepsShort.map((step) => (
            <div key={step} data-on="true">{step}</div>
          ))}
        </div>

        <div className="side">
          <div className="grid" style={{ gap: 14 }}>
            <section className="panel">
              <div className="panel-head">
                <span className="label">{t.newJob.stepSource}</span>
              </div>
              <div className="panel-body grid" style={{ gap: 14 }}>
                <Field label={t.newJob.inputType} hint={activeSource?.hint}>
                  <Chips
                    value={form.source_type}
                    onChange={(value) => {
                      set("source_type", value);
                      setUploads([]);
                      set("attachments", []);
                    }}
                    options={sourceOptions.map((s) => ({ value: s.value, label: s.label }))}
                  />
                </Field>

                {form.source_type !== "imagem" ? (
                  <Field
                    label={form.source_type === "roteiro"
                      ? t.newJob.yourScript : t.newJob.input}
                    hint={form.source_type === "video"
                      ? (linkCount > 1
                        ? f(t.newJob.severalLinks, { n: linkCount })
                        : t.newJob.linkOrFile)
                      : undefined}
                  >
                    {/* video takes a textarea too: several links, one per
                        line, become a montage across all of them */}
                    {form.source_type === "texto" || form.source_type === "roteiro"
                      || form.source_type === "video" ? (
                      <textarea
                        className="textarea"
                        style={form.source_type === "video"
                          ? { minHeight: 64 } : undefined}
                        placeholder={t.sources.placeholders[form.source_type]}
                        value={form.source}
                        onChange={(e) => set("source", e.target.value)}
                      />
                    ) : (
                      <input
                        className="input"
                        placeholder={t.sources.placeholders[form.source_type]}
                        value={form.source}
                        onChange={(e) => set("source", e.target.value)}
                      />
                    )}
                  </Field>
                ) : null}

                {acceptsFiles ? (
                  <Field
                    label={form.source_type === "imagem"
                      ? t.newJob.images : t.newJob.videoFile}
                    hint={form.source_type === "imagem"
                      ? t.newJob.imagesHint
                      : t.newJob.videoHint}
                  >
                    <div className="grid" style={{ gap: 8 }}>
                      <input
                        ref={fileInput}
                        className="input"
                        type="file"
                        accept={form.source_type === "imagem" ? "image/*" : "video/*"}
                        multiple={form.source_type === "imagem"}
                        onChange={(e) => handleFiles(e.target.files)}
                      />
                      {uploading ? <span className="label">{t.newJob.uploading}</span> : null}
                      {uploads.map((upload) => (
                        <div className="row spread wrap" key={upload.id}>
                          <span className="mono" style={{ fontSize: 12 }}>
                            {upload.filename}{" "}
                            <span className="dimmer">
                              ({(upload.size_bytes / 1048576).toFixed(1)} MB)
                            </span>
                          </span>
                          <button className="btn sm danger"
                                  onClick={() => removeUpload(upload.id)}>
                            {t.common.remove}
                          </button>
                        </div>
                      ))}
                    </div>
                  </Field>
                ) : null}

                {isLongForm ? (
                  <Field
                    label={t.newJob.videoMode}
                    hint={t.newJob.videoModeHint}
                  >
                    <Chips
                      value={form.edit_mode}
                      onChange={(value) => set("edit_mode", value)}
                      options={[
                        { value: "narrar_por_cima", label: t.newJob.narrateOver },
                        { value: "resumo", label: t.newJob.summarize },
                      ]}
                    />
                    {hasVideoUpload ? (
                      <p className="dimmer" style={{ margin: "8px 0 0", fontSize: 12 }}>
                        {t.newJob.batchHint}{" "}
                        <a href={`/lote?attachment=${uploads[0].id}&name=${encodeURIComponent(uploads[0].filename)}`}
                           style={{ color: "var(--amber)" }}>
                          {t.newJob.batchLink}
                        </a>
                      </p>
                    ) : null}
                  </Field>
                ) : null}

                {form.source_type !== "roteiro" ? (
                  <Field
                    label={t.newJob.instruction}
                    hint={t.newJob.instructionHint}
                  >
                    <textarea
                      className="textarea"
                      style={{ minHeight: 62 }}
                      placeholder={t.newJob.instructionPlaceholder}
                      value={form.instruction}
                      onChange={(e) => set("instruction", e.target.value)}
                    />
                  </Field>
                ) : null}

                {form.source_type !== "roteiro" ? (
                  <Field label={t.newJob.angle} hint={t.newJob.angleHint}>
                    <Chips
                      value={form.angle}
                      onChange={(value) => set("angle", value)}
                      options={angleOptions}
                    />
                  </Field>
                ) : null}

                {form.source_type !== "roteiro" ? (
                  <Field label={t.newJob.niche} hint={t.newJob.nicheHint}>
                    <Chips
                      value={form.niche}
                      onChange={(value) => set("niche", value)}
                      options={nicheOptions}
                    />
                  </Field>
                ) : null}

                {/* The short's language, not the panel's. They start equal —
                    someone using the interface in Portuguese usually wants a
                    Portuguese short — but a channel that publishes in two
                    languages must not have to switch the whole interface to
                    make the second video. The narration voice follows this
                    too: without it, an English script was read by a Brazilian
                    voice. */}
                <Field label={t.newJob.language} hint={t.newJob.languageHint}>
                  <Chips
                    value={form.language}
                    onChange={(value) => {
                      languageTouched.current = true;
                      set("language", value);
                    }}
                    options={languageOptions}
                  />
                </Field>

                <div className="two">
                  <Field label={t.newJob.duration}
                         hint={`${form.duration}${t.common.seconds}`}>
                    <input
                      type="range"
                      min={15}
                      max={90}
                      step={5}
                      value={form.duration}
                      onChange={(e) => set("duration", Number(e.target.value))}
                    />
                  </Field>
                  <Field label={t.newJob.cta}>
                    <input
                      className="input"
                      value={form.cta}
                      onChange={(e) => {
                        ctaTouched.current = true;
                        set("cta", e.target.value);
                      }}
                    />
                  </Field>
                </div>
              </div>
            </section>

            <section className="panel">
              <div className="panel-head">
                <span className="label">{t.newJob.stepVoice}</span>
              </div>
              <div className="panel-body grid" style={{ gap: 14 }}>
                <div className="two">
                  <Field label={t.newJob.voice} hint={t.newJob.voiceHint}>
                    <select
                      className="select"
                      value={form.voice_id ?? ""}
                      onChange={(e) => set("voice_id", e.target.value || null)}
                    >
                      <option value="">{t.newJob.voiceDefault}</option>
                      {voices.map((voice) => (
                        <option key={voice.id} value={voice.id}>
                          {voice.name} · {voice.provider}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label={t.newJob.captionPosition}>
                    <select
                      className="select"
                      value={form.caption_position}
                      onChange={(e) => set("caption_position",
                        e.target.value as JobInput["caption_position"])}
                    >
                      <option value="centro">{t.positions.centro}</option>
                      <option value="baixo">{t.positions.baixo}</option>
                      <option value="topo">{t.positions.topo}</option>
                    </select>
                  </Field>
                </div>

                <Field label={t.newJob.captionStyle}>
                  <Chips
                    value={form.caption_style}
                    onChange={(value) => set("caption_style", value)}
                    options={captionStyles(t)}
                  />
                </Field>
              </div>
            </section>

            <section className="panel">
              <div className="panel-head">
                <span className="label">{t.newJob.stepImage}</span>
              </div>
              <div className="panel-body grid" style={{ gap: 14 }}>
                <Field label={t.newJob.background}>
                  <Chips
                    value={form.background}
                    onChange={(value) => set("background", value)}
                    options={backgrounds(t)}
                  />
                </Field>

                <Field label={t.newJob.scroll} hint={t.newJob.scrollHint}>
                  <Chips
                    value={form.scroll}
                    onChange={(value) => set("scroll", value)}
                    options={scrolls(t)}
                  />
                </Field>

                <div className="two">
                  {/* The same field carries two different things depending on
                      the background, so it says which one it wants: search
                      terms for stock footage, an address for the recording.
                      Asking for a URL under the label "B-roll search" is how
                      someone ends up with no idea where the page goes. */}
                  <Field
                    label={form.background === "site_scroll"
                      ? t.newJob.pageToRecord : t.newJob.brollQuery}
                    hint={form.background === "site_scroll"
                      ? (form.source_type === "url" || form.source_type === "github"
                        ? t.newJob.pageToRecordHintSource
                        : t.newJob.pageToRecordHint)
                      : t.newJob.brollQueryHint}>
                    <input
                      className="input"
                      placeholder={form.background === "site_scroll"
                        ? "https://github.com/rtk-ai/rtk"
                        : "server room, hacker typing"}
                      value={form.background_query}
                      onChange={(e) => set("background_query", e.target.value)}
                    />
                  </Field>
                  <Field label={t.newJob.watermark}
                         hint={form.watermark ? t.editor.watermarkHint : undefined}>
                    <input
                      className="input"
                      placeholder={t.newJob.watermarkPlaceholder}
                      value={form.watermark}
                      onChange={(e) => {
                        watermarkTouched.current = true;
                        set("watermark", e.target.value);
                        // typing here also updates the remembered handle, so the
                        // next short starts from the value actually in use
                        saveDefaultWatermark(e.target.value);
                      }}
                    />
                  </Field>
                </div>

                {form.watermark.trim() ? (
                  <div className="grid" style={{ gap: 12 }}>
                    <Field label={t.editor.watermarkPositionField}>
                      <Chips
                        value={form.watermark_position}
                        onChange={(v) => set("watermark_position", v)}
                        options={WATERMARK_POSITION_VALUES.map((value) => ({
                          value, label: t.editor.watermarkPositions[value],
                        }))}
                      />
                    </Field>
                    <div className="two">
                      <Field label={t.editor.watermarkSizeField}>
                        <Chips
                          value={form.watermark_size}
                          onChange={(v) => set("watermark_size", v)}
                          options={WATERMARK_SIZE_VALUES.map((value) => ({
                            value, label: t.editor.watermarkSizes[value],
                          }))}
                        />
                      </Field>
                      <Field label={t.editor.watermarkOpacityField}
                             hint={`${Math.round(form.watermark_opacity * 100)}%`}>
                        <input type="range" min={0.15} max={1} step={0.05}
                               value={form.watermark_opacity}
                               onChange={(e) =>
                                 set("watermark_opacity", Number(e.target.value))} />
                      </Field>
                    </div>
                  </div>
                ) : null}

                <div className="row wrap" style={{ gap: 18 }}>
                  <Toggle label={t.newJob.musicToggle} on={form.music}
                          onChange={(v) => set("music", v)} />
                  <Toggle label={t.newJob.titleOverlay} on={form.title_overlay}
                          onChange={(v) => set("title_overlay", v)} />
                  <Toggle label={t.newJob.hardHook} on={form.hook_hard}
                          onChange={(v) => set("hook_hard", v)} />
                </div>
              </div>
            </section>

            <section className="panel">
              <div className="panel-head">
                <span className="label">{t.newJob.stepQa}</span>
              </div>
              <div className="panel-body grid" style={{ gap: 12 }}>
                <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                  {t.newJob.qaExplain}
                </p>
                <div className="row wrap" style={{ gap: 18 }}>
                  <Toggle label={t.newJob.qaAutofix} on={form.qa_autofix}
                          onChange={(v) => set("qa_autofix", v)} />
                  <Field label={t.newJob.qaAttempts} hint={`${form.qa_max_attempts}`}>
                    <input
                      type="range" min={1} max={5} step={1}
                      value={form.qa_max_attempts}
                      disabled={!form.qa_autofix}
                      onChange={(e) => set("qa_max_attempts", Number(e.target.value))}
                    />
                  </Field>
                </div>
              </div>
            </section>
          </div>

          <aside className="panel" style={{ position: "sticky", top: 76 }}>
            <div className="panel-head">
              <span className="label">{t.newJob.summary}</span>
            </div>
            <div className="panel-body grid" style={{ gap: 12 }}>
              <Summary k={t.newJob.summaryOrigin}
                       v={activeSource?.label ?? form.source_type} />
              {isLongForm ? (
                <Summary
                  k={t.newJob.summaryMode}
                  v={form.edit_mode === "resumo"
                    ? t.newJob.summarize : t.newJob.narrateOver}
                />
              ) : null}
              {form.attachments.length ? (
                <Summary k={t.newJob.summaryFiles} v={`${form.attachments.length}`} />
              ) : null}
              <Summary
                k={t.newJob.summaryNiche}
                v={t.niches[form.niche as keyof typeof t.niches] ?? form.niche}
              />
              {form.angle !== "auto" ? (
                <Summary
                  k={t.newJob.summaryAngle}
                  v={angleOptions.find((a) => a.value === form.angle)?.label ?? form.angle}
                />
              ) : null}
              {form.instruction.trim() ? (
                <Summary k={t.newJob.summaryInstruction}
                         v={t.newJob.summaryInstructionSet} />
              ) : null}
              <Summary k={t.newJob.summaryDuration}
                       v={`${form.duration}${t.common.seconds}`} />
              <Summary
                k={t.newJob.summaryCaption}
                v={`${t.captionStyles[`${form.caption_style}Short`]} · ${t.positions[`${form.caption_position}Short`]}`}
              />
              <Summary
                k={t.newJob.summaryBackground}
                v={`${t.backgrounds[form.background]}${
                  form.scroll !== "nenhum" ? ` + ${t.scrolls[form.scroll]}` : ""}`}
              />
              <Summary
                k={t.newJob.summaryVoice}
                v={voices.find((v) => v.id === form.voice_id)?.name ?? t.common.default}
              />
              <Summary
                k={t.newJob.summaryAutofix}
                v={form.qa_autofix
                  ? f(t.newJob.summaryAutofixOn, { n: form.qa_max_attempts })
                  : t.newJob.summaryAutofixOff}
              />
              <hr className="rule" />
              <button className="btn primary" onClick={submit} disabled={busy || uploading}>
                {busy ? t.newJob.submitting : t.newJob.submit}
              </button>
            </div>
          </aside>
        </div>
      </div>
      {node}
    </>
  );
}

function Summary({ k, v }: { k: string; v: string }) {
  return (
    <div className="row spread">
      <span className="label">{k}</span>
      <span className="mono" style={{ fontSize: 12 }}>{v}</span>
    </div>
  );
}

function Toggle({ label, on, onChange }: {
  label: string; on: boolean; onChange: (value: boolean) => void;
}) {
  return (
    <button type="button" className="chip" data-on={on} onClick={() => onChange(!on)}>
      {on ? "✓ " : "○ "}
      {label}
    </button>
  );
}
