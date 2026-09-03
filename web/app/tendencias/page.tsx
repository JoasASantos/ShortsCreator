"use client";

import { useEffect, useState } from "react";

import { api, type TrendItem, type TrendSource } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Chips, Topbar, useToast } from "@/components/ui";

// A chave é o que a API entende; o rótulo é o que o idioma escolhido mostra.
const NICHE_KEYS = [
  "tecnologia", "ciberseguranca", "programacao", "cinema", "historia",
  "ciencia", "curiosidades", "negocios", "generico",
] as const;

const GEO_KEYS = ["BR", "US", "PT", "ES", "RU", "CN"] as const;

/** Filtro "todas as fontes": valor técnico, não muda com o idioma. */
const ALL_SOURCES = "todas";

export default function Tendencias() {
  const { t, f } = useI18n();
  const { node } = useToast();
  const [niche, setNiche] = useState("tecnologia");
  const [geo, setGeo] = useState("BR");
  const [items, setItems] = useState<TrendItem[]>([]);
  const [feeds, setFeeds] = useState<TrendSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [source, setSource] = useState(ALL_SOURCES);

  const niches: { value: string; label: string }[] =
    NICHE_KEYS.map((value) => ({ value, label: t.niches[value] }));
  const geos: { value: string; label: string }[] =
    GEO_KEYS.map((value) => ({ value, label: t.trends.regions[value] }));

  const sourceLabel = (key: string) =>
    (t.trends.sourceNames as Record<string, string>)[key] ?? key;

  useEffect(() => {
    let vivo = true;
    setLoading(true);
    api.trends(niche, geo)
      .then((r) => { if (vivo) { setItems(r.items); setFeeds(r.sources); } })
      .catch(() => { if (vivo) { setItems([]); setFeeds([]); } })
      .finally(() => { if (vivo) setLoading(false); });
    // troca rápida de nicho: descarta a resposta da consulta abandonada
    return () => { vivo = false; };
  }, [niche, geo]);

  const sources = [ALL_SOURCES, ...Array.from(new Set(items.map((i) => i.source)))];
  const visible = source === ALL_SOURCES ? items : items.filter((i) => i.source === source);
  const maxHeat = Math.max(1, ...items.map((i) => i.heat));

  return (
    <>
      <Topbar title={t.trends.title}>
        {loading ? <i className="dot pulse" style={{ color: "var(--cyan)" }} /> : null}
        <span className="label">{f(t.trends.subtitle, { n: items.length })}</span>
      </Topbar>

      <div className="content grid" style={{ gap: 16 }}>
        <section className="panel">
          <div className="panel-body grid" style={{ gap: 12 }}>
            <div className="row spread wrap" style={{ gap: 12 }}>
              <Chips value={niche} onChange={setNiche} options={niches} />
              <Chips value={geo} onChange={setGeo} options={geos} />
            </div>
            <div className="chips">
              {sources.map((s) => (
                <button key={s} type="button" className="chip" data-on={source === s}
                        onClick={() => setSource(s)}>
                  {s === ALL_SOURCES ? t.common.all : sourceLabel(s)}
                </button>
              ))}
            </div>
            {feeds.length ? (
              <div className="row wrap" style={{ gap: 14 }}>
                {feeds.map((feed) => (
                  <span className="mono dimmer" key={feed.source} style={{ fontSize: 11 }}
                        title={feed.items === 0
                          ? t.trends.sourceEmpty
                          : feed.age_seconds != null
                            ? f(t.trends.sourceAge, { n: Math.round(feed.age_seconds / 60) })
                            : ""}>
                    <i className="dot" style={{ marginRight: 5,
                       color: feed.items ? "var(--ok)" : "var(--ink-3)" }} />
                    {sourceLabel(feed.source)}
                    {feed.items ? ` ${feed.items}` : " —"}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        </section>

        {/* com itens em tela, uma nova consulta não apaga a lista: só o ponto
            pulsando na barra de título indica que está atualizando */}
        {loading && items.length === 0 ? (
          <div className="empty">{t.trends.loading}</div>
        ) : visible.length === 0 ? (
          <div className="empty">{t.trends.empty}</div>
        ) : (
          <div className="grid" style={{ gap: 8 }}>
            {visible.map((item, index) => (
              <div className="panel" key={`${item.source}-${index}`}>
                <div className="panel-body" style={{ display: "grid",
                     gridTemplateColumns: "44px minmax(0,1fr) auto", gap: 14, alignItems: "center" }}>
                  <div className="grid" style={{ gap: 4, justifyItems: "center" }}>
                    <b className="display" style={{ fontSize: 22, color: "var(--amber)" }}>
                      {String(index + 1).padStart(2, "0")}
                    </b>
                    <div className="bar" style={{ width: 36 }}>
                      <i style={{ width: `${Math.max(6, (item.heat / maxHeat) * 100)}%` }} />
                    </div>
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div className="row wrap" style={{ gap: 8, marginBottom: 4 }}>
                      <span className="tag">{sourceLabel(item.source)}</span>
                      <span className="mono dimmer" style={{ fontSize: 11 }}>{item.heat_label}</span>
                    </div>
                    <h3 style={{ margin: 0, fontSize: 15, lineHeight: 1.4 }}>{item.title}</h3>
                    {item.snippet ? (
                      <p className="dim" style={{ margin: "4px 0 0", fontSize: 12.5, lineHeight: 1.5,
                         overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {item.snippet}
                      </p>
                    ) : null}
                  </div>
                  <div className="row wrap" style={{ gap: 6 }}>
                    {item.url ? (
                      <a className="btn sm ghost" href={item.url} target="_blank" rel="noreferrer">
                        {t.common.source} ↗
                      </a>
                    ) : null}
                    <a className="btn sm primary"
                       href={`/novo?tema=${encodeURIComponent(item.title)}&niche=${niche}${
                         item.url && !item.url.includes("reddit.com") && !item.url.includes("youtube.com")
                           ? `&url=${encodeURIComponent(item.url)}` : ""}`}>
                      {t.trends.createShort}
                    </a>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      {node}
    </>
  );
}
