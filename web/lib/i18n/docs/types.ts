/** A página de instalação é longa e cheia de código. Em vez de espalhar
 *  centenas de strings pelo dicionário principal, ela é descrita como uma
 *  estrutura de blocos: cada idioma preenche o mesmo esqueleto, e a tela
 *  apenas percorre e renderiza. */

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
