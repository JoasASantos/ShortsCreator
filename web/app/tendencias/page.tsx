"use client";

import { useEffect, useState } from "react";

import { api, type TrendItem } from "@/lib/api";
import { Chips, Topbar, useToast } from "@/components/ui";

const NICHES = [
  { value: "tecnologia", label: "Tecnologia" },
  { value: "ciberseguranca", label: "Cibersegurança" },
  { value: "programacao", label: "Programação" },
  { value: "cinema", label: "Cinema" },
  { value: "historia", label: "História" },
  { value: "ciencia", label: "Ciência" },
  { value: "curiosidades", label: "Curiosidades" },
  { value: "negocios", label: "Negócios" },
  { value: "generico", label: "Geral" },
];

const GEOS = [
  { value: "BR", label: "Brasil" },
  { value: "US", label: "EUA" },
  { value: "PT", label: "Portugal" },
];

export default function Tendencias() {
  const { node } = useToast();
  const [niche, setNiche] = useState("tecnologia");
  const [geo, setGeo] = useState("BR");
  const [items, setItems] = useState<TrendItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [source, setSource] = useState("todas");

  useEffect(() => {
    setLoading(true);
    api.trends(niche, geo)
      .then((r) => setItems(r.items))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, [niche, geo]);

  const sources = ["todas", ...Array.from(new Set(items.map((i) => i.source)))];
  const visible = source === "todas" ? items : items.filter((i) => i.source === source);
  const maxHeat = Math.max(1, ...items.map((i) => i.heat));

  return (
    <>
      <Topbar title="Tendências">
        <span className="label">{items.length} assunto(s) em alta · cache 30 min</span>
      </Topbar>

      <div className="content grid" style={{ gap: 16 }}>
        <section className="panel">
          <div className="panel-body grid" style={{ gap: 12 }}>
            <div className="row spread wrap" style={{ gap: 12 }}>
              <Chips value={niche} onChange={setNiche} options={NICHES} />
              <Chips value={geo} onChange={setGeo} options={GEOS} />
            </div>
            <div className="chips">
              {sources.map((s) => (
                <button key={s} type="button" className="chip" data-on={source === s}
                        onClick={() => setSource(s)}>{s}</button>
              ))}
            </div>
          </div>
        </section>

        {loading ? (
          <div className="empty">consultando Google Trends, Reddit, Hacker News e YouTube…</div>
        ) : visible.length === 0 ? (
          <div className="empty">
            Nada encontrado agora. As fontes podem estar limitando requisições — tente de novo em alguns minutos.
          </div>
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
                    <div className="row" style={{ gap: 8, marginBottom: 4 }}>
                      <span className="tag">{item.source}</span>
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
                  <div className="row" style={{ gap: 6 }}>
                    {item.url ? (
                      <a className="btn sm ghost" href={item.url} target="_blank" rel="noreferrer">Fonte ↗</a>
                    ) : null}
                    <a className="btn sm primary"
                       href={`/novo?tema=${encodeURIComponent(item.title)}&niche=${niche}${
                         item.url && !item.url.includes("reddit.com") && !item.url.includes("youtube.com")
                           ? `&url=${encodeURIComponent(item.url)}` : ""}`}>
                      Criar short
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
