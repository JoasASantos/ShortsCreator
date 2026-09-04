"use client";

import { Fragment } from "react";

import { useDocs, type Block } from "@/lib/i18n/docs";
import { RequirementsPanel } from "@/components/Requirements";
import { Topbar } from "@/components/ui";

/** Minimal markup accepted in the translated texts: **bold** and `code`.
 *  Avoids duplicating HTML across five languages without losing term emphasis. */
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

      {/* minmax(0, 1fr) and not the default `auto`: a grid track sized to its
          content is as wide as the widest line in the guide's code blocks,
          which pushes the whole page sideways on a phone instead of letting
          each block scroll inside itself. */}
      <div className="content grid"
           style={{ gap: 24, gridTemplateColumns: "minmax(0, 1fr)" }}>
        {/* The written guide covers every platform; the panel above it answers
            the only question that is about this machine. It comes first
            because someone opening this screen is usually mid-install. */}
        <RequirementsPanel />

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
