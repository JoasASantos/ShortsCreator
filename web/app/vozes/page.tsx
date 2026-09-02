"use client";

import { useEffect, useRef, useState } from "react";

import { api, type Voice, type VoicePreset } from "@/lib/api";
import { Chips, Field, Topbar, useToast } from "@/components/ui";
import { VoiceBrowser } from "@/components/VoiceBrowser";

export default function Vozes() {
  const { toast, node } = useToast();
  const [voices, setVoices] = useState<Voice[]>([]);
  const [catalog, setCatalog] = useState<{ id: string; name: string; gender: string }[]>([]);
  const [presets, setPresets] = useState<VoicePreset[]>([]);
  const [installing, setInstalling] = useState("");
  const [provider, setProvider] = useState<"edge" | "elevenlabs" | "xtts" | "fishaudio">("edge");
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
    api.edgeCatalog().then(setCatalog).catch(() => setCatalog([]));
  }, []);

  const install = async (preset: VoicePreset) => {
    setInstalling(preset.id);
    try {
      await api.installPreset(preset.id);
      await Promise.all([pull(), pullPresets()]);
      toast(`Voz "${preset.name}" adicionada.`);
    } catch (error) {
      toast((error as Error).message);
    } finally { setInstalling(""); }
  };

  // agrupa por categoria preservando a ordem que veio do backend
  const groups = presets.reduce<Record<string, VoicePreset[]>>((acc, preset) => {
    (acc[preset.category_label] ||= []).push(preset);
    return acc;
  }, {});

  const save = async () => {
    if (!name.trim()) return toast("Dê um nome ao personagem.");
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
      toast("Voz cadastrada.");
    } catch (error) {
      toast((error as Error).message);
    }
  };

  return (
    <>
      <Topbar title="Vozes">
        <span className="label">{voices.length} cadastrada(s)</span>
      </Topbar>

      <div className="content side">
        <div className="grid" style={{ gap: 12 }}>
          {voices.length === 0 ? (
            <div className="empty">
              Nenhuma voz personalizada. O sistema usa a voz neural padrão em pt-BR até você
              cadastrar um personagem.
            </div>
          ) : (
            voices.map((voice) => (
              <div className="panel panel-body row spread" key={voice.id}>
                <div>
                  <div style={{ fontWeight: 500, marginBottom: 5 }}>{voice.name}</div>
                  <div className="row" style={{ gap: 6 }}>
                    <span className="tag" data-tone="amber">{voice.provider}</span>
                    {voice.provider_voice_id ? (
                      <span className="tag mono">{voice.provider_voice_id.slice(0, 22)}</span>
                    ) : null}
                    {voice.sample_path ? <span className="tag">sample enviado</span> : null}
                  </div>
                </div>
                <div className="row" style={{ gap: 6 }}>
                  {voice.provider === "fishaudio" && voice.provider_voice_id ? (
                    <audio controls preload="none" style={{ height: 30, maxWidth: 210 }}
                           src={api.sampleUrl(voice.provider_voice_id)} />
                  ) : (
                    <form action={`/api/voices/${voice.id}/preview`} method="post" target="_blank">
                      <button className="btn sm ghost" type="submit">Ouvir</button>
                    </form>
                  )}
                  <button
                    className="btn sm danger"
                    onClick={() => api.deleteVoice(voice.id).then(pull)}
                  >
                    Remover
                  </button>
                </div>
              </div>
            ))
          )}

          <section className="panel">
            <div className="panel-head">
              <span className="label">Buscar no catálogo</span>
              <div className="grow" />
              <span className="label">ouça antes de adicionar</span>
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
                  <div className="row spread" key={preset.id}>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 13.5, marginBottom: 2 }}>
                        {preset.name}{" "}
                        <span className="dimmer" style={{ fontSize: 11 }}>♥{preset.likes}</span>
                      </div>
                      <div className="dimmer" style={{ fontSize: 11.5 }}>{preset.note}</div>
                    </div>
                    {preset.installed ? (
                      <span className="tag" data-tone="ok">adicionada</span>
                    ) : (
                      <button className="btn sm" disabled={installing === preset.id}
                              onClick={() => install(preset)}>
                        {installing === preset.id ? "Adicionando…" : "Adicionar"}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </section>
          ))}

          <section className="panel">
            <div className="panel-head">
              <span className="label">Como clonar a voz de um personagem</span>
            </div>
            <div className="panel-body prose" style={{ fontSize: 13 }}>
              <p>
                Para narrar com a voz de um personagem (Seu Madruga, um dublador, sua própria voz),
                há dois caminhos:
              </p>
              <ul>
                <li>
                  <b>ElevenLabs</b> — crie a voz clonada no painel deles, copie o <code>voice_id</code>{" "}
                  e cadastre aqui. Traz timings por caractere, então a legenda karaokê fica perfeita.
                </li>
                <li>
                  <b>XTTS local</b> — suba o servidor XTTS e envie um sample de 6 a 30 segundos do
                  personagem. Roda offline, sem custo por caractere.
                </li>
              </ul>
              <p style={{ marginBottom: 0 }}>
                Use apenas vozes que você tem direito de usar. Clonar a voz de uma pessoa real sem
                autorização pode violar direitos de imagem e os termos das plataformas.
              </p>
            </div>
          </section>
        </div>

        <section className="panel" style={{ position: "sticky", top: 76 }}>
          <div className="panel-head">
            <span className="label">Cadastrar voz</span>
          </div>
          <div className="panel-body grid" style={{ gap: 13 }}>
            <Field label="Provedor">
              <Chips
                value={provider}
                onChange={setProvider}
                options={[
                  { value: "edge", label: "Edge (grátis)" },
                  { value: "fishaudio", label: "fish.audio" },
                  { value: "elevenlabs", label: "ElevenLabs" },
                  { value: "xtts", label: "XTTS local" },
                ]}
              />
            </Field>

            <Field label="Nome do personagem">
              <input
                className="input"
                placeholder="Narrador grave / Seu personagem"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </Field>

            {provider === "edge" ? (
              <Field label="Voz do catálogo pt-BR">
                <select className="select" value={voiceId} onChange={(e) => setVoiceId(e.target.value)}>
                  <option value="">Selecione…</option>
                  {catalog.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id} · {item.gender}
                    </option>
                  ))}
                </select>
              </Field>
            ) : provider === "xtts" ? (
              <Field label="Sample de referência" hint="6 a 30s, wav ou mp3">
                <input className="input" type="file" accept="audio/*" ref={sample} />
              </Field>
            ) : (
              <Field label="voice_id do provedor">
                <input
                  className="input"
                  placeholder="ex.: 21m00Tcm4TlvDq8ikWAM"
                  value={voiceId}
                  onChange={(e) => setVoiceId(e.target.value)}
                />
              </Field>
            )}

            {provider === "edge" ? (
              <div className="two">
                <Field label="Velocidade">
                  <input className="input" value={rate} onChange={(e) => setRate(e.target.value)} />
                </Field>
                <Field label="Tom">
                  <input className="input" value={pitch} onChange={(e) => setPitch(e.target.value)} />
                </Field>
              </div>
            ) : null}

            <button className="btn primary" onClick={save}>Salvar voz</button>
          </div>
        </section>
      </div>
      {node}
    </>
  );
}
