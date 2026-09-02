"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { api, type JobInput, type SourceType, type UploadResult, type Voice } from "@/lib/api";
import { Chips, Field, Topbar, useToast } from "@/components/ui";

const NICHES = [
  { value: "tecnologia", label: "Tecnologia" },
  { value: "ciberseguranca", label: "Cibersegurança" },
  { value: "programacao", label: "Programação" },
  { value: "cinema", label: "Cinema" },
  { value: "historia", label: "História" },
  { value: "ciencia", label: "Ciência" },
  { value: "curiosidades", label: "Curiosidades" },
  { value: "negocios", label: "Negócios" },
  { value: "generico", label: "Genérico" },
];

const ANGLES = [
  { value: "auto", label: "Automático" },
  { value: "critica", label: "Crítica / resenha" },
  { value: "analise", label: "Análise" },
  { value: "contexto", label: "Bastidores" },
  { value: "historia", label: "Contar história" },
  { value: "enredo", label: "Resumo do enredo" },
  { value: "curiosidade", label: "Curiosidades" },
  { value: "explicacao", label: "Explicação" },
  { value: "tutorial", label: "Tutorial" },
];

const SOURCES: { value: SourceType; label: string; hint: string }[] = [
  { value: "tema", label: "Tema", hint: "Descreva o assunto e o roteiro é escrito do zero." },
  { value: "url", label: "Link de artigo", hint: "O texto da página vira roteiro." },
  { value: "video", label: "Vídeo", hint: "Link ou arquivo. Pode resumir um episódio inteiro." },
  { value: "github", label: "Repositório", hint: "Clona o repo, lê o código e narra sobre ele." },
  { value: "imagem", label: "Imagens", hint: "Fotos com zoom e pan lento sob a narração." },
  { value: "texto", label: "Texto colado", hint: "Um texto bruto que vira roteiro." },
  { value: "roteiro", label: "Roteiro pronto", hint: "Seu texto final, narrado sem passar por IA." },
];

const DEFAULTS: JobInput = {
  source_type: "tema",
  source: "",
  attachments: [],
  edit_mode: "narrar_por_cima",
  angle: "auto",
  instruction: "",
  niche: "tecnologia",
  language: "pt-BR",
  voice_id: null,
  duration: 45,
  caption_style: "karaoke",
  caption_position: "centro",
  scroll: "nenhum",
  background: "auto",
  background_query: "",
  music: true,
  music_track: "",
  music_volume: 0.12,
  caption_offset: 0,
  hook_hard: true,
  cta: "Segue pra mais.",
  title_overlay: true,
  watermark: "",
  variants: 1,
  qa_autofix: true,
  qa_max_attempts: 3,
};

const PLACEHOLDERS: Record<SourceType, string> = {
  url: "https://exemplo.com/artigo-sobre-a-falha-do-crowdstrike",
  video: "https://youtube.com/watch?v=… ou envie o arquivo abaixo",
  github: "https://github.com/pallets/flask",
  tema: "Como o ataque de cadeia de suprimentos da SolarWinds funcionou",
  texto: "Cole aqui o texto bruto que deve virar short…",
  roteiro: "Cole o roteiro já pronto. Cada frase vira um trecho narrado.",
  imagem: "",
};

export default function NovoShort() {
  const router = useRouter();
  const { toast, node } = useToast();
  const [form, setForm] = useState<JobInput>(DEFAULTS);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [uploads, setUploads] = useState<UploadResult[]>([]);
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.voices().then(setVoices).catch(() => setVoices([]));
  }, []);

  const set = <K extends keyof JobInput>(key: K, value: JobInput[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const acceptsFiles = form.source_type === "imagem" || form.source_type === "video";
  const isLongForm = form.source_type === "video";

  const handleFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    try {
      const results: UploadResult[] = [];
      for (const file of Array.from(files)) {
        results.push(await api.upload(file));
      }
      // vídeo é sempre um só; imagens acumulam numa sequência
      const merged = form.source_type === "video" ? results.slice(-1)
        : [...uploads, ...results];
      setUploads(merged);
      set("attachments", merged.map((u) => u.id));
      toast(`${results.length} arquivo(s) enviado(s).`);
    } catch (error) {
      toast(`Falha no upload: ${(error as Error).message}`);
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const removeUpload = (id: string) => {
    const next = uploads.filter((u) => u.id !== id);
    setUploads(next);
    set("attachments", next.map((u) => u.id));
  };

  const submit = async () => {
    if (form.source_type === "imagem" && form.attachments.length === 0) {
      toast("Envie ao menos uma imagem.");
      return;
    }
    if (!form.source.trim() && form.attachments.length === 0) {
      toast("Informe uma origem ou envie um arquivo.");
      return;
    }
    setBusy(true);
    try {
      const { job_id } = await api.createJob(form);
      router.push(`/job/${job_id}`);
    } catch (error) {
      toast(`Falha ao enfileirar: ${(error as Error).message}`);
      setBusy(false);
    }
  };

  const activeSource = SOURCES.find((s) => s.value === form.source_type);

  return (
    <>
      <Topbar title="Novo short">
        <span className="label">saída fixa 1080×1920 · 9:16</span>
      </Topbar>

      <div className="content grid" style={{ gap: 18 }}>
        <div className="steps">
          <div data-on="true">01 · origem</div>
          <div data-on="true">02 · voz</div>
          <div data-on="true">03 · imagem</div>
          <div data-on="true">04 · qa</div>
        </div>

        <div className="side">
          <div className="grid" style={{ gap: 14 }}>
            <section className="panel">
              <div className="panel-head">
                <span className="label">01 · Origem do conteúdo</span>
              </div>
              <div className="panel-body grid" style={{ gap: 14 }}>
                <Field label="Tipo de entrada" hint={activeSource?.hint}>
                  <Chips
                    value={form.source_type}
                    onChange={(value) => {
                      set("source_type", value);
                      setUploads([]);
                      set("attachments", []);
                    }}
                    options={SOURCES.map((s) => ({ value: s.value, label: s.label }))}
                  />
                </Field>

                {form.source_type !== "imagem" ? (
                  <Field
                    label={form.source_type === "roteiro" ? "Seu roteiro" : "Entrada"}
                    hint={form.source_type === "video" ? "link ou arquivo" : undefined}
                  >
                    {form.source_type === "texto" || form.source_type === "roteiro" ? (
                      <textarea
                        className="textarea"
                        placeholder={PLACEHOLDERS[form.source_type]}
                        value={form.source}
                        onChange={(e) => set("source", e.target.value)}
                      />
                    ) : (
                      <input
                        className="input"
                        placeholder={PLACEHOLDERS[form.source_type]}
                        value={form.source}
                        onChange={(e) => set("source", e.target.value)}
                      />
                    )}
                  </Field>
                ) : null}

                {acceptsFiles ? (
                  <Field
                    label={form.source_type === "imagem" ? "Imagens" : "Arquivo de vídeo"}
                    hint={form.source_type === "imagem"
                      ? "uma por trecho narrado, em ordem"
                      : "episódio, trailer, gravação…"}
                  >
                    <div className="grid" style={{ gap: 8 }}>
                      <input
                        ref={fileInput}
                        className="input"
                        type="file"
                        accept={form.source_type === "imagem" ? "image/*" : "video/*"}
                        multiple={form.source_type === "imagem"}
                        onChange={(e) => handleFiles(e.target.files)}
                      />
                      {uploading ? <span className="label">enviando…</span> : null}
                      {uploads.map((upload) => (
                        <div className="row spread" key={upload.id}>
                          <span className="mono" style={{ fontSize: 12 }}>
                            {upload.filename}{" "}
                            <span className="dimmer">
                              ({(upload.size_bytes / 1048576).toFixed(1)} MB)
                            </span>
                          </span>
                          <button className="btn sm danger"
                                  onClick={() => removeUpload(upload.id)}>
                            Remover
                          </button>
                        </div>
                      ))}
                    </div>
                  </Field>
                ) : null}

                {isLongForm ? (
                  <Field
                    label="O que fazer com o vídeo"
                    hint="resumo condensa um conteúdo longo"
                  >
                    <Chips
                      value={form.edit_mode}
                      onChange={(value) => set("edit_mode", value)}
                      options={[
                        { value: "narrar_por_cima", label: "Narrar por cima" },
                        { value: "resumo", label: "Resumir e cortar destaques" },
                      ]}
                    />
                  </Field>
                ) : null}

                {form.source_type !== "roteiro" ? (
                  <Field
                    label="Instrução para este vídeo"
                    hint="o que você quer que ele fale"
                  >
                    <textarea
                      className="textarea"
                      style={{ minHeight: 62 }}
                      placeholder="ex.: fale sobre o filme usando a cena como imagem de fundo, não descreva a cena · faça uma crítica do jogo · conte a história por trás dessa música"
                      value={form.instruction}
                      onChange={(e) => set("instruction", e.target.value)}
                    />
                  </Field>
                ) : null}

                {form.source_type !== "roteiro" ? (
                  <Field label="Ângulo" hint="qual o tipo de abordagem">
                    <Chips
                      value={form.angle}
                      onChange={(value) => set("angle", value)}
                      options={ANGLES}
                    />
                  </Field>
                ) : null}

                {form.source_type !== "roteiro" ? (
                  <Field label="Nicho" hint="ajusta o tom e o ritmo do roteiro">
                    <Chips
                      value={form.niche}
                      onChange={(value) => set("niche", value)}
                      options={NICHES}
                    />
                  </Field>
                ) : null}

                <div className="two">
                  <Field label="Duração alvo" hint={`${form.duration}s`}>
                    <input
                      type="range"
                      min={15}
                      max={90}
                      step={5}
                      value={form.duration}
                      onChange={(e) => set("duration", Number(e.target.value))}
                    />
                  </Field>
                  <Field label="Chamada final (CTA)">
                    <input
                      className="input"
                      value={form.cta}
                      onChange={(e) => set("cta", e.target.value)}
                    />
                  </Field>
                </div>
              </div>
            </section>

            <section className="panel">
              <div className="panel-head">
                <span className="label">02 · Narração e legenda</span>
              </div>
              <div className="panel-body grid" style={{ gap: 14 }}>
                <div className="two">
                  <Field label="Voz" hint="cadastre personagens em Vozes">
                    <select
                      className="select"
                      value={form.voice_id ?? ""}
                      onChange={(e) => set("voice_id", e.target.value || null)}
                    >
                      <option value="">Padrão do sistema (edge-tts pt-BR)</option>
                      {voices.map((voice) => (
                        <option key={voice.id} value={voice.id}>
                          {voice.name} · {voice.provider}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Posição da legenda">
                    <select
                      className="select"
                      value={form.caption_position}
                      onChange={(e) => set("caption_position",
                        e.target.value as JobInput["caption_position"])}
                    >
                      <option value="centro">Centro (recomendado)</option>
                      <option value="baixo">Base</option>
                      <option value="topo">Topo</option>
                    </select>
                  </Field>
                </div>

                <Field label="Estilo da legenda">
                  <Chips
                    value={form.caption_style}
                    onChange={(value) => set("caption_style", value)}
                    options={[
                      { value: "karaoke", label: "Karaokê (palavra destacada)" },
                      { value: "bloco", label: "Bloco de frase" },
                      { value: "palavra", label: "Uma palavra por vez" },
                    ]}
                  />
                </Field>
              </div>
            </section>

            <section className="panel">
              <div className="panel-head">
                <span className="label">03 · Imagem e movimento</span>
              </div>
              <div className="panel-body grid" style={{ gap: 14 }}>
                <Field label="Fundo">
                  <Chips
                    value={form.background}
                    onChange={(value) => set("background", value)}
                    options={[
                      { value: "auto", label: "Automático" },
                      { value: "broll", label: "B-roll de banco" },
                      { value: "video_fonte", label: "Vídeo de origem" },
                      { value: "imagem_kenburns", label: "Imagens (Ken Burns)" },
                      { value: "codigo_scroll", label: "Código rolando" },
                      { value: "gradiente", label: "Gradiente" },
                    ]}
                  />
                </Field>

                <Field label="Efeito de scroll" hint="movimento contínuo prende o olho">
                  <Chips
                    value={form.scroll}
                    onChange={(value) => set("scroll", value)}
                    options={[
                      { value: "nenhum", label: "Sem scroll" },
                      { value: "texto", label: "Texto rolando" },
                      { value: "codigo", label: "Código rolando" },
                      { value: "pan", label: "Pan lento" },
                    ]}
                  />
                </Field>

                <div className="two">
                  <Field label="Busca de B-roll" hint="opcional, em inglês">
                    <input
                      className="input"
                      placeholder="server room, hacker typing"
                      value={form.background_query}
                      onChange={(e) => set("background_query", e.target.value)}
                    />
                  </Field>
                  <Field label="Marca d'água">
                    <input
                      className="input"
                      placeholder="@seucanal"
                      value={form.watermark}
                      onChange={(e) => set("watermark", e.target.value)}
                    />
                  </Field>
                </div>

                <div className="row wrap" style={{ gap: 18 }}>
                  <Toggle label="Trilha de fundo" on={form.music}
                          onChange={(v) => set("music", v)} />
                  <Toggle label="Título na abertura" on={form.title_overlay}
                          onChange={(v) => set("title_overlay", v)} />
                  <Toggle label="Hook agressivo" on={form.hook_hard}
                          onChange={(v) => set("hook_hard", v)} />
                </div>
              </div>
            </section>

            <section className="panel">
              <div className="panel-head">
                <span className="label">04 · Controle de qualidade</span>
              </div>
              <div className="panel-body grid" style={{ gap: 12 }}>
                <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                  Toda renderização é auditada. Com o autoajuste ligado, uma reprovação
                  vira uma correção concreta (alongar o roteiro, mover a legenda, baixar
                  a trilha) e o vídeo é refeito até passar.
                </p>
                <div className="row wrap" style={{ gap: 18 }}>
                  <Toggle label="Corrigir automaticamente" on={form.qa_autofix}
                          onChange={(v) => set("qa_autofix", v)} />
                  <Field label="Tentativas" hint={`${form.qa_max_attempts}`}>
                    <input
                      type="range" min={1} max={5} step={1}
                      value={form.qa_max_attempts}
                      disabled={!form.qa_autofix}
                      onChange={(e) => set("qa_max_attempts", Number(e.target.value))}
                    />
                  </Field>
                </div>
              </div>
            </section>
          </div>

          <aside className="panel" style={{ position: "sticky", top: 76 }}>
            <div className="panel-head">
              <span className="label">Resumo da ordem</span>
            </div>
            <div className="panel-body grid" style={{ gap: 12 }}>
              <Summary k="Origem" v={activeSource?.label ?? form.source_type} />
              {isLongForm ? (
                <Summary k="Modo" v={form.edit_mode === "resumo" ? "resumo" : "narrar por cima"} />
              ) : null}
              {form.attachments.length ? (
                <Summary k="Arquivos" v={`${form.attachments.length}`} />
              ) : null}
              <Summary k="Nicho" v={form.niche} />
              {form.angle !== "auto" ? (
                <Summary k="Ângulo" v={ANGLES.find((a) => a.value === form.angle)?.label ?? form.angle} />
              ) : null}
              {form.instruction.trim() ? (
                <Summary k="Instrução" v="definida" />
              ) : null}
              <Summary k="Duração" v={`${form.duration}s`} />
              <Summary k="Legenda" v={`${form.caption_style} · ${form.caption_position}`} />
              <Summary k="Fundo" v={`${form.background}${form.scroll !== "nenhum" ? ` + ${form.scroll}` : ""}`} />
              <Summary k="Voz" v={voices.find((v) => v.id === form.voice_id)?.name ?? "padrão"} />
              <Summary k="Autoajuste" v={form.qa_autofix ? `até ${form.qa_max_attempts}x` : "desligado"} />
              <hr className="rule" />
              <button className="btn primary" onClick={submit} disabled={busy || uploading}>
                {busy ? "Enfileirando…" : "Gerar short"}
              </button>
            </div>
          </aside>
        </div>
      </div>
      {node}
    </>
  );
}

function Summary({ k, v }: { k: string; v: string }) {
  return (
    <div className="row spread">
      <span className="label">{k}</span>
      <span className="mono" style={{ fontSize: 12 }}>{v}</span>
    </div>
  );
}

function Toggle({ label, on, onChange }: {
  label: string; on: boolean; onChange: (value: boolean) => void;
}) {
  return (
    <button type="button" className="chip" data-on={on} onClick={() => onChange(!on)}>
      {on ? "✓ " : "○ "}
      {label}
    </button>
  );
}
