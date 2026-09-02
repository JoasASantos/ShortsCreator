"use client";

import { useEffect, useState } from "react";

import { api, type Account } from "@/lib/api";
import { Topbar, useToast } from "@/components/ui";

export default function Contas() {
  const { toast, node } = useToast();
  const [accounts, setAccounts] = useState<Account[]>([]);

  const pull = () => api.accounts().then(setAccounts).catch(() => setAccounts([]));
  useEffect(() => {
    pull();
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

  return (
    <>
      <Topbar title="Contas" />

      <div className="content grid" style={{ gap: 18 }}>
        <div className="two">
          <Connector
            name="YouTube Shorts"
            detail="Upload resumível via Data API v3. Aceita agendamento nativo (publishAt) e privacidade por vídeo."
            requirement="Requer client_secret OAuth em data/secrets/youtube_client_secret.json"
            onConnect={() => connect("youtube")}
          />
          <Connector
            name="TikTok"
            detail="Content Posting API v2. Contas sem auditoria enviam para a caixa de rascunhos do app; com auditoria aprovada, publica direto."
            requirement="Requer TIKTOK_CLIENT_KEY e TIKTOK_CLIENT_SECRET no .env"
            onConnect={() => connect("tiktok")}
          />
        </div>

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

function Connector({ name, detail, requirement, onConnect }: {
  name: string; detail: string; requirement: string; onConnect: () => void;
}) {
  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{name}</span>
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
        <p className="dim" style={{ margin: 0, lineHeight: 1.65, fontSize: 13 }}>{detail}</p>
        <p className="mono dimmer" style={{ margin: 0, fontSize: 11, lineHeight: 1.6 }}>
          {requirement}
        </p>
        <button className="btn" onClick={onConnect}>Conectar {name}</button>
      </div>
    </section>
  );
}
