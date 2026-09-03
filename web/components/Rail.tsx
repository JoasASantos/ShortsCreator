"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { LocalePicker } from "@/components/LocalePicker";
import { api, type Health } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

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

  const links = [
    { href: "/", label: t.nav.dashboard, key: "01" },
    { href: "/novo", label: t.nav.new, key: "02" },
    { href: "/lote", label: t.nav.batch, key: "03" },
    { href: "/tendencias", label: t.nav.trends, key: "04" },
    { href: "/agenda", label: t.nav.schedule, key: "05" },
    { href: "/desempenho", label: t.nav.performance, key: "06" },
    { href: "/vozes", label: t.nav.voices, key: "07" },
    { href: "/contas", label: t.nav.accounts, key: "08" },
    { href: "/docs", label: t.nav.install, key: "09" },
  ];

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
        {links.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            data-active={link.href === "/" ? path === "/" : path.startsWith(link.href)}
          >
            {link.label}
            <span className="k">{link.key}</span>
          </Link>
        ))}
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
            value={step.model}
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

function Stat({ label, ok, value, readyText, offText }: {
  label: string; ok?: boolean; value?: string; readyText: string; offText: string;
}) {
  return (
    <div className="stat-line">
      <span>{label}</span>
      <b style={{ color: ok ? "var(--ok)" : "var(--ink-3)" }}>
        {value ? value : ok ? readyText : offText}
      </b>
    </div>
  );
}
