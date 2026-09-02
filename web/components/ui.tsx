"use client";

import { useEffect, useState } from "react";

export function Topbar({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <header className="topbar">
      <h2>{title}</h2>
      <div className="grow" />
      {children}
    </header>
  );
}

export function StatusTag({ status }: { status: string }) {
  const map: Record<string, { tone: string; text: string }> = {
    queued: { tone: "amber", text: "na fila" },
    running: { tone: "run", text: "processando" },
    done: { tone: "ok", text: "pronto" },
    error: { tone: "err", text: "erro" },
    pending: { tone: "amber", text: "agendado" },
    publishing: { tone: "run", text: "enviando" },
    published: { tone: "ok", text: "publicado" },
    // planos de lote: analisar é demorado e "rendered" não é "publicado"
    analisando: { tone: "run", text: "transcrevendo" },
    ready: { tone: "amber", text: "trechos prontos" },
    rendered: { tone: "ok", text: "shorts gerados" },
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
