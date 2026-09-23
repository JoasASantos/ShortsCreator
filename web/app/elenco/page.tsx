"use client";

import { useEffect, useRef, useState } from "react";

import { api, type Personagem, type Voice } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Chips, Field, Topbar, useToast } from "@/components/ui";

const SIDES = ["esquerda", "centro", "direita"] as const;

export default function Elenco() {
  const { t } = useI18n();
  const { toast, node } = useToast();

  const [people, setPeople] = useState<Personagem[]>([]);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [name, setName] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [side, setSide] = useState<string>("esquerda");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const image = useRef<HTMLInputElement>(null);

  const pull = () => api.elenco().then(setPeople).catch(() => setPeople([]));

  useEffect(() => {
    pull();
    api.voices().then((list) => {
      setVoices(list);
      if (list.length && !voiceId) setVoiceId(list[0].id);
    }).catch(() => setVoices([]));
  }, []);

  const save = async () => {
    if (!name.trim()) return toast(t.elenco.needName);
    if (!voiceId) return toast(t.elenco.needVoice);
    setSaving(true);
    try {
      const form = new FormData();
      form.append("name", name.trim());
      form.append("voice_id", voiceId);
      form.append("side", side);
      form.append("note", note.trim());
      const file = image.current?.files?.[0];
      if (file) form.append("image", file);
      await api.createPersonagem(form);
      setName(""); setNote("");
      if (image.current) image.current.value = "";
      pull();
      toast(t.elenco.saved);
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: string) => {
    try {
      await api.deletePersonagem(id);
      pull();
    } catch (error) {
      toast((error as Error).message);
    }
  };

  return (
    <>
      <Topbar title={t.elenco.title}>
        <span className="dim" style={{ fontSize: 12 }}>{t.elenco.subtitle}</span>
      </Topbar>

      <div className="grid" style={{ gap: 14 }}>
        <section className="panel">
          <div className="panel-head">
            <span className="label">{t.elenco.newTitle}</span>
          </div>
          <div className="panel-body grid" style={{ gap: 13 }}>
            {/* The three things a character is, and why they are one thing:
                the script says "Peter says this" and that has to become a
                voice, a picture and a side of the screen without anyone
                wiring it by hand. */}
            <p className="dim" style={{ fontSize: 12.5, lineHeight: 1.6,
                                        margin: 0 }}>
              {t.elenco.explain}
            </p>

            <div className="two">
              <Field label={t.elenco.name}>
                <input className="input" placeholder="Peter" value={name}
                       onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label={t.elenco.voice} hint={t.elenco.voiceHint}>
                <select className="select" value={voiceId}
                        onChange={(e) => setVoiceId(e.target.value)}>
                  {voices.map((voice) => (
                    <option key={voice.id} value={voice.id}>
                      {voice.name} · {voice.provider}
                    </option>
                  ))}
                </select>
              </Field>
            </div>

            <Field label={t.elenco.side} hint={t.elenco.sideHint}>
              <Chips value={side} onChange={setSide}
                     options={SIDES.map((value) => ({
                       value, label: t.elenco.sides[value],
                     }))} />
            </Field>

            <Field label={t.elenco.note} hint={t.elenco.noteHint}>
              <input className="input" placeholder={t.elenco.notePlaceholder}
                     value={note} onChange={(e) => setNote(e.target.value)} />
            </Field>

            <Field label={t.elenco.image} hint={t.elenco.imageHint}>
              <input className="input" type="file" accept="image/*" ref={image} />
            </Field>

            <button className="btn primary" onClick={save} disabled={saving}>
              {saving ? t.common.saving : t.elenco.save}
            </button>
          </div>
        </section>

        {people.length ? (
          <section className="panel">
            <div className="panel-head">
              <span className="label">{t.elenco.cast}</span>
            </div>
            <div className="panel-body grid" style={{ gap: 10 }}>
              {people.map((person) => (
                <div key={person.id} className="row spread wrap"
                     style={{ gap: 10, alignItems: "center" }}>
                  <div className="row" style={{ gap: 10, alignItems: "center",
                                                minWidth: 0 }}>
                    {person.has_image ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={api.personagemImage(person.id)} alt={person.name}
                           style={{ width: 40, height: 40, objectFit: "cover",
                                    borderRadius: 4 }} />
                    ) : (
                      <span className="tag">{t.elenco.noImage}</span>
                    )}
                    <div className="grid" style={{ gap: 2, minWidth: 0 }}>
                      <b style={{ fontSize: 13 }}>{person.name}</b>
                      <span className="dimmer" style={{ fontSize: 11.5 }}>
                        {person.voice_name} · {t.elenco.sides[
                          person.side as keyof typeof t.elenco.sides] ?? person.side}
                        {person.note ? ` · ${person.note}` : ""}
                      </span>
                    </div>
                  </div>
                  <button className="btn sm ghost"
                          onClick={() => remove(person.id)}>
                    {t.common.remove}
                  </button>
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
