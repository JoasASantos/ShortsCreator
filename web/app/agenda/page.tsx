"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, PLATFORM_LABEL, type Account, type Schedule } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { StatusTag, TableWrap, Topbar, useToast } from "@/components/ui";

export default function Agenda() {
  const { t, f, date, time } = useI18n();
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
    const day = date(item.publish_at, {
      weekday: "short", day: "2-digit", month: "short",
    });
    (acc[day] ||= []).push(item);
    return acc;
  }, {});

  return (
    <>
      <Topbar title={t.schedule.title}>
        <span className="label">{f(t.schedule.count, { n: items.length })}</span>
      </Topbar>

      <div className="content grid" style={{ gap: 16 }}>
        {items.length === 0 ? (
          <div className="empty">{t.schedule.empty}</div>
        ) : (
          Object.entries(byDay).map(([day, list]) => (
            <section className="panel" key={day}>
              <div className="panel-head">
                <span className="label">{day}</span>
                <div className="grow" />
                <span className="label">{list.length} {t.common.item}</span>
              </div>
              <TableWrap>
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t.schedule.time}</th>
                      <th>{t.schedule.platform}</th>
                      <th>{t.schedule.account}</th>
                      <th>{t.schedule.short}</th>
                      <th>{t.schedule.status}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((item) => (
                      <tr key={item.id}>
                        <td className="mono">{time(item.publish_at)}</td>
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
                              onClick={() =>
                                api.runSchedule(item.id).then(() => { toast(t.schedule.fired); pull(); })}
                            >
                              {t.schedule.publishNow}
                            </button>
                            <button
                              className="btn sm danger"
                              onClick={() => api.deleteSchedule(item.id).then(pull)}
                            >
                              {t.common.remove}
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </section>
          ))
        )}
      </div>
      {node}
    </>
  );
}
