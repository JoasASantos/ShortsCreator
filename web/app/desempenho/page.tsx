"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, PLATFORM_LABEL, type LLMSummary, type MetricRow } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { TableWrap, Topbar, useToast } from "@/components/ui";

type Summary = { published: number; views: number; likes: number;
                 avg_retention: number | null; last_fetch: string | null };

export default function Desempenho() {
  const { t, f, date, dateTime } = useI18n();
  const { toast, node } = useToast();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [items, setItems] = useState<MetricRow[]>([]);
  const [llm, setLlm] = useState<LLMSummary[]>([]);
  const [briefing, setBriefing] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [order, setOrder] = useState<"views" | "avg_view_pct" | "likes">("views");

  const pull = () => {
    api.metrics().then((r) => { setSummary(r.summary); setItems(r.items); setLlm(r.llm); })
      .catch(() => undefined);
    api.insights().then((r) => setBriefing(r.briefing)).catch(() => undefined);
  };
  useEffect(() => { pull(); }, []);

  const refresh = async () => {
    setRefreshing(true);
    try {
      const r = await api.refreshMetrics();
      toast(f(t.performance.refreshed, { n: r.updated }));
      pull();
    } catch (e) { toast((e as Error).message); } finally { setRefreshing(false); }
  };

  const sorted = [...items].sort((a, b) => ((b[order] ?? 0) as number) - ((a[order] ?? 0) as number));

  const orderLabel: Record<typeof order, string> = {
    views: t.performance.byViews,
    likes: t.performance.byLikes,
    avg_view_pct: t.performance.byRetention,
  };

  return (
    <>
      <Topbar title={t.performance.title}>
        {summary?.last_fetch ? (
          <span className="label">
            {f(t.performance.collectedAt, { when: dateTime(summary.last_fetch) })}
          </span>
        ) : null}
        <button className="btn sm" onClick={refresh} disabled={refreshing}>
          {refreshing ? t.performance.refreshing : t.performance.refresh}
        </button>
      </Topbar>

      <div className="content grid" style={{ gap: 20 }}>
        <div className="metrics">
          <Counter label={t.performance.published} value={summary?.published ?? 0} />
          <Counter label={t.performance.views} value={summary?.views ?? 0} tone="var(--amber)" />
          <Counter label={t.performance.likes} value={summary?.likes ?? 0} tone="var(--cyan)" />
          <Counter label={t.performance.avgRetention}
                   value={summary?.avg_retention != null ? `${summary.avg_retention.toFixed(0)}%` : "—"}
                   tone="var(--ok)" />
        </div>

        {briefing ? (
          <section className="panel">
            <div className="panel-head">
              <span className="label">{t.performance.learnedTitle}</span>
              <div className="grow" />
              <span className="tag" data-tone="amber">{t.performance.learnedBadge}</span>
            </div>
            <div className="panel-body">
              <pre className="mono" style={{ margin: 0, fontSize: 12, lineHeight: 1.65,
                   whiteSpace: "pre-wrap", color: "var(--ink-2)" }}>{briefing}</pre>
            </div>
          </section>
        ) : null}

        <section className="panel">
          <div className="panel-head">
            <span className="label">{t.performance.publishedShorts}</span>
            <div className="grow" />
            <div className="chips">
              {(["views", "avg_view_pct", "likes"] as const).map((k) => (
                <button key={k} type="button" className="chip" data-on={order === k}
                        onClick={() => setOrder(k)}>
                  {orderLabel[k]}
                </button>
              ))}
            </div>
          </div>
          {items.length === 0 ? (
            <div className="panel-body">
              <div className="empty" style={{ border: 0, padding: "26px 0" }}>
                {t.performance.empty}
              </div>
            </div>
          ) : (
            <TableWrap>
              <table className="table">
                <thead>
                  <tr>
                    <th>{t.performance.colShort}</th>
                    <th>{t.performance.colPlatform}</th>
                    <th>{t.performance.colNiche}</th>
                    <th>{t.performance.colViews}</th>
                    <th>{t.performance.colLikes}</th>
                    <th>{t.performance.colComments}</th>
                    <th>{t.performance.colShares}</th>
                    <th>{t.performance.colRetention}</th>
                    <th>{t.performance.colPublished}</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((r) => (
                    <tr key={r.schedule_id}>
                      <td>
                        <Link href={`/job/${r.job_id}`} style={{ color: "var(--amber)" }}>
                          {r.title || r.job_id.slice(0, 12)}
                        </Link>
                        {r.error ? (
                          <div className="mono" style={{ fontSize: 10.5, color: "var(--err)" }}>{r.error.slice(0, 70)}</div>
                        ) : null}
                      </td>
                      <td>
                        {r.url ? <a href={r.url} target="_blank" rel="noreferrer">{PLATFORM_LABEL[r.platform] ?? r.platform} ↗</a>
                               : PLATFORM_LABEL[r.platform] ?? r.platform}
                      </td>
                      <td className="dim">
                        {(r.niche && t.niches[r.niche as keyof typeof t.niches]) || r.niche || "—"}
                      </td>
                      <td className="mono">{formatCount(r.views)}</td>
                      <td className="mono">{formatCount(r.likes)}</td>
                      <td className="mono">{formatCount(r.comments)}</td>
                      <td className="mono">{formatCount(r.shares)}</td>
                      <td className="mono">{r.avg_view_pct != null ? `${r.avg_view_pct.toFixed(0)}%` : "—"}</td>
                      <td className="mono dim">
                        {r.published_at ? date(r.published_at) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </section>

        {llm.length ? (
          <section className="panel">
            <div className="panel-head">
              <span className="label">{t.performance.aiTitle}</span>
            </div>
            <TableWrap>
              <table className="table">
                <thead>
                  <tr>
                    <th>{t.performance.aiProvider}</th>
                    <th>{t.performance.aiModel}</th>
                    <th>{t.performance.aiCalls}</th>
                    <th>{t.performance.aiSuccess}</th>
                    <th>{t.performance.aiLatency}</th>
                    <th>{t.performance.aiTotal}</th>
                  </tr>
                </thead>
                <tbody>
                  {llm.map((r) => (
                    <tr key={`${r.provider}-${r.model}`}>
                      <td>{r.provider}</td>
                      <td className="mono">{r.model || t.common.default}</td>
                      <td className="mono">{r.calls}</td>
                      <td className="mono" style={{ color: r.ok === r.calls ? "var(--ok)" : "var(--warn)" }}>
                        {Math.round((r.ok / r.calls) * 100)}%
                      </td>
                      <td className="mono">{r.avg_seconds}{t.common.seconds}</td>
                      <td className="mono">
                        {f(t.performance.aiMinutes, { n: Math.round(r.total_seconds / 60) })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </section>
        ) : null}
      </div>
      {node}
    </>
  );
}

function Counter({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
  return (
    <div className="metric">
      <span className="label">{label}</span>
      <b className="display" style={{ fontSize: 30, color: tone }}>
        {typeof value === "number" ? formatCount(value) : value}
      </b>
    </div>
  );
}
