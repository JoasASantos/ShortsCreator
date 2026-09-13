"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  api, type AvatarChoice, type AvatarProvider, type AvatarReport,
  type AvatarVoiceChoice, type GeneratorState, type Job, type Voice,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Chips, Field, StatusTag, Topbar, useToast } from "@/components/ui";
import { MicRecorder } from "@/components/MicRecorder";
import { DockerService } from "@/components/DockerService";

/* Neither state that matters here is red. A key nobody registered and a local
   server that is not running are both normal states with an obvious next step
   — the same reading the generators screen takes. */
const STATE_TONE: Record<GeneratorState, string> = {
  ready: "ok",
  not_configured: "amber",
  unreachable: "warn",
  incapable: "",
};

type ClonePath = "" | "xtts" | "fishaudio";

export default function AvatarStudio() {
  const { t, f, narrationLanguage } = useI18n();
  const { toast, node } = useToast();

  const [report, setReport] = useState<AvatarReport | null>(null);
  const [busy, setBusy] = useState(true);
  const [failure, setFailure] = useState("");

  // ---- my voice
  const [voiceName, setVoiceName] = useState("");
  const [path, setPath] = useState<ClonePath>("");
  const [sample, setSample] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [cloning, setCloning] = useState(false);
  const [voices, setVoices] = useState<Voice[]>([]);
  const sampleInput = useRef<HTMLInputElement>(null);

  // ---- the avatar video
  const [avatars, setAvatars] = useState<AvatarChoice[]>([]);
  const [avatarVoices, setAvatarVoices] = useState<AvatarVoiceChoice[]>([]);
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [avatarId, setAvatarId] = useState("");
  const [avatarVoiceId, setAvatarVoiceId] = useState("");
  const [script, setScript] = useState("");
  const [title, setTitle] = useState("");
  const [sending, setSending] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);

  // probe=true: the local XTTS server's reachability is part of its state and
  // only a round trip proves it. That is why this is asked for on demand and
  // never polled.
  const pullState = useCallback(async () => {
    setBusy(true);
    try {
      setReport(await api.avatarCapabilities(true));
      setFailure("");
    } catch (error) {
      setFailure((error as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  // Only voices with a stored sample are yours: a preset points at someone
  // else's reference id and has no audio of yours behind it.
  const pullVoices = useCallback(() => {
    api.voices()
      .then((all) => setVoices(all.filter((voice) => voice.sample_path)))
      .catch(() => setVoices([]));
  }, []);

  const pullJobs = useCallback(() => {
    api.jobs()
      .then((all) => setJobs(all.filter((job) => job.input?.edit_mode === "avatar")))
      .catch(() => setJobs([]));
  }, []);

  useEffect(() => { pullState(); pullVoices(); pullJobs(); },
           [pullState, pullVoices, pullJobs]);

  // Follows only while something is actually rendering — an avatar render takes
  // minutes on the provider's side, and a finished list has nothing to poll.
  const working = jobs.some((job) => job.status === "queued" || job.status === "running");
  useEffect(() => {
    if (!working) return;
    const id = setInterval(pullJobs, 6000);
    return () => clearInterval(id);
  }, [working, pullJobs]);

  const limits = report?.limits;
  const avatarReady = report?.avatar.state === "ready";
  const clonePaths = report?.voice_clone ?? [];
  const cloneReady = clonePaths.some((provider) => provider.state === "ready");

  const clone = async () => {
    if (!voiceName.trim()) return toast(t.avatarPage.needName);
    if (!sample) return toast(t.avatarPage.needSample);

    setCloning(true);
    try {
      const form = new FormData();
      form.append("name", voiceName.trim());
      if (path) form.append("provider", path);
      form.append("sample", sample);
      const voice = await api.cloneVoice(form);
      setVoiceName("");
      setSample(null);
      if (sampleInput.current) sampleInput.current.value = "";
      pullVoices();
      toast(f(t.avatarPage.clonedToast, { provider: voice.cloned_with }));
    } catch (error) {
      // The backend's own sentence: it carries the minimum length, or the URL
      // of the server that is not answering.
      toast((error as Error).message);
    } finally {
      setCloning(false);
    }
  };

  const loadCatalog = async () => {
    setLoadingCatalog(true);
    try {
      const [people, spoken] = await Promise.all([
        api.avatarChoices(), api.avatarVoiceChoices(),
      ]);
      setAvatars(people);
      setAvatarVoices(spoken);
      setAvatarId(people[0]?.id ?? "");
      setAvatarVoiceId(spoken[0]?.id ?? "");
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setLoadingCatalog(false);
    }
  };

  const generate = async () => {
    if (!script.trim()) return toast(t.avatarPage.needScript);
    if (!avatarId) return toast(t.avatarPage.needAvatar);
    if (!avatarVoiceId) return toast(t.avatarPage.needVoice);

    setSending(true);
    try {
      await api.createAvatarVideo({
        script: script.trim(),
        avatar_id: avatarId,
        voice_id: avatarVoiceId,
        title: title.trim(),
        language: narrationLanguage,
      });
      setScript("");
      setTitle("");
      pullJobs();
      toast(t.avatarPage.queuedToast);
    } catch (error) {
      toast((error as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      <Topbar title={t.avatarPage.title}>
        <span className="label">{t.avatarPage.subtitle}</span>
      </Topbar>

      {/* minmax(0, 1fr): the backend's reasons carry URLs and shell lines, and
          a content-sized track would widen the page instead of wrapping them */}
      <div className="content grid"
           style={{ gap: 18, gridTemplateColumns: "minmax(0, 1fr)" }}>
        <section className="panel">
          <div className="panel-head">
            <span className="label">{t.avatarPage.stateTitle}</span>
            <div className="grow" />
            <button className="btn sm ghost" onClick={pullState} disabled={busy}>
              {busy ? t.generators.checking : t.generators.refresh}
            </button>
          </div>
          <div className="panel-body grid" style={{ gap: 14 }}>
            <p className="dim" style={{ margin: 0, fontSize: 13, lineHeight: 1.65 }}>
              {t.avatarPage.intro}
            </p>
            <span className="dimmer" style={{ fontSize: 12, lineHeight: 1.6 }}>
              {t.avatarPage.stateHint}
            </span>

            {failure ? (
              <div className="issue" data-sev="fatal"
                   style={{ gridTemplateColumns: "minmax(0, 1fr)" }}>
                <span className="mono" style={{ fontSize: 12, overflowWrap: "anywhere" }}>
                  {f(t.avatarPage.failed, { message: failure })}
                </span>
              </div>
            ) : null}

            {!report && busy ? (
              <div className="empty">{t.generators.checking}</div>
            ) : null}

            {report ? (
              <div className="two" style={{ alignItems: "start" }}>
                {report.providers.map((provider) => (
                  <PathCard key={provider.id} provider={provider}
                            onChanged={pullState} />
                ))}
              </div>
            ) : null}
          </div>
        </section>

        <div className="side">
          <div className="grid" style={{ gap: 18 }}>
            <section className="panel">
              <div className="panel-head">
                <span className="label">{t.avatarPage.videoStep}</span>
                {avatarReady ? null : (
                  <>
                    <div className="grow" />
                    <span className="tag" data-tone="amber">
                      <i className="dot" />
                      {t.generators.states[report?.avatar.state ?? "not_configured"]}
                    </span>
                  </>
                )}
              </div>
              <div className="panel-body grid" style={{ gap: 14 }}>
                <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                  {t.avatarPage.videoIntro}
                </p>

                {avatarReady ? null : (
                  <div className="issue" data-sev="aviso"
                       style={{ gridTemplateColumns: "minmax(0, 1fr)", gap: 8 }}>
                    <span className="label">{t.generators.whatToDo}</span>
                    <p className="dim" style={{ margin: 0, fontSize: 12.5,
                                                lineHeight: 1.6,
                                                overflowWrap: "anywhere" }}>
                      {report?.avatar.reason || report?.avatar.setup}
                    </p>
                    <Link href="/contas" className="btn sm ghost"
                          style={{ justifySelf: "start", textDecoration: "none" }}>
                      {t.requirements.openAccounts}
                    </Link>
                  </div>
                )}

                <div className="row wrap" style={{ gap: 8 }}>
                  <button className="btn sm" onClick={loadCatalog}
                          disabled={!avatarReady || loadingCatalog}>
                    {loadingCatalog
                      ? t.avatarPage.loadingCatalog : t.avatarPage.loadCatalog}
                  </button>
                </div>

                {avatars.length ? (
                  <Field label={t.avatarPage.pickAvatar}>
                    <select className="select" value={avatarId}
                            onChange={(event) => setAvatarId(event.target.value)}>
                      {avatars.map((choice) => (
                        <option key={choice.id} value={choice.id}>
                          {choice.name}{choice.gender ? ` · ${choice.gender}` : ""}
                        </option>
                      ))}
                    </select>
                  </Field>
                ) : avatarReady && !loadingCatalog ? (
                  <span className="dimmer" style={{ fontSize: 12 }}>
                    {t.avatarPage.catalogEmpty}
                  </span>
                ) : null}

                {avatarVoices.length ? (
                  <Field label={t.avatarPage.pickVoice}>
                    <select className="select" value={avatarVoiceId}
                            onChange={(event) => setAvatarVoiceId(event.target.value)}>
                      {avatarVoices.map((choice) => (
                        <option key={choice.id} value={choice.id}>
                          {choice.name}
                          {choice.language ? ` · ${choice.language}` : ""}
                        </option>
                      ))}
                    </select>
                  </Field>
                ) : avatarReady && !loadingCatalog ? (
                  <span className="dimmer" style={{ fontSize: 12 }}>
                    {t.avatarPage.voicesEmpty}
                  </span>
                ) : null}

                <Field
                  label={t.avatarPage.scriptLabel}
                  hint={limits
                    ? f(t.avatarPage.scriptCount,
                        { n: script.trim().length, max: limits.max_script_chars })
                    : undefined}
                >
                  <textarea className="textarea" value={script}
                            maxLength={limits?.max_script_chars}
                            placeholder={t.avatarPage.scriptPlaceholder}
                            onChange={(event) => setScript(event.target.value)} />
                </Field>

                <Field label={t.avatarPage.titleLabel} hint={t.common.optional}>
                  <input className="input" value={title}
                         placeholder={t.avatarPage.titlePlaceholder}
                         onChange={(event) => setTitle(event.target.value)} />
                </Field>

                <button className="btn primary" onClick={generate}
                        disabled={!avatarReady || sending}>
                  {sending ? t.avatarPage.generating : t.avatarPage.generate}
                </button>

                <span className="dimmer" style={{ fontSize: 11.5, lineHeight: 1.6 }}>
                  {t.avatarPage.queuedHint}
                </span>
              </div>
            </section>

            <section className="panel">
              <div className="panel-head">
                <span className="label">{t.avatarPage.mine}</span>
                <div className="grow" />
                <span className="label">{jobs.length}</span>
              </div>
              <div className="panel-body grid" style={{ gap: 10 }}>
                {jobs.length === 0 ? (
                  <div className="empty">{t.avatarPage.noMine}</div>
                ) : (
                  jobs.map((job) => (
                    <div className="row spread wrap" key={job.id} style={{ gap: 8 }}>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontSize: 13, marginBottom: 3,
                                      overflowWrap: "anywhere" }}>
                          {job.title || job.id}
                        </div>
                        <StatusTag status={job.status} />
                      </div>
                      <Link href={`/job/${job.id}`} className="btn sm ghost"
                            style={{ textDecoration: "none" }}>
                        {t.avatarPage.openJob}
                      </Link>
                    </div>
                  ))
                )}
              </div>
            </section>
          </div>

          <section className="panel">
            <div className="panel-head">
              <span className="label">{t.avatarPage.voiceStep}</span>
              {cloneReady ? null : (
                <>
                  <div className="grow" />
                  {/* Which path is missing is spelled out in the panel above;
                      here it only has to be visible that none is up. */}
                  <span className="tag" data-tone="amber">
                    <i className="dot" />
                    {t.generators.states.not_configured}
                  </span>
                </>
              )}
            </div>
            <div className="panel-body grid" style={{ gap: 14 }}>
              <p className="dimmer" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}>
                {t.avatarPage.voiceIntro}
              </p>

              <Field label={t.avatarPage.voiceName}>
                <input className="input" value={voiceName}
                       placeholder={t.avatarPage.voiceNamePlaceholder}
                       onChange={(event) => setVoiceName(event.target.value)} />
              </Field>

              <Field label={t.avatarPage.pathLabel} hint={t.avatarPage.pathHint}>
                <Chips<ClonePath>
                  value={path}
                  onChange={setPath}
                  options={[
                    { value: "", label: t.avatarPage.pathAuto },
                    { value: "xtts", label: t.avatarPage.pathXtts },
                    { value: "fishaudio", label: t.avatarPage.pathFish },
                  ]}
                />
              </Field>

              <Field
                label={t.avatarPage.sampleLabel}
                hint={limits
                  ? f(t.avatarPage.sampleHint,
                      { min: limits.min_sample_seconds,
                        ideal: limits.recommended_sample_seconds })
                  : undefined}
              >
                {/* Recording is the common case — "clone your voice" should not
                    start with opening a recorder app, exporting and coming
                    back. The file picker below stays for the clip someone
                    already has. */}
                <MicRecorder
                  minSeconds={limits?.min_sample_seconds ?? 3}
                  idealSeconds={limits?.recommended_sample_seconds ?? 10}
                  onRecorded={(file) => {
                    setSample(file);
                    if (sampleInput.current) sampleInput.current.value = "";
                  }}
                  labels={{
                    start: t.avatarPage.recordStart,
                    stop: t.avatarPage.recordStop,
                    recording: t.avatarPage.recordingNow,
                    unsupported: t.avatarPage.recordUnsupported,
                    denied: t.avatarPage.recordDenied,
                    tooShort: limits
                      ? f(t.avatarPage.recordTooShort,
                          { min: limits.min_sample_seconds })
                      : t.avatarPage.recordDenied,
                    hint: t.avatarPage.recordHint,
                  }}
                />
                <label
                  onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={(event) => {
                    event.preventDefault();
                    setDragging(false);
                    const dropped = event.dataTransfer.files?.[0];
                    if (dropped) setSample(dropped);
                  }}
                  style={{
                    display: "grid",
                    placeItems: "center",
                    gap: 6,
                    minHeight: 96,
                    padding: "18px 14px",
                    textAlign: "center",
                    cursor: "pointer",
                    borderRadius: 3,
                    border: `1px dashed ${dragging ? "var(--amber)" : "var(--line)"}`,
                    background: dragging ? "#191300" : "var(--void)",
                    color: "var(--ink-2)",
                    fontSize: 12.5,
                    lineHeight: 1.6,
                  }}
                >
                  <span>{dragging ? t.reels.dropping : t.avatarPage.drop}</span>
                  {/* audio OR video: the recording most people have is a clip
                      of themselves talking, and the audio is extracted server
                      side rather than asking them to do it */}
                  <input
                    ref={sampleInput}
                    type="file"
                    accept="audio/*,video/*"
                    onChange={(event) => setSample(event.target.files?.[0] ?? null)}
                    style={{ position: "absolute", width: 1, height: 1,
                             opacity: 0, pointerEvents: "none" }}
                  />
                </label>
              </Field>

              {sample ? (
                <div className="row spread wrap" style={{ gap: 8 }}>
                  <span className="mono" style={{ fontSize: 12, minWidth: 0,
                                                  overflowWrap: "anywhere" }}>
                    {sample.name}
                    <span className="dimmer">
                      {" "}({(sample.size / 1048576).toFixed(1)} MB)
                    </span>
                  </span>
                  <button className="btn sm ghost"
                          onClick={() => {
                            setSample(null);
                            if (sampleInput.current) sampleInput.current.value = "";
                          }}>
                    {t.batch.swap}
                  </button>
                </div>
              ) : null}

              <span className="dimmer" style={{ fontSize: 11.5, lineHeight: 1.6 }}>
                {limits
                  ? f(t.avatarPage.sampleRules, { min: limits.min_sample_seconds })
                  : null}
              </span>

              <button className="btn primary" onClick={clone} disabled={cloning}>
                {cloning ? t.avatarPage.cloning : t.avatarPage.cloneAction}
              </button>

              <div className="grid" style={{ gap: 10 }}>
                <span className="label">{t.avatarPage.myVoices}</span>
                {voices.length === 0 ? (
                  <div className="empty">{t.avatarPage.noVoices}</div>
                ) : (
                  voices.map((voice) => (
                    <MyVoice key={voice.id} voice={voice} onRemoved={pullVoices} />
                  ))
                )}
              </div>
            </div>
          </section>
        </div>
      </div>
      {node}
    </>
  );
}

/** One path of the feature with the reason it is in the state it is in. The
 *  reason is the backend's own sentence — it names the URL that was tried, or
 *  the exact credential that is missing. */
function PathCard({ provider, onChanged }: {
  provider: AvatarProvider;
  onChanged?: () => void;
}) {
  const { t } = useI18n();

  return (
    <div className="issue" data-sev={provider.state === "ready" ? "info" : "aviso"}
         style={{ gridTemplateColumns: "minmax(0, 1fr)", gap: 8 }}>
      <div className="row wrap" style={{ gap: 8 }}>
        <b style={{ fontSize: 13 }}>{provider.label}</b>
        <span className="tag" data-tone={STATE_TONE[provider.state]}>
          <i className="dot" />{t.generators.states[provider.state]}
        </span>
        <span className="tag" data-tone={provider.cost === "free" ? "ok" : ""}>
          {provider.cost === "free" ? t.generators.free : t.generators.paid}
        </span>
        <span className="tag">
          {provider.hosting === "local" ? t.generators.local : t.generators.hosted}
        </span>
      </div>

      <div className="grid" style={{ gap: 4 }}>
        <span className="label">{t.requirements.unlocks}</span>
        {provider.unlocks.map((unlock) => (
          <span className="dim" key={unlock}
                style={{ fontSize: 12.5, lineHeight: 1.6 }}>
            {unlock}
          </span>
        ))}
      </div>

      <span className="mono dim" style={{ fontSize: 11.5, lineHeight: 1.6,
                                          overflowWrap: "anywhere" }}>
        {provider.reason}
      </span>

      <div className="row wrap" style={{ gap: 8 }}>
        {provider.connector ? (
          <Link href="/contas" className="btn sm ghost"
                style={{ textDecoration: "none" }}>
            {t.requirements.openAccounts}
          </Link>
        ) : null}
        {provider.docs ? (
          <a className="btn sm ghost" href={provider.docs} target="_blank"
             rel="noreferrer" style={{ textDecoration: "none" }}>
            {t.common.docs} ↗
          </a>
        ) : null}
      </div>

      {/* A path that runs in a container can be started from here. The card
          was already printing the docker command; this is the same thing with
          the copy-paste removed — and it still asks first. */}
      {provider.id === "voicestudio" ? (
        <DockerService serviceId="voicestudio" onReady={onChanged} />
      ) : null}
    </div>
  );
}

/** A voice of yours: what it was cloned with, the sample itself, and a
 *  synthesized preview. The sample plays whether or not anything is
 *  configured; /preview has to synthesize, so it needs the path to be up. */
function MyVoice({ voice, onRemoved }: { voice: Voice; onRemoved: () => void }) {
  const { t, f } = useI18n();
  const [showSample, setShowSample] = useState(false);

  let seconds = 0;
  try {
    seconds = Number(JSON.parse(voice.settings_json || "{}").sample_seconds ?? 0);
  } catch {
    seconds = 0;   // a hand-edited row is not worth a broken card
  }

  return (
    <div className="grid" style={{ gap: 8 }}>
      <div className="row spread wrap" style={{ gap: 8 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 13, marginBottom: 4, overflowWrap: "anywhere" }}>
            {voice.name}
          </div>
          <div className="row wrap" style={{ gap: 6 }}>
            <span className="tag" data-tone="amber">
              {f(t.avatarPage.clonedWith, { provider: voice.provider })}
            </span>
            {seconds ? (
              <span className="tag">
                {f(t.avatarPage.sampleSeconds, { n: seconds.toFixed(1) })}
              </span>
            ) : null}
          </div>
        </div>
        <div className="row wrap" style={{ gap: 6 }}>
          <button className="btn sm ghost" onClick={() => setShowSample((on) => !on)}>
            {t.avatarPage.listenSample}
          </button>
          <form action={`/api/voices/${voice.id}/preview`} method="post" target="_blank">
            <button className="btn sm ghost" type="submit">
              {t.avatarPage.listenVoice}
            </button>
          </form>
          <button className="btn sm danger"
                  onClick={() => api.deleteVoice(voice.id).then(onRemoved)}>
            {t.common.remove}
          </button>
        </div>
      </div>
      {showSample ? (
        <audio controls preload="none" style={{ width: "100%", height: 32 }}
               src={api.voiceSampleUrl(voice.id)} />
      ) : null}
    </div>
  );
}
