"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { api, type Health } from "@/lib/api";

const LINKS = [
  { href: "/", label: "Painel", key: "01" },
  { href: "/novo", label: "Novo short", key: "02" },
  { href: "/lote", label: "Lote", key: "03" },
  { href: "/tendencias", label: "Tendências", key: "04" },
  { href: "/agenda", label: "Agenda", key: "05" },
  { href: "/desempenho", label: "Desempenho", key: "06" },
  { href: "/vozes", label: "Vozes", key: "07" },
  { href: "/contas", label: "Contas", key: "08" },
  { href: "/docs", label: "Instalação", key: "09" },
];

export function Rail() {
  const path = usePathname();
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    const tick = () => api.health().then(setHealth).catch(() => setHealth(null));
    tick();
    const id = setInterval(tick, 8000);
    return () => clearInterval(id);
  }, []);

  return (
    <aside className="rail">
      <div className="brand">
        <h1>
          Shorts<br />
          <em>Creator</em>
        </h1>
      </div>

      <nav className="nav">
        {LINKS.map((link) => (
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
        <Stat label="FFmpeg" ok={health?.ffmpeg} />
        <Stat label="yt-dlp" ok={health?.ytdlp} />
        <Stat label="LLM" ok={health?.llm_key_set} value={health?.llm_provider} />
        {health?.llm_chain?.map((step, i) => (
          <Stat
            key={`${step.provider}:${step.model}`}
            label={i === 0 ? "↳ principal" : `↳ reserva ${i}`}
            ok={step.ready}
            value={step.model}
          />
        ))}
        <Stat label="Auth" ok={health?.llm_key_set} value={health?.llm_auth} />
        <Stat label="TTS" ok={!!health?.tts_provider} value={health?.tts_provider} />
        <Stat label="B-roll" ok={health?.broll_ready} />
        <div className="stat-line" style={{ marginTop: 6 }}>
          <span>FORMATO</span>
          <b>1080×1920</b>
        </div>
        <div className="stat-line">
          <span>FILA</span>
          <b>{health?.queue ?? "—"}</b>
        </div>
      </div>
    </aside>
  );
}

function Stat({ label, ok, value }: { label: string; ok?: boolean; value?: string }) {
  return (
    <div className="stat-line">
      <span>{label}</span>
      <b style={{ color: ok ? "var(--ok)" : "var(--ink-3)" }}>
        {value ? value : ok ? "pronto" : "off"}
      </b>
    </div>
  );
}
