"use client";

import { useCallback, useEffect, useState } from "react";

import { api, type ChainStep, type ModelsReport } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

/** Which model writes the script, and the chain that keeps it getting written.
 *
 *  A link that cannot be used is not a failure — the chain exists precisely so
 *  the next one answers, which is why a skipped link is dressed the same way an
 *  unconfigured provider is on this screen. The single red state here is
 *  `any_ready` false: then there is nothing left to fall through to. */
export function ModelPicker({ toast }: { toast: (message: string) => void }) {
  const { t, f } = useI18n();
  const [report, setReport] = useState<ModelsReport | null>(null);
  const [busy, setBusy] = useState(true);
  const [failure, setFailure] = useState("");

  // Every endpoint answers with the whole report, so the screen is repainted
  // from what the backend now holds instead of from a guess about the write.
  const run = useCallback(async (call: () => Promise<ModelsReport>, done = "") => {
    setBusy(true);
    try {
      setReport(await call());
      setFailure("");
      if (done) toast(done);
    } catch (error) {
      setFailure((error as Error).message);
    } finally {
      setBusy(false);
    }
  }, [toast]);

  useEffect(() => { run(() => api.models()); }, [run]);

  const chosen = report?.models.find((model) => model.name === report.chosen);
  // The link that will actually write: the first one nothing is wrong with.
  const running = report?.chain.find((step) => step.ready);

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.models.title}</span>
        <div className="grow" />
        <button className="btn sm ghost" disabled={busy}
                onClick={() => run(() => api.refreshModels(), t.models.refreshed)}>
          {/* "re-reading" only once there is something to re-read: on the very
              first load the panel is simply loading. */}
          {busy && report ? t.models.refreshing : t.models.refresh}
        </button>
      </div>

      <div className="panel-body grid" style={{ gap: 18 }}>
        <p className="dim" style={{ margin: 0, fontSize: 13, lineHeight: 1.65 }}>
          {t.models.intro}
        </p>

        {failure ? (
          <div className="issue" data-sev="fatal"
               style={{ gridTemplateColumns: "minmax(0, 1fr)" }}>
            <div className="mono" style={{ fontSize: 12, overflowWrap: "anywhere" }}>
              {f(t.models.failed, { message: failure })}
            </div>
          </div>
        ) : null}

        {!report ? <div className="empty">{t.common.loading}</div> : null}

        {report ? (
          <>
            {/* Nothing can write a script: the one state on this panel that is
                a real failure rather than a link the chain routes around. */}
            {report.any_ready ? null : (
              <div className="issue" data-sev="fatal"
                   style={{ gridTemplateColumns: "minmax(0, 1fr)", gap: 6 }}>
                <span className="label" style={{ color: "var(--err)" }}>
                  {t.models.noneTitle}
                </span>
                <p className="dim" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                  {t.models.noneBody}
                </p>
              </div>
            )}

            {/* Without this the picker just looks broken: the choice is saved
                and the chain still comes out of LLM_CHAIN. */}
            {report.chain_override ? (
              <div className="issue" data-sev="aviso"
                   style={{ gridTemplateColumns: "minmax(0, 1fr)", gap: 6 }}>
                <span className="label">{t.models.overrideTitle}</span>
                <p className="dim" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                  {t.models.overrideBody}
                </p>
              </div>
            ) : null}

            <div className="grid" style={{ gap: 10 }}>
              <div className="row spread wrap" style={{ gap: 8 }}>
                <span className="label">{t.models.inUse}</span>
                <span className="tag"
                      data-tone={report.source === "interface" ? "amber" : ""}>
                  {report.source === "interface"
                    ? t.models.sourceInterface : t.models.sourceEnv}
                </span>
              </div>

              <div className="chips">
                {report.models.map((model) => (
                  <button
                    type="button"
                    key={model.name}
                    className="chip"
                    data-on={model.name === report.chosen}
                    disabled={busy}
                    // A model that is not ready is still choosable on purpose:
                    // the chain carries on, and the pick survives the install
                    // that makes it work.
                    title={model.reason || model.note}
                    style={{ opacity: model.ready ? 1 : 0.62 }}
                    onClick={() => run(() => api.chooseModel(model.name),
                                       f(t.models.saved, { model: model.name }))}
                  >
                    {model.name}
                  </button>
                ))}
              </div>

              {chosen ? (
                <span className="mono dimmer"
                      style={{ fontSize: 11.5, lineHeight: 1.6, overflowWrap: "anywhere" }}>
                  {chosen.model} · {chosen.note}
                </span>
              ) : null}

              {report.source === "interface" ? (
                <button className="btn sm ghost" disabled={busy}
                        style={{ justifySelf: "start" }}
                        onClick={() => run(() => api.resetModel())}>
                  {t.models.useEnv}
                </button>
              ) : null}
            </div>

            <div className="grid" style={{ gap: 8 }}>
              <span className="label">{t.models.chainTitle}</span>
              <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                {t.models.chainHint}
              </p>
              {report.chain.map((step, index) => (
                <ChainRow key={`${step.provider}:${step.model}`} step={step}
                          position={index + 1} running={step === running} />
              ))}
            </div>

            <p className="dimmer" style={{ margin: 0, fontSize: 12, lineHeight: 1.6 }}>
              {t.models.refreshHint}
            </p>
          </>
        ) : null}
      </div>
    </section>
  );
}

function ChainRow({ step, position, running }: {
  step: ChainStep; position: number; running: boolean;
}) {
  const { t } = useI18n();

  return (
    <div className="issue" data-sev={running ? "aviso" : "info"}
         style={{ gridTemplateColumns: "minmax(0, 1fr)", gap: 6 }}>
      <div className="row wrap" style={{ gap: 8 }}>
        <span className="mono dimmer" style={{ fontSize: 11 }}>
          {String(position).padStart(2, "0")}
        </span>
        <b style={{ fontSize: 13, overflowWrap: "anywhere" }}>{step.model}</b>
        <span className="mono dimmer" style={{ fontSize: 11 }}>{step.provider}</span>
        <span className="tag"
              data-tone={running ? "amber" : step.ready ? "ok" : "warn"}>
          {running ? t.generators.chosen
                   : step.ready ? t.common.ready : t.generators.skipped}
        </span>
      </div>
      {/* The backend's own sentence: it names the binary to install or the model
          id the CLI does not offer, which a friendlier rewrite would lose. */}
      {step.reason ? (
        <span className="mono dim" style={{ fontSize: 11.5, lineHeight: 1.6,
                                            overflowWrap: "anywhere" }}>
          {step.reason}
        </span>
      ) : null}
    </div>
  );
}
