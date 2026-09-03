export const LOCALES = ["pt-BR", "en", "es", "ru", "zh"] as const;
export type Locale = (typeof LOCALES)[number];

export const LOCALE_NAMES: Record<Locale, string> = {
  "pt-BR": "Português",
  en: "English",
  es: "Español",
  ru: "Русский",
  zh: "中文",
};

/** Bandeira/rótulo curto para o seletor compacto da barra lateral. */
export const LOCALE_SHORT: Record<Locale, string> = {
  "pt-BR": "PT",
  en: "EN",
  es: "ES",
  ru: "RU",
  zh: "中",
};

/** Idioma que a narração assume por padrão em cada locale da interface.
 *  Trocar a interface para espanhol não deve obrigar a narrar em português. */
export const NARRATION_LANGUAGE: Record<Locale, string> = {
  "pt-BR": "pt-BR",
  en: "en-US",
  es: "es-ES",
  ru: "ru-RU",
  zh: "zh-CN",
};

/** Locale do Intl para datas e números. */
export const INTL_LOCALE: Record<Locale, string> = {
  "pt-BR": "pt-BR",
  en: "en-US",
  es: "es-ES",
  ru: "ru-RU",
  zh: "zh-CN",
};
