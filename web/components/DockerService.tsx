"use client";

import { useEffect, useRef, useState } from "react";

import { api, type ServiceStatus } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

/** Start (and stop) a local server from the panel, with a yes/no first.
 *
 *  The screen already printed the exact `docker run` line — asking someone to
 *  copy it into a terminal was the whole friction. What a person legitimately
 *  has to decide is not the command, it is whether a container may be started
 *  on their machine at all: that is the confirmation, and nothing runs before
 *  it is answered.
 *
 *  After "yes" the wait is real — the first pull is gigabytes — so the button
 *  keeps polling the service's own probe and shows the container's log tail,
 *  because a screen with no output is indistinguishable from a stuck one.
 */
export function DockerService({ serviceId, onReady }: {
  serviceId: string;
  onReady?: () => void;
}) {
  const { t, f } = useI18n();
  const [status, setStatus] = useState<ServiceStatus | null>(null);
  const [asking, setAsking] = useState(false);
  const [working, setWorking] = useState(false);
  const [logs, setLogs] = useState("");
  const [error, setError] = useState("");
  const poller = useRef<ReturnType<typeof setInterval> | null>(null);

  const read = () =>
    api.service(serviceId).then(setStatus).catch(() => setStatus(null));

  useEffect(() => {
    read();
    return () => { if (poller.current) clearInterval(poller.current); };
  }, [serviceId]);

  const watchUntilReady = () => {
    if (poller.current) clearInterval(poller.current);
    poller.current = setInterval(async () => {
      const [now, tail] = await Promise.all([
        api.service(serviceId).catch(() => null),
        api.serviceLogs(serviceId).catch(() => ({ logs: "" })),
      ]);
      if (now) setStatus(now);
      setLogs(tail.logs.trim().split("\n").slice(-3).join("\n"));
      if (now?.state === "rodando") {
        // Running is not the same as answering: the container is up long
        // before the model is loaded, and what the panel cares about is
        // whether the feature works now.
        onReady?.();
      }
    }, 4000);
  };

  const confirm = async () => {
    setAsking(false);
    setWorking(true);
    setError("");
    try {
      const result = await api.startService(serviceId);
      setStatus((prev) => (prev ? { ...prev, state: result.state } : prev));
      watchUntilReady();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setWorking(false);
    }
  };

  const halt = async () => {
    setWorking(true);
    setError("");
    try {
      await api.stopService(serviceId);
      if (poller.current) clearInterval(poller.current);
      setLogs("");
      await read();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setWorking(false);
    }
  };

  if (!status) return null;

  // Docker itself missing or asleep: there is nothing to offer, only something
  // to say. The reason is the backend's own sentence, with the next step in it.
  if (status.state === "sem_docker" || status.state === "docker_parado") {
    return (
      <span className="dim" style={{ fontSize: 12, lineHeight: 1.6 }}>
        {status.reason}
      </span>
    );
  }

  if (status.state === "rodando") {
    return (
      <div className="row wrap" style={{ gap: 8, alignItems: "center" }}>
        <span className="tag" data-tone="ok">
          <i className="dot" />{f(t.services.running, { label: status.label })}
        </span>
        <button className="btn sm ghost" onClick={halt} disabled={working}>
          {working ? t.common.loading : t.services.stop}
        </button>
        <span className="dimmer" style={{ fontSize: 11.5 }}>
          {t.services.stopKeeps}
        </span>
      </div>
    );
  }

  if (asking) {
    return (
      <div className="grid" style={{ gap: 6 }}>
        <span style={{ fontSize: 12.5, lineHeight: 1.6 }}>
          {f(t.services.confirm, { label: status.label })}
        </span>
        <span className="mono dimmer" style={{ fontSize: 11.5,
                                               overflowWrap: "anywhere" }}>
          {status.image}
        </span>
        {status.state === "nao_criado" ? (
          <span className="dim" style={{ fontSize: 12, lineHeight: 1.6 }}>
            {status.note}
          </span>
        ) : null}
        <div className="row wrap" style={{ gap: 8 }}>
          <button className="btn sm primary" onClick={confirm}>
            {t.services.yes}
          </button>
          <button className="btn sm ghost" onClick={() => setAsking(false)}>
            {t.services.no}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="grid" style={{ gap: 6 }}>
      <div className="row wrap" style={{ gap: 8, alignItems: "center" }}>
        <button className="btn sm" onClick={() => setAsking(true)} disabled={working}>
          {working ? t.common.loading
            : status.state === "parado" ? t.services.restart : t.services.start}
        </button>
        {working || logs ? (
          <span className="dimmer" style={{ fontSize: 11.5 }}>
            {t.services.starting}
          </span>
        ) : null}
      </div>
      {logs ? (
        <pre className="mono dimmer" style={{ fontSize: 11, margin: 0,
                                              whiteSpace: "pre-wrap",
                                              overflowWrap: "anywhere" }}>
          {logs}
        </pre>
      ) : null}
      {error ? (
        <span style={{ fontSize: 12, color: "var(--amber)" }}>{error}</span>
      ) : null}
    </div>
  );
}
