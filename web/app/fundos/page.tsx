"use client";

import { useEffect, useState } from "react";

import { api, type Fundo, type StockItem } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Field, Topbar, useToast } from "@/components/ui";

function minutes(seconds: number): string {
  return `${Math.round(seconds / 60)} min`;
}

export default function Fundos() {
  const { t, f } = useI18n();
  const { toast, node } = useToast();

  const [fundos, setFundos] = useState<Fundo[]>([]);
  const [catalog, setCatalog] = useState<StockItem[]>([]);
  const [folder, setFolder] = useState("");
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState("");

  const pull = () =>
    api.fundosFull()
      .then((body) => {
        setFundos(body.fundos);
        setCatalog(body.catalogo);
        setFolder(body.pasta);
      })
      .catch(() => setFundos([]));

  useEffect(() => { pull(); }, []);

  const save = async () => {
    if (!link.trim()) return toast(t.fundos.needLink);
    setBusy("link");
    try {
      const fundo = await api.saveFundo(link.trim());
      setLink("");
      pull();
      toast(f(t.fundos.saved, { name: fundo.name }));
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setBusy("");
    }
  };

  const fetchStock = async (item: StockItem) => {
    setBusy(item.id);
    try {
      await api.fetchStock(item.id);
      pull();
      toast(f(t.fundos.saved, { name: item.name }));
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setBusy("");
    }
  };

  const remove = async (id: string) => {
    try {
      await api.deleteFundo(id);
      pull();
    } catch (error) {
      toast((error as Error).message);
    }
  };

  return (
    <>
      <Topbar title={t.fundos.title}>
        <span className="dim" style={{ fontSize: 12 }}>{t.fundos.subtitle}</span>
      </Topbar>

      <div className="grid" style={{ gap: 14 }}>
        <section className="panel">
          <div className="panel-head">
            <span className="label">{t.fundos.folderTitle}</span>
          </div>
          <div className="panel-body grid" style={{ gap: 10 }}>
            {/* Arrastar para uma pasta é menos fricção que qualquer
                formulário, e o arquivo é lido de onde está: copiar um gameplay
                de duas horas para "guardar" duplicaria gigabytes por nada. */}
            <p className="dim" style={{ fontSize: 12.5, lineHeight: 1.6,
                                        margin: 0 }}>
              {t.fundos.folderExplain}
            </p>
            <code className="mono" style={{ fontSize: 12,
                                            overflowWrap: "anywhere" }}>
              {folder || "…"}
            </code>
            <button className="btn sm ghost" onClick={pull}
                    style={{ justifySelf: "start" }}>
              {t.fundos.rescan}
            </button>
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <span className="label">{t.fundos.linkTitle}</span>
          </div>
          <div className="panel-body grid" style={{ gap: 10 }}>
            <Field label={t.fundos.link} hint={t.fundos.linkHint}>
              <div className="row" style={{ gap: 8 }}>
                <input className="input" placeholder="https://youtu.be/…"
                       value={link} onChange={(e) => setLink(e.target.value)}
                       onKeyDown={(e) => { if (e.key === "Enter") save(); }} />
                <button className="btn primary" onClick={save}
                        disabled={busy === "link"}>
                  {busy === "link" ? t.fundos.saving : t.fundos.save}
                </button>
              </div>
            </Field>
          </div>
        </section>

        {catalog.length ? (
          <section className="panel">
            <div className="panel-head">
              <span className="label">{t.fundos.catalogTitle}</span>
            </div>
            <div className="panel-body grid" style={{ gap: 10 }}>
              <p className="dim" style={{ fontSize: 12.5, lineHeight: 1.6,
                                          margin: 0 }}>
                {t.fundos.catalogExplain}
              </p>
              {catalog.map((item) => (
                <div key={item.id} className="row spread wrap"
                     style={{ gap: 10, alignItems: "center" }}>
                  <div className="grid" style={{ gap: 2, minWidth: 0 }}>
                    <b style={{ fontSize: 13 }}>{item.name}</b>
                    <span className="dimmer" style={{ fontSize: 11.5 }}>
                      {item.declared}
                    </span>
                  </div>
                  {item.saved ? (
                    <span className="tag" data-tone="ok">{t.fundos.here}</span>
                  ) : (
                    <button className="btn sm" onClick={() => fetchStock(item)}
                            disabled={Boolean(busy)}>
                      {busy === item.id ? t.fundos.downloading : t.fundos.download}
                    </button>
                  )}
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {fundos.length ? (
          <section className="panel">
            <div className="panel-head">
              <span className="label">{t.fundos.savedTitle}</span>
            </div>
            <div className="panel-body grid" style={{ gap: 8 }}>
              {fundos.map((item) => (
                <div key={item.id} className="row spread wrap"
                     style={{ gap: 10, alignItems: "center" }}>
                  <div className="grid" style={{ gap: 2, minWidth: 0 }}>
                    <b style={{ fontSize: 13, overflowWrap: "anywhere" }}>
                      {item.name}
                    </b>
                    <span className="dimmer mono" style={{ fontSize: 11.5 }}>
                      {minutes(item.seconds)} · {item.size_mb} MB ·{" "}
                      {item.origem === "pasta" ? t.fundos.fromFolder
                        : t.fundos.downloaded}
                    </span>
                  </div>
                  {item.origem === "pasta" ? (
                    <span className="dimmer" style={{ fontSize: 11.5 }}>
                      {t.fundos.deleteInFolder}
                    </span>
                  ) : (
                    <button className="btn sm ghost"
                            onClick={() => remove(item.id)}>
                      {t.common.remove}
                    </button>
                  )}
                </div>
              ))}
            </div>
          </section>
        ) : null}
      </div>
      {node}
    </>
  );
}
