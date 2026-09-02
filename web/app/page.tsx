"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, type Job, type JobMetricsSummary } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { StatusTag, Topbar, useToast } from "@/components/ui";

export default function Painel() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  // exclusão apaga arquivos e histórico: pede confirmação no próprio botão
  const [confirming, setConfirming] = useState("");
  const [deleting, setDeleting] = useState("");
  const { toast, node } = useToast();

  useEffect(() => {
    const pull = () =>
      api.jobs().then((data) => { setJobs(data); setLoading(false); }).catch(() => setLoading(false));
    pull();
    const id = setInterval(pull, 3000);
    return () => clearInterval(id);
  }, []);

  const remove = async (job: Job) => {
    setDeleting(job.id);
    try {
      await api.deleteJob(job.id);
      setJobs((prev) => prev.filter((j) => j.id !== job.id));
      toast(`"${job.title || "Short"}" excluído.`);
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setDeleting("");
      setConfirming("");
    }
  };

  const running = jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  const approved = jobs.filter((j) => j.qa?.passed).length;
  const failed = jobs.filter((j) => j.status === "error").length;

  return (
    <>
      <Topbar title="Painel">
        <Link className="btn primary" href="/novo">
          Novo short
        </Link>
      </Topbar>

      <div className="content grid" style={{ gap: 20 }}>
        <div className="metrics">
          <Counter label="Shorts gerados" value={jobs.length} />
          <Counter label="Na esteira" value={running} tone="var(--cyan)" />
          <Counter label="Aprovados no QA" value={approved} tone="var(--ok)" />
          <Counter label="Com falha" value={failed} tone={failed ? "var(--err)" : undefined} />
        </div>

        <section className="grid" style={{ gap: 10 }}>
          <div className="row spread">
            <span className="label">Produções recentes</span>
            <span className="label">{jobs.length} registro(s)</span>
          </div>

          {loading ? (
            <div className="empty">carregando…</div>
          ) : jobs.length === 0 ? (
            <div className="empty">
              <p style={{ margin: "0 0 14px" }}>
                Nada produzido ainda. Cole um link, um tema ou um texto e o pipeline cuida do resto.
              </p>
              <Link className="btn primary" href="/novo">
                Criar o primeiro short
              </Link>
            </div>
          ) : (
            <div className="jobs">
              {jobs.map((job, index) => (
                <Link
                  href={`/job/${job.id}`}
                  key={job.id}
                  className="job"
                  style={{ animationDelay: `${Math.min(index, 12) * 28}ms` }}
                >
                  <Thumb job={job} />
                  <div className="grow" style={{ minWidth: 0 }}>
                    <h3>{job.title || job.input.source.slice(0, 70) || "Sem título"}</h3>
                    <div className="row" style={{ gap: 8, marginBottom: 8 }}>
                      <StatusTag status={job.status} />
                      <span className="tag">{job.input.niche}</span>
                      {job.qa ? (
                        <span className="tag" data-tone={job.qa.passed ? "ok" : "err"}>
                          QA {job.qa.score}
                        </span>
                      ) : null}
                      {job.result ? (
                        <span className="tag">{job.result.duration.toFixed(0)}s</span>
                      ) : null}
                      {metricsOf(job) ? (
                        <span className="tag" data-tone="amber" title="views somadas nas plataformas">
                          {formatCount(metricsOf(job)!.views)} views
                          {metricsOf(job)!.avg_view_pct ? ` · ${metricsOf(job)!.avg_view_pct!.toFixed(0)}% ret.` : ""}
                        </span>
                      ) : null}
                    </div>
                    {job.status === "running" || job.status === "queued" ? (
                      <div className="bar">
                        <i style={{ width: `${Math.max(job.progress * 100, 4)}%` }} />
                      </div>
                    ) : (
                      <div className="mono dimmer" style={{ fontSize: 11 }}>
                        {job.error
                          ? job.error.slice(0, 110)
                          : new Date(job.created_at).toLocaleString("pt-BR")}
                      </div>
                    )}
                  </div>
                  <div className="row job-actions" style={{ gap: 6 }}>
                    <span className="label">{job.stage ?? ""}</span>
                    {confirming === job.id ? (
                      <>
                        <button
                          className="btn sm danger"
                          disabled={deleting === job.id}
                          onClick={(e) => { e.preventDefault(); remove(job); }}
                        >
                          {deleting === job.id ? "Excluindo…" : "Confirmar"}
                        </button>
                        <button
                          className="btn sm ghost"
                          onClick={(e) => { e.preventDefault(); setConfirming(""); }}
                        >
                          Cancelar
                        </button>
                      </>
                    ) : (
                      <button
                        className="btn sm ghost job-delete"
                        title="Excluir este short"
                        onClick={(e) => { e.preventDefault(); setConfirming(job.id); }}
                      >
                        Excluir
                      </button>
                    )}
                  </div>
                </Link>
              ))}
            </div>
          )}
        </section>
      </div>
      {node}
    </>
  );
}

function metricsOf(job: Job): JobMetricsSummary | null {
  const m = job.metrics;
  if (!m || Array.isArray(m)) return null;
  return m.views || m.likes ? m : null;
}

// Miniatura estática; ao passar o mouse troca pelo GIF dos 3 primeiros
// segundos — o hook em movimento, sem abrir o job.
function Thumb({ job }: { job: Job }) {
  const [hover, setHover] = useState(false);
  const base = `/api/jobs/${job.id}/file/`;
  const still = job.result ? `url(${base}thumb.jpg?v=${job.updated_at})` : undefined;
  const gif = job.result?.preview_gif ? `url(${base}preview.gif?v=${job.updated_at})` : null;
  return (
    <div
      className="thumb"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{ backgroundImage: hover && gif ? gif : still }}
    />
  );
}

function Counter({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="metric">
      <span className="label">{label}</span>
      <b className="display" style={{ fontSize: 30, color: tone }}>
        {String(value).padStart(2, "0")}
      </b>
    </div>
  );
}
