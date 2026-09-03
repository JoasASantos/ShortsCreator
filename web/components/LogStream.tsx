"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type Event = { id: number; level: string; message: string; created_at: string };

export function LogStream({ jobId, live }: { jobId: string; live: boolean }) {
  const { t, time } = useI18n();
  const [events, setEvents] = useState<Event[]>([]);
  const box = useRef<HTMLDivElement>(null);
  const cursor = useRef(0);

  useEffect(() => {
    let stopped = false;
    let inFlight = false;
    cursor.current = 0;
    setEvents([]);
    const pull = async () => {
      if (inFlight) return; // evita corrida entre StrictMode e o interval
      inFlight = true;
      try {
        const batch = await api.events(jobId, cursor.current);
        if (stopped || batch.length === 0) return;
        cursor.current = batch[batch.length - 1].id;
        setEvents((prev) => {
          const seen = new Set(prev.map((e) => e.id));
          return [...prev, ...batch.filter((e) => !seen.has(e.id))];
        });
      } catch {
        /* silencioso: o log não deve derrubar a tela */
      } finally {
        inFlight = false;
      }
    };
    pull();
    if (!live) return;
    const id = setInterval(pull, 1600);
    return () => {
      stopped = true;
      clearInterval(id);
    };
  }, [jobId, live]);

  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" });
  }, [events.length]);

  return (
    <div className="log" ref={box}>
      {events.length === 0 ? <div className="dimmer">{t.job.logWaiting}</div> : null}
      {events.map((event) => (
        <div key={event.id}>
          <time>{time(event.created_at)}</time>
          <span data-lv={event.level}>{event.message}</span>
        </div>
      ))}
    </div>
  );
}
