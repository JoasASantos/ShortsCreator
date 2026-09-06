"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { LocalePicker } from "@/components/LocalePicker";
import { api, type Health } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

/** Which categories the person collapsed, remembered between visits.
 *  Guarded on both sides: a private window makes the accessor itself throw. */
const NAV_KEY = "shortscreator.nav";

function readCollapsed(): Record<string, boolean> {
  try {
    const raw = window.localStorage.getItem(NAV_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : null;
    if (!parsed || typeof parsed !== "object") return {};
    // only the flags we understand survive a hand-edited or stale value
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>)
        .filter(([, v]) => v === true)
        .map(([k]) => [k, true]),
    );
  } catch {
    return {};   // no storage: every category simply starts open
  }
}

function saveCollapsed(value: Record<string, boolean>): void {
  try {
    window.localStorage.setItem(NAV_KEY, JSON.stringify(value));
  } catch {
    /* no storage: the choice holds for this session only */
  }
}

export function Rail({ open = false }: { open?: boolean }) {
  const path = usePathname();
  const { t } = useI18n();
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    const tick = () => api.health().then(setHealth).catch(() => setHealth(null));
    tick();
    const id = setInterval(tick, 8000);
    return () => clearInterval(id);
  }, []);

  // the dashboard is the home, so it sits above the categories
  const home = { href: "/", label: t.nav.dashboard };

  const groups = [
    {
      id: "create",
      label: t.nav.groups.create,
      items: [
        { href: "/novo", label: t.nav.new },
        { href: "/filmes", label: t.nav.filmStudio },
        { href: "/lote", label: t.nav.batch },
        { href: "/tendencias", label: t.nav.trends },
      ],
    },
    {
      id: "myVideo",
      label: t.nav.groups.myVideo,
      items: [
        { href: "/reels", label: t.nav.reelsEditor },
        { href: "/cortes", label: t.nav.liveCuts },
        { href: "/avatar", label: t.nav.avatar },
      ],
    },
    {
      id: "publish",
      label: t.nav.groups.publish,
      items: [
        { href: "/agenda", label: t.nav.schedule },
        { href: "/desempenho", label: t.nav.performance },
      ],
    },
    {
      id: "configure",
      label: t.nav.groups.configure,
      items: [
        { href: "/vozes", label: t.nav.voices },
        { href: "/contas", label: t.nav.accounts },
        { href: "/geradores", label: t.nav.generators },
        { href: "/docs", label: t.nav.install },
      ],
    },
  ];

  const isActive = (href: string) =>
    href === "/" ? path === "/" : path.startsWith(href);

  const activeGroup = groups.find((g) => g.items.some((i) => isActive(i.href)))?.id;

  // Starts empty on purpose: the server HTML has to match the first client
  // paint, so what the browser remembered is applied in the effect below.
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  useEffect(() => { setCollapsed(readCollapsed()); }, []);

  // the category holding the current route expands itself
  useEffect(() => {
    if (!activeGroup) return;
    setCollapsed((prev) => {
      if (!prev[activeGroup]) return prev;
      const next = { ...prev };
      delete next[activeGroup];
      saveCollapsed(next);
      return next;
    });
  }, [activeGroup]);

  const toggleGroup = (id: string) => {
    setCollapsed((prev) => {
      const next = { ...prev };
      if (next[id]) delete next[id];
      else next[id] = true;
      saveCollapsed(next);
      return next;
    });
  };

  const authLabel = health?.llm_auth
    ? ({ assinatura: t.rail.subscription, "chave de API": t.rail.apiKey,
         local: t.rail.local, misto: t.rail.mixed } as Record<string, string>)[health.llm_auth]
      ?? health.llm_auth
    : undefined;

  return (
    <aside className="rail" data-open={open}>
      <div className="brand">
        <h1>
          Shorts<br />
          <em>Creator</em>
        </h1>
      </div>

      <nav className="nav">
        <Link href={home.href} data-active={isActive(home.href)}>
          {home.label}
        </Link>

        {groups.map((group) => {
          const expanded = !collapsed[group.id];
          return (
            <div className="nav-group" key={group.id}>
              <button
                type="button"
                className="nav-group-head"
                aria-expanded={expanded}
                aria-controls={`nav-${group.id}`}
                data-has-active={group.id === activeGroup}
                onClick={() => toggleGroup(group.id)}
              >
                <span className="k" aria-hidden>▸</span>
                {group.label}
              </button>
              <div className="nav-group-items" id={`nav-${group.id}`} hidden={!expanded}>
                {group.items.map((item) => (
                  <Link key={item.href} href={item.href} data-active={isActive(item.href)}>
                    {item.label}
                  </Link>
                ))}
              </div>
            </div>
          );
        })}
      </nav>

      <div className="rail-foot">
        <Stat label={t.rail.ffmpeg} ok={health?.ffmpeg} readyText={t.common.ready}
              offText={t.common.off} />
        <Stat label={t.rail.ytdlp} ok={health?.ytdlp} readyText={t.common.ready}
              offText={t.common.off} />
        <Stat label={t.rail.llm} ok={health?.llm_key_set} value={health?.llm_provider}
              readyText={t.common.ready} offText={t.common.off} />
        {health?.llm_chain?.map((step, i) => (
          <Stat
            key={`${step.provider}:${step.model}`}
            label={i === 0 ? `↳ ${t.rail.primary}` : `↳ ${t.rail.fallback} ${i}`}
            ok={step.ready}
            // The model id alone said nothing about whether this link will be
            // tried, so a skipped one read exactly like a working one. The
            // backend's reason is the tooltip, verbatim.
            value={step.ready ? step.model : `${step.model} · ${t.rail.skipped}`}
            title={step.reason}
            readyText={t.common.ready}
            offText={t.common.off}
          />
        ))}
        <Stat label={t.rail.auth} ok={health?.llm_key_set} value={authLabel}
              readyText={t.common.ready} offText={t.common.off} />
        <Stat label={t.rail.tts} ok={!!health?.tts_provider} value={health?.tts_provider}
              readyText={t.common.ready} offText={t.common.off} />
        <Stat label={t.rail.broll} ok={health?.broll_ready} readyText={t.common.ready}
              offText={t.common.off} />
        <div className="stat-line" style={{ marginTop: 6 }}>
          <span>{t.common.format}</span>
          <b>1080×1920</b>
        </div>
        <div className="stat-line">
          <span>{t.common.queue}</span>
          <b>{health?.queue ?? "—"}</b>
        </div>
        <div style={{ marginTop: 10 }}>
          <LocalePicker />
        </div>
      </div>
    </aside>
  );
}

function Stat({ label, ok, value, title, readyText, offText }: {
  label: string; ok?: boolean; value?: string; title?: string;
  readyText: string; offText: string;
}) {
  return (
    <div className="stat-line" title={title || undefined}>
      <span>{label}</span>
      <b style={{ color: ok ? "var(--ok)" : "var(--ink-3)" }}>
        {value ? value : ok ? readyText : offText}
      </b>
    </div>
  );
}
