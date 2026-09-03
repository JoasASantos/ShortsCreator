"use client";

import { useEffect, useRef, useState } from "react";

import {
  api, type Job, type MusicTrack, type ScriptDraft, type ScriptEdit,
  type ScriptSegment, type Voice, type WatermarkPosition, type WatermarkSize,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { readDefaultWatermark, saveDefaultWatermark } from "@/lib/watermark";
import { Chips, Field } from "@/components/ui";
import { VoiceBrowser } from "@/components/VoiceBrowser";

// Technical values sent to the backend; the label comes from the dictionary.
const KINDS = ["hook", "corpo", "cta"];
const AUTOSAVE_MS = 1200;

const WATERMARK_POSITIONS: WatermarkPosition[] = [
  "baixo_centro", "baixo_esquerda", "baixo_direita",
  "topo_centro", "topo_esquerda", "topo_direita",
];
const WATERMARK_SIZES: WatermarkSize[] = ["pequeno", "medio", "grande"];

export function Editor({ job, onApplied, toast }: {
  job: Job;
  onApplied: () => void;
  toast: (message: string) => void;
}) {
  const { t, f, dateTime } = useI18n();
  const [segments, setSegments] = useState<ScriptSegment[]>(
    job.result?.script.segments ?? []);
  const [title, setTitle] = useState(job.result?.title ?? "");
  const [voices, setVoices] = useState<Voice[]>([]);
  const [tracks, setTracks] = useState<MusicTrack[]>([]);
  const [voiceId, setVoiceId] = useState(job.input.voice_id ?? "");
  const [captionStyle, setCaptionStyle] = useState(job.input.caption_style);
  const [captionPosition, setCaptionPosition] = useState(job.input.caption_position);
  const [offset, setOffset] = useState(job.input.caption_offset ?? 0);
  const [music, setMusic] = useState(job.input.music);
  const [musicTrack, setMusicTrack] = useState(job.input.music_track ?? "");
  const [musicVolume, setMusicVolume] = useState(job.input.music_volume);
  const [watermark, setWatermark] = useState(job.input.watermark ?? "");
  const [markPosition, setMarkPosition] =
    useState<WatermarkPosition>(job.input.watermark_position ?? "baixo_centro");
  const [markSize, setMarkSize] =
    useState<WatermarkSize>(job.input.watermark_size ?? "medio");
  const [markOpacity, setMarkOpacity] = useState(job.input.watermark_opacity ?? 0.6);
  // whether this handle should come pre-filled on the next shorts
  const [rememberBrand, setRememberBrand] = useState(false);
  const [busy, setBusy] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [refining, setRefining] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  // "limpo" = same as server | "pendente" = typing | "salvo" = draft on disk
  const [rascunho, setRascunho] = useState<"limpo" | "pendente" | "salvando" | "salvo">("limpo");
  const [salvoEm, setSalvoEm] = useState<string | null>(null);
  const [carregado, setCarregado] = useState(false);
  const musicInput = useRef<HTMLInputElement>(null);
  const voiceAudio = useRef<HTMLAudioElement | null>(null);
  // flags that the on-screen content is the user's edit, not what came from the server
  const editando = useRef(false);

  const loadTracks = () => api.music().then(setTracks).catch(() => setTracks([]));

  useEffect(() => {
    api.voices().then(setVoices).catch(() => setVoices([]));
    loadTracks();
  }, []);

  // Draft from disk: whatever was being typed when the screen was closed.
  useEffect(() => {
    api.draft(job.id)
      .then(({ draft }) => {
        if (!draft) return;
        setSegments(draft.segments);
        setTitle(draft.title);
        setVoiceId(draft.voice_id);
        setCaptionStyle(draft.caption_style as typeof captionStyle);
        setCaptionPosition(draft.caption_position as typeof captionPosition);
        setOffset(draft.caption_offset);
        setMusic(draft.music);
        setMusicTrack(draft.music_track);
        setMusicVolume(draft.music_volume);
        if (draft.watermark !== undefined) setWatermark(draft.watermark);
        if (draft.watermark_position) setMarkPosition(draft.watermark_position);
        if (draft.watermark_size) setMarkSize(draft.watermark_size);
        if (draft.watermark_opacity !== undefined) setMarkOpacity(draft.watermark_opacity);
        setSalvoEm(draft.saved_at ?? null);
        setRascunho("salvo");
        editando.current = true;   // keeps polling from overwriting it
      })
      .catch(() => undefined)
      .finally(() => setCarregado(true));
  }, [job.id]);

  // A watermark saved as the default shows as already ticked, and an empty
  // field on an existing job is filled with it — retyping your own handle on
  // every short is the friction this removes.
  useEffect(() => {
    const saved = readDefaultWatermark();
    if (!saved) return;
    setRememberBrand(saved === (job.input.watermark ?? "").trim());
    if (!(job.input.watermark ?? "").trim()) setWatermark(saved);
  }, [job.input.watermark]);

  // The script can change outside this component (prompt refinement,
  // re-rendering). We sync with the server, but NEVER on top of an edit in
  // progress: `serverScript` is a brand-new array on every poll, so comparing
  // by reference wiped out whatever was being typed every 2.5s.
  const serverScript = job.result?.script.segments;
  const serverKey = serverScript ? JSON.stringify(serverScript) : "";
  const serverTitle = job.result?.title ?? "";
  useEffect(() => {
    if (editando.current || !serverKey) return;
    setSegments(JSON.parse(serverKey));
    if (serverTitle) setTitle(serverTitle);
  }, [serverKey, serverTitle]);

  // Autosave: only after the existing draft has been loaded, so we don't
  // write the initial state over what was already saved.
  const draftAtual = (): ScriptDraft => ({
    segments, title, voice_id: voiceId, caption_style: captionStyle,
    caption_position: captionPosition, caption_offset: offset,
    music, music_track: musicTrack, music_volume: musicVolume, watermark,
    watermark_position: markPosition, watermark_size: markSize,
    watermark_opacity: markOpacity,
  });
  const draftKey = JSON.stringify(draftAtual());

  useEffect(() => {
    if (!carregado || !editando.current || rascunho === "limpo") return;
    setRascunho("pendente");
    const timer = setTimeout(() => {
      setRascunho("salvando");
      api.saveDraft(job.id, JSON.parse(draftKey))
        .then((r) => { setSalvoEm(r.saved_at); setRascunho("salvo"); })
        .catch(() => setRascunho("pendente"));
    }, AUTOSAVE_MS);
    return () => clearTimeout(timer);
  }, [draftKey, carregado, job.id]);   // eslint-disable-line react-hooks/exhaustive-deps

  // Closing the tab with something still unsaved: the browser asks to confirm.
  useEffect(() => {
    const aviso = (e: BeforeUnloadEvent) => {
      if (rascunho === "pendente" || rascunho === "salvando") e.preventDefault();
    };
    window.addEventListener("beforeunload", aviso);
    return () => window.removeEventListener("beforeunload", aviso);
  }, [rascunho]);

  /** Every user change goes through here: it turns on the autosave and locks
   *  out syncing with the server. */
  const tocar = () => {
    editando.current = true;
    if (rascunho === "limpo") setRascunho("pendente");
  };

  const words = job.result?.words ?? [];
  const totalWords = segments.reduce((sum, s) => sum + s.text.split(/\s+/).filter(Boolean).length, 0);
  // ~2.6 words per second is the pace the pipeline assumes when writing a script
  const estimate = totalWords / 2.6;

  const updateSegment = (index: number, patch: Partial<ScriptSegment>) => {
    tocar();
    setSegments((prev) => prev.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  };

  const addSegment = (index: number) => {
    tocar();
    setSegments((prev) => [
      ...prev.slice(0, index + 1),
      { kind: "corpo", text: "", broll_query: "", on_screen: "" },
      ...prev.slice(index + 1),
    ]);
  };

  const removeSegment = (index: number) => {
    tocar();
    setSegments((prev) => prev.filter((_, i) => i !== index));
  };

  const move = (index: number, delta: number) => {
    tocar();
    setSegments((prev) => {
      const next = [...prev];
      const target = index + delta;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  const apply = async (withScript: boolean) => {
    const clean = segments.filter((s) => s.text.trim());
    if (withScript && clean.length === 0) {
      toast(t.editor.needSegment);
      return;
    }
    const edit: ScriptEdit = {
      voice_id: voiceId || null,
      caption_style: captionStyle,
      caption_position: captionPosition,
      caption_offset: offset,
      music,
      music_track: musicTrack,
      music_volume: musicVolume,
      watermark,
      watermark_position: markPosition,
      watermark_size: markSize,
      watermark_opacity: markOpacity,
    };
    if (withScript) {
      edit.segments = clean;
      edit.title = title;
    }
    setBusy(true);
    try {
      const result = await api.editJob(job.id, edit);
      // the draft became the official version: the backend already deleted the file
      editando.current = false;
      setRascunho("limpo");
      setSalvoEm(null);
      toast(f(t.editor.applied, { n: result.applied.length }));
      onApplied();
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const descartarRascunho = async () => {
    try {
      await api.discardDraft(job.id);
      editando.current = false;
      setRascunho("limpo");
      setSalvoEm(null);
      if (serverScript) setSegments(serverScript);
      if (serverTitle) setTitle(serverTitle);
      toast(t.editor.draftDiscarded);
    } catch (error) {
      toast((error as Error).message);
    }
  };

  const refine = async () => {
    if (!prompt.trim()) {
      toast(t.editor.promptEmpty);
      return;
    }
    setRefining(true);
    try {
      // render=false: shows the result in the editor so you can review it first
      const result = await api.refineScript(job.id, prompt, false);
      tocar();
      setSegments(result.script.segments);
      setTitle(result.script.title);
      setPrompt("");
      toast(t.editor.rewritten);
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setRefining(false);
    }
  };

  const selectedVoice = voices.find((v) => v.id === voiceId);
  const kindLabel = (kind: string) =>
    (t.segmentKinds as Record<string, string>)[kind] ?? kind;

  /** Plays the chosen voice's sample straight from the catalog, no synthesis spent. */
  const previewVoice = () => {
    voiceAudio.current?.pause();
    if (previewing) { setPreviewing(false); return; }
    if (!selectedVoice?.provider_voice_id) {
      toast(t.editor.noSample);
      return;
    }
    const audio = new Audio(api.sampleUrl(selectedVoice.provider_voice_id));
    audio.onended = () => setPreviewing(false);
    audio.onerror = () => { setPreviewing(false); toast(t.editor.sampleUnavailable); };
    audio.play().catch(() => { setPreviewing(false); toast(t.editor.cantPlay); });
    voiceAudio.current = audio;
    setPreviewing(true);
  };

  const uploadMusic = async (files: FileList | null) => {
    if (!files?.length) return;
    try {
      const track = await api.uploadMusic(files[0]);
      await loadTracks();
      setMusicTrack(track.id);
      setMusic(true);
      toast(f(t.editor.musicAdded, { name: track.name }));
    } catch (error) {
      toast((error as Error).message);
    } finally {
      if (musicInput.current) musicInput.current.value = "";
    }
  };

  return (
    <div className="grid" style={{ gap: 14 }}>
      <section className="panel">
        <div className="panel-head">
          <span className="label">{t.editor.scriptTitle}</span>
          <div className="grow" />
          <RascunhoTag estado={rascunho} salvoEm={salvoEm} />
          <span className="label">
            {f(t.editor.wordsEstimate,
               { words: totalWords, seconds: estimate.toFixed(0) })}
          </span>
        </div>
        <div className="panel-body grid" style={{ gap: 12 }}>
          <Field label={t.editor.titleField}>
            <input className="input" value={title}
                   onChange={(e) => { tocar(); setTitle(e.target.value); }} />
          </Field>

          <Field
            label={t.editor.promptField}
            hint={t.editor.promptHint}
          >
            <div className="grid" style={{ gap: 8 }}>
              <textarea
                className="textarea"
                style={{ minHeight: 58 }}
                placeholder={t.editor.promptPlaceholder}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) refine();
                }}
              />
              <div className="row spread wrap">
                <span className="label">
                  {refining ? t.editor.promptRunning : t.editor.promptShortcut}
                </span>
                <button className="btn sm" onClick={refine} disabled={refining}>
                  {refining ? t.editor.rewriting : t.editor.rewrite}
                </button>
              </div>
              {refining ? (
                <div className="bar"><i className="indeterminate" /></div>
              ) : null}
            </div>
          </Field>

          {segments.map((segment, index) => (
            <div className="panel" style={{ background: "var(--void)" }} key={index}>
              <div className="panel-head" style={{ padding: "8px 12px" }}>
                <select
                  className="select"
                  style={{ width: 110, padding: "4px 8px", fontSize: 11 }}
                  value={segment.kind}
                  onChange={(e) => updateSegment(index, { kind: e.target.value })}
                >
                  {KINDS.map((k) => (
                    <option key={k} value={k}>{kindLabel(k)}</option>
                  ))}
                </select>
                <span className="label">
                  {segment.text.split(/\s+/).filter(Boolean).length}p
                </span>
                <div className="grow" />
                <button className="btn sm ghost" onClick={() => move(index, -1)}
                        disabled={index === 0}>↑</button>
                <button className="btn sm ghost" onClick={() => move(index, 1)}
                        disabled={index === segments.length - 1}>↓</button>
                <button className="btn sm ghost" onClick={() => addSegment(index)}>+</button>
                <button className="btn sm danger" onClick={() => removeSegment(index)}
                        disabled={segments.length <= 1}>×</button>
              </div>
              <div className="panel-body grid" style={{ gap: 8, padding: 12 }}>
                <textarea
                  className="textarea"
                  style={{ minHeight: 64 }}
                  value={segment.text}
                  placeholder={t.editor.segmentPlaceholder}
                  onChange={(e) => updateSegment(index, { text: e.target.value })}
                />
                <div className="two">
                  <input
                    className="input"
                    placeholder={t.editor.brollPlaceholder}
                    value={segment.broll_query ?? ""}
                    onChange={(e) => updateSegment(index, { broll_query: e.target.value })}
                  />
                  <input
                    className="input"
                    placeholder={t.editor.onScreenPlaceholder}
                    value={segment.on_screen ?? ""}
                    onChange={(e) => updateSegment(index, { on_screen: e.target.value })}
                  />
                </div>
              </div>
            </div>
          ))}

          <button className="btn sm ghost" onClick={() => addSegment(segments.length - 1)}>
            {t.editor.addSegment}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <span className="label">{t.editor.audioTitle}</span>
        </div>
        <div className="panel-body grid" style={{ gap: 13 }}>
          <Field label={t.editor.voiceField}
                 hint={f(t.editor.voiceSaved, { n: voices.length })}>
            <div className="grid" style={{ gap: 8 }}>
              <div className="row wrap" style={{ gap: 8 }}>
                <select className="select grow" value={voiceId}
                        onChange={(e) => { tocar(); setVoiceId(e.target.value); }}>
                  <option value="">{t.editor.voiceDefault}</option>
                  {voices.map((voice) => (
                    <option key={voice.id} value={voice.id}>
                      {voice.name} · {voice.provider}
                    </option>
                  ))}
                </select>
                <button
                  className="btn sm ghost"
                  onClick={previewVoice}
                  disabled={!selectedVoice?.provider_voice_id}
                  title={selectedVoice?.provider_voice_id
                    ? t.editor.listenTooltip
                    : t.editor.listenUnavailable}
                >
                  {previewing ? t.editor.stop : t.editor.listen}
                </button>
              </div>

              <VoiceBrowser
                toast={toast}
                onInstalled={(id) => {
                  // leave the just-saved voice already selected
                  api.voices().then((list) => {
                    setVoices(list);
                    setVoiceId(id);
                  }).catch(() => undefined);
                }}
              />
            </div>
          </Field>

          <Field label={t.editor.musicField}
                 hint={f(t.editor.musicLibrary, { n: tracks.length })}>
            <div className="grid" style={{ gap: 8 }}>
              <select className="select" value={music ? musicTrack : ""}
                      onChange={(e) => {
                        tocar();
                        setMusicTrack(e.target.value);
                        setMusic(Boolean(e.target.value));
                      }}>
                <option value="">{t.editor.noMusic}</option>
                {tracks.map((track) => (
                  <option key={track.id} value={track.id}>
                    {track.name} · {track.duration}{t.common.seconds}
                  </option>
                ))}
              </select>
              <div className="row wrap" style={{ gap: 8 }}>
                <input ref={musicInput} className="input grow" type="file"
                       accept="audio/*" onChange={(e) => uploadMusic(e.target.files)} />
                {musicTrack ? (
                  <audio controls preload="none" style={{ height: 32 }}
                         src={`/api/music/${musicTrack}/preview`} />
                ) : null}
              </div>
            </div>
          </Field>

          {music ? (
            <Field label={t.editor.musicVolume} hint={musicVolume.toFixed(2)}>
              <input type="range" min={0.02} max={0.4} step={0.01}
                     value={musicVolume}
                     onChange={(e) => { tocar(); setMusicVolume(Number(e.target.value)); }} />
            </Field>
          ) : null}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <span className="label">{t.editor.brandTitle}</span>
          <div className="grow" />
          <span className="label">
            {watermark.trim() ? watermark : t.editor.watermarkNone}
          </span>
        </div>
        <div className="panel-body grid" style={{ gap: 10 }}>
          <Field label={t.editor.watermarkField} hint={t.editor.watermarkHint}>
            <input
              className="input"
              placeholder={t.editor.watermarkPlaceholder}
              value={watermark}
              onChange={(e) => { tocar(); setWatermark(e.target.value); }}
            />
          </Field>

          {watermark.trim() ? (
            <>
              <Field label={t.editor.watermarkPositionField}>
                <Chips
                  value={markPosition}
                  onChange={(v) => { tocar(); setMarkPosition(v); }}
                  options={WATERMARK_POSITIONS.map((value) => ({
                    value, label: t.editor.watermarkPositions[value],
                  }))}
                />
              </Field>
              <div className="two">
                <Field label={t.editor.watermarkSizeField}>
                  <Chips
                    value={markSize}
                    onChange={(v) => { tocar(); setMarkSize(v); }}
                    options={WATERMARK_SIZES.map((value) => ({
                      value, label: t.editor.watermarkSizes[value],
                    }))}
                  />
                </Field>
                <Field label={t.editor.watermarkOpacityField}
                       hint={`${Math.round(markOpacity * 100)}%`}>
                  <input type="range" min={0.15} max={1} step={0.05}
                         value={markOpacity}
                         onChange={(e) => {
                           tocar(); setMarkOpacity(Number(e.target.value));
                         }} />
                </Field>
              </div>
            </>
          ) : null}

          <button
            type="button"
            className="chip"
            data-on={rememberBrand}
            style={{ justifySelf: "start" }}
            onClick={() => {
              const next = !rememberBrand;
              setRememberBrand(next);
              saveDefaultWatermark(next ? watermark : "");
              toast(next && watermark.trim()
                ? f(t.editor.watermarkSavedDefault, { name: watermark.trim() })
                : t.editor.watermarkClearedDefault);
            }}
          >
            {rememberBrand ? "✓ " : "○ "}
            {t.editor.watermarkRemembered}
          </button>
          <p className="dimmer" style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5 }}>
            {t.editor.watermarkRememberedHint}
          </p>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <span className="label">{t.editor.captionTitle}</span>
          <div className="grow" />
          <span className="label">{f(t.editor.timedWords, { n: words.length })}</span>
        </div>
        <div className="panel-body grid" style={{ gap: 13 }}>
          <Field label={t.editor.styleField}>
            <Chips
              value={captionStyle}
              onChange={(v) => { tocar(); setCaptionStyle(v); }}
              options={[
                { value: "karaoke", label: t.captionStyles.karaokeShort },
                { value: "bloco", label: t.captionStyles.blocoShort },
                { value: "palavra", label: t.captionStyles.palavraShort },
              ]}
            />
          </Field>

          <Field label={t.editor.positionField}>
            <Chips
              value={captionPosition}
              onChange={(v) => { tocar(); setCaptionPosition(v); }}
              options={[
                { value: "centro", label: t.positions.centroShort },
                { value: "baixo", label: t.positions.baixoShort },
                { value: "topo", label: t.positions.topoShort },
              ]}
            />
          </Field>

          <Field
            label={t.editor.offsetField}
            hint={`${offset >= 0 ? "+" : ""}${offset.toFixed(2)}${t.common.seconds}`}
          >
            <div className="grid" style={{ gap: 6 }}>
              <input type="range" min={-1} max={1} step={0.05} value={offset}
                     onChange={(e) => { tocar(); setOffset(Number(e.target.value)); }} />
              <div className="row spread wrap">
                <span className="label">{t.editor.offsetEarly}</span>
                <button className="btn sm ghost"
                        onClick={() => { tocar(); setOffset(0); }}>
                  {t.editor.offsetReset}
                </button>
                <span className="label">{t.editor.offsetLate}</span>
              </div>
            </div>
          </Field>

          <p className="dimmer" style={{ margin: 0, fontSize: 12, lineHeight: 1.6 }}>
            {t.editor.offsetExplain}
          </p>
        </div>
      </section>

      <div className="grid" style={{ gap: 8 }}>
        <div className="row wrap" style={{ gap: 10 }}>
          <button className="btn primary grow" onClick={() => apply(true)} disabled={busy}>
            {busy ? t.common.applying : t.editor.saveAndRender}
          </button>
          <button className="btn" onClick={() => apply(false)} disabled={busy}>
            {t.editor.audioOnly}
          </button>
          {rascunho !== "limpo" ? (
            <button className="btn ghost" onClick={descartarRascunho}>
              {t.editor.discardDraft}
            </button>
          ) : (
            <button
              className="btn ghost"
              onClick={() => api.resetEdit(job.id).then(() => {
                editando.current = false;
                toast(t.editor.manualDiscarded);
              })}
            >
              {t.editor.backToAi}
            </button>
          )}
        </div>
        <p className="dimmer" style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5 }}>
          {t.editor.persistExplain}
        </p>
      </div>
    </div>
  );
}

/** Draft status, so the user is never left wondering whether text was lost. */
function RascunhoTag({ estado, salvoEm }: {
  estado: "limpo" | "pendente" | "salvando" | "salvo";
  salvoEm: string | null;
}) {
  const { t, f, dateTime } = useI18n();
  if (estado === "limpo") return null;
  const mapa = {
    pendente: { tone: "amber", texto: t.editor.draftUnsaved },
    salvando: { tone: "amber", texto: t.editor.draftSaving },
    salvo: { tone: "ok", texto: t.editor.draftSaved },
  }[estado];
  return (
    <span className="tag" data-tone={mapa.tone}
          title={salvoEm ? f(t.editor.draftSavedAt, { when: dateTime(salvoEm) }) : ""}>
      {mapa.texto}
    </span>
  );
}
