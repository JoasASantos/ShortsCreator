"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { api, type Voice, type VoicePreset } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Chips, Field, Topbar, useToast } from "@/components/ui";
import { VoiceBrowser } from "@/components/VoiceBrowser";

export default function Vozes() {
  const { t, f, narrationLanguage } = useI18n();
  const { toast, node } = useToast();
  const [voices, setVoices] = useState<Voice[]>([]);
  const [catalog, setCatalog] = useState<{ id: string; name: string; gender: string }[]>([]);
  const [presets, setPresets] = useState<VoicePreset[]>([]);
  const [installing, setInstalling] = useState("");
  const [provider, setProvider] = useState<"edge" | "elevenlabs" | "xtts" | "fishaudio" | "voicestudio">("edge");
  const [name, setName] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [rate, setRate] = useState("+0%");
  const [pitch, setPitch] = useState("+0Hz");
  const sample = useRef<HTMLInputElement>(null);

  const pull = () => api.voices().then(setVoices).catch(() => setVoices([]));

  const pullPresets = () =>
    api.voicePresets().then(setPresets).catch(() => setPresets([]));

  useEffect(() => {
    pull();
    pullPresets();
    // the Edge catalog comes in this locale's narration language
    api.edgeCatalog(narrationLanguage).then(setCatalog).catch(() => setCatalog([]));
  }, [narrationLanguage]);

  const install = async (preset: VoicePreset) => {
    setInstalling(preset.id);
    try {
      await api.installPreset(preset.id);
      await Promise.all([pull(), pullPresets()]);
      toast(f(t.voicesPage.installedToast, { name: preset.name }));
    } catch (error) {
      toast((error as Error).message);
    } finally { setInstalling(""); }
  };

  // groups by category, preserving the order that came from the backend
  const groups = presets.reduce<Record<string, VoicePreset[]>>((acc, preset) => {
    (acc[preset.category_label] ||= []).push(preset);
    return acc;
  }, {});

  const save = async () => {
    if (!name.trim()) return toast(t.voicesPage.needName);
    const form = new FormData();
    form.append("name", name);
    form.append("provider", provider);
    form.append("provider_voice_id", voiceId);
    form.append("rate", rate);
    form.append("pitch", pitch);
    const file = sample.current?.files?.[0];
    if (file) form.append("sample", file);
    try {
      await api.createVoice(form);
      setName(""); setVoiceId("");
      pull();
      toast(t.voicesPage.savedToast);
    } catch (error) {
      toast((error as Error).message);
    }
  };

  return (
    <>
      <Topbar title={t.voices.title}>
        <span className="label">{f(t.voicesPage.registered, { n: voices.length })}</span>
      </Topbar>

      <div className="content side">
        <div className="grid" style={{ gap: 12 }}>
          {voices.length === 0 ? (
            <div className="empty">{t.voicesPage.empty}</div>
          ) : (
            voices.map((voice) => (
              <div className="panel panel-body row spread wrap" key={voice.id}>
                <div>
                  <div style={{ fontWeight: 500, marginBottom: 5 }}>{voice.name}</div>
                  <div className="row wrap" style={{ gap: 6 }}>
                    <span className="tag" data-tone="amber">{voice.provider}</span>
                    {voice.provider_voice_id ? (
                      <span className="tag mono">{voice.provider_voice_id.slice(0, 22)}</span>
                    ) : null}
                    {voice.sample_path ? (
                      <span className="tag">{t.voicesPage.sampleUploaded}</span>
                    ) : null}
                  </div>
                </div>
                <div className="row wrap" style={{ gap: 6 }}>
                  {voice.provider === "fishaudio" && voice.provider_voice_id ? (
                    <audio controls preload="none" style={{ height: 30, maxWidth: 210 }}
                           src={api.sampleUrl(voice.provider_voice_id)} />
                  ) : (
                    <form action={`/api/voices/${voice.id}/preview`} method="post" target="_blank">
                      <button className="btn sm ghost" type="submit">
                        {t.voicesPage.listen}
                      </button>
                    </form>
                  )}
                  <button
                    className="btn sm danger"
                    onClick={() => api.deleteVoice(voice.id).then(pull)}
                  >
                    {t.common.remove}
                  </button>
                </div>
              </div>
            ))
          )}

          <section className="panel">
            <div className="panel-head">
              <span className="label">{t.voicesPage.catalogTitle}</span>
              <div className="grow" />
              <span className="label">{t.voicesPage.catalogHint}</span>
            </div>
            <div className="panel-body">
              <VoiceBrowser toast={toast} onInstalled={() => { pull(); pullPresets(); }} />
            </div>
          </section>

          {Object.entries(groups).map(([label, items]) => (
            <section className="panel" key={label}>
              <div className="panel-head">
                <span className="label">{label}</span>
                <div className="grow" />
                <span className="label">{items[0]?.category_hint}</span>
              </div>
              <div className="panel-body grid" style={{ gap: 8 }}>
                {items.map((preset) => (
                  <div className="row spread wrap" key={preset.id}>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 13.5, marginBottom: 2 }}>
                        {preset.name}{" "}
                        <span className="dimmer" style={{ fontSize: 11 }}>♥{preset.likes}</span>
                      </div>
                      <div className="dimmer" style={{ fontSize: 11.5 }}>{preset.note}</div>
                    </div>
                    {preset.installed ? (
                      <span className="tag" data-tone="ok">{t.voicesPage.installed}</span>
                    ) : (
                      <button className="btn sm" disabled={installing === preset.id}
                              onClick={() => install(preset)}>
                        {installing === preset.id
                          ? t.voicesPage.installing : t.voicesPage.install}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </section>
          ))}

          <section className="panel">
            <div className="panel-head">
              <span className="label">{t.voicesPage.cloneTitle}</span>
            </div>
            <div className="panel-body prose" style={{ fontSize: 13 }}>
              <p>{t.voicesPage.cloneIntro}</p>
              <ul>
                <li>
                  <b>{t.voicesPage.providers.elevenlabs}</b>{" "}
                  {f(t.voicesPage.cloneEleven, { code: "voice_id" })}
                </li>
                <li>
                  <b>{t.voicesPage.providers.xtts}</b> {t.voicesPage.cloneXtts}
                </li>
              </ul>
              <p>{t.voicesPage.cloneWarning}</p>
              {/* The question this answers: someone reading "clone" on this
                  screen looks for a record button here, and cloning lives on
                  the Avatar screen. */}
              <p style={{ marginBottom: 0 }}>
                <b>{t.voicesPage.cloneOwnTitle}</b> {t.voicesPage.cloneOwnWhere}{" "}
                <Link href="/avatar" className="link">{t.nav.avatar}</Link>.
              </p>
            </div>
          </section>
        </div>

        <section className="panel" style={{ position: "sticky", top: 76 }}>
          <div className="panel-head">
            <span className="label">{t.voicesPage.formTitle}</span>
          </div>
          <div className="panel-body grid" style={{ gap: 13 }}>
            <Field label={t.voicesPage.providerField}>
              <Chips
                value={provider}
                onChange={setProvider}
                options={[
                  { value: "edge", label: t.voicesPage.providers.edge },
                  { value: "fishaudio", label: t.voicesPage.providers.fishaudio },
                  { value: "elevenlabs", label: t.voicesPage.providers.elevenlabs },
                  { value: "xtts", label: t.voicesPage.providers.xtts },
                  { value: "voicestudio", label: t.voicesPage.providers.voicestudio },
                ]}
              />
            </Field>

            <Field label={t.voicesPage.nameField}>
              <input
                className="input"
                placeholder={t.voicesPage.namePlaceholder}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </Field>

            {provider === "edge" ? (
              <Field label={f(t.voicesPage.edgeVoiceField, { lang: narrationLanguage })}>
                <select className="select" value={voiceId} onChange={(e) => setVoiceId(e.target.value)}>
                  <option value="">{t.voicesPage.select}</option>
                  {catalog.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id} · {item.gender}
                    </option>
                  ))}
                </select>
              </Field>
            ) : provider === "voicestudio" ? (
              <Field label={t.voicesPage.voicestudioField}
                     hint={t.voicesPage.voicestudioHint}>
                <input
                  className="input"
                  placeholder="ab12cd34"
                  value={voiceId}
                  onChange={(e) => setVoiceId(e.target.value)}
                />
              </Field>
            ) : provider === "xtts" ? (
              <Field label={t.voicesPage.sampleField} hint={t.voicesPage.sampleHint}>
                <input className="input" type="file" accept="audio/*" ref={sample} />
              </Field>
            ) : (
              <Field label={t.voicesPage.providerVoiceField}>
                <input
                  className="input"
                  placeholder={t.voicesPage.providerVoicePlaceholder}
                  value={voiceId}
                  onChange={(e) => setVoiceId(e.target.value)}
                />
              </Field>
            )}

            {provider === "edge" ? (
              <div className="two">
                <Field label={t.voicesPage.rateField}>
                  <input className="input" value={rate} onChange={(e) => setRate(e.target.value)} />
                </Field>
                <Field label={t.voicesPage.pitchField}>
                  <input className="input" value={pitch} onChange={(e) => setPitch(e.target.value)} />
                </Field>
              </div>
            ) : null}

            <button className="btn primary" onClick={save}>{t.voicesPage.saveVoice}</button>
          </div>
        </section>
      </div>
      {node}
    </>
  );
}
