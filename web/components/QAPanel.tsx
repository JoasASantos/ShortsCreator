"use client";

import type { QAReport } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export function QAPanel({ report, onRerun }: { report: QAReport; onRerun?: () => void }) {
  const { t, f } = useI18n();
  const tone = report.passed ? "var(--ok)" : report.score >= 60 ? "var(--warn)" : "var(--err)";
  const metricLabel = (key: string) =>
    t.qa.metrics[key as keyof typeof t.qa.metrics] ?? key;
  const severityLabel = (severity: string) =>
    t.qa.severity[severity as keyof typeof t.qa.severity] ?? severity;

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.qa.title}</span>
        <div className="grow" />
        <span className="tag" data-tone={report.passed ? "ok" : "err"}>
          <i className="dot" />
          {report.passed ? t.qa.passed : t.qa.failed}
        </span>
        {onRerun ? (
          <button className="btn sm ghost" onClick={onRerun}>
            {t.qa.rerun}
          </button>
        ) : null}
      </div>

      <div className="panel-body grid" style={{ gap: 16 }}>
        <div className="row wrap" style={{ gap: 18 }}>
          <div className="score" style={{ borderColor: tone, color: tone }}>
            <b className="display">{report.score}</b>
          </div>
          <div className="grow grid" style={{ gap: 6 }}>
            <span className="label">{t.qa.verdict}</span>
            <p className="dim" style={{ margin: 0, lineHeight: 1.6 }}>
              {report.passed
                ? t.qa.verdictPassed
                : f(t.qa.verdictFailed, {
                    blocking: report.issues.filter(
                      (i) => i.severity === "fatal" || i.severity === "erro").length,
                    warnings: report.issues.filter((i) => i.severity === "aviso").length,
                  })}
            </p>
          </div>
        </div>

        {report.issues.length > 0 ? (
          <div className="grid" style={{ gap: 7 }}>
            {report.issues.map((issue, index) => (
              <div className="issue" data-sev={issue.severity} key={`${issue.check}-${index}`}>
                <span className="label" style={{ paddingTop: 2, minWidth: 52 }}>
                  {severityLabel(issue.severity)}
                </span>
                <div>
                  <div style={{ marginBottom: issue.fix ? 4 : 0 }}>{issue.message}</div>
                  {issue.fix ? (
                    <div className="mono dimmer" style={{ fontSize: 11.5 }}>
                      {t.qa.fix}: {issue.fix}
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
              <span className="label">{metricLabel(key)}</span>
              <b>{String(value)}</b>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
