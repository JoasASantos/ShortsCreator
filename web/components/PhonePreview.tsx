"use client";

import { useRef, useState } from "react";

/**
 * Moldura 9:16 com sobreposição das zonas cobertas pela UI do TikTok/Shorts.
 * É o que responde à pergunta "isso está mesmo no formato de short?" sem
 * precisar subir o vídeo para descobrir.
 */
export function PhonePreview({ src, poster }: { src: string; poster?: string }) {
  const [guides, setGuides] = useState(true);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const video = useRef<HTMLVideoElement>(null);

  return (
    <div className="grid" style={{ gap: 12 }}>
      <div className="stage">
        <div className="phone">
          <video
            ref={video}
            src={src}
            poster={poster}
            controls
            playsInline
            onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
            onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
          />
          <div className="safe" style={{ opacity: guides ? 1 : 0 }}>
            <div className="zone top" />
            <div className="zone right" />
            <div className="zone bottom" />
            <div className="badge">ZONA DA UI DO APP</div>
            <div className="caption-line" style={{ top: "59%" }} />
          </div>
        </div>
      </div>

      <div className="row spread">
        <button
          className="btn sm ghost"
          onClick={() => setGuides((on) => !on)}
        >
          {guides ? "Ocultar guias de safe area" : "Mostrar guias de safe area"}
        </button>
        <span className="mono dimmer" style={{ fontSize: 11 }}>
          {time.toFixed(1)}s / {duration ? duration.toFixed(1) : "—"}s · 1080×1920 · 9:16
        </span>
      </div>
    </div>
  );
}
