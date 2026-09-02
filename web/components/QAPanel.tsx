"use client";

import type { QAReport } from "@/lib/api";

const METRIC_LABEL: Record<string, string> = {
  width: "largura",
  height: "altura",
  aspect_ratio: "proporção",
  duration_s: "duração",
  fps: "fps",
  video_codec: "codec vídeo",
  audio_codec: "codec áudio",
  pix_fmt: "pix_fmt",
  size_mb: "tamanho (MB)",
  bitrate_kbps: "bitrate (kbps)",
  integrated_lufs: "loudness (LUFS)",
  true_peak_dbfs: "pico real (dBFS)",
  loudness_range: "faixa dinâmica",
  silence_blocks: "blocos de silêncio",
  cropdetect: "área útil",
  overlay_count: "sobreposições",
  caption_coverage: "cobertura legenda",
  sar: "SAR",
  sample_rate: "sample rate",
};

export function QAPanel({ report, onRerun }: { report: QAReport; onRerun?: () => void }) {
  const tone = report.passed ? "var(--ok)" : report.score >= 60 ? "var(--warn)" : "var(--err)";

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">Auditoria de formato</span>
        <div className="grow" />
        <span className="tag" data-tone={report.passed ? "ok" : "err"}>
          <i className="dot" />
          {report.passed ? "aprovado" : "reprovado"}
        </span>
        {onRerun ? (
          <button className="btn sm ghost" onClick={onRerun}>
            Reauditar
          </button>
        ) : null}
      </div>

      <div className="panel-body grid" style={{ gap: 16 }}>
        <div className="row" style={{ gap: 18 }}>
          <div className="score" style={{ borderColor: tone, color: tone }}>
            <b className="display">{report.score}</b>
          </div>
          <div className="grow grid" style={{ gap: 6 }}>
            <span className="label">Veredito</span>
            <p className="dim" style={{ margin: 0, lineHeight: 1.6 }}>
              {report.passed
                ? "O arquivo atende ao formato vertical exigido pelo YouTube Shorts e TikTok: proporção, codec, áudio e legenda dentro da área segura."
                : `${report.issues.filter((i) => i.severity === "fatal" || i.severity === "erro").length} problema(s) bloqueante(s) e ${report.issues.filter((i) => i.severity === "aviso").length} aviso(s). Corrija antes de publicar.`}
            </p>
          </div>
        </div>

        {report.issues.length > 0 ? (
          <div className="grid" style={{ gap: 7 }}>
            {report.issues.map((issue, index) => (
              <div className="issue" data-sev={issue.severity} key={`${issue.check}-${index}`}>
                <span className="label" style={{ paddingTop: 2, minWidth: 52 }}>
                  {issue.severity}
                </span>
                <div>
                  <div style={{ marginBottom: issue.fix ? 4 : 0 }}>{issue.message}</div>
                  {issue.fix ? (
                    <div className="mono dimmer" style={{ fontSize: 11.5 }}>
                      correção: {issue.fix}
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        ) : null}

        <div className="metrics">
          {Object.entries(report.metrics).map(([key, value]) => (
            <div className="metric" key={key}>
              <span className="label">{METRIC_LABEL[key] ?? key}</span>
              <b>{String(value)}</b>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
