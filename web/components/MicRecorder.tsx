"use client";

import { useEffect, useRef, useState } from "react";

/** Record a voice sample from the microphone, here, instead of going to find a
 *  file.
 *
 *  Cloning needs ten seconds of your own speech, and the screen used to accept
 *  only an upload — so "clone your voice" started with opening a recorder app,
 *  exporting, and coming back. The recording IS the common case; the file
 *  picker stays for the clip someone already has.
 *
 *  The blob becomes an ordinary `File`, so everything downstream — the drop
 *  zone's preview, the FormData, the server's ffmpeg — treats it exactly like
 *  an uploaded one.
 */
export function MicRecorder({
  onRecorded, minSeconds, idealSeconds, labels,
}: {
  onRecorded: (file: File) => void;
  minSeconds: number;
  idealSeconds: number;
  labels: {
    start: string; stop: string; recording: string;
    unsupported: string; denied: string; tooShort: string; hint: string;
  };
}) {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const ticker = useRef<ReturnType<typeof setInterval> | null>(null);

  // The microphone light must go out when this unmounts, whatever happened.
  useEffect(() => () => {
    if (ticker.current) clearInterval(ticker.current);
    recorder.current?.stream.getTracks().forEach((track) => track.stop());
  }, []);

  const start = async () => {
    setError("");
    // getUserMedia only exists on a secure origin: over plain http from another
    // machine it is simply not there, and saying so beats a silent no-op.
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia
        || typeof MediaRecorder === "undefined") {
      setError(labels.unsupported);
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch {
      setError(labels.denied);
      return;
    }

    // webm/opus on Chrome, mp4 on Safari: whichever this browser actually
    // supports, since the server transcodes with ffmpeg anyway.
    const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"]
      .find((candidate) => MediaRecorder.isTypeSupported(candidate)) ?? "";
    const media = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
    chunks.current = [];
    media.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.current.push(event.data);
    };
    media.onstop = () => {
      stream.getTracks().forEach((track) => track.stop());
      const blob = new Blob(chunks.current, { type: media.mimeType || "audio/webm" });
      const extension = (media.mimeType || "").includes("mp4") ? "m4a" : "webm";
      onRecorded(new File([blob], `gravacao.${extension}`,
                          { type: blob.type || "audio/webm" }));
    };
    recorder.current = media;
    media.start();
    setSeconds(0);
    setRecording(true);
    ticker.current = setInterval(() => setSeconds((n) => n + 1), 1000);
  };

  const stop = () => {
    if (ticker.current) clearInterval(ticker.current);
    ticker.current = null;
    setRecording(false);
    if (seconds < minSeconds) {
      // Below the minimum the server refuses anyway; refusing here saves the
      // round trip and keeps whatever sample was already chosen.
      setError(labels.tooShort);
      recorder.current?.stream.getTracks().forEach((track) => track.stop());
      recorder.current = null;
      return;
    }
    recorder.current?.stop();
    recorder.current = null;
  };

  const enough = seconds >= idealSeconds;

  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row" style={{ gap: 10, alignItems: "center" }}>
        <button
          className={`btn sm ${recording ? "danger" : ""}`}
          onClick={recording ? stop : start}
          type="button"
        >
          {recording ? labels.stop : labels.start}
        </button>
        {recording ? (
          <span className="mono" style={{ fontSize: 12.5 }}>
            <span style={{ color: "var(--red, #e5484d)" }}>●</span>{" "}
            {labels.recording} {String(Math.floor(seconds / 60)).padStart(2, "0")}
            :{String(seconds % 60).padStart(2, "0")}
            <span className="dimmer">
              {" "}/ {idealSeconds}s{enough ? " ✓" : ""}
            </span>
          </span>
        ) : (
          <span className="dimmer" style={{ fontSize: 12 }}>{labels.hint}</span>
        )}
      </div>
      {error ? (
        <span className="dimmer" style={{ fontSize: 12, color: "var(--amber)" }}>
          {error}
        </span>
      ) : null}
    </div>
  );
}
