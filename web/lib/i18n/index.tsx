"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { en } from "./en";
import { es } from "./es";
import { ptBR, type Dictionary } from "./pt-BR";
import { ru } from "./ru";
import { zh } from "./zh";
import {
  INTL_LOCALE, LOCALES, LOCALE_NAMES, LOCALE_SHORT, NARRATION_LANGUAGE, type Locale,
} from "./types";

export { LOCALES, LOCALE_NAMES, LOCALE_SHORT, NARRATION_LANGUAGE };
export type { Locale, Dictionary };

const DICTIONARIES: Record<Locale, Dictionary> = {
  "pt-BR": ptBR,
  en,
  es,
  ru,
  zh,
};

const STORAGE_KEY = "shortscreator.locale";
const DEFAULT_LOCALE: Locale = "pt-BR";

/** Detects the language from the browser; falls back to Portuguese with no match. */
function detectLocale(): Locale {
  if (typeof navigator === "undefined") return DEFAULT_LOCALE;
  for (const candidate of navigator.languages ?? [navigator.language]) {
    const tag = (candidate || "").toLowerCase();
    if (tag.startsWith("pt")) return "pt-BR";
    if (tag.startsWith("es")) return "es";
    if (tag.startsWith("ru")) return "ru";
    if (tag.startsWith("zh")) return "zh";
    if (tag.startsWith("en")) return "en";
  }
  return DEFAULT_LOCALE;
}

function readStored(): Locale | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value && (LOCALES as readonly string[]).includes(value)
      ? (value as Locale) : null;
  } catch {
    return null;   // browser with storage blocked
  }
}

type Ctx = {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: Dictionary;
  /** Replaces {key} with the values passed in. */
  f: (template: string, vars: Record<string, string | number>) => string;
  /** Date/time in the chosen language's format. */
  date: (value: string | number | Date, opts?: Intl.DateTimeFormatOptions) => string;
  time: (value: string | number | Date) => string;
  dateTime: (value: string | number | Date) => string;
  /** Language the narration should use by default in this locale. */
  narrationLanguage: string;
};

const I18nContext = createContext<Ctx | null>(null);

export function I18nProvider({ children }: { children: React.ReactNode }) {
  // Starts at the default so the server HTML matches the client's; the real
  // language is applied in the first effect, avoiding a hydration error.
  const [locale, setLocaleState] = useState<Locale>(DEFAULT_LOCALE);

  useEffect(() => {
    const escolhido = readStored() ?? detectLocale();
    setLocaleState(escolhido);
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    try {
      window.localStorage.setItem(STORAGE_KEY, l);
    } catch {
      /* no storage: the choice only holds for this session */
    }
  }, []);

  const t = DICTIONARIES[locale];
  const intl = INTL_LOCALE[locale];

  const f = useCallback(
    (template: string, vars: Record<string, string | number>) =>
      template.replace(/\{(\w+)\}/g, (_, key) =>
        key in vars ? String(vars[key]) : `{${key}}`),
    [],
  );

  const date = useCallback(
    (value: string | number | Date, opts?: Intl.DateTimeFormatOptions) =>
      new Date(value).toLocaleDateString(intl, opts),
    [intl],
  );
  const time = useCallback(
    (value: string | number | Date) =>
      new Date(value).toLocaleTimeString(intl, { hour: "2-digit", minute: "2-digit" }),
    [intl],
  );
  const dateTime = useCallback(
    (value: string | number | Date) => new Date(value).toLocaleString(intl),
    [intl],
  );

  return (
    <I18nContext.Provider
      value={{ locale, setLocale, t, f, date, time, dateTime,
               narrationLanguage: NARRATION_LANGUAGE[locale] }}
    >
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n(): Ctx {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n precisa estar dentro de <I18nProvider>");
  return ctx;
}

/** Shortcut for code that only needs the dictionary. */
export function useT(): Dictionary {
  return useI18n().t;
}
