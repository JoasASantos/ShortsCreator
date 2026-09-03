#!/usr/bin/env node
/**
 * Audit of the language dictionaries.
 *
 * TypeScript already guarantees that no KEY is missing — `Dictionary` is
 * inferred from pt-BR and the translations are typed with it. What the
 * compiler cannot see is the content: a key copied from Portuguese and never
 * translated, a `{x}` placeholder lost in translation (which would show up
 * literally on screen) or an empty text. That is what this script checks.
 *
 *   node lib/i18n/check.mjs
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const LOCALES = ["pt-BR", "en", "es", "ru", "zh"];
const REFERENCIA = "pt-BR";

// Words that give away Portuguese text left behind in a translation.
const MARCAS_PT = [
  "roteiro", "legenda", "gancho", "você", "não ", "está ", "são ",
  "publicação", "cadastr", "arquivo", "nenhum", "trecho", "fundo",
  "salvar", "excluir", "escolh", "servidor", "atualiz", "guardad",
];

/**
 * Words that are the same in Portuguese and in another language — identical
 * spelling is not a forgotten translation. Spanish and Portuguese share a
 * good deal of vocabulary.
 */
const HOMOGRAFOS = {
  es: ["idioma", "vídeo", "vídeo por ia", "cinema", "voz", "local", "total",
       "formato", "no", "editor", "audio", "modo", "nicho", "@tucanal"],
  en: ["local", "total", "editor", "audio", "no", "b-roll"],
  ru: [],
  zh: [],
};

function achatar(obj, prefixo = "", saida = {}) {
  for (const [k, v] of Object.entries(obj)) {
    const chave = prefixo ? `${prefixo}.${k}` : k;
    if (Array.isArray(v)) v.forEach((item, i) => { saida[`${chave}[${i}]`] = item; });
    else if (v && typeof v === "object") achatar(v, chave, saida);
    else saida[chave] = v;
  }
  return saida;
}

/** Reads the exported literal without having to compile TypeScript. */
function carregar(locale) {
  const fonte = readFileSync(join(AQUI, `${locale}.ts`), "utf8");
  const inicio = fonte.indexOf("= {");
  const corpo = fonte.slice(inicio + 2, fonte.lastIndexOf("};") + 1);
  return achatar(eval(`(${corpo})`));
}

const dicionarios = Object.fromEntries(LOCALES.map((l) => [l, carregar(l)]));
const base = dicionarios[REFERENCIA];
const chaves = Object.keys(base);
let falhas = 0;

console.log(`${chaves.length} chaves em ${REFERENCIA}\n`);

for (const locale of LOCALES) {
  const d = dicionarios[locale];
  const homografos = HOMOGRAFOS[locale] ?? [];

  const faltando = chaves.filter((k) => !(k in d));
  const sobrando = Object.keys(d).filter((k) => !chaves.includes(k));
  const vazias = chaves.filter((k) => d[k] === "" && base[k] !== "");

  // a {placeholder} lost in translation would show up literally on screen
  const placeholders = chaves.filter((k) => {
    const esperado = String(base[k] ?? "").match(/\{\w+\}/g) ?? [];
    const obtido = String(d[k] ?? "").match(/\{\w+\}/g) ?? [];
    return esperado.sort().join() !== obtido.sort().join();
  });

  const naoTraduzidas = locale === REFERENCIA ? [] : chaves.filter((k) => {
    if (d[k] !== base[k]) return false;                    // translated
    const valor = String(d[k] ?? "").toLowerCase().trim();
    if (!valor || homografos.includes(valor)) return false;
    return MARCAS_PT.some((marca) => valor.includes(marca));
  });

  const problemas = [
    faltando.length && `${faltando.length} faltando: ${faltando.slice(0, 5).join(", ")}`,
    sobrando.length && `${sobrando.length} sobrando: ${sobrando.slice(0, 5).join(", ")}`,
    vazias.length && `${vazias.length} vazia(s): ${vazias.slice(0, 5).join(", ")}`,
    placeholders.length &&
      `${placeholders.length} com placeholder divergente: ${placeholders.slice(0, 5).join(", ")}`,
    naoTraduzidas.length &&
      `${naoTraduzidas.length} em português: ${naoTraduzidas.slice(0, 6).join(", ")}`,
  ].filter(Boolean);

  if (problemas.length) {
    falhas += problemas.length;
    console.log(`FALHOU  ${locale}`);
    problemas.forEach((p) => console.log(`        ${p}`));
  } else {
    console.log(`ok      ${locale} — ${Object.keys(d).length} chaves`);
  }
}

if (falhas) {
  console.log(`\n${falhas} problema(s) encontrado(s).`);
  process.exit(1);
}
console.log("\nDicionários íntegros nos cinco idiomas.");
