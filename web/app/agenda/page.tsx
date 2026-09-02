"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, PLATFORM_LABEL, type Account, type Schedule } from "@/lib/api";
import { StatusTag, Topbar, useToast } from "@/components/ui";

export default function Agenda() {
  const { toast, node } = useToast();
  const [items, setItems] = useState<Schedule[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);

  const pull = () => {
    api.schedules().then(setItems).catch(() => setItems([]));
    api.accounts().then(setAccounts).catch(() => setAccounts([]));
  };

  useEffect(() => {
    pull();
    const id = setInterval(pull, 6000);
    return () => clearInterval(id);
  }, []);

  const byDay = items.reduce<Record<string, Schedule[]>>((acc, item) => {
    const day = new Date(item.publish_at).toLocaleDateString("pt-BR", {
      weekday: "short", day: "2-digit", month: "short",
    });
    (acc[day] ||= []).push(item);
    return acc;
  }, {});

  return (
    <>
      <Topbar title="Agenda">
        <span className="label">{items.length} publicação(ões)</span>
      </Topbar>

      <div className="content grid" style={{ gap: 16 }}>
        {items.length === 0 ? (
          <div className="empty">
            Nada agendado. Abra um short concluído e use o painel <b>Publicar</b> para marcar data e hora.
          </div>
        ) : (
          Object.entries(byDay).map(([day, list]) => (
            <section className="panel" key={day}>
              <div className="panel-head">
                <span className="label">{day}</span>
                <div className="grow" />
                <span className="label">{list.length} item(ns)</span>
              </div>
              <table className="table">
                <thead>
                  <tr>
                    <th>Horário</th>
                    <th>Plataforma</th>
                    <th>Conta</th>
                    <th>Short</th>
                    <th>Status</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {list.map((item) => (
                    <tr key={item.id}>
                      <td className="mono">
                        {new Date(item.publish_at).toLocaleTimeString("pt-BR", {
                          hour: "2-digit", minute: "2-digit",
                        })}
                      </td>
                      <td>{PLATFORM_LABEL[item.platform] ?? item.platform}</td>
                      <td className="dim">
                        {accounts.find((a) => a.id === item.account_id)?.display_name ?? "—"}
                      </td>
                      <td>
                        <Link href={`/job/${item.job_id}`} style={{ color: "var(--amber)" }}>
                          {item.job_id.slice(0, 12)}
                        </Link>
                      </td>
                      <td>
                        <StatusTag status={item.status} />
                        {item.error ? (
                          <div className="mono" style={{ fontSize: 10.5, color: "var(--err)" }}>
                            {item.error.slice(0, 70)}
                          </div>
                        ) : null}
                      </td>
                      <td>
                        <div className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                          <button
                            className="btn sm ghost"
                            onClick={() => api.runSchedule(item.id).then(() => { toast("Disparado."); pull(); })}
                          >
                            Publicar já
                          </button>
                          <button
                            className="btn sm danger"
                            onClick={() => api.deleteSchedule(item.id).then(pull)}
                          >
                            Remover
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ))
        )}
      </div>
      {node}
    </>
  );
}
