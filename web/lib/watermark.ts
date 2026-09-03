/**
 * The watermark someone types is almost always the same across every short —
 * their own handle. Keeping it in the browser means it comes pre-filled on the
 * next one instead of being retyped, while still being editable per video.
 *
 * Browser-local on purpose: it is a personal convenience, not project state,
 * and the value the pipeline actually burns into the video is the one stored on
 * the job. Every read and write is guarded because private windows and
 * "block site data" make the accessor itself throw.
 */
const KEY = "shortscreator.watermark";

export function readDefaultWatermark(): string {
  try {
    return window.localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function saveDefaultWatermark(value: string): void {
  try {
    const clean = value.trim();
    if (clean) window.localStorage.setItem(KEY, clean);
    else window.localStorage.removeItem(KEY);
  } catch {
    /* no storage: the choice applies to this session only */
  }
}
