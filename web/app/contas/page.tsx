"use client";

import { useEffect, useState } from "react";

import { api, type Account, type Connector } from "@/lib/api";
import { Topbar, useToast } from "@/components/ui";

const CATEGORY_LABEL: Record<Connector["category"], string> = {
  publicacao: "Publicação",
  video: "Geração de vídeo",
  avatar: "Avatar falante",
  voz: "Voz",
  broll: "Banco de b-roll",
};

const CATEGORY_ORDER: Connector["category"][] = [
  "publicacao", "video", "avatar", "voz", "broll",
];

export default function Contas() {
  const { toast, node } = useToast();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [loaded, setLoaded] = useState(false);

  const pull = () => {
    api.accounts().then(setAccounts).catch(() => setAccounts([]));
    api.connectors().then(setConnectors).catch(() => setConnectors([])).finally(() => setLoaded(true));
  };
  useEffect(() => { pull(); }, []);

  // callback OAuth do YouTube/TikTok volta pra cá com ?connected= ou ?error=
  // — sem isso, uma falha na troca do code por token passava em silêncio.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("connected");
    const error = params.get("error");
    if (connected) toast(`Conta ${connected === "youtube" ? "YouTube" : "TikTok"} conectada.`);
    if (error) toast(`Falha ao conectar: ${error}`);
    if (connected || error) window.history.replaceState({}, "", "/contas");
  }, []);

  const connect = async (platform: "youtube" | "tiktok") => {
    try {
      const { auth_url } =
        platform === "youtube" ? await api.youtubeAuth() : await api.tiktokAuth();
      window.location.href = auth_url;
    } catch (error) {
      toast((error as Error).message);
    }
  };

  const grouped = CATEGORY_ORDER.map((cat) => ({
    cat,
    items: connectors.filter((c) => c.category === cat),
  })).filter((g) => g.items.length > 0);

  return (
    <>
      <Topbar title="Contas" />

      <div className="content grid" style={{ gap: 22 }}>
        {loaded && grouped.map(({ cat, items }) => (
          <section key={cat} className="grid" style={{ gap: 10 }}>
            <span className="label dim">{CATEGORY_LABEL[cat]}</span>
            <div className="two" style={{ alignItems: "start" }}>
              {items.map((connector) => (
                <ConnectorCard
                  key={connector.id}
                  connector={connector}
                  onConnectOAuth={
                    connector.auth === "oauth" ? () => connect(connector.id as "youtube" | "tiktok") : undefined
                  }
                  onSaved={pull}
                  toast={toast}
                />
              ))}
            </div>
          </section>
        ))}

        <section className="panel">
          <div className="panel-head">
            <span className="label">Conectadas</span>
          </div>
          {accounts.length === 0 ? (
            <div className="panel-body">
              <div className="empty" style={{ border: 0, padding: "26px 0" }}>
                Nenhuma conta conectada ainda.
              </div>
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Plataforma</th>
                  <th>Conta</th>
                  <th>Conectada em</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {accounts.map((account) => (
                  <tr key={account.id}>
                    <td>
                      <span className="tag" data-tone="amber">
                        {account.platform === "youtube" ? "YouTube" : "TikTok"}
                      </span>
                    </td>
                    <td>{account.display_name}</td>
                    <td className="mono dim">
                      {new Date(account.created_at).toLocaleString("pt-BR")}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <button
                        className="btn sm danger"
                        onClick={() => api.deleteAccount(account.id).then(pull)}
                      >
                        Desconectar
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
      {node}
    </>
  );
}

function ConnectorCard({ connector, onConnectOAuth, onSaved, toast }: {
  connector: Connector;
  onConnectOAuth?: () => void;
  onSaved: () => void;
  toast: (msg: string) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const planned = connector.status === "planejado";

  const set = (key: string, v: string) => setValues((prev) => ({ ...prev, [key]: v }));

  const save = async () => {
    setSaving(true);
    setTestResult(null);
    try {
      await api.saveConnector(connector.id, values);
      setValues({});
      toast(`${connector.name} salvo.`);
      onSaved();
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const clear = async () => {
    try {
      await api.clearConnector(connector.id);
      toast(`${connector.name} desconfigurado.`);
      onSaved();
    } catch (error) {
      toast((error as Error).message);
    }
  };

  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testConnector(connector.id);
      setTestResult(result);
    } catch (error) {
      setTestResult({ ok: false, message: (error as Error).message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <section className="panel" style={{ opacity: planned ? 0.72 : 1 }}>
      <div className="panel-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span className="label">{connector.name}</span>
        <div className="row" style={{ gap: 6 }}>
          {planned && <span className="tag">Em breve</span>}
          {connector.configured && (
            <span className="tag" data-tone="amber">
              {connector.source === "painel" ? "configurado" : "via .env"}
            </span>
          )}
          {connector.accounts > 0 && (
            <span className="tag" data-tone="amber">{connector.accounts} conta{connector.accounts > 1 ? "s" : ""}</span>
          )}
        </div>
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
        <p className="dim" style={{ margin: 0, lineHeight: 1.65, fontSize: 13 }}>{connector.detail}</p>
        <p className="mono dimmer" style={{ margin: 0, fontSize: 11, lineHeight: 1.6 }}>
          Requer {connector.requirement}
          {" — "}
          <a href={connector.docs} target="_blank" rel="noreferrer" style={{ color: "inherit" }}>docs ↗</a>
        </p>

        {planned ? (
          <p className="dimmer" style={{ margin: 0, fontSize: 12 }}>
            Conector previsto — ainda sem chamada de publicação/geração implementada.
          </p>
        ) : connector.auth === "oauth" && connector.fields.length === 0 ? (
          <button className="btn" onClick={onConnectOAuth}>Conectar {connector.name}</button>
        ) : (
          <>
            <div className="grid" style={{ gap: 8 }}>
              {connector.fields.map((f) => (
                <div className="field" key={f.key}>
                  <label className="dim" style={{ fontSize: 11.5 }}>{f.label}</label>
                  <input
                    className="input"
                    type={f.secret ? "password" : "text"}
                    placeholder={f.filled ? "•••••••• (já salvo — digite para trocar)" : `cole aqui, ou defina ${f.env} no .env`}
                    value={values[f.key] ?? ""}
                    onChange={(e) => set(f.key, e.target.value)}
                  />
                </div>
              ))}
            </div>
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <button className="btn sm" disabled={saving} onClick={save}>
                {saving ? "Salvando…" : "Salvar"}
              </button>
              {connector.testable && (
                <button className="btn sm ghost" disabled={testing || !connector.configured} onClick={test}>
                  {testing ? "Testando…" : "Testar conexão"}
                </button>
              )}
              {connector.configured && connector.source === "painel" && (
                <button className="btn sm ghost" onClick={clear}>Remover</button>
              )}
              {connector.auth === "oauth" && onConnectOAuth && (
                <button className="btn sm" onClick={onConnectOAuth}>Conectar conta</button>
              )}
            </div>
            {testResult && (
              <p className="mono" style={{ margin: 0, fontSize: 11.5, color: testResult.ok ? "var(--amber)" : "var(--err)" }}>
                {testResult.ok ? "✓ " : "✗ "}{testResult.message}
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}
