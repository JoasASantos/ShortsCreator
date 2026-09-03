/** The install page is long and full of code. Instead of scattering hundreds
 *  of strings across the main dictionary, it is described as a block
 *  structure: each language fills in the same skeleton, and the screen just
 *  walks it and renders. */

export type Block =
  | { kind: "p"; text: string }
  | { kind: "code"; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "h"; text: string };

export type Section = {
  title: string;
  blocks: Block[];
};

export type DocsPage = {
  title: string;
  intro: string;
  sections: Section[];
};
