"use client";

import { useEffect, useRef, useState } from "react";

import {
  api, type Job, type MusicTrack, type ScriptEdit, type ScriptSegment, type Voice,
} from "@/lib/api";
import { Chips, Field } from "@/components/ui";

const KINDS = ["hook", "corpo", "cta"];

export function Editor({ job, onApplied, toast }: {
  job: Job;
  onApplied: () => void;
  toast: (message: string) => void;
}) {
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
  const [busy, setBusy] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [refining, setRefining] = useState(false);
  const musicInput = useRef<HTMLInputElement>(null);

  const loadTracks = () => api.music().then(setTracks).catch(() => setTracks([]));

  useEffect(() => {
    api.voices().then(setVoices).catch(() => setVoices([]));
    loadTracks();
  }, []);

  // O roteiro pode mudar fora deste componente (refinamento por prompt,
  // re-renderização). Sem este efeito o editor continuaria mostrando a versão
  // antiga até a página ser recarregada.
  const serverScript = job.result?.script.segments;
  useEffect(() => {
    if (serverScript) setSegments(serverScript);
    if (job.result?.title) setTitle(job.result.title);
  }, [serverScript, job.result?.title]);

  const words = job.result?.words ?? [];
  const totalWords = segments.reduce((sum, s) => sum + s.text.split(/\s+/).filter(Boolean).length, 0);
  // ~2,6 palavras por segundo é o ritmo que o pipeline assume ao escrever roteiro
  const estimate = totalWords / 2.6;

  const updateSegment = (index: number, patch: Partial<ScriptSegment>) =>
    setSegments((prev) => prev.map((s, i) => (i === index ? { ...s, ...patch } : s)));

  const addSegment = (index: number) =>
    setSegments((prev) => [
      ...prev.slice(0, index + 1),
      { kind: "corpo", text: "", broll_query: "", on_screen: "" },
      ...prev.slice(index + 1),
    ]);

  const removeSegment = (index: number) =>
    setSegments((prev) => prev.filter((_, i) => i !== index));

  const move = (index: number, delta: number) =>
    setSegments((prev) => {
      const next = [...prev];
      const target = index + delta;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });

  const apply = async (withScript: boolean) => {
    const clean = segments.filter((s) => s.text.trim());
    if (withScript && clean.length === 0) {
      toast("O roteiro precisa de ao menos um trecho com texto.");
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
    };
    if (withScript) {
      edit.segments = clean;
      edit.title = title;
    }
    setBusy(true);
    try {
      const result = await api.editJob(job.id, edit);
      toast(`Re-renderizando (${result.applied.length} ajuste(s)).`);
      onApplied();
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const refine = async () => {
    if (!prompt.trim()) {
      toast("Escreva o que você quer mudar no roteiro.");
      return;
    }
    setRefining(true);
    try {
      // render=false: mostra o resultado no editor para você revisar antes
      const result = await api.refineScript(job.id, prompt, false);
      setSegments(result.script.segments);
      setTitle(result.script.title);
      setPrompt("");
      toast("Roteiro reescrito. Revise e salve para renderizar.");
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setRefining(false);
    }
  };

  const uploadMusic = async (files: FileList | null) => {
    if (!files?.length) return;
    try {
      const track = await api.uploadMusic(files[0]);
      await loadTracks();
      setMusicTrack(track.id);
      setMusic(true);
      toast(`Trilha "${track.name}" adicionada.`);
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
          <span className="label">Roteiro</span>
          <div className="grow" />
          <span className="label">
            {totalWords} palavras · ~{estimate.toFixed(0)}s narrados
          </span>
        </div>
        <div className="panel-body grid" style={{ gap: 12 }}>
          <Field label="Título">
            <input className="input" value={title}
                   onChange={(e) => setTitle(e.target.value)} />
          </Field>

          <Field
            label="Pedir mudança por prompt"
            hint="reescreve o roteiro sem perder o resto"
          >
            <div className="grid" style={{ gap: 8 }}>
              <textarea
                className="textarea"
                style={{ minHeight: 58 }}
                placeholder="ex.: deixa o hook mais agressivo · corta pela metade · tira o jargão técnico · adiciona um dado sobre o preço"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) refine();
                }}
              />
              <div className="row spread">
                <span className="label">
                  {refining
                    ? "a IA está reescrevendo — costuma levar de 30 a 60 segundos"
                    : "⌘/Ctrl + Enter para aplicar"}
                </span>
                <button className="btn sm" onClick={refine} disabled={refining}>
                  {refining ? "Reescrevendo…" : "Reescrever roteiro"}
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
                  {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
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
                  placeholder="Texto narrado deste trecho…"
                  onChange={(e) => updateSegment(index, { text: e.target.value })}
                />
                <div className="two">
                  <input
                    className="input"
                    placeholder="busca de B-roll (inglês)"
                    value={segment.broll_query ?? ""}
                    onChange={(e) => updateSegment(index, { broll_query: e.target.value })}
                  />
                  <input
                    className="input"
                    placeholder="texto de destaque na tela"
                    value={segment.on_screen ?? ""}
                    onChange={(e) => updateSegment(index, { on_screen: e.target.value })}
                  />
                </div>
              </div>
            </div>
          ))}

          <button className="btn sm ghost" onClick={() => addSegment(segments.length - 1)}>
            + Adicionar trecho
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <span className="label">Áudio</span>
        </div>
        <div className="panel-body grid" style={{ gap: 13 }}>
          <Field label="Voz da narração">
            <select className="select" value={voiceId}
                    onChange={(e) => setVoiceId(e.target.value)}>
              <option value="">Padrão do sistema (edge-tts pt-BR)</option>
              {voices.map((voice) => (
                <option key={voice.id} value={voice.id}>
                  {voice.name} · {voice.provider}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Trilha de fundo" hint={`${tracks.length} na biblioteca`}>
            <div className="grid" style={{ gap: 8 }}>
              <select className="select" value={music ? musicTrack : ""}
                      onChange={(e) => {
                        setMusicTrack(e.target.value);
                        setMusic(Boolean(e.target.value));
                      }}>
                <option value="">Sem trilha</option>
                {tracks.map((track) => (
                  <option key={track.id} value={track.id}>
                    {track.name} · {track.duration}s
                  </option>
                ))}
              </select>
              <div className="row" style={{ gap: 8 }}>
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
            <Field label="Volume da trilha" hint={musicVolume.toFixed(2)}>
              <input type="range" min={0.02} max={0.4} step={0.01}
                     value={musicVolume}
                     onChange={(e) => setMusicVolume(Number(e.target.value))} />
            </Field>
          ) : null}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <span className="label">Legenda</span>
          <div className="grow" />
          <span className="label">{words.length} palavras cronometradas</span>
        </div>
        <div className="panel-body grid" style={{ gap: 13 }}>
          <Field label="Estilo">
            <Chips
              value={captionStyle}
              onChange={setCaptionStyle}
              options={[
                { value: "karaoke", label: "Karaokê" },
                { value: "bloco", label: "Bloco" },
                { value: "palavra", label: "Palavra a palavra" },
              ]}
            />
          </Field>

          <Field label="Posição">
            <Chips
              value={captionPosition}
              onChange={setCaptionPosition}
              options={[
                { value: "centro", label: "Centro" },
                { value: "baixo", label: "Base" },
                { value: "topo", label: "Topo" },
              ]}
            />
          </Field>

          <Field
            label="Ajuste fino de sincronia"
            hint={`${offset >= 0 ? "+" : ""}${offset.toFixed(2)}s`}
          >
            <div className="grid" style={{ gap: 6 }}>
              <input type="range" min={-1} max={1} step={0.05} value={offset}
                     onChange={(e) => setOffset(Number(e.target.value))} />
              <div className="row spread">
                <span className="label">legenda adianta</span>
                <button className="btn sm ghost" onClick={() => setOffset(0)}>zerar</button>
                <span className="label">legenda atrasa</span>
              </div>
            </div>
          </Field>

          <p className="dimmer" style={{ margin: 0, fontSize: 12, lineHeight: 1.6 }}>
            Os tempos vêm marcados palavra a palavra pelo próprio sintetizador de voz,
            então normalmente não precisa de ajuste. Use este controle apenas se a voz
            escolhida tiver um atraso de ataque perceptível.
          </p>
        </div>
      </section>

      <div className="row" style={{ gap: 10 }}>
        <button className="btn primary grow" onClick={() => apply(true)} disabled={busy}>
          {busy ? "Aplicando…" : "Salvar roteiro e re-renderizar"}
        </button>
        <button className="btn" onClick={() => apply(false)} disabled={busy}>
          Só áudio e legenda
        </button>
        <button
          className="btn ghost"
          onClick={() => api.resetEdit(job.id).then(() => toast("Roteiro manual descartado."))}
        >
          Descartar edição
        </button>
      </div>
    </div>
  );
}
