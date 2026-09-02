"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { api, PLATFORM_LABEL, type Account, type ClipPlan, type UploadResult, type Voice } from "@/lib/api";
import { formatSeconds } from "@/lib/format";
import { Chips, Field, StatusTag, Topbar, useToast } from "@/components/ui";

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

// Um vídeo longo vira N shorts: transcreve, o LLM escolhe os momentos, cada
// um vira um job normal — e, se quiser, já sai agendado um por dia.
export default function Lote() {
  const { toast, node } = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const [upload, setUpload] = useState<UploadResult | null>(null);
  const [uploading, setUploading] = useState(false);
  const [count, setCount] = useState(5);
  const [target, setTarget] = useState(45);
  const [niche, setNiche] = useState("tecnologia");
  const [plans, setPlans] = useState<ClipPlan[]>([]);
  const [active, setActive] = useState<string>("");
  const [busy, setBusy] = useState(false);

  const pull = () => api.clipPlans().then(setPlans).catch(() => setPlans([]));

  useEffect(() => {
    pull();
    const id = setInterval(pull, 5000);
    // vindo de /novo com o vídeo já enviado
    const params = new URLSearchParams(window.location.search);
    const attachment = params.get("attachment");
    if (attachment) {
      setUpload({ id: attachment, kind: "video", filename: params.get("name") || attachment, size_bytes: 0 });
      window.history.replaceState({}, "", "/lote");
    }
    return () => clearInterval(id);
  }, []);

  const handleFile = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    try {
      setUpload(await api.upload(files[0]));
    } catch (e) { toast(`Falha no upload: ${(e as Error).message}`); }
    finally { setUploading(false); if (fileInput.current) fileInput.current.value = ""; }
  };

  const analyze = async () => {
    if (!upload) { toast("Envie o vídeo primeiro."); return; }
    setBusy(true);
    try {
      const { plan_id } = await api.createClipPlan({
        attachment_id: upload.id, count, target_seconds: target, niche,
      });
      setActive(plan_id);
      toast("Análise enfileirada — transcrição leva alguns minutos.");
      pull();
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };

  const current = plans.find((p) => p.id === active) ?? plans[0];

  return (
    <>
      <Topbar title="Lote">
        <span className="label">um vídeo longo → vários shorts</span>
      </Topbar>

      <div className="content grid" style={{ gap: 18 }}>
        <div className="side">
          <div className="grid" style={{ gap: 14 }}>
            <section className="panel">
              <div className="panel-head"><span className="label">01 · Vídeo de origem</span></div>
              <div className="panel-body grid" style={{ gap: 12 }}>
                <input ref={fileInput} className="input" type="file" accept="video/*"
                       onChange={(e) => handleFile(e.target.files)} />
                {uploading ? <span className="label">enviando…</span> : null}
                {upload ? (
                  <div className="row spread">
                    <span className="mono" style={{ fontSize: 12 }}>
                      {upload.filename}
                      {upload.size_bytes ? <span className="dimmer"> ({(upload.size_bytes / 1048576).toFixed(0)} MB)</span> : null}
                    </span>
                    <button className="btn sm ghost" onClick={() => setUpload(null)}>Trocar</button>
                  </div>
                ) : (
                  <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                    Podcast, aula, live, entrevista. Precisa ter fala — a escolha dos momentos
                    é feita sobre a transcrição.
                  </p>
                )}
                <div className="two">
                  <Field label="Quantos shorts" hint={`${count}`}>
                    <input type="range" min={1} max={12} step={1} value={count}
                           onChange={(e) => setCount(Number(e.target.value))} />
                  </Field>
                  <Field label="Duração de cada um" hint={`${target}s`}>
                    <input type="range" min={20} max={90} step={5} value={target}
                           onChange={(e) => setTarget(Number(e.target.value))} />
                  </Field>
                </div>
                <Field label="Nicho">
                  <Chips value={niche} onChange={setNiche} options={NICHES} />
                </Field>
                <button className="btn primary" onClick={analyze} disabled={busy || uploading || !upload}>
                  {busy ? "Enfileirando…" : "Encontrar os melhores momentos"}
                </button>
              </div>
            </section>

            {current ? <PlanPanel plan={current} toast={toast} onChanged={pull} /> : null}
          </div>

          <aside className="grid" style={{ gap: 10 }}>
            <span className="label">Lotes anteriores</span>
            {plans.length === 0 ? (
              <div className="empty" style={{ padding: 22 }}>nenhum lote ainda</div>
            ) : plans.map((p) => (
              <button key={p.id} className="panel" style={{ textAlign: "left", cursor: "pointer",
                      borderColor: p.id === current?.id ? "var(--amber)" : undefined }}
                      onClick={() => setActive(p.id)}>
                <div className="panel-body grid" style={{ gap: 6 }}>
                  <div className="row spread">
                    <StatusTag status={p.status} />
                    <span className="mono dimmer" style={{ fontSize: 11 }}>
                      {new Date(p.created_at).toLocaleDateString("pt-BR")}
                    </span>
                  </div>
                  <span className="mono" style={{ fontSize: 12 }}>
                    {p.requested} × {p.target_seconds}s · {p.options?.niche}
                    {p.jobs?.length ? ` · ${p.jobs.length} short(s)` : ""}
                  </span>
                </div>
              </button>
            ))}
          </aside>
        </div>
      </div>
      {node}
    </>
  );
}

function PlanPanel({ plan, toast, onChanged }: {
  plan: ClipPlan; toast: (m: string) => void; onChanged: () => void;
}) {
  const [selected, setSelected] = useState<number[]>([]);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [voiceId, setVoiceId] = useState("");
  const [schedule, setSchedule] = useState(false);
  const [accountId, setAccountId] = useState("");
  const [startAt, setStartAt] = useState("");
  const [everyHours, setEveryHours] = useState(24);
  const [privacy, setPrivacy] = useState("public");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.voices().then(setVoices).catch(() => setVoices([]));
    api.accounts().then(setAccounts).catch(() => setAccounts([]));
  }, []);
  useEffect(() => {
    setSelected(plan.clips ? plan.clips.map((_, i) => i) : []);
  }, [plan.id, plan.clips?.length]);

  const toggle = (i: number) =>
    setSelected((prev) => prev.includes(i) ? prev.filter((x) => x !== i) : [...prev, i].sort());

  const render = async () => {
    if (!selected.length) { toast("Selecione ao menos um trecho."); return; }
    if (schedule && (!accountId || !startAt)) { toast("Escolha conta e data inicial."); return; }
    setBusy(true);
    try {
      const r = await api.renderClips(plan.id, {
        selected, voice_id: voiceId || null, caption_style: "karaoke",
        caption_position: "centro", music: true, watermark: "", qa_autofix: true,
        schedule: schedule ? {
          account_id: accountId, start_at: new Date(startAt).toISOString(),
          every_hours: everyHours, privacy,
        } : null,
      });
      toast(`${r.jobs.length} short(s) na esteira${r.schedules.length ? `, ${r.schedules.length} agendado(s)` : ""}.`);
      onChanged();
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">02 · Momentos encontrados</span>
        <div className="grow" />
        <StatusTag status={plan.status} />
        <button className="btn sm ghost" onClick={() => api.deleteClipPlan(plan.id).then(onChanged)}>Remover</button>
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
        {plan.status === "queued" || plan.status === "analisando" ? (
          <div className="empty" style={{ border: 0 }}>
            transcrevendo e escolhendo os trechos… pode levar alguns minutos num vídeo longo
          </div>
        ) : plan.status === "error" ? (
          <div className="issue" data-sev="fatal">
            <span className="label" style={{ minWidth: 52 }}>falha</span>
            <div className="mono" style={{ fontSize: 12 }}>{plan.error}</div>
          </div>
        ) : plan.clips?.length ? (
          <>
            {plan.clips.map((clip, i) => (
              <label key={i} className="issue" data-sev={selected.includes(i) ? "aviso" : "info"}
                     style={{ cursor: "pointer", alignItems: "start" }}>
                <input type="checkbox" checked={selected.includes(i)} onChange={() => toggle(i)}
                       style={{ marginTop: 3 }} />
                <div className="grow">
                  <div className="row" style={{ gap: 8, marginBottom: 3 }}>
                    <span className="tag" data-tone="amber">
                      {formatSeconds(clip.inicio)} → {formatSeconds(clip.fim)}
                    </span>
                    <span className="mono dimmer" style={{ fontSize: 11 }}>{Math.round(clip.fim - clip.inicio)}s</span>
                  </div>
                  <b style={{ fontSize: 14 }}>{clip.titulo}</b>
                  <p className="dim" style={{ margin: "3px 0 0", fontSize: 12.5, lineHeight: 1.5 }}>{clip.motivo}</p>
                </div>
              </label>
            ))}

            {plan.jobs?.length ? (
              <div className="row wrap" style={{ gap: 6 }}>
                <span className="label" style={{ alignSelf: "center" }}>shorts gerados:</span>
                {plan.jobs.map((j) => (
                  <Link key={j} href={`/job/${j}`} className="tag" style={{ color: "var(--amber)" }}>
                    {j.slice(4, 12)}
                  </Link>
                ))}
              </div>
            ) : null}

            <hr className="rule" />
            <div className="two">
              <Field label="Voz">
                <select className="select" value={voiceId} onChange={(e) => setVoiceId(e.target.value)}>
                  <option value="">Padrão do sistema</option>
                  {voices.map((v) => <option key={v.id} value={v.id}>{v.name} · {v.provider}</option>)}
                </select>
              </Field>
              <Field label="Publicação">
                <Chips value={schedule ? "sim" : "nao"} onChange={(v) => setSchedule(v === "sim")}
                       options={[{ value: "nao", label: "Só gerar" }, { value: "sim", label: "Agendar em sequência" }]} />
              </Field>
            </div>

            {schedule ? (
              accounts.length === 0 ? (
                <p className="dimmer" style={{ margin: 0, fontSize: 12.5 }}>
                  Nenhuma conta conectada em <b>Contas</b>.
                </p>
              ) : (
                <div className="grid" style={{ gap: 10 }}>
                  <div className="two">
                    <Field label="Conta">
                      <select className="select" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                        <option value="">Selecione…</option>
                        {accounts.map((a) => (
                          <option key={a.id} value={a.id}>{PLATFORM_LABEL[a.platform] ?? a.platform} · {a.display_name}</option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Primeiro short em">
                      <input className="input" type="datetime-local" value={startAt}
                             onChange={(e) => setStartAt(e.target.value)} />
                    </Field>
                  </div>
                  <div className="two">
                    <Field label="Intervalo" hint={everyHours >= 24 ? `${everyHours / 24} dia(s)` : `${everyHours}h`}>
                      <input type="range" min={2} max={72} step={2} value={everyHours}
                             onChange={(e) => setEveryHours(Number(e.target.value))} />
                    </Field>
                    <Field label="Privacidade">
                      <select className="select" value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
                        <option value="public">Público</option>
                        <option value="unlisted">Não listado</option>
                        <option value="private">Privado</option>
                      </select>
                    </Field>
                  </div>
                  <p className="dimmer" style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5 }}>
                    Cada publicação espera o próprio short terminar de renderizar e passar no QA.
                  </p>
                </div>
              )
            ) : null}

            <button className="btn primary" onClick={render} disabled={busy}>
              {busy ? "Enfileirando…" : `Gerar ${selected.length} short(s)${schedule ? " e agendar" : ""}`}
            </button>
          </>
        ) : (
          <div className="empty" style={{ border: 0 }}>nenhum trecho aproveitável encontrado</div>
        )}
      </div>
    </section>
  );
}
