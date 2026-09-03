"use client";

import { Fragment } from "react";

import { useDocs, type Block } from "@/lib/i18n/docs";
import { Topbar } from "@/components/ui";

/** Marcação mínima aceita nos textos traduzidos: **negrito** e `código`.
 *  Evita duplicar HTML em cinco idiomas sem perder o destaque dos termos. */
const INLINE = /\*\*([^*]+)\*\*|`([^`]+)`/g;

function inline(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  let cursor = 0;

  for (const match of text.matchAll(INLINE)) {
    const at = match.index ?? 0;
    if (at > cursor) parts.push(text.slice(cursor, at));
    parts.push(
      match[1] !== undefined
        ? <b key={at}>{match[1]}</b>
        : <code key={at}>{match[2]}</code>,
    );
    cursor = at + match[0].length;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));

  return parts.length === 1 ? parts[0] : parts;
}

function Piece({ block }: { block: Block }) {
  switch (block.kind) {
    case "h":
      return <h3>{inline(block.text)}</h3>;
    case "p":
      return <p>{inline(block.text)}</p>;
    case "code":
      return <pre><code>{block.text}</code></pre>;
    case "ul":
      return (
        <ul>
          {block.items.map((item, i) => <li key={i}>{inline(item)}</li>)}
        </ul>
      );
  }
}

export default function Docs() {
  const docs = useDocs();

  return (
    <>
      <Topbar title={docs.title}>
        <span className="label">{docs.intro}</span>
      </Topbar>

      <div className="content">
        <div className="prose">
          {docs.sections.map((section) => (
            <Fragment key={section.title}>
              <h3>{section.title}</h3>
              {section.blocks.map((block, i) => <Piece key={i} block={block} />)}
            </Fragment>
          ))}
        </div>
      </div>
    </>
  );
}
