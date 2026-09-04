"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api, type Requirement, type RequirementsReport } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useToast } from "@/components/ui";

/** The one-shot setup per host. Windows carries the execution-policy prefix
 *  because a default box refuses to run an unsigned script without it, and a
 *  command that fails on paste is worse than no command. */
const SETUP: Record<string, string> = {
  macos: "sh scripts/setup.sh",
  linux: "sh scripts/setup.sh",
  windows: "powershell -ExecutionPolicy Bypass -File scripts\\setup.ps1",
};

/** Grid and flex items start at `min-width: auto`, so a container ends up as
 *  wide as the longest unbreakable line inside it — and a `brew tap … && brew
 *  install …` line is wider than a phone. Every wrapper between the panel and
 *  a command block carries this, otherwise the block stops scrolling inside
 *  itself and takes the whole page sideways with it. */
const SHRINK = { minWidth: 0 } as const;

/** The doctor's `install` field is a shell command for a binary but plain
 *  advice for a credential or an asset folder ("Accounts screen, or
 *  PEXELS_API_KEY…", "drop a .ttf into assets/fonts/"). Only the first kind
 *  belongs in a copyable block — a copy button on a sentence is noise. */
const COMMAND_START =
  /^(brew|sudo|apt|dnf|pacman|zypper|winget|choco|npm|pip|pipx|python3?|sh|bash|powershell|make|docker|\.venv|https?:\/\/)/;

const isCommand = (text: string) => COMMAND_START.test(text.trim());

/** A missing REQUIRED item is the only thing on this screen that means
 *  something is broken. An absent optional one is deliberately drawn as
 *  neutral information: the doctor's own note says what the pipeline does
 *  instead, and alarming someone into installing what they do not need is how
 *  a working install starts looking defective. */
function severity(check: Requirement): string {
  if (check.found) return "info";
  return check.required ? "fatal" : "info";
}

export function RequirementsPanel() {
  const { t, f } = useI18n();
  const { toast, node } = useToast();
  const [report, setReport] = useState<RequirementsReport | null>(null);
  const [busy, setBusy] = useState(true);
  const [failure, setFailure] = useState("");

  // Every check here spawns a process, so this is asked for on open and on
  // demand — never on a timer like /api/health.
  const pull = useCallback(async () => {
    setBusy(true);
    try {
      setReport(await api.requirements());
      setFailure("");
    } catch (error) {
      setFailure((error as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { pull(); }, [pull]);

  const groups = [
    { level: "required" as const, title: t.requirements.requiredTitle,
      hint: t.requirements.requiredHint },
    { level: "optional" as const, title: t.requirements.optionalTitle,
      hint: t.requirements.optionalHint },
  ];

  const host = report?.platform.os ?? "";
  const setup = SETUP[host] ?? SETUP.linux;

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="label">{t.requirements.title}</span>
        <div className="grow" />
        {report ? (
          <div className="row wrap" style={{ gap: 6 }}>
            {report.ok ? (
              <span className="tag" data-tone="ok">
                <i className="dot" />{t.requirements.allSet}
              </span>
            ) : (
              <span className="tag" data-tone="err">
                <i className="dot" />
                {f(t.requirements.missingRequired, { n: report.missing_required.length })}
              </span>
            )}
            {/* no tone on purpose: absent optionals are information, not a fault */}
            {report.missing_optional.length ? (
              <span className="tag">
                {f(t.requirements.optionalOff, { n: report.missing_optional.length })}
              </span>
            ) : null}
          </div>
        ) : null}
        <button className="btn sm ghost" onClick={pull} disabled={busy}>
          {busy ? t.requirements.checking : t.requirements.refresh}
        </button>
      </div>

      <div className="panel-body grid" style={{ gap: 16 }}>
        <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
          {t.requirements.subtitle}
        </p>

        {failure ? (
          <div className="issue" data-sev="fatal" style={{ gridTemplateColumns: "minmax(0, 1fr)" }}>
            <div className="mono" style={{ fontSize: 12, overflowWrap: "anywhere" }}>
              {f(t.requirements.failed, { message: failure })}
            </div>
          </div>
        ) : null}

        {report ? (
          <>
            <div className="row wrap" style={{ gap: 6 }}>
              <span className="tag" data-tone="amber">
                {t.requirements.platform}: {report.platform.os} · {report.platform.machine}
              </span>
              <span className="tag">
                {t.requirements.packageManager}:{" "}
                {report.platform.package_manager || t.requirements.noManager}
              </span>
            </div>

            <div className="grid" style={{ gap: 8, ...SHRINK }}>
              <span className="label">{t.requirements.setupTitle}</span>
              <Command text={setup} toast={toast} />
              <p className="dimmer" style={{ margin: 0, fontSize: 12, lineHeight: 1.6 }}>
                {t.requirements.setupHint}
              </p>
            </div>

            {groups.map((group) => {
              const rows = report.checks.filter((c) => c.level === group.level);
              if (!rows.length) return null;
              return (
                <div className="grid" key={group.level} style={{ gap: 10, ...SHRINK }}>
                  <div className="grid" style={{ gap: 4 }}>
                    <span className="label">{group.title}</span>
                    <span className="dimmer" style={{ fontSize: 12, lineHeight: 1.6 }}>
                      {group.hint}
                    </span>
                  </div>
                  {rows.map((check) => (
                    <CheckRow key={check.id} check={check} toast={toast} />
                  ))}
                </div>
              );
            })}

            <div className="grid" style={{ gap: 8 }}>
              <p className="dimmer" style={{ margin: 0, fontSize: 12, lineHeight: 1.6 }}>
                {t.requirements.keysNote}
              </p>
              <Link href="/contas" className="btn sm ghost"
                    style={{ justifySelf: "start", textDecoration: "none" }}>
                {t.requirements.openAccounts}
              </Link>
            </div>
          </>
        ) : busy ? (
          <div className="empty" style={{ border: 0 }}>{t.requirements.checking}</div>
        ) : null}
      </div>
      {node}
    </section>
  );
}

function CheckRow({ check, toast }: { check: Requirement; toast: (m: string) => void }) {
  const { t } = useI18n();

  const tone = check.found ? "ok" : check.required ? "err" : "";
  const state = check.found
    ? t.requirements.installed
    : check.required ? t.requirements.missing : t.requirements.notInstalled;

  return (
    <div className="issue" data-sev={severity(check)}
         style={{ gridTemplateColumns: "minmax(0, 1fr)", gap: 8, ...SHRINK }}>
      <div className="row wrap" style={{ gap: 8 }}>
        <span className="tag" data-tone={tone}>{state}</span>
        <b style={{ fontSize: 13.5, overflowWrap: "anywhere" }}>{check.label}</b>
        {check.version ? (
          <span className="mono dimmer" style={{ fontSize: 11, overflowWrap: "anywhere" }}>
            {check.version}
          </span>
        ) : null}
      </div>

      {/* An installed item needs nothing else: what it unlocks only matters
          while it is missing. */}
      {check.found ? null : (
        <div className="grid" style={{ gap: 10, ...SHRINK }}>
          {check.note ? (
            <div className="grid" style={{ gap: 3 }}>
              <span className="label">{t.requirements.without}</span>
              <span className="dim" style={{ fontSize: 12.5, lineHeight: 1.6 }}>
                {check.note}
              </span>
            </div>
          ) : null}
          <div className="grid" style={{ gap: 3 }}>
            <span className="label">{t.requirements.unlocks}</span>
            <span className="dimmer" style={{ fontSize: 12, lineHeight: 1.6 }}>
              {check.unlocks}
            </span>
          </div>
          {check.install ? (
            <div className="grid" style={{ gap: 6, ...SHRINK }}>
              <span className="label">
                {isCommand(check.install)
                  ? t.requirements.installWith
                  : t.generators.whatToDo}
              </span>
              {isCommand(check.install) ? (
                <Command text={check.install} toast={toast} />
              ) : (
                <span className="dim" style={{ fontSize: 12.5, lineHeight: 1.6 }}>
                  {check.install}
                </span>
              )}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

/** A command with a copy button. The block scrolls inside itself: a `brew tap
 *  … && brew install …` line is wider than a phone and must not push the page
 *  sideways. */
function Command({ text, toast }: { text: string; toast: (m: string) => void }) {
  const { t } = useI18n();

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast(t.reels.copied);
    } catch {
      // no clipboard permission, or an insecure origin
      toast(t.publish.copyFailed);
    }
  };

  return (
    // grid rather than flex: a minmax(0, …) track is the one thing that caps
    // the block's width no matter how long the command is
    <div className="grid"
         style={{ gridTemplateColumns: "minmax(0, 1fr) auto", gap: 8,
                  alignItems: "start", ...SHRINK }}>
      <pre
        style={{
          margin: 0, overflowX: "auto", maxWidth: "100%", ...SHRINK,
          background: "#060708", border: "1px solid var(--line)",
          borderLeft: "2px solid var(--amber-dim)", borderRadius: "var(--r)",
          padding: "10px 12px", fontFamily: "var(--font-mono), monospace",
          fontSize: 11.5, lineHeight: 1.7,
        }}
      >
        <code>{text}</code>
      </pre>
      <button className="btn sm ghost" onClick={copy}>{t.publish.copy}</button>
    </div>
  );
}
