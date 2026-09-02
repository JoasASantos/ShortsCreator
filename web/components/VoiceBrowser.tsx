"use client";

import { useRef, useState } from "react";

import { api, type CatalogVoice } from "@/lib/api";

const ATALHOS = ["narrador", "trailer", "documentário", "cinema", "games",
                 "tecnologia", "personagem", "grave", "feminina"];

/** Busca vozes no catálogo do fish.audio, com prévia antes de instalar. */
export function VoiceBrowser({ onInstalled, toast }: {
  onInstalled: (voiceId: string, name: string) => void;
  toast: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CatalogVoice[]>([]);
  const [loading, setLoading] = useState(false);
  const [playing, setPlaying] = useState("");
  const [adding, setAdding] = useState("");
  const [searched, setSearched] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const search = async (term: string) => {
    const value = term.trim();
    if (!value) return;
    setQuery(value);
    setLoading(true);
    setSearched(true);
    try {
      setResults(await api.searchVoices(value));
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
    audio.onerror = () => { setPlaying(""); toast("Amostra indisponível."); };
    audio.play().catch(() => { setPlaying(""); toast("Não foi possível tocar."); });
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
      toast(`Voz "${voice.name}" salva na sua lista.`);
    } catch (error) {
      toast((error as Error).message);
    } finally { setAdding(""); }
  };

  if (!open) {
    return (
      <button className="btn sm ghost" onClick={() => setOpen(true)}>
        Buscar mais vozes
      </button>
    );
  }

  return (
    <div className="panel" style={{ background: "var(--void)" }}>
      <div className="panel-head" style={{ padding: "10px 12px" }}>
        <span className="label">Catálogo fish.audio</span>
        <div className="grow" />
        <button className="btn sm ghost" onClick={() => {
          audioRef.current?.pause();
          setOpen(false);
        }}>Fechar</button>
      </div>

      <div className="panel-body grid" style={{ gap: 10, padding: 12 }}>
        <div className="row" style={{ gap: 8 }}>
          <input
            className="input grow"
            placeholder="narrador de trailer, voz grave, personagem…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") search(query); }}
          />
          <button className="btn sm" onClick={() => search(query)} disabled={loading}>
            {loading ? "Buscando…" : "Buscar"}
          </button>
        </div>

        <div className="chips">
          {ATALHOS.map((atalho) => (
            <button className="chip" key={atalho} onClick={() => search(atalho)}>
              {atalho}
            </button>
          ))}
        </div>

        {loading ? <div className="bar"><i className="indeterminate" /></div> : null}

        {searched && !loading && results.length === 0 ? (
          <p className="dimmer" style={{ margin: 0, fontSize: 12.5 }}>
            Nenhuma voz encontrada para “{query}”.
          </p>
        ) : null}

        {results.length > 0 ? (
          <div className="voice-results">
            {results.map((voice) => (
              <div className="row spread voice-row" key={voice.id}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, marginBottom: 2 }}>
                    {voice.name}{" "}
                    <span className="dimmer" style={{ fontSize: 11 }}>
                      ♥{voice.likes}
                      {voice.author ? ` · ${voice.author}` : ""}
                    </span>
                  </div>
                  <div className="dimmer" style={{ fontSize: 11.3, lineHeight: 1.45 }}>
                    {voice.description || voice.sample_text || "sem descrição"}
                  </div>
                </div>
                <div className="row" style={{ gap: 6, flexShrink: 0 }}>
                  <button className="btn sm ghost" disabled={!voice.has_sample}
                          onClick={() => preview(voice)}>
                    {playing === voice.id ? "■" : "▶"}
                  </button>
                  <button className="btn sm" disabled={adding === voice.id}
                          onClick={() => add(voice)}>
                    {adding === voice.id ? "…" : "Usar"}
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
