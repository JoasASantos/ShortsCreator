"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, PLATFORM_LABEL, type LLMSummary, type MetricRow } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { Topbar, useToast } from "@/components/ui";

type Summary = { published: number; views: number; likes: number;
                 avg_retention: number | null; last_fetch: string | null };

export default function Desempenho() {
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
      toast(`${r.updated} publicação(ões) atualizada(s).`);
      pull();
    } catch (e) { toast((e as Error).message); } finally { setRefreshing(false); }
  };

  const sorted = [...items].sort((a, b) => ((b[order] ?? 0) as number) - ((a[order] ?? 0) as number));

  return (
    <>
      <Topbar title="Desempenho">
        {summary?.last_fetch ? (
          <span className="label">coletado {new Date(summary.last_fetch).toLocaleString("pt-BR")}</span>
        ) : null}
        <button className="btn sm" onClick={refresh} disabled={refreshing}>
          {refreshing ? "Coletando…" : "Atualizar agora"}
        </button>
      </Topbar>

      <div className="content grid" style={{ gap: 20 }}>
        <div className="metrics">
          <Counter label="Publicados" value={summary?.published ?? 0} />
          <Counter label="Views" value={summary?.views ?? 0} tone="var(--amber)" />
          <Counter label="Likes" value={summary?.likes ?? 0} tone="var(--cyan)" />
          <Counter label="Retenção média"
                   value={summary?.avg_retention != null ? `${summary.avg_retention.toFixed(0)}%` : "—"}
                   tone="var(--ok)" />
        </div>

        {briefing ? (
          <section className="panel">
            <div className="panel-head">
              <span className="label">O que o roteirista aprendeu</span>
              <div className="grow" />
              <span className="tag" data-tone="amber">entra em todo roteiro novo</span>
            </div>
            <div className="panel-body">
              <pre className="mono" style={{ margin: 0, fontSize: 12, lineHeight: 1.65,
                   whiteSpace: "pre-wrap", color: "var(--ink-2)" }}>{briefing}</pre>
            </div>
          </section>
        ) : null}

        <section className="panel">
          <div className="panel-head">
            <span className="label">Shorts publicados</span>
            <div className="grow" />
            <div className="chips">
              {(["views", "avg_view_pct", "likes"] as const).map((k) => (
                <button key={k} type="button" className="chip" data-on={order === k}
                        onClick={() => setOrder(k)}>
                  {k === "views" ? "por views" : k === "likes" ? "por likes" : "por retenção"}
                </button>
              ))}
            </div>
          </div>
          {items.length === 0 ? (
            <div className="panel-body">
              <div className="empty" style={{ border: 0, padding: "26px 0" }}>
                Nenhuma publicação medida ainda. Publique um short por uma conta conectada;
                as métricas chegam sozinhas a cada 6 horas.
              </div>
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Short</th><th>Plataforma</th><th>Nicho</th>
                  <th>Views</th><th>Likes</th><th>Coment.</th><th>Comp.</th><th>Retenção</th><th>Publicado</th>
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
                    <td className="dim">{r.niche ?? "—"}</td>
                    <td className="mono">{formatCount(r.views)}</td>
                    <td className="mono">{formatCount(r.likes)}</td>
                    <td className="mono">{formatCount(r.comments)}</td>
                    <td className="mono">{formatCount(r.shares)}</td>
                    <td className="mono">{r.avg_view_pct != null ? `${r.avg_view_pct.toFixed(0)}%` : "—"}</td>
                    <td className="mono dim">
                      {r.published_at ? new Date(r.published_at).toLocaleDateString("pt-BR") : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        {llm.length ? (
          <section className="panel">
            <div className="panel-head">
              <span className="label">Uso de IA · últimos 30 dias</span>
            </div>
            <table className="table">
              <thead>
                <tr><th>Provider</th><th>Modelo</th><th>Chamadas</th><th>Sucesso</th><th>Latência média</th><th>Tempo total</th></tr>
              </thead>
              <tbody>
                {llm.map((r) => (
                  <tr key={`${r.provider}-${r.model}`}>
                    <td>{r.provider}</td>
                    <td className="mono">{r.model || "padrão"}</td>
                    <td className="mono">{r.calls}</td>
                    <td className="mono" style={{ color: r.ok === r.calls ? "var(--ok)" : "var(--warn)" }}>
                      {Math.round((r.ok / r.calls) * 100)}%
                    </td>
                    <td className="mono">{r.avg_seconds}s</td>
                    <td className="mono">{Math.round(r.total_seconds / 60)} min</td>
                  </tr>
                ))}
              </tbody>
            </table>
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
