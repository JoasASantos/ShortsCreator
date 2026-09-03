"use client";

import { useEffect, useState } from "react";

import { api, PLATFORM_LABEL, type Account, type Connector } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { TableWrap, Topbar, useToast } from "@/components/ui";

// the category order does not change with the language; only the label comes from the dictionary
const CATEGORY_ORDER: Connector["category"][] = [
  "publicacao", "notificacao", "video", "avatar", "voz", "broll",
];

export default function Contas() {
  const { t, f, dateTime } = useI18n();
  const { toast, node } = useToast();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [loaded, setLoaded] = useState(false);

  const pull = () => {
    api.accounts().then(setAccounts).catch(() => setAccounts([]));
    api.connectors().then(setConnectors).catch(() => setConnectors([])).finally(() => setLoaded(true));
  };
  useEffect(() => { pull(); }, []);

  // the YouTube/TikTok OAuth callback comes back here with ?connected= or ?error=
  // — without this, a failure exchanging the code for a token passed silently.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("connected");
    const error = params.get("error");
    if (connected) {
      toast(f(t.accounts.connectedToast, { name: PLATFORM_LABEL[connected] ?? connected }));
    }
    if (error) toast(f(t.accounts.connectFailed, { message: error }));
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
      <Topbar title={t.accounts.title} />

      <div className="content grid" style={{ gap: 22 }}>
        {loaded && grouped.map(({ cat, items }) => (
          <section key={cat} className="grid" style={{ gap: 10 }}>
            <span className="label dim">{t.accounts.categories[cat]}</span>
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
            <span className="label">{t.accounts.connected}</span>
          </div>
          {accounts.length === 0 ? (
            <div className="panel-body">
              <div className="empty" style={{ border: 0, padding: "26px 0" }}>
                {t.accounts.noneConnected}
              </div>
            </div>
          ) : (
            <TableWrap>
              <table className="table">
                <thead>
                  <tr>
                    <th>{t.accounts.platform}</th>
                    <th>{t.accounts.account}</th>
                    <th>{t.accounts.connectedAt}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {accounts.map((account) => (
                    <tr key={account.id}>
                      <td>
                        <span className="tag" data-tone="amber">
                          {PLATFORM_LABEL[account.platform] ?? account.platform}
                        </span>
                      </td>
                      <td>{account.display_name}</td>
                      <td className="mono dim">{dateTime(account.created_at)}</td>
                      <td style={{ textAlign: "right" }}>
                        <button
                          className="btn sm danger"
                          onClick={() => api.deleteAccount(account.id).then(pull)}
                        >
                          {t.accounts.disconnect}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
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
  const { t, f } = useI18n();
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
      toast(f(t.accounts.saved, { name: connector.name }));
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
      toast(f(t.accounts.cleared, { name: connector.name }));
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
          {planned && <span className="tag">{t.accounts.comingSoon}</span>}
          {connector.configured && (
            <span className="tag" data-tone="amber">
              {connector.source === "painel" ? t.accounts.configured : t.accounts.viaEnv}
            </span>
          )}
          {connector.accounts > 0 && (
            <span className="tag" data-tone="amber">
              {f(t.accounts.accountCount, { n: connector.accounts })}
            </span>
          )}
        </div>
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
        <p className="dim" style={{ margin: 0, lineHeight: 1.65, fontSize: 13 }}>{connector.detail}</p>
        <p className="mono dimmer" style={{ margin: 0, fontSize: 11, lineHeight: 1.6 }}>
          {f(t.accounts.requires, { what: connector.requirement })}
          {" — "}
          <a href={connector.docs} target="_blank" rel="noreferrer" style={{ color: "inherit" }}>
            {t.common.docs} ↗
          </a>
        </p>

        {planned ? (
          <p className="dimmer" style={{ margin: 0, fontSize: 12 }}>
            {t.accounts.plannedNote}
          </p>
        ) : connector.auth === "oauth" && connector.fields.length === 0 ? (
          <button className="btn" onClick={onConnectOAuth}>
            {f(t.accounts.connect, { name: connector.name })}
          </button>
        ) : (
          <>
            <div className="grid" style={{ gap: 8 }}>
              {connector.fields.map((field) => (
                <div className="field" key={field.key}>
                  <label className="dim" style={{ fontSize: 11.5 }}>{field.label}</label>
                  <input
                    className="input"
                    type={field.secret ? "password" : "text"}
                    placeholder={field.filled
                      ? t.accounts.fieldSaved
                      : f(t.accounts.fieldPlaceholder, { env: field.env })}
                    value={values[field.key] ?? ""}
                    onChange={(e) => set(field.key, e.target.value)}
                  />
                </div>
              ))}
            </div>
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <button className="btn sm" disabled={saving} onClick={save}>
                {saving ? t.common.saving : t.common.save}
              </button>
              {connector.testable && (
                <button className="btn sm ghost" disabled={testing || !connector.configured} onClick={test}>
                  {testing ? t.common.testing : t.common.test}
                </button>
              )}
              {connector.configured && connector.source === "painel" && (
                <button className="btn sm ghost" onClick={clear}>{t.common.remove}</button>
              )}
              {connector.auth === "oauth" && onConnectOAuth && (
                <button className="btn sm" onClick={onConnectOAuth}>{t.accounts.connectAccount}</button>
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
