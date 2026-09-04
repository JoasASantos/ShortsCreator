"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  api, type Generator, type GeneratorCandidate, type GeneratorCapability,
  type GeneratorState, type GeneratorsReport,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Topbar, useToast } from "@/components/ui";

// Same order the registry declares them in, so the screen names capabilities
// the way the pipeline talks about them.
const CAPABILITIES: GeneratorCapability[] = [
  "text_to_video", "image_to_video", "text_to_image", "image_to_image",
];

/* Only `unreachable` and a missing credential are worth a colour, and neither
   is red: a local server that is not running and a key nobody registered are
   both normal states with an obvious next step. `incapable` gets no tone at
   all — it is not a problem, it just means this provider does not do that
   one thing. */
const STATE_TONE: Record<GeneratorState, string> = {
  ready: "ok",
  not_configured: "amber",
  unreachable: "warn",
  incapable: "",
};

export default function Generators() {
  const { t, f } = useI18n();
  const { toast, node } = useToast();
  const [report, setReport] = useState<GeneratorsReport | null>(null);
  const [busy, setBusy] = useState(true);
  const [failure, setFailure] = useState("");

  // probe=true: a local server's reachability is part of its state, and only
  // a round trip proves it. That is why this is not polled.
  const pull = useCallback(async () => {
    setBusy(true);
    try {
      setReport(await api.generators(true));
      setFailure("");
    } catch (error) {
      setFailure((error as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { pull(); }, [pull]);

  const providers = report?.providers ?? [];
  // Local and free first, and under their own heading: for anyone without a
  // paid key they are not an alternative, they are the whole feature.
  const groups = [
    {
      id: "local",
      title: t.generators.localTitle,
      hint: t.generators.localHint,
      items: providers.filter((p) => p.hosting === "local"),
    },
    {
      id: "hosted",
      title: t.generators.hostedTitle,
      hint: t.generators.hostedHint,
      items: providers.filter((p) => p.hosting !== "local"),
    },
  ];

  return (
    <>
      <Topbar title={t.generators.title}>
        <span className="label">{t.generators.subtitle}</span>
      </Topbar>

      {/* minmax(0, 1fr): the backend's messages carry URLs and shell lines, and
          a content-sized track would widen the page instead of wrapping them */}
      <div className="content grid"
           style={{ gap: 22, gridTemplateColumns: "minmax(0, 1fr)" }}>
        <section className="panel">
          <div className="panel-head">
            <span className="label">{t.generators.title}</span>
            <div className="grow" />
            <button className="btn sm ghost" onClick={pull} disabled={busy}>
              {busy ? t.generators.checking : t.generators.refresh}
            </button>
          </div>
          <div className="panel-body">
            <p className="dim" style={{ margin: 0, fontSize: 13, lineHeight: 1.65 }}>
              {t.generators.intro}
            </p>
          </div>
        </section>

        {failure ? (
          <div className="issue" data-sev="fatal"
               style={{ gridTemplateColumns: "minmax(0, 1fr)" }}>
            <div className="mono" style={{ fontSize: 12, overflowWrap: "anywhere" }}>
              {f(t.generators.failed, { message: failure })}
            </div>
          </div>
        ) : null}

        {!report && busy ? (
          <div className="empty">{t.generators.checking}</div>
        ) : null}

        {report && !providers.length ? (
          <div className="empty">{t.generators.empty}</div>
        ) : null}

        {groups.map((group) => group.items.length ? (
          <section className="grid" key={group.id} style={{ gap: 10 }}>
            <div className="grid" style={{ gap: 4 }}>
              <span className="label">{group.title}</span>
              <span className="dimmer" style={{ fontSize: 12, lineHeight: 1.6 }}>
                {group.hint}
              </span>
            </div>
            <div className="two" style={{ alignItems: "start" }}>
              {group.items.map((provider) => (
                <ProviderCard key={provider.id} provider={provider} toast={toast} />
              ))}
            </div>
          </section>
        ) : null)}

        {report ? <SelectionPanel report={report} /> : null}
      </div>
      {node}
    </>
  );
}

function ProviderCard({ provider, toast }: {
  provider: Generator; toast: (m: string) => void;
}) {
  const { t } = useI18n();
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  const test = async () => {
    setTesting(true);
    setResult(null);
    try {
      const answer = await api.testGenerator(provider.id);
      setResult({ ok: true, message: answer.message });
    } catch (error) {
      // The 400 body is the instruction ("start ComfyUI…", "register the key
      // on Accounts…"), so it is shown as written instead of a generic error.
      setResult({ ok: false, message: (error as Error).message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{provider.label}</span>
        <div className="grow" />
        <span className="tag" data-tone={STATE_TONE[provider.state]}>
          <i className="dot" />{t.generators.states[provider.state]}
        </span>
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
        <div className="row wrap" style={{ gap: 6 }}>
          <span className="tag" data-tone={provider.cost === "free" ? "ok" : ""}>
            {provider.cost === "free" ? t.generators.free : t.generators.paid}
          </span>
          <span className="tag">
            {provider.hosting === "local" ? t.generators.local : t.generators.hosted}
          </span>
          <span className="tag">{provider.speed}</span>
        </div>

        <div className="grid" style={{ gap: 6 }}>
          <span className="label">{t.generators.canDo}</span>
          <div className="row wrap" style={{ gap: 6 }}>
            {provider.capabilities.map((capability) => (
              <span className="tag" data-tone="amber" key={capability}>
                {t.generators.capabilities[capability]}
              </span>
            ))}
          </div>
        </div>

        {/* same wording as the install screen: what having it working buys */}
        <div className="grid" style={{ gap: 4 }}>
          <span className="label">{t.requirements.unlocks}</span>
          {provider.unlocks.map((unlock) => (
            <span className="dim" key={unlock} style={{ fontSize: 12.5, lineHeight: 1.6 }}>
              {unlock}
            </span>
          ))}
        </div>

        {provider.base_url ? (
          <div className="grid" style={{ gap: 4 }}>
            <span className="label">{t.generators.server}</span>
            <span className="mono dimmer" style={{ fontSize: 11.5, overflowWrap: "anywhere" }}>
              {provider.base_url}
            </span>
          </div>
        ) : null}

        {provider.models.length ? (
          <div className="grid" style={{ gap: 6 }}>
            <span className="label">{t.generators.models}</span>
            <div className="row wrap" style={{ gap: 6 }}>
              {provider.models.map((model) => (
                <span className="tag" key={model.id}
                      data-tone={model.id === provider.default_model ? "amber" : ""}>
                  {model.label}
                  {model.id === provider.default_model ? ` · ${t.generators.modelDefault}` : ""}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        {provider.state === "ready" ? null : (
          <div className="issue" data-sev="aviso"
               style={{ gridTemplateColumns: "minmax(0, 1fr)", gap: 8 }}>
            <span className="label">{t.generators.whatToDo}</span>
            {/* the backend's own sentence: it names the URL that was tried, or
                the exact credential that is missing */}
            <p className="dim" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6,
                                        overflowWrap: "anywhere" }}>
              {provider.reason || provider.setup}
            </p>
            {provider.connector ? (
              <Link href="/contas" className="btn sm ghost"
                    style={{ justifySelf: "start", textDecoration: "none" }}>
                {t.requirements.openAccounts}
              </Link>
            ) : null}
          </div>
        )}

        <div className="row wrap" style={{ gap: 8 }}>
          <button className="btn sm" onClick={test} disabled={testing}>
            {testing ? t.common.testing : t.common.test}
          </button>
          {provider.docs ? (
            <a className="btn sm ghost" href={provider.docs} target="_blank"
               rel="noreferrer" style={{ textDecoration: "none" }}>
              {t.common.docs} ↗
            </a>
          ) : null}
        </div>

        {result ? (
          <p className="mono" style={{ margin: 0, fontSize: 11.5, lineHeight: 1.6,
                                       overflowWrap: "anywhere",
                                       color: result.ok ? "var(--amber)" : "var(--err)" }}>
            {result.ok ? "✓ " : "✗ "}{result.message}
          </p>
        ) : null}
      </div>
    </section>
  );
}

/** The ordered plan per capability, exactly as the registry returns it. The
 *  first eligible row is the provider that will run, so this is the answer to
 *  "why did my video come out of ComfyUI and not Seedance". */
function SelectionPanel({ report }: { report: GeneratorsReport }) {
  const { t } = useI18n();

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.generators.selectionTitle}</span>
      </div>
      <div className="panel-body grid" style={{ gap: 18 }}>
        <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
          {t.generators.selectionHint}
        </p>

        {CAPABILITIES.map((capability) => {
          const plan = report.selection[capability] ?? [];
          const chosen = plan.find((candidate) => candidate.eligible);
          return (
            <div className="grid" key={capability} style={{ gap: 8 }}>
              <div className="row spread wrap" style={{ gap: 8 }}>
                <span className="label">{t.generators.capabilities[capability]}</span>
                {chosen ? (
                  <span className="tag" data-tone="ok">
                    <i className="dot" />{chosen.label}
                  </span>
                ) : (
                  <span className="tag" data-tone="warn">{t.generators.skipped}</span>
                )}
              </div>

              {chosen ? null : (
                <p className="dim" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                  {t.generators.noneFor}
                </p>
              )}

              {plan.map((candidate, index) => (
                <CandidateRow key={candidate.provider} candidate={candidate}
                              position={index + 1}
                              chosen={candidate.provider === chosen?.provider} />
              ))}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function CandidateRow({ candidate, position, chosen }: {
  candidate: GeneratorCandidate; position: number; chosen: boolean;
}) {
  const { t } = useI18n();

  return (
    <div className="issue" data-sev={chosen ? "aviso" : "info"}
         style={{ gridTemplateColumns: "minmax(0, 1fr)", gap: 6 }}>
      <div className="row wrap" style={{ gap: 8 }}>
        <span className="mono dimmer" style={{ fontSize: 11 }}>
          {String(position).padStart(2, "0")}
        </span>
        <b style={{ fontSize: 13 }}>{candidate.label}</b>
        <span className="tag" data-tone={chosen ? "amber" : STATE_TONE[candidate.state]}>
          {chosen ? t.generators.chosen : t.generators.states[candidate.state]}
        </span>
      </div>
      {/* "does not do text_to_video" would only repeat the tag next to it */}
      {candidate.state === "incapable" ? null : (
        <span className="mono dim" style={{ fontSize: 11.5, lineHeight: 1.6,
                                            overflowWrap: "anywhere" }}>
          {candidate.reason}
        </span>
      )}
    </div>
  );
}
