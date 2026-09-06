"use client";

import { useRef, useState } from "react";

import { FORMAT_ASPECT, FORMAT_SIZE, type TimelineFormat } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

const ASPECT_LABEL: Record<TimelineFormat, string> = {
  vertical: "9:16", horizontal: "16:9", quadrado: "1:1",
};

/**
 * The finished file in the frame it was made for.
 *
 * For a short that is a phone, with the zones the TikTok/Shorts UI covers
 * drawn over it — the answer to "is this really in short format?" without
 * uploading to find out. A documentary is a 16:9 frame with no interface to
 * dodge, so the guides make no sense there and stay off.
 */
export function PhonePreview({ src, poster, format = "vertical" }: {
  src: string; poster?: string; format?: TimelineFormat;
}) {
  const { t } = useI18n();
  const landscape = format === "horizontal";
  const [guides, setGuides] = useState(!landscape);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const video = useRef<HTMLVideoElement>(null);

  return (
    <div className="grid" style={{ gap: 12 }}>
      <div className="stage">
        <div className="phone" data-landscape={landscape}
             style={{ "--frame-aspect": FORMAT_ASPECT[format] } as React.CSSProperties}>
          <video
            ref={video}
            src={src}
            poster={poster}
            controls
            playsInline
            onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
            onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
          />
          {!landscape ? (
            <div className="safe" style={{ opacity: guides ? 1 : 0 }}>
              <div className="zone top" />
              <div className="zone right" />
              <div className="zone bottom" />
              <div className="badge">{t.preview.uiZone}</div>
              <div className="caption-line" style={{ top: "59%" }} />
            </div>
          ) : null}
        </div>
      </div>

      <div className="row spread wrap">
        {!landscape ? (
          <button
            className="btn sm ghost"
            onClick={() => setGuides((on) => !on)}
          >
            {guides ? t.preview.hideGuides : t.preview.showGuides}
          </button>
        ) : <span />}
        <span className="mono dimmer" style={{ fontSize: 11 }}>
          {time.toFixed(1)}s / {duration ? duration.toFixed(1) : "—"}s ·{" "}
          {FORMAT_SIZE[format]} · {ASPECT_LABEL[format]}
        </span>
      </div>
    </div>
  );
}
