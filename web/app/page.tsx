"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, type Job, type JobMetricsSummary } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { StatusTag, Topbar, useToast } from "@/components/ui";

export default function Painel() {
  const { t, f, dateTime } = useI18n();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  // deleting wipes files and history: ask for confirmation on the button itself
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
      toast(f(t.dashboard.deleted, { title: job.title || t.job.untitled }));
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
      <Topbar title={t.dashboard.title}>
        <Link className="btn primary" href="/novo">
          {t.dashboard.newShort}
        </Link>
      </Topbar>

      <div className="content grid" style={{ gap: 20 }}>
        <div className="metrics">
          <Counter label={t.dashboard.generated} value={jobs.length} />
          <Counter label={t.dashboard.inQueue} value={running} tone="var(--cyan)" />
          <Counter label={t.dashboard.qaApproved} value={approved} tone="var(--ok)" />
          <Counter label={t.dashboard.failed} value={failed}
                   tone={failed ? "var(--err)" : undefined} />
        </div>

        <section className="grid" style={{ gap: 10 }}>
          <div className="row spread">
            <span className="label">{t.dashboard.recent}</span>
            <span className="label">{jobs.length} {t.common.record}</span>
          </div>

          {loading ? (
            <div className="empty">{t.common.loading}</div>
          ) : jobs.length === 0 ? (
            <div className="empty">
              <p style={{ margin: "0 0 14px" }}>{t.dashboard.emptyTitle}</p>
              <Link className="btn primary" href="/novo">
                {t.dashboard.emptyAction}
              </Link>
            </div>
          ) : (
            <div className="jobs">
              {jobs.map((job, index) => {
                const metrics = metricsOf(job);
                return (
                  <Link
                    href={`/job/${job.id}`}
                    key={job.id}
                    className="job"
                    style={{ animationDelay: `${Math.min(index, 12) * 28}ms` }}
                  >
                    <Thumb job={job} />
                    <div className="grow" style={{ minWidth: 0 }}>
                      <h3>{job.title || job.input.source.slice(0, 70) || t.dashboard.untitled}</h3>
                      <div className="row wrap" style={{ gap: 8, marginBottom: 8 }}>
                        <StatusTag status={job.status} />
                        <span className="tag">{t.niches[job.input.niche as keyof typeof t.niches] ?? job.input.niche}</span>
                        {job.qa ? (
                          <span className="tag" data-tone={job.qa.passed ? "ok" : "err"}>
                            QA {job.qa.score}
                          </span>
                        ) : null}
                        {job.result ? (
                          <span className="tag">{job.result.duration.toFixed(0)}{t.common.seconds}</span>
                        ) : null}
                        {metrics ? (
                          <span className="tag" data-tone="amber" title={t.dashboard.viewsTooltip}>
                            {formatCount(metrics.views)} {t.performance.views.toLowerCase()}
                            {metrics.avg_view_pct
                              ? ` · ${metrics.avg_view_pct.toFixed(0)}% ${t.dashboard.retentionShort}`
                              : ""}
                          </span>
                        ) : null}
                      </div>
                      {job.status === "running" || job.status === "queued" ? (
                        <div className="bar">
                          <i style={{ width: `${Math.max(job.progress * 100, 4)}%` }} />
                        </div>
                      ) : (
                        <div className="mono dimmer" style={{ fontSize: 11 }}>
                          {job.error ? job.error.slice(0, 110) : dateTime(job.created_at)}
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
                            {deleting === job.id ? t.common.deleting : t.common.confirm}
                          </button>
                          <button
                            className="btn sm ghost"
                            onClick={(e) => { e.preventDefault(); setConfirming(""); }}
                          >
                            {t.common.cancel}
                          </button>
                        </>
                      ) : (
                        <button
                          className="btn sm ghost job-delete"
                          title={t.dashboard.deleteTitle}
                          onClick={(e) => { e.preventDefault(); setConfirming(job.id); }}
                        >
                          {t.common.delete}
                        </button>
                      )}
                    </div>
                  </Link>
                );
              })}
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

// Static thumbnail; on hover it swaps for the GIF of the first 3 seconds —
// the hook in motion, without opening the job. Touch has no hover, so the
// GIF comes in on touch down and leaves on touch up.
function Thumb({ job }: { job: Job }) {
  const [ativo, setAtivo] = useState(false);
  const base = `/api/jobs/${job.id}/file/`;
  const still = job.result ? `url(${base}thumb.jpg?v=${job.updated_at})` : undefined;
  const gif = job.result?.preview_gif ? `url(${base}preview.gif?v=${job.updated_at})` : null;
  return (
    <div
      className="thumb"
      onMouseEnter={() => setAtivo(true)}
      onMouseLeave={() => setAtivo(false)}
      onTouchStart={() => setAtivo(true)}
      onTouchEnd={() => setAtivo(false)}
      style={{ backgroundImage: ativo && gif ? gif : still }}
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
