"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  api, type Timeline, type TimelineCue, type TimelineMedia,
  type TimelineVideoClip,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Field } from "@/components/ui";

type Selection =
  | { track: "video"; id: string }
  | { track: "audio"; id: string }
  | { track: "caption"; id: string }
  | { track: "media"; id: string }
  | null;

type Drag = {
  mode: "move" | "trim-start" | "trim-end";
  track: "video" | "audio" | "caption" | "media";
  id: string;
  originX: number;
  origStart: number;
  origIn: number;
  origOut: number;
  origEnd: number;
};

const MIN_LEN = 0.2;

/** How far back undo reaches. An editing session is long and a timeline is not
 *  a small object; nobody walks back a hundred steps. */
const HISTORY_LIMIT = 60;

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
  // Undo lives in refs: the stacks are only read inside callbacks, and putting
  // whole timelines in state would re-render the editor on every snapshot.
  // The depths are state, because the buttons have to enable and disable.
  const undoRef = useRef<Timeline[]>([]);
  const redoRef = useRef<Timeline[]>([]);
  const [historyDepth, setHistoryDepth] = useState(0);
  const [redoDepth, setRedoDepth] = useState(0);
  const dragRef = useRef<Drag | null>(null);
  const pendingSnapshot = useRef(false);
  const laneRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  // while the video plays, it drives the playhead; when the playhead is
  // dragged, the video follows. This lock keeps the two from fighting over
  // the same state.
  const seekingRef = useRef(false);

  useEffect(() => {
    api.timeline(jobId).then(setTimeline).catch((e) => setError(String(e.message)));
  }, [jobId]);

  /** Snapshot the current timeline before a change, so it can be undone.
   *  Editing without an undo is editing carefully instead of editing. */
  const pushHistory = useCallback(() => {
    setTimeline((prev) => {
      if (prev) {
        undoRef.current.push(structuredClone(prev));
        // A bounded stack: an editing session is long and a timeline is not
        // small, and nobody walks back a hundred steps.
        if (undoRef.current.length > HISTORY_LIMIT) undoRef.current.shift();
        redoRef.current = [];
        setHistoryDepth(undoRef.current.length);
        setRedoDepth(0);
      }
      return prev;
    });
  }, []);

  const undo = useCallback(() => {
    setTimeline((prev) => {
      const previous = undoRef.current.pop();
      if (!previous || !prev) return prev;
      redoRef.current.push(structuredClone(prev));
      setHistoryDepth(undoRef.current.length);
      setRedoDepth(redoRef.current.length);
      setDirty(true);
      return previous;
    });
    setSelection(null);
  }, []);

  const redo = useCallback(() => {
    setTimeline((prev) => {
      const next = redoRef.current.pop();
      if (!next || !prev) return prev;
      undoRef.current.push(structuredClone(prev));
      setHistoryDepth(undoRef.current.length);
      setRedoDepth(redoRef.current.length);
      setDirty(true);
      return next;
    });
    setSelection(null);
  }, []);

  // dragging and trimming listen on the window: the pointer usually leaves
  // the lane mid-gesture and we would lose the movement if we only listened
  // on the lane itself
  useEffect(() => {
    const onMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag || !timeline) return;
      const delta = (event.clientX - drag.originX) / pxPerSec;
      // The gesture turned out to be a real drag: bank the state it started
      // from, once.
      if (pendingSnapshot.current) {
        pendingSnapshot.current = false;
        pushHistory();
      }

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

        if (drag.track === "media") {
          const item = next.media.find((m) => m.id === drag.id);
          if (!item) return next;
          if (drag.mode === "move") {
            const len = drag.origEnd - drag.origStart;
            item.start = Math.max(0, drag.origStart + delta);
            item.end = item.start + len;
          } else if (drag.mode === "trim-start") {
            // A video overlay trimmed from the left has to advance its in
            // point inside the source too, or the same frames just start
            // later. A still has no in point to advance.
            const shift = Math.min(Math.max(delta, -drag.origStart),
                                   drag.origEnd - drag.origStart - MIN_LEN);
            item.start = Math.max(0, drag.origStart + shift);
            if (item.kind === "video") {
              item.in_point = Math.max(0, drag.origIn + shift);
            }
          } else {
            item.end = Math.max(drag.origEnd + delta, item.start + MIN_LEN);
          }
          return next;
        }

        const list = drag.track === "video" ? next.video : next.audio;
        const clip = list.find((c) => c.id === drag.id);
        if (!clip) return next;
        if (drag.mode === "move") {
          clip.start = Math.max(0, drag.origStart + delta);
        } else if (drag.mode === "trim-start") {
          // trimming from the left advances the in point inside the source
          // file and shifts the timeline position by the same amount
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

    const onUp = () => {
      dragRef.current = null;
      pendingSnapshot.current = false;
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [pxPerSec, timeline, pushHistory]);

  const startDrag = (
    event: React.PointerEvent, mode: Drag["mode"],
    track: Drag["track"], id: string,
    start: number, inPoint: number, outPoint: number, end: number,
  ) => {
    event.stopPropagation();
    // A click on a clip is also the start of a drag, so the snapshot is held
    // aside and only enters the undo stack once the pointer actually moves.
    // Pushing it here instead would fill the history with states identical to
    // the current one, and Ctrl+Z would undo clicks rather than edits.
    pendingSnapshot.current = true;
    dragRef.current = {
      mode, track, id, originX: event.clientX,
      origStart: start, origIn: inPoint, origOut: outPoint, origEnd: end,
    };
    setSelection({ track, id } as Selection);
  };

  const mutate = useCallback((fn: (draft: Timeline) => void) => {
    pushHistory();
    setTimeline((prev) => {
      if (!prev) return prev;
      const next = structuredClone(prev);
      fn(next);
      return next;
    });
    setDirty(true);
  }, [pushHistory]);

  /** Geometry of one overlay, in fractions of the frame — the same units the
   *  renderer stores, so what the preview shows is what gets composited. */
  const setMedia = (id: string, patch: Partial<TimelineMedia>) =>
    mutate((draft) => {
      const item = draft.media.find((m) => m.id === id);
      if (item) Object.assign(item, patch);
    });

  const moveMediaTo = (event: React.PointerEvent, id: string) => {
    const box = event.currentTarget.getBoundingClientRect();
    setMedia(id, {
      x: Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1),
      y: Math.min(Math.max((event.clientY - box.top) / box.height, 0), 1),
    });
  };

  const removeSelected = () => {
    if (!selection) return;
    mutate((draft) => {
      if (selection.track === "video")
        draft.video = draft.video.filter((c) => c.id !== selection.id);
      else if (selection.track === "audio")
        draft.audio = draft.audio.filter((c) => c.id !== selection.id);
      else if (selection.track === "media")
        draft.media = draft.media.filter((m) => m.id !== selection.id);
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

  // The keyboard handler is bound once and must not go stale: these refs let
  // it reach the current closures without re-binding on every edit.
  const splitRef = useRef(splitSelected);
  const removeRef = useRef(removeSelected);
  splitRef.current = splitSelected;
  removeRef.current = removeSelected;

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

  // The shortcuts every cutting tool has. They are what makes trimming feel
  // like editing instead of like filling a form — but they must never fire
  // while someone is typing a caption, so a focused field opts out.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT"
        || target.tagName === "TEXTAREA" || target.isContentEditable);
      if (typing) return;

      const meta = event.metaKey || event.ctrlKey;
      if (meta && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) redo(); else undo();
        return;
      }
      if (meta) return;

      if (event.key === " ") {
        event.preventDefault();
        const video = videoRef.current;
        if (!video) return;
        if (video.paused) { video.play(); setPlaying(true); }
        else { video.pause(); setPlaying(false); }
      } else if (event.key.toLowerCase() === "s") {
        event.preventDefault();
        splitRef.current();
      } else if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        removeRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo]);

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

  // caption on air at the playhead instant — check the text without rendering
  const activeCue = timeline.captions.find(
    (c) => playhead >= c.start && playhead <= c.end);

  const selectedCue = selection?.track === "caption"
    ? timeline.captions.find((c) => c.id === selection.id) : undefined;

  const selectedMedia = selection?.track === "media"
    ? (timeline.media ?? []).find((m) => m.id === selection.id) : undefined;

  const selectedAudio = selection?.track === "audio"
    ? timeline.audio.find((a) => a.id === selection.id) : undefined;

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
            {/* Wraps: next to the 168px monitor this column is ~184px on a
                phone, and two buttons plus the timecode do not fit on one
                line there — they used to push the page sideways instead. */}
            <div className="row wrap" style={{ gap: 8 }}>
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
                    title={`${t.timeline.split} (S)`}
                    disabled={selection?.track !== "video"}>{t.timeline.split}</button>
            <button className="btn sm danger" onClick={removeSelected}
                    title={`${t.common.remove} (Del)`}
                    disabled={!selection}>{t.common.remove}</button>
            <button className="btn sm ghost" onClick={undo}
                    title={`${t.timeline.undo} (Ctrl+Z)`}
                    disabled={historyDepth === 0}>{t.timeline.undo}</button>
            <button className="btn sm ghost" onClick={redo}
                    title={`${t.timeline.redo} (Ctrl+Shift+Z)`}
                    disabled={redoDepth === 0}>{t.timeline.redo}</button>
            <button className="btn sm ghost" onClick={closeGaps}>{t.timeline.closeGaps}</button>
            <button className="btn sm ghost" onClick={fillToAudio}>{t.timeline.fillToAudio}</button>
          </div>

          <span className="dimmer" style={{ fontSize: 11, lineHeight: 1.6 }}>
            {t.timeline.shortcuts}
          </span>

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
                      <Filmstrip src={`/api/jobs/${jobId}/file/short.mp4?v=${version}`}
                                 from={clip.in_point} to={clip.out_point}
                                 width={length * pxPerSec} />
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

              {/* Media laid over the video — the picture-in-picture. It sits
                  between the video and the captions because that is the order
                  it is composited in: over the footage, under the words. */}
              <TrackLabel text={t.timeline.trackMedia} />
              <div className="tl-lane">
                {(timeline.media ?? []).map((item) => (
                  <div
                    key={item.id}
                    className="tl-clip media"
                    data-on={selection?.track === "media" && selection.id === item.id}
                    style={{ left: item.start * pxPerSec,
                             width: Math.max((item.end - item.start) * pxPerSec, 8) }}
                    onPointerDown={(e) => startDrag(e, "move", "media", item.id,
                      item.start, item.in_point, 0, item.end)}
                  >
                    <i className="tl-handle left" onPointerDown={(e) =>
                      startDrag(e, "trim-start", "media", item.id,
                        item.start, item.in_point, 0, item.end)} />
                    <span>{item.source.replace(/\.[^.]+$/, "")}</span>
                    <i className="tl-handle right" onPointerDown={(e) =>
                      startDrag(e, "trim-end", "media", item.id,
                        item.start, item.in_point, 0, item.end)} />
                  </div>
                ))}
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

      {/* Balancing a music bed against a voice is the most common audio edit
          there is, and `gain` was already in the model and already applied by
          the renderer — it just had nothing to set it. */}
      {selectedAudio ? (
        <section className="panel">
          <div className="panel-head">
            <span className="label">{t.timeline.audioTitle}</span>
            <div className="grow" />
            <span className="mono dimmer" style={{ fontSize: 11 }}>
              {selectedAudio.role}
            </span>
          </div>
          <div className="panel-body">
            <Field label={t.timeline.volume}
                   hint={selectedAudio.gain === 0 ? t.timeline.muted
                         : `${Math.round(selectedAudio.gain * 100)}%`}>
              <div className="row" style={{ gap: 8 }}>
                <input type="range" min={0} max={2} step={0.05}
                       className="grow"
                       value={selectedAudio.gain}
                       onChange={(e) => mutate((draft) => {
                         const clip = draft.audio.find(
                           (a) => a.id === selectedAudio.id);
                         if (clip) clip.gain = Number(e.target.value);
                       })} />
                <button className="btn sm ghost" onClick={() => mutate((draft) => {
                  const clip = draft.audio.find((a) => a.id === selectedAudio.id);
                  // Mute is a toggle back to full, not to whatever it was —
                  // remembering a previous gain nobody can see is a surprise.
                  if (clip) clip.gain = clip.gain === 0 ? 1 : 0;
                })}>
                  {selectedAudio.gain === 0 ? t.timeline.unmute : t.timeline.mute}
                </button>
              </div>
            </Field>
          </div>
        </section>
      ) : null}

      {selectedMedia ? (
        <section className="panel">
          <div className="panel-head">
            <span className="label">{t.timeline.mediaTitle}</span>
            <div className="grow" />
            <span className="mono dimmer" style={{ fontSize: 11 }}>
              {selectedMedia.start.toFixed(2)}s → {selectedMedia.end.toFixed(2)}s
            </span>
          </div>
          <div className="panel-body two">
            <div className="grid" style={{ gap: 10 }}>
              <Field label={t.timeline.mediaSize}
                     hint={f(t.timeline.mediaSizeValue,
                             { n: Math.round(selectedMedia.width * 100) })}>
                <input type="range" min={0.05} max={1} step={0.01}
                       value={selectedMedia.width}
                       onChange={(e) => setMedia(selectedMedia.id,
                         { width: Number(e.target.value) })} />
              </Field>
              <Field label={t.timeline.mediaOpacity}
                     hint={`${Math.round(selectedMedia.opacity * 100)}%`}>
                <input type="range" min={0.05} max={1} step={0.05}
                       value={selectedMedia.opacity}
                       onChange={(e) => setMedia(selectedMedia.id,
                         { opacity: Number(e.target.value) })} />
              </Field>
            </div>
            {/* Dragging on a 9:16 preview beats typing coordinates, and the
                fractions it produces are exactly what the renderer stores. */}
            <Field label={t.timeline.mediaPosition} hint={t.timeline.mediaPositionHint}>
              <div
                onPointerDown={(e) => {
                  (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
                  moveMediaTo(e, selectedMedia.id);
                }}
                onPointerMove={(e) => {
                  if (e.buttons) moveMediaTo(e, selectedMedia.id);
                }}
                style={{
                  position: "relative", width: "100%", maxWidth: 132,
                  aspectRatio: "9/16", borderRadius: "var(--r)",
                  border: "1px solid var(--line)", overflow: "hidden",
                  background: "#000", cursor: "crosshair", touchAction: "none",
                }}
              >
                <img alt="" src={`/api/jobs/${jobId}/file/thumb.jpg`}
                     style={{ width: "100%", height: "100%", objectFit: "cover",
                              opacity: 0.5, pointerEvents: "none" }} />
                <div style={{
                  position: "absolute",
                  left: `${selectedMedia.x * 100}%`,
                  top: `${selectedMedia.y * 100}%`,
                  width: `${selectedMedia.width * 100}%`,
                  // A square stand-in: the real height follows the media's own
                  // aspect ratio, which only FFmpeg knows at scale time.
                  aspectRatio: "1/1",
                  transform: "translate(-50%, -50%)",
                  border: "1px solid var(--amber)",
                  background: `rgba(255,176,0,${0.18 * selectedMedia.opacity})`,
                  pointerEvents: "none",
                }} />
              </div>
            </Field>
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

/** Frames along the video track, so you can see where a cut lands instead of
 *  scrubbing to find out.
 *
 *  Drawn in the browser from the rendered MP4 rather than asked of the server:
 *  the file is already being streamed for the monitor, and an endpoint that
 *  shells out to FFmpeg per thumbnail would turn scrubbing into a queue of
 *  subprocesses. Failure here is cosmetic — the strip simply stays empty. */
function Filmstrip({ src, from, to, width }: {
  src: string; from: number; to: number; width: number;
}) {
  const [frames, setFrames] = useState<string[]>([]);

  useEffect(() => {
    const span = to - from;
    if (span <= 0 || width < 40) { setFrames([]); return; }
    // One frame per ~64px: enough to read the cut, few enough to decode fast.
    const count = Math.max(1, Math.min(Math.round(width / 64), 12));

    let cancelled = false;
    const video = document.createElement("video");
    video.src = src;
    video.muted = true;
    video.crossOrigin = "anonymous";
    const canvas = document.createElement("canvas");
    const shots: string[] = [];

    const grab = (index: number) => {
      if (cancelled || index >= count) {
        if (!cancelled) setFrames(shots);
        return;
      }
      video.currentTime = from + (span * (index + 0.5)) / count;
      video.onseeked = () => {
        if (cancelled) return;
        canvas.width = 48;
        canvas.height = 85;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        try {
          ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
          shots.push(canvas.toDataURL("image/jpeg", 0.5));
        } catch {
          // a tainted canvas or a codec the browser will not decode
          cancelled = true;
          return;
        }
        grab(index + 1);
      };
    };

    video.onloadeddata = () => grab(0);
    video.onerror = () => setFrames([]);
    return () => { cancelled = true; video.src = ""; };
  }, [src, from, to, width]);

  if (!frames.length) return null;
  return (
    <div aria-hidden style={{
      position: "absolute", inset: 0, display: "flex", overflow: "hidden",
      borderRadius: 3, opacity: 0.55, pointerEvents: "none",
    }}>
      {frames.map((frame, index) => (
        <img key={index} src={frame} alt=""
             style={{ height: "100%", flex: 1, minWidth: 0, objectFit: "cover" }} />
      ))}
    </div>
  );
}

function TrackLabel({ text }: { text: string }) {
  return <div className="label" style={{ margin: "10px 0 4px" }}>{text}</div>;
}

function Ruler({ duration, pxPerSec }: { duration: number; pxPerSec: number }) {
  // ticks every 1s, 5s or 10s depending on zoom, so it doesn't become a wall of marks
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
