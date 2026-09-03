"use client";

import { useEffect, useState } from "react";

import { useDrawer } from "@/components/Shell";
import { useI18n } from "@/lib/i18n";

export function Topbar({ title, children }: { title: string; children?: React.ReactNode }) {
  const { open, toggle } = useDrawer();
  const { t } = useI18n();
  return (
    <header className="topbar">
      <button
        type="button"
        className="menu-toggle"
        aria-label={t.common.language ? "Menu" : "Menu"}
        aria-expanded={open}
        onClick={toggle}
      >
        ☰
      </button>
      <h2>{title}</h2>
      <div className="grow" />
      {children}
    </header>
  );
}

export function StatusTag({ status }: { status: string }) {
  const { t } = useI18n();
  const map: Record<string, { tone: string; text: string }> = {
    queued: { tone: "amber", text: t.status.queued },
    running: { tone: "run", text: t.status.running },
    done: { tone: "ok", text: t.status.done },
    error: { tone: "err", text: t.status.error },
    pending: { tone: "amber", text: t.status.pending },
    publishing: { tone: "run", text: t.status.publishing },
    published: { tone: "ok", text: t.status.published },
    // planos de lote: analisar é demorado e "rendered" não é "publicado"
    analisando: { tone: "run", text: t.status.analyzing },
    ready: { tone: "amber", text: t.status.clipsReady },
    rendered: { tone: "ok", text: t.status.rendered },
  };
  const item = map[status] ?? { tone: "", text: status };
  return (
    <span className="tag" data-tone={item.tone}>
      <i className={`dot${status === "running" || status === "publishing" ? " pulse" : ""}`} />
      {item.text}
    </span>
  );
}

export function Toast({ message, onDone }: { message: string; onDone: () => void }) {
  useEffect(() => {
    const id = setTimeout(onDone, 4200);
    return () => clearTimeout(id);
  }, [message, onDone]);
  return <div className="toast">{message}</div>;
}

export function useToast() {
  const [message, setMessage] = useState("");
  const node = message ? <Toast message={message} onDone={() => setMessage("")} /> : null;
  return { toast: setMessage, node };
}

export function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <div className="field">
      <div className="row spread">
        <span className="label">{label}</span>
        {hint ? <span className="label" style={{ letterSpacing: "0.06em" }}>{hint}</span> : null}
      </div>
      {children}
    </div>
  );
}

export function Chips<T extends string>({ options, value, onChange }: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="chips">
      {options.map((option) => (
        <button
          type="button"
          key={option.value}
          className="chip"
          data-on={value === option.value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** Envolve tabelas largas para elas rolarem no celular em vez de estourar
 *  a largura da página. */
export function TableWrap({ children }: { children: React.ReactNode }) {
  return <div className="table-wrap">{children}</div>;
}
