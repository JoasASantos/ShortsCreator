"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, type RecycleItem, type RecycleScan } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Chips, Field, Topbar, useToast } from "@/components/ui";

const PLATFORMS = ["tiktok", "instagram", "youtube"] as const;

// The nine the script prompt and the voices both cover.
const LANGUAGES = [
  ["pt-BR", "Português (BR)"], ["en", "English"], ["es", "Español"],
  ["fr", "Français"], ["de", "Deutsch"], ["it", "Italiano"],
  ["ru", "Русский"], ["zh", "简体中文"], ["ja", "日本語"],
] as const;

function views(count: number | null, unknown: string): string {
  if (count === null || count === undefined) return unknown;
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(0)}k`;
  return String(count);
}

function clock(seconds: number | null | undefined): string {
  if (!seconds) return "";
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export default function Reciclar() {
  const router = useRouter();
  const { t, f, narrationLanguage } = useI18n();
  const { toast, node } = useToast();

  const [target, setTarget] = useState("");
  const [platform, setPlatform] = useState<string>("tiktok");
  const [language, setLanguage] = useState<string>(narrationLanguage);
  const [keepAudio, setKeepAudio] = useState(true);
  const [credit, setCredit] = useState(true);
  const [scan, setScan] = useState<RecycleScan | null>(null);
  const [scanning, setScanning] = useState(false);
  const [sending, setSending] = useState("");
  const [failure, setFailure] = useState("");

  const look = async () => {
    if (!target.trim()) return toast(t.recycle.needHandle);
    setScanning(true);
    setFailure("");
    try {
      setScan(await api.recycleScan(target.trim(), platform));
    } catch (error) {
      setScan(null);
      // The backend's own sentence: private, non-existent, or a platform
      // asking for a session — three different next steps.
      setFailure((error as Error).message);
    } finally {
      setScanning(false);
    }
  };

  const dub = async (item: RecycleItem) => {
    setSending(item.url);
    try {
      const job = await api.createJob({
        source_type: "video",
        source: item.url,
        attachments: [],
        research: "",
        research_attachments: [],
        edit_mode: "dublar",
        angle: "auto",
        instruction: "",
        niche: "generico",
        language,
        voice_id: null,
        duration: 45,
        caption_style: "karaoke",
        caption_position: "centro",
        scroll: "nenhum",
        background: "video_fonte",
        background_query: "",
        music: false,
        music_track: "",
        music_volume: 0.12,
        caption_offset: 0,
        hook_hard: false,
        cta: "",
        title_overlay: false,
        watermark: "",
        watermark_position: "baixo_centro",
        watermark_size: "medio",
        watermark_opacity: 0.6,
        variants: 1,
        qa_autofix: true,
        qa_max_attempts: 3,
        keep_audio: keepAudio,
        credit_source: credit,
      });
      router.push(`/job/${job.job_id}`);
    } catch (error) {
      toast((error as Error).message);
      setSending("");
    }
  };

  return (
    <>
      <Topbar title={t.recycle.title}>
        <span className="dim" style={{ fontSize: 12 }}>
          {t.recycle.subtitle}
        </span>
      </Topbar>

      <div className="grid" style={{ gap: 14 }}>
        <section className="panel">
          <div className="panel-head">
            <span className="label">{t.recycle.findTitle}</span>
          </div>
          <div className="panel-body grid" style={{ gap: 13 }}>
            <Field label={t.recycle.platform}>
              <Chips
                value={platform}
                onChange={setPlatform}
                options={PLATFORMS.map((value) => ({
                  value, label: t.recycle.platforms[value],
                }))}
              />
            </Field>

            <Field label={t.recycle.handle} hint={t.recycle.handleHint}>
              <div className="row" style={{ gap: 8 }}>
                <input
                  className="input"
                  placeholder="@alguem"
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") look(); }}
                />
                <button className="btn primary" onClick={look} disabled={scanning}>
                  {scanning ? t.recycle.scanning : t.recycle.scan}
                </button>
              </div>
            </Field>

            <div className="two">
              <Field label={t.recycle.dubInto} hint={t.recycle.dubIntoHint}>
                <select className="select" value={language}
                        onChange={(e) => setLanguage(e.target.value)}>
                  {LANGUAGES.map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </Field>
              <Field label={t.recycle.originalAudio}
                     hint={t.recycle.originalAudioHint}>
                <Chips
                  value={keepAudio ? "sim" : "nao"}
                  onChange={(value) => setKeepAudio(value === "sim")}
                  options={[
                    { value: "sim", label: t.recycle.keepUnder },
                    { value: "nao", label: t.recycle.dropIt },
                  ]}
                />
              </Field>
            </div>

            <Field label={t.recycle.credit} hint={t.recycle.creditHint}>
              <Chips
                value={credit ? "sim" : "nao"}
                onChange={(value) => setCredit(value === "sim")}
                options={[
                  { value: "sim", label: t.recycle.creditOn },
                  { value: "nao", label: t.recycle.creditOff },
                ]}
              />
            </Field>

            {failure ? (
              <span className="mono" style={{ fontSize: 12, lineHeight: 1.6,
                                              color: "var(--amber)",
                                              overflowWrap: "anywhere" }}>
                {failure}
              </span>
            ) : null}
          </div>
        </section>

        {scan ? (
          <section className="panel">
            <div className="panel-head row spread">
              <span className="label">
                {f(t.recycle.found, { count: scan.items.length,
                                      author: scan.author })}
              </span>
              <a className="btn sm ghost" href={scan.profile_url}
                 target="_blank" rel="noreferrer"
                 style={{ textDecoration: "none" }}>
                {t.recycle.openProfile} ↗
              </a>
            </div>
            <div className="panel-body grid" style={{ gap: 10 }}>
              {scan.note ? (
                <span className="dim" style={{ fontSize: 12 }}>{scan.note}</span>
              ) : null}

              {scan.items.length === 0 ? (
                <span className="dim" style={{ fontSize: 12.5 }}>
                  {t.recycle.empty}
                </span>
              ) : null}

              {scan.items.map((item) => (
                <div key={item.url} className="issue" data-sev="info"
                     style={{ gridTemplateColumns: "minmax(0, 1fr)", gap: 8 }}>
                  <div className="row wrap" style={{ gap: 10,
                                                     alignItems: "center" }}>
                    <span className="tag" data-tone="ok">
                      {views(item.views, t.recycle.unknownViews)}
                    </span>
                    {item.duration ? (
                      <span className="tag mono">{clock(item.duration)}</span>
                    ) : null}
                    <b style={{ fontSize: 13, minWidth: 0,
                                overflowWrap: "anywhere" }}>
                      {item.title || item.url}
                    </b>
                  </div>
                  <div className="row wrap" style={{ gap: 8 }}>
                    <button className="btn sm primary"
                            onClick={() => dub(item)}
                            disabled={Boolean(sending)}>
                      {sending === item.url ? t.recycle.sending
                        : f(t.recycle.dubIt, {
                            lang: LANGUAGES.find(([v]) => v === language)?.[1]
                                  ?? language,
                          })}
                    </button>
                    <a className="btn sm ghost" href={item.url} target="_blank"
                       rel="noreferrer" style={{ textDecoration: "none" }}>
                      {t.recycle.watch} ↗
                    </a>
                  </div>
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
