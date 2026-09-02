"use client";

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { api, type Account, type Job } from "@/lib/api";
import { Editor } from "@/components/Editor";
import { TimelineEditor } from "@/components/TimelineEditor";
import { LogStream } from "@/components/LogStream";
import { PhonePreview } from "@/components/PhonePreview";
import { QAPanel } from "@/components/QAPanel";
import { Chips, Field, StatusTag, Topbar, useToast } from "@/components/ui";

const STAGES = ["ingest", "roteiro", "voz", "legendas", "fundo", "render", "qa"];

export default function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { toast, node } = useToast();
  const [job, setJob] = useState<Job | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [tab, setTab] = useState<"preview" | "editor" | "timeline">("preview");

  useEffect(() => {
    const pull = () => api.job(id).then(setJob).catch(() => undefined);
    pull();
    const timer = setInterval(pull, 2500);
    api.accounts().then(setAccounts).catch(() => setAccounts([]));
    return () => clearInterval(timer);
  }, [id]);

  if (!job) {
    return (
      <>
        <Topbar title="Carregando" />
        <div className="content">
          <div className="empty">buscando produção…</div>
        </div>
      </>
    );
  }

  const live = job.status === "running" || job.status === "queued";

  return (
    <>
      <Topbar title={job.title || "Short"}>
        <StatusTag status={job.status} />
        {job.status === "error" ? (
          <button className="btn sm" onClick={() => api.retryJob(id).then(() => toast("Refilado."))}>
            Reprocessar
          </button>
        ) : null}
        <button
          className="btn sm danger"
          onClick={async () => {
            await api.deleteJob(id);
            router.push("/");
          }}
        >
          Excluir
        </button>
      </Topbar>

      <div className="content grid" style={{ gap: 18 }}>
        <div className="steps">
          {STAGES.map((stage) => (
            <div key={stage} data-on={STAGES.indexOf(job.stage ?? "") >= STAGES.indexOf(stage)}>
              {stage}
            </div>
          ))}
        </div>

        {live ? (
          <div className="panel panel-body grid" style={{ gap: 10 }}>
            <div className="row spread">
              <span className="label">Etapa atual · {job.stage}</span>
              <span className="mono dim" style={{ fontSize: 12 }}>
                {(job.progress * 100).toFixed(0)}%
              </span>
            </div>
            <div className="bar">
              <i style={{ width: `${Math.max(job.progress * 100, 3)}%` }} />
            </div>
          </div>
        ) : null}

        {job.error ? (
          <div className="issue" data-sev="fatal">
            <span className="label" style={{ minWidth: 52 }}>falha</span>
            <div className="mono" style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>{job.error}</div>
          </div>
        ) : null}

        <div className="side">
          <div className="grid" style={{ gap: 16 }}>
            {job.result ? (
              <div className="steps">
                <div data-on={tab === "preview"} onClick={() => setTab("preview")}
                     style={{ cursor: "pointer" }}>preview</div>
                <div data-on={tab === "editor"} onClick={() => setTab("editor")}
                     style={{ cursor: "pointer" }}>editor de roteiro</div>
                <div data-on={tab === "timeline"} onClick={() => setTab("timeline")}
                     style={{ cursor: "pointer" }}>linha do tempo</div>
              </div>
            ) : null}

            {tab === "timeline" && job.result ? (
              <TimelineEditor
                jobId={id}
                version={job.updated_at}
                toast={toast}
                onRendered={() => {
                  setTab("preview");
                  api.job(id).then(setJob).catch(() => undefined);
                }}
              />
            ) : tab === "editor" && job.result ? (
              <>
                <Editor
                  job={job}
                  toast={toast}
                  onApplied={() => {
                    setTab("preview");
                    api.job(id).then(setJob).catch(() => undefined);
                  }}
                />
                <div className="row spread">
                  <span className="label">
                    corte, mova e estique clipes na linha do tempo
                  </span>
                  <button className="btn" onClick={() => setTab("timeline")}>
                    Abrir linha do tempo →
                  </button>
                </div>
              </>
            ) : job.result ? (
              <>
                <PhonePreview
                  src={`/api/jobs/${id}/file/short.mp4?v=${job.updated_at}`}
                  poster={`/api/jobs/${id}/file/thumb.jpg?v=${job.updated_at}`}
                />
                <div className="row" style={{ gap: 10 }}>
                  <button className="btn grow" onClick={() => setTab("editor")}>
                    Editar roteiro, voz e legenda
                  </button>
                  <button className="btn grow" onClick={() => setTab("timeline")}>
                    Editar na linha do tempo
                  </button>
                </div>
              </>
            ) : (
              <div className="stage">
                <div className="phone" style={{ display: "grid", placeItems: "center" }}>
                  <span className="label">renderizando…</span>
                </div>
              </div>
            )}

            {job.qa && tab === "preview" ? (
              <QAPanel
                report={job.qa}
                onRerun={() => api.rerunQA(id).then(() => toast("Auditoria refeita."))}
              />
            ) : null}

            {job.result?.qa_attempts?.length && tab === "preview" ? (
              <section className="panel">
                <div className="panel-head">
                  <span className="label">Autoajuste do QA</span>
                  <div className="grow" />
                  <span className="tag" data-tone="amber">
                    {job.result.qa_attempts.length} correção(ões)
                  </span>
                </div>
                <div className="panel-body grid" style={{ gap: 8 }}>
                  <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                    A auditoria reprovou e o pipeline se corrigiu sozinho antes de entregar:
                  </p>
                  {job.result.qa_attempts.map((attempt) => (
                    <div className="issue" data-sev="aviso" key={attempt.attempt}>
                      <span className="label" style={{ minWidth: 52, paddingTop: 2 }}>
                        #{attempt.attempt}
                      </span>
                      <div>
                        <div style={{ marginBottom: 4 }}>{attempt.action}</div>
                        <div className="mono dimmer" style={{ fontSize: 11.5 }}>
                          score antes: {attempt.report.score}/100
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}

            {job.result && tab === "preview" ? (
              <section className="panel">
                <div className="panel-head">
                  <span className="label">Roteiro narrado</span>
                  <div className="grow" />
                  <a className="btn sm ghost" href={`/api/jobs/${id}/file/captions.srt`}>
                    Baixar .srt
                  </a>
                  <a className="btn sm ghost" href={`/api/jobs/${id}/file/short.mp4`} download>
                    Baixar MP4
                  </a>
                </div>
                <div className="panel-body grid" style={{ gap: 10 }}>
                  {job.result.script.segments.map((segment, index) => (
                    <div className="row" style={{ gap: 12, alignItems: "start" }} key={index}>
                      <span className="tag" data-tone={segment.kind === "hook" ? "amber" : ""}>
                        {segment.kind}
                      </span>
                      <p style={{ margin: 0, lineHeight: 1.6 }} className="grow">
                        {segment.text}
                      </p>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}
          </div>

          <div className="grid" style={{ gap: 16 }}>
            {job.result ? (
              <PublishBox jobId={id} job={job} accounts={accounts} toast={toast}
                          onCaption={() => api.job(id).then(setJob).catch(() => undefined)} />
            ) : null}

            <section className="panel">
              <div className="panel-head">
                <span className="label">Log do pipeline</span>
                {live ? <i className="dot pulse" style={{ color: "var(--cyan)" }} /> : null}
              </div>
              <div className="panel-body">
                <LogStream jobId={id} live={live} />
              </div>
            </section>

            <section className="panel">
              <div className="panel-head">
                <span className="label">Parâmetros da ordem</span>
              </div>
              <div className="panel-body grid" style={{ gap: 9 }}>
                {Object.entries(job.input).map(([key, value]) => (
                  <div className="row spread" key={key}>
                    <span className="label">{key}</span>
                    <span className="mono dim" style={{ fontSize: 11.5, maxWidth: 190,
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {String(value)}
                    </span>
                  </div>
                ))}
              </div>
            </section>
          </div>
        </div>
      </div>
      {node}
    </>
  );
}

function PublishBox({ jobId, job, accounts, toast, onCaption }: {
  jobId: string; job: Job; accounts: Account[];
  toast: (m: string) => void; onCaption: () => void;
}) {
  const [accountId, setAccountId] = useState("");
  const [privacy, setPrivacy] = useState("private");
  const [mode, setMode] = useState<"agora" | "agendar">("agora");
  const [when, setWhen] = useState("");
  const [busy, setBusy] = useState(false);
  const [generating, setGenerating] = useState(false);

  const caption = job.result?.caption;
  const account = accounts.find((a) => a.id === accountId);
  const platform = account?.platform;
  const blocked = job.qa ? !job.qa.passed : false;

  // cada plataforma tem seu texto; o título só existe no YouTube
  const [title, setTitle] = useState(caption?.youtube_titulo ?? job.result?.title ?? "");
  const [body, setBody] = useState(caption?.youtube_descricao ?? job.result?.description ?? "");

  useEffect(() => {
    if (!caption) return;
    setTitle(caption.youtube_titulo);
    setBody(platform === "tiktok" ? caption.tiktok_legenda : caption.youtube_descricao);
  }, [caption, platform]);

  const generate = async () => {
    setGenerating(true);
    try {
      await api.buildCaption(jobId);
      onCaption();
      toast("Legenda de publicação gerada.");
    } catch (error) {
      toast((error as Error).message);
    } finally { setGenerating(false); }
  };

  const send = async () => {
    if (!account) { toast("Selecione uma conta conectada."); return; }
    if (mode === "agendar" && !when) { toast("Escolha a data e a hora."); return; }
    setBusy(true);
    try {
      await api.publish({
        job_id: jobId,
        account_id: accountId,
        platform: account.platform,
        title,
        description: body,
        tags: caption?.hashtags ?? job.result?.hashtags ?? [],
        privacy,
        publish_at: mode === "agendar" ? new Date(when).toISOString() : null,
      });
      toast(mode === "agendar" ? "Publicação agendada." : "Publicação enfileirada.");
    } catch (error) {
      toast((error as Error).message);
    } finally { setBusy(false); }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">Publicar</span>
        <div className="grow" />
        {blocked ? (
          <span className="tag" data-tone="err">QA reprovado</span>
        ) : (
          <span className="tag" data-tone="ok">aprovado</span>
        )}
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
        {blocked ? (
          <div className="issue" data-sev="erro">
            <span className="label" style={{ minWidth: 52, paddingTop: 2 }}>bloqueio</span>
            <div>
              A auditoria reprovou este arquivo. Corrija no editor antes de publicar —
              ou publique mesmo assim por sua conta e risco depois de reauditar.
            </div>
          </div>
        ) : null}

        <div className="row spread">
          <span className="label">Legenda do post</span>
          <button className="btn sm ghost" onClick={generate} disabled={generating}>
            {generating ? "Gerando…" : caption ? "Gerar de novo" : "Gerar com IA"}
          </button>
        </div>

        {caption ? (
          <div className="chips">
            {caption.hashtags.map((tag) => (
              <span className="tag" key={tag}>{tag}</span>
            ))}
          </div>
        ) : (
          <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
            Ainda sem legenda. Gere o texto de publicação a partir do roteiro narrado.
          </p>
        )}

        {accounts.length === 0 ? (
          <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
            Nenhuma conta conectada. Vá em <b>Contas</b> para autorizar YouTube ou TikTok.
          </p>
        ) : (
          <>
            <Field label="Conta">
              <select className="select" value={accountId}
                      onChange={(e) => setAccountId(e.target.value)}>
                <option value="">Selecione…</option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.platform === "youtube" ? "YouTube" : "TikTok"} · {a.display_name}
                  </option>
                ))}
              </select>
            </Field>

            {platform !== "tiktok" ? (
              <Field label="Título" hint={`${title.length}/100`}>
                <input className="input" value={title} maxLength={100}
                       onChange={(e) => setTitle(e.target.value)} />
              </Field>
            ) : null}

            <Field
              label={platform === "tiktok" ? "Legenda" : "Descrição"}
              hint={`${body.length} caracteres`}
            >
              <textarea className="textarea" style={{ minHeight: 96 }} value={body}
                        onChange={(e) => setBody(e.target.value)} />
            </Field>

            <Field label="Quando publicar">
              <Chips
                value={mode}
                onChange={setMode}
                options={[
                  { value: "agora", label: "Publicar agora" },
                  { value: "agendar", label: "Agendar" },
                ]}
              />
            </Field>

            <div className="two">
              <Field label="Privacidade">
                <select className="select" value={privacy}
                        onChange={(e) => setPrivacy(e.target.value)}>
                  <option value="private">Privado</option>
                  <option value="unlisted">Não listado</option>
                  <option value="public">Público</option>
                </select>
              </Field>
              {mode === "agendar" ? (
                <Field label="Data e hora">
                  <input className="input" type="datetime-local" value={when}
                         onChange={(e) => setWhen(e.target.value)} />
                </Field>
              ) : null}
            </div>

            <button className="btn primary" onClick={send} disabled={busy || blocked}>
              {busy ? "Enviando…" : mode === "agendar" ? "Agendar publicação" : "Publicar agora"}
            </button>
          </>
        )}
      </div>
    </section>
  );
}
