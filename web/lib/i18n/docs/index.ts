import { useI18n } from "../index";
import type { Locale } from "../types";

import { docsEn } from "./en";
import { docsEs } from "./es";
import { docsPtBR } from "./pt-BR";
import { docsRu } from "./ru";
import type { DocsPage } from "./types";
import { docsZh } from "./zh";

export type { Block, Section, DocsPage } from "./types";

export const DOCS: Record<Locale, DocsPage> = {
  "pt-BR": docsPtBR,
  en: docsEn,
  es: docsEs,
  ru: docsRu,
  zh: docsZh,
};

/** Conteúdo da página de instalação no idioma escolhido na interface. */
export function useDocs(): DocsPage {
  return DOCS[useI18n().locale];
}
