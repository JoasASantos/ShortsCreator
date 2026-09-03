"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, type Timeline, type TimelineCue, type TimelineVideoClip } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type Selection =
  | { track: "video"; id: string }
  | { track: "audio"; id: string }
  | { track: "caption"; id: string }
  | null;

type Drag = {
  mode: "move" | "trim-start" | "trim-end";
  track: "video" | "audio" | "caption";
  id: string;
  originX: number;
  origStart: number;
  origIn: number;
  origOut: number;
  origEnd: number;
};

const MIN_LEN = 0.2;

export function TimelineEditor({ jobId, version, onRendered, toast }: {
  jobId: string;
  version: string;
  onRendered: () => void;
  toast: (message: string) => void;
}) {
  const { t, f } = useI18n();
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [pxPerSec, setPxPerSec] = useState(28);
  const [playhead, setPlayhead] = useState(0);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState(false);
  const dragRef = useRef<Drag | null>(null);
  const laneRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  // enquanto o vídeo toca, ele manda no cursor; ao arrastar o cursor, o
  // vídeo obedece. Esta trava evita os dois brigarem pelo mesmo estado.
  const seekingRef = useRef(false);

  useEffect(() => {
    api.timeline(jobId).then(setTimeline).catch((e) => setError(String(e.message)));
  }, [jobId]);

  // arrastar e redimensionar acontecem na janela: o ponteiro costuma sair
  // da faixa durante o gesto e perderíamos o movimento se ouvíssemos só nela
  useEffect(() => {
    const onMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag || !timeline) return;
      const delta = (event.clientX - drag.originX) / pxPerSec;

      setTimeline((prev) => {
        if (!prev) return prev;
        const next = structuredClone(prev);
        if (drag.track === "caption") {
          const cue = next.captions.find((c) => c.id === drag.id);
          if (!cue) return next;
          if (drag.mode === "move") {
            const len = drag.origEnd - drag.origStart;
            cue.start = Math.max(0, drag.origStart + delta);
            cue.end = cue.start + len;
          } else if (drag.mode === "trim-start") {
            cue.start = Math.min(Math.max(0, drag.origStart + delta), cue.end - MIN_LEN);
          } else {
            cue.end = Math.max(drag.origEnd + delta, cue.start + MIN_LEN);
          }
          return next;
        }

        const list = drag.track === "video" ? next.video : next.audio;
        const clip = list.find((c) => c.id === drag.id);
        if (!clip) return next;
        if (drag.mode === "move") {
          clip.start = Math.max(0, drag.origStart + delta);
        } else if (drag.mode === "trim-start") {
          // encurtar pela esquerda avança o ponto de entrada no arquivo de
          // origem e desloca a posição na linha do tempo em igual medida
          const shift = Math.min(Math.max(delta, -drag.origIn),
                                 drag.origOut - drag.origIn - MIN_LEN);
          clip.in_point = drag.origIn + shift;
          clip.start = Math.max(0, drag.origStart + shift);
        } else {
          clip.out_point = Math.max(drag.origOut + delta, clip.in_point + MIN_LEN);
        }
        return next;
      });
      setDirty(true);
    };

    const onUp = () => { dragRef.current = null; };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [pxPerSec, timeline]);

  const startDrag = (
    event: React.PointerEvent, mode: Drag["mode"],
    track: Drag["track"], id: string,
    start: number, inPoint: number, outPoint: number, end: number,
  ) => {
    event.stopPropagation();
    dragRef.current = {
      mode, track, id, originX: event.clientX,
      origStart: start, origIn: inPoint, origOut: outPoint, origEnd: end,
    };
    setSelection({ track: track === "caption" ? "caption" : track, id } as Selection);
  };

  const mutate = useCallback((fn: (draft: Timeline) => void) => {
    setTimeline((prev) => {
      if (!prev) return prev;
      const next = structuredClone(prev);
      fn(next);
      return next;
    });
    setDirty(true);
  }, []);

  const removeSelected = () => {
    if (!selection) return;
    mutate((draft) => {
      if (selection.track === "video")
        draft.video = draft.video.filter((c) => c.id !== selection.id);
      else if (selection.track === "audio")
        draft.audio = draft.audio.filter((c) => c.id !== selection.id);
      else
        draft.captions = draft.captions.filter((c) => c.id !== selection.id);
    });
    setSelection(null);
  };

  const splitSelected = () => {
    if (!selection || selection.track !== "video" || !timeline) return;
    const clip = timeline.video.find((c) => c.id === selection.id);
    if (!clip) return;
    const local = playhead - clip.start;
    const length = clip.out_point - clip.in_point;
    if (local <= MIN_LEN || local >= length - MIN_LEN) {
      toast(t.timeline.splitNeedsPlayhead);
      return;
    }
    mutate((draft) => {
      const target = draft.video.find((c) => c.id === selection.id);
      if (!target) return;
      const cutAt = target.in_point + local;
      const right: TimelineVideoClip = {
        ...target,
        id: `${target.id}_b${Math.random().toString(36).slice(2, 6)}`,
        in_point: cutAt,
        start: target.start + local,
      };
      target.out_point = cutAt;
      draft.video.splice(draft.video.indexOf(target) + 1, 0, right);
    });
  };

  const closeGaps = () => mutate((draft) => {
    let cursor = 0;
    draft.video.sort((a, b) => a.start - b.start);
    for (const clip of draft.video) {
      clip.start = cursor;
      cursor += clip.out_point - clip.in_point;
    }
  });

  const fillToAudio = () => mutate((draft) => {
    const audioEnd = Math.max(
      0, ...draft.audio.map((a) => a.start + a.out_point - a.in_point));
    const last = [...draft.video].sort((a, b) => a.start - b.start).pop();
    if (!last) return;
    const videoEnd = last.start + last.out_point - last.in_point;
    if (videoEnd < audioEnd) last.out_point += audioEnd - videoEnd;
  });

  const save = async () => {
    if (!timeline) return;
    setBusy(true);
    try {
      const saved = await api.saveTimeline(jobId, timeline);
      setTimeline(saved);
      setDirty(false);
      toast(t.timeline.saved);
    } catch (e) {
      toast((e as Error).message);
    } finally { setBusy(false); }
  };

  const render = async () => {
    if (!timeline) return;
    setBusy(true);
    try {
      const result = await api.renderTimeline(jobId, timeline);
      setDirty(false);
      toast(result.qa.passed
        ? f(t.timeline.renderedOk,
            { duration: result.duration.toFixed(1), score: result.qa.score })
        : f(t.timeline.renderedFailed, { score: result.qa.score }));
      onRendered();
    } catch (e) {
      toast((e as Error).message);
    } finally { setBusy(false); }
  };

  if (error) return (
    <div className="empty">{f(t.timeline.unavailable, { message: error })}</div>
  );
  if (!timeline) return <div className="empty">{t.timeline.loading}</div>;

  const videoEnd = Math.max(0,
    ...timeline.video.map((c) => c.start + c.out_point - c.in_point));
  const audioEnd = Math.max(0,
    ...timeline.audio.map((c) => c.start + c.out_point - c.in_point));
  const width = Math.max(timeline.duration, videoEnd, audioEnd) * pxPerSec + 40;
  const gap = audioEnd - videoEnd;

  const seek = (event: React.MouseEvent) => {
    const rect = laneRef.current?.getBoundingClientRect();
    if (!rect) return;
    const at = Math.max(
      0, (event.clientX - rect.left + (laneRef.current?.scrollLeft ?? 0)) / pxPerSec);
    setPlayhead(at);
    const video = videoRef.current;
    if (video && Number.isFinite(video.duration)) {
      seekingRef.current = true;
      video.currentTime = Math.min(at, video.duration);
      window.setTimeout(() => { seekingRef.current = false; }, 60);
    }
  };

  const togglePlay = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) { video.play(); setPlaying(true); }
    else { video.pause(); setPlaying(false); }
  };

  // legenda que está no ar no instante do cursor — confere o texto sem renderizar
  const activeCue = timeline.captions.find(
    (c) => playhead >= c.start && playhead <= c.end);

  const selectedCue = selection?.track === "caption"
    ? timeline.captions.find((c) => c.id === selection.id) : undefined;

  return (
    <div className="grid" style={{ gap: 12 }}>
      <section className="panel">
        <div className="panel-head">
          <span className="label">{t.timeline.monitorTitle}</span>
          <div className="grow" />
          {dirty ? (
            <span className="tag" data-tone="amber">{t.timeline.unrendered}</span>
          ) : null}
        </div>
        <div className="panel-body row wrap" style={{ gap: 16, alignItems: "flex-start" }}>
          <div className="tl-monitor">
            <video
              ref={videoRef}
              src={`/api/jobs/${jobId}/file/short.mp4?v=${version}`}
              playsInline
              onTimeUpdate={(e) => {
                if (seekingRef.current) return;
                setPlayhead(e.currentTarget.currentTime);
              }}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onClick={togglePlay}
            />
          </div>
          <div className="grid grow" style={{ gap: 10 }}>
            <div className="row" style={{ gap: 8 }}>
              <button className="btn sm" onClick={togglePlay}>
                {playing ? t.timeline.pause : t.timeline.play}
              </button>
              <button className="btn sm ghost" onClick={() => {
                setPlayhead(0);
                if (videoRef.current) videoRef.current.currentTime = 0;
              }}>{t.timeline.toStart}</button>
              <span className="mono dimmer" style={{ fontSize: 11 }}>
                {playhead.toFixed(2)}s
              </span>
            </div>

            <div className="field">
              <span className="label">{t.timeline.cueNow}</span>
              <div className="tl-cue-preview">
                {activeCue ? activeCue.text : <span className="dimmer">—</span>}
              </div>
            </div>

            <p className="dimmer" style={{ margin: 0, fontSize: 11.5, lineHeight: 1.6 }}>
              {t.timeline.monitorHint}
            </p>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <span className="label">{t.timeline.title}</span>
          <div className="grow" />
          <span className="mono dimmer" style={{ fontSize: 11 }}>
            {playhead.toFixed(2)}s / {timeline.duration.toFixed(2)}s
          </span>
          <button className="btn sm ghost" title={t.timeline.zoomOut} aria-label={t.timeline.zoomOut}
                  onClick={() => setPxPerSec((z) => Math.max(8, z - 8))}>−</button>
          <button className="btn sm ghost" title={t.timeline.zoomIn} aria-label={t.timeline.zoomIn}
                  onClick={() => setPxPerSec((z) => Math.min(120, z + 8))}>+</button>
        </div>

        <div className="panel-body grid" style={{ gap: 10 }}>
          <div className="row wrap" style={{ gap: 6 }}>
            <button className="btn sm" onClick={splitSelected}
                    disabled={selection?.track !== "video"}>{t.timeline.split}</button>
            <button className="btn sm danger" onClick={removeSelected}
                    disabled={!selection}>{t.common.remove}</button>
            <button className="btn sm ghost" onClick={closeGaps}>{t.timeline.closeGaps}</button>
            <button className="btn sm ghost" onClick={fillToAudio}>{t.timeline.fillToAudio}</button>
          </div>

          {gap > 0.5 ? (
            <div className="issue" data-sev="aviso">
              <span className="label" style={{ minWidth: 52, paddingTop: 2 }}>
                {t.qa.severity.aviso}
              </span>
              <div>
                {f(t.timeline.gapWarning,
                   { gap: gap.toFixed(1), action: t.timeline.fillToAudio })}
              </div>
            </div>
          ) : null}

          <div className="tl" ref={laneRef} onClick={seek}>
            <div style={{ width, position: "relative" }}>
              <Ruler duration={Math.max(timeline.duration, videoEnd, audioEnd)}
                     pxPerSec={pxPerSec} />

              <TrackLabel text={t.timeline.trackVideo} />
              <div className="tl-lane">
                {timeline.video.map((clip) => {
                  const length = clip.out_point - clip.in_point;
                  return (
                    <div
                      key={clip.id}
                      className="tl-clip"
                      data-on={selection?.track === "video" && selection.id === clip.id}
                      style={{ left: clip.start * pxPerSec, width: Math.max(length * pxPerSec, 8) }}
                      onPointerDown={(e) => startDrag(e, "move", "video", clip.id,
                        clip.start, clip.in_point, clip.out_point, clip.start + length)}
                    >
                      <i className="tl-handle left" onPointerDown={(e) =>
                        startDrag(e, "trim-start", "video", clip.id,
                          clip.start, clip.in_point, clip.out_point, clip.start + length)} />
                      <span>{clip.source.replace(/\.[^.]+$/, "")}</span>
                      <i className="tl-handle right" onPointerDown={(e) =>
                        startDrag(e, "trim-end", "video", clip.id,
                          clip.start, clip.in_point, clip.out_point, clip.start + length)} />
                    </div>
                  );
                })}
              </div>

              <TrackLabel text={t.timeline.trackAudio} />
              <div className="tl-lane">
                {timeline.audio.map((clip) => {
                  const length = clip.out_point - clip.in_point;
                  return (
                    <div
                      key={clip.id}
                      className="tl-clip audio"
                      data-on={selection?.track === "audio" && selection.id === clip.id}
                      style={{ left: clip.start * pxPerSec, width: Math.max(length * pxPerSec, 8) }}
                      onPointerDown={(e) => startDrag(e, "move", "audio", clip.id,
                        clip.start, clip.in_point, clip.out_point, clip.start + length)}
                    >
                      <span>{clip.role}</span>
                      <i className="tl-handle right" onPointerDown={(e) =>
                        startDrag(e, "trim-end", "audio", clip.id,
                          clip.start, clip.in_point, clip.out_point, clip.start + length)} />
                    </div>
                  );
                })}
              </div>

              <TrackLabel text={t.timeline.trackCaption} />
              <div className="tl-lane">
                {timeline.captions.map((cue) => (
                  <div
                    key={cue.id}
                    className="tl-clip caption"
                    data-on={selection?.track === "caption" && selection.id === cue.id}
                    style={{ left: cue.start * pxPerSec,
                             width: Math.max((cue.end - cue.start) * pxPerSec, 6) }}
                    onPointerDown={(e) => startDrag(e, "move", "caption", cue.id,
                      cue.start, 0, 0, cue.end)}
                  >
                    <i className="tl-handle left" onPointerDown={(e) =>
                      startDrag(e, "trim-start", "caption", cue.id, cue.start, 0, 0, cue.end)} />
                    <span>{cue.text}</span>
                    <i className="tl-handle right" onPointerDown={(e) =>
                      startDrag(e, "trim-end", "caption", cue.id, cue.start, 0, 0, cue.end)} />
                  </div>
                ))}
              </div>

              <div className="tl-playhead" style={{ left: playhead * pxPerSec }} />
            </div>
          </div>
        </div>
      </section>

      {selectedCue ? (
        <section className="panel">
          <div className="panel-head">
            <span className="label">{t.timeline.cueTitle}</span>
            <div className="grow" />
            <span className="mono dimmer" style={{ fontSize: 11 }}>
              {selectedCue.start.toFixed(2)}s → {selectedCue.end.toFixed(2)}s
            </span>
          </div>
          <div className="panel-body">
            <textarea
              className="textarea"
              style={{ minHeight: 62 }}
              value={selectedCue.text}
              onChange={(e) => mutate((draft) => {
                const cue = draft.captions.find((c) => c.id === selectedCue.id);
                if (cue) cue.text = e.target.value;
              })}
            />
          </div>
        </section>
      ) : null}

      <div className="row wrap" style={{ gap: 10 }}>
        <button className="btn primary grow" onClick={render} disabled={busy}>
          {busy ? t.timeline.rendering : t.timeline.render}
        </button>
        <button className="btn" onClick={save} disabled={busy || !dirty}>
          {t.timeline.saveOnly}
        </button>
        <button className="btn ghost" onClick={() =>
          api.timeline(jobId).then((loaded) => { setTimeline(loaded); setDirty(false); })}>
          {t.timeline.reload}
        </button>
      </div>
    </div>
  );
}

function TrackLabel({ text }: { text: string }) {
  return <div className="label" style={{ margin: "10px 0 4px" }}>{text}</div>;
}

function Ruler({ duration, pxPerSec }: { duration: number; pxPerSec: number }) {
  // marca a cada 1s, 5s ou 10s conforme o zoom, para não virar uma parede de traços
  const step = pxPerSec > 60 ? 1 : pxPerSec > 20 ? 5 : 10;
  const ticks = [];
  for (let t = 0; t <= duration; t += step) {
    ticks.push(
      <div key={t} className="tl-tick" style={{ left: t * pxPerSec }}>
        <span>{t}s</span>
      </div>,
    );
  }
  return <div className="tl-ruler">{ticks}</div>;
}
