"use client";

import { useEffect, useState } from "react";

import { api, type TrendItem } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { Chips, Topbar, useToast } from "@/components/ui";

// The key is what the API understands; the label is what the chosen language shows.
const NICHE_KEYS = [
  "tecnologia", "ciberseguranca", "programacao", "cinema", "historia",
  "ciencia", "curiosidades", "negocios", "games", "saude", "politica", "generico",
] as const;

const GEO_KEYS = ["BR", "US", "PT", "ES", "RU", "CN"] as const;

/** The LLM-curated web search can take a minute: while the backend reports it
 *  pending, the list is refreshed at this pace, this many times at most. */
const PENDING_POLL_MS = 8000;
const PENDING_POLL_MAX = 8;

/** Links that are not an article: /novo cannot ingest them, so the trend goes
 *  in as a topic instead of a URL. */
const NOT_INGESTIBLE = ["reddit.com", "youtube.com", "news.google.com"];

export default function Tendencias() {
  const { t, f } = useI18n();
  const { node } = useToast();
  const [niche, setNiche] = useState("tecnologia");
  const [geo, setGeo] = useState("BR");
  const [items, setItems] = useState<TrendItem[]>([]);
  const [pending, setPending] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  const niches: { value: string; label: string }[] =
    NICHE_KEYS.map((value) => ({ value, label: t.niches[value] }));
  const geos: { value: string; label: string }[] =
    GEO_KEYS.map((value) => ({ value, label: t.trends.regions[value] }));

  /** Sources with a technical key (google_trends, web_search…) get a translated
   *  name; a publication ("IGN", "G1 Política") is its own name in every language. */
  const sourceLabel = (key: string) =>
    (t.trends.sourceNames as Record<string, string>)[key] ?? key;

  /** The heat line comes from the backend as numbers, so the wording follows
   *  the chosen language instead of being frozen in Portuguese. */
  const heatLabel = (item: TrendItem) => {
    const template = (t.trends.heat as Record<string, string>)[item.heat_kind];
    if (!template) return "";
    const data = { ...item.heat_data } as Record<string, string | number>;
    // large counts read better abbreviated on a phone
    for (const key of ["views", "points", "comments"]) {
      if (typeof data[key] === "number") data[key] = formatCount(data[key] as number);
    }
    return f(template, data);
  };

  /** The niche /novo is prefilled with: the one on screen when the item
   *  belongs to it, otherwise the item's own (a Google Trends item seen from
   *  "games" is still generic). */
  const nicheFor = (item: TrendItem) =>
    item.niches?.includes(niche) ? niche : item.niches?.[0] ?? niche;

  const createHref = (item: TrendItem) => {
    const ingestible = item.url && !NOT_INGESTIBLE.some((host) => item.url.includes(host));
    return `/novo?tema=${encodeURIComponent(item.title)}&niche=${nicheFor(item)}${
      ingestible ? `&url=${encodeURIComponent(item.url)}` : ""}`;
  };

  useEffect(() => {
    let vivo = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let polls = 0;
    const load = (first: boolean) => {
      if (first) setLoading(true);
      api.trends(niche, geo)
        .then((r) => {
          if (!vivo) return;
          setItems(r.items);
          const waiting = r.pending ?? [];
          setPending(waiting);
          if (waiting.length && polls < PENDING_POLL_MAX) {
            polls += 1;
            timer = setTimeout(() => load(false), PENDING_POLL_MS);
          }
        })
        .catch(() => { if (vivo && first) { setItems([]); setPending([]); } })
        .finally(() => { if (vivo && first) setLoading(false); });
    };
    load(true);
    // quick niche switching: discards the response of the abandoned query
    return () => { vivo = false; if (timer) clearTimeout(timer); };
  }, [niche, geo]);

  const maxHeat = Math.max(1, ...items.map((i) => i.heat));
  const aiPending = pending.includes("web_search");

  return (
    <>
      <Topbar title={t.trends.title}>
        {loading || pending.length ? <i className="dot pulse" style={{ color: "var(--cyan)" }} /> : null}
        <span className="label">{f(t.trends.subtitle, { n: items.length })}</span>
      </Topbar>

      <div className="content grid" style={{ gap: 16 }}>
        <section className="panel">
          <div className="panel-body grid" style={{ gap: 12 }}>
            <Chips value={niche} onChange={setNiche} options={niches} />
            <Chips value={geo} onChange={setGeo} options={geos} />
            {aiPending && !loading ? (
              <span className="mono dimmer" style={{ fontSize: 11 }}>
                <i className="dot pulse" style={{ marginRight: 5, color: "var(--cyan)" }} />
                {t.trends.pendingAi}
              </span>
            ) : null}
          </div>
        </section>

        {/* with items on screen, a new query does not wipe the list: only the
            pulsing dot in the title bar signals that it is refreshing */}
        {loading && items.length === 0 ? (
          <div className="empty">{t.trends.loading}</div>
        ) : items.length === 0 ? (
          <div className="empty">{t.trends.empty}</div>
        ) : (
          <div className="grid" style={{ gap: 8 }}>
            {items.map((item, index) => (
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
                      {/* attribution only — the source is no longer a filter */}
                      <span className="tag">{sourceLabel(item.source)}</span>
                      <span className="mono dimmer" style={{ fontSize: 11 }}>{heatLabel(item)}</span>
                    </div>
                    <h3 style={{ margin: 0, fontSize: 15, lineHeight: 1.4 }}>{item.title}</h3>
                    {item.snippet ? (
                      <p className="dim" style={{ margin: "4px 0 0", fontSize: 12.5, lineHeight: 1.5,
                         overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {item.snippet}
                      </p>
                    ) : null}
                    {item.angle ? (
                      <p style={{ margin: "4px 0 0", fontSize: 12.5, lineHeight: 1.5, color: "var(--cyan)" }}>
                        <span className="mono dimmer" style={{ fontSize: 11, marginRight: 6 }}>
                          {t.trends.angle}
                        </span>
                        {item.angle}
                      </p>
                    ) : null}
                  </div>
                  <div className="row wrap" style={{ gap: 6 }}>
                    {item.url ? (
                      <a className="btn sm ghost" href={item.url} target="_blank" rel="noreferrer">
                        {t.common.source} ↗
                      </a>
                    ) : null}
                    <a className="btn sm primary" href={createHref(item)}>
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
