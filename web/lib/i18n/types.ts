export const LOCALES = ["pt-BR", "en", "es", "ru", "zh"] as const;
export type Locale = (typeof LOCALES)[number];

export const LOCALE_NAMES: Record<Locale, string> = {
  "pt-BR": "Português",
  en: "English",
  es: "Español",
  ru: "Русский",
  zh: "中文",
};

/** Flag/short label for the sidebar's compact picker. */
export const LOCALE_SHORT: Record<Locale, string> = {
  "pt-BR": "PT",
  en: "EN",
  es: "ES",
  ru: "RU",
  zh: "中",
};

/** Language the narration defaults to in each interface locale.
 *  Switching the interface to Spanish must not force narration in Portuguese. */
export const NARRATION_LANGUAGE: Record<Locale, string> = {
  "pt-BR": "pt-BR",
  en: "en-US",
  es: "es-ES",
  ru: "ru-RU",
  zh: "zh-CN",
};

/** Intl locale for dates and numbers. */
export const INTL_LOCALE: Record<Locale, string> = {
  "pt-BR": "pt-BR",
  en: "en-US",
  es: "es-ES",
  ru: "ru-RU",
  zh: "zh-CN",
};
