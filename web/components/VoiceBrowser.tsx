"use client";

import { useRef, useState } from "react";

import { api, type CatalogVoice } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

/** Busca vozes no catálogo do fish.audio, com prévia antes de instalar. */
export function VoiceBrowser({ onInstalled, toast }: {
  onInstalled: (voiceId: string, name: string) => void;
  toast: (message: string) => void;
}) {
  const { t, f, narrationLanguage } = useI18n();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CatalogVoice[]>([]);
  const [loading, setLoading] = useState(false);
  const [playing, setPlaying] = useState("");
  const [adding, setAdding] = useState("");
  const [searched, setSearched] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // o catálogo é filtrado pelo idioma da narração deste locale ("pt", "en"…)
  const language = narrationLanguage.split("-")[0];

  const search = async (term: string) => {
    const value = term.trim();
    if (!value) return;
    setQuery(value);
    setLoading(true);
    setSearched(true);
    try {
      setResults(await api.searchVoices(value, language));
    } catch (error) {
      toast((error as Error).message);
      setResults([]);
    } finally { setLoading(false); }
  };

  const preview = (voice: CatalogVoice) => {
    // só um player: tocar outra voz interrompe a anterior
    audioRef.current?.pause();
    if (playing === voice.id) { setPlaying(""); return; }
    const audio = new Audio(api.sampleUrl(voice.id));
    audio.onended = () => setPlaying("");
    audio.onerror = () => { setPlaying(""); toast(t.editor.sampleUnavailable); };
    audio.play().catch(() => { setPlaying(""); toast(t.editor.cantPlay); });
    audioRef.current = audio;
    setPlaying(voice.id);
  };

  const add = async (voice: CatalogVoice) => {
    setAdding(voice.id);
    try {
      const form = new FormData();
      form.append("name", voice.name);
      form.append("provider", "fishaudio");
      form.append("provider_voice_id", voice.id);
      const saved = await api.addCatalogVoice(form);
      onInstalled(saved.id, saved.name);
      toast(f(t.voicesPage.savedToList, { name: voice.name }));
    } catch (error) {
      toast((error as Error).message);
    } finally { setAdding(""); }
  };

  if (!open) {
    return (
      <button className="btn sm ghost" onClick={() => setOpen(true)}>
        {t.voicesPage.browseMore}
      </button>
    );
  }

  return (
    <div className="panel" style={{ background: "var(--void)" }}>
      <div className="panel-head" style={{ padding: "10px 12px" }}>
        <span className="label">{t.voicesPage.browserTitle}</span>
        <div className="grow" />
        <button className="btn sm ghost" onClick={() => {
          audioRef.current?.pause();
          setOpen(false);
        }}>{t.common.close}</button>
      </div>

      <div className="panel-body grid" style={{ gap: 10, padding: 12 }}>
        <div className="row" style={{ gap: 8 }}>
          <input
            className="input grow"
            placeholder={t.voicesPage.searchPlaceholder}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") search(query); }}
          />
          <button className="btn sm" onClick={() => search(query)} disabled={loading}>
            {loading ? t.voicesPage.searching : t.voicesPage.search}
          </button>
        </div>

        <div className="chips">
          {t.voicesPage.searchTags.map((tag) => (
            <button className="chip" key={tag} onClick={() => search(tag)}>
              {tag}
            </button>
          ))}
        </div>

        {loading ? <div className="bar"><i className="indeterminate" /></div> : null}

        {searched && !loading && results.length === 0 ? (
          <p className="dimmer" style={{ margin: 0, fontSize: 12.5 }}>
            {f(t.voicesPage.noResults, { query })}
          </p>
        ) : null}

        {results.length > 0 ? (
          <div className="voice-results">
            {results.map((voice) => (
              <div className="row spread wrap voice-row" key={voice.id}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, marginBottom: 2 }}>
                    {voice.name}{" "}
                    <span className="dimmer" style={{ fontSize: 11 }}>
                      ♥{voice.likes}
                      {voice.author ? ` · ${voice.author}` : ""}
                    </span>
                  </div>
                  <div className="dimmer" style={{ fontSize: 11.3, lineHeight: 1.45 }}>
                    {voice.description || voice.sample_text || t.voicesPage.noDescription}
                  </div>
                </div>
                <div className="row" style={{ gap: 6, flexShrink: 0 }}>
                  <button className="btn sm ghost" disabled={!voice.has_sample}
                          title={playing === voice.id ? t.editor.stop : t.editor.listenTooltip}
                          onClick={() => preview(voice)}>
                    {playing === voice.id ? "■" : "▶"}
                  </button>
                  <button className="btn sm" disabled={adding === voice.id}
                          onClick={() => add(voice)}>
                    {adding === voice.id ? "…" : t.voicesPage.use}
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
