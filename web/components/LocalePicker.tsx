"use client";

import { useEffect, useRef, useState } from "react";

import { LOCALES, LOCALE_NAMES, LOCALE_SHORT, useI18n } from "@/lib/i18n";

/** Seletor compacto de idioma. Fica no pé da barra lateral e, no celular,
 *  dentro do menu — em ambos os casos com área de toque de 40px. */
export function LocalePicker({ compact = false }: { compact?: boolean }) {
  const { locale, setLocale, t } = useI18n();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const fora = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", fora);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", fora);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  return (
    <div className="locale" ref={box}>
      <button
        type="button"
        className="locale-btn"
        aria-label={t.common.language}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="mono">{LOCALE_SHORT[locale]}</span>
        {compact ? null : <span className="locale-name">{LOCALE_NAMES[locale]}</span>}
        <span aria-hidden>{open ? "▴" : "▾"}</span>
      </button>
      {open ? (
        <div className="locale-menu" role="listbox">
          {LOCALES.map((l) => (
            <button
              key={l}
              type="button"
              role="option"
              aria-selected={l === locale}
              data-on={l === locale}
              onClick={() => { setLocale(l); setOpen(false); }}
            >
              <span className="mono" style={{ width: 24 }}>{LOCALE_SHORT[l]}</span>
              {LOCALE_NAMES[l]}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
