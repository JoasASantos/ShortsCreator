"use client";

import { useRef, useState } from "react";

import { api, type UploadResult } from "@/lib/api";
import { Chips, Field } from "@/components/ui";

/** The two ways a video gets in: a file, or a link to one somewhere else.
 *
 *  They are mutually exclusive, and enforced here rather than validated later:
 *  the endpoints that take a video answer 400 when `attachment_id` and `url`
 *  both arrive, so making it impossible to fill both beats explaining the
 *  error afterwards. Switching side clears what the other side held. */
export type Origin = "file" | "url";

export type SourceValue = {
  origin: Origin;
  upload: UploadResult | null;
  url: string;
};

export const EMPTY_SOURCE: SourceValue = { origin: "file", upload: null, url: "" };

/** True once there is something to send. */
export const hasSource = (value: SourceValue) =>
  value.origin === "file" ? Boolean(value.upload) : Boolean(value.url.trim());

/** The request body fragment — exactly one key, never both. */
export const sourceBody = (value: SourceValue) =>
  value.origin === "file"
    ? { attachment_id: value.upload!.id }
    : { url: value.url.trim() };

/** Labels come from the caller so each screen keeps its own wording (a live
 *  VOD and a reel you just recorded are not described the same way) without
 *  this component needing to know which dictionary section to read. */
export type SourceLabels = {
  origin: string;
  exclusive: string;
  fromFile: string;
  fromUrl: string;
  drop: string;
  dropping: string;
  uploading: string;
  uploadFailed: string;
  swap: string;
  urlLabel: string;
  urlHint: string;
  urlPlaceholder: string;
};

export function SourcePicker({ value, onChange, labels, toast, accept = "video/*" }: {
  value: SourceValue;
  onChange: (value: SourceValue) => void;
  labels: SourceLabels;
  toast: (message: string) => void;
  accept?: string;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);

  const clearInput = () => { if (fileInput.current) fileInput.current.value = ""; };

  const sendFile = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    try {
      const upload = await api.upload(files[0]);
      onChange({ origin: "file", upload, url: "" });
    } catch (e) {
      toast(labels.uploadFailed.replace("{message}", (e as Error).message));
    } finally {
      setUploading(false);
      clearInput();
    }
  };

  const switchOrigin = (origin: Origin) => {
    clearInput();
    onChange(origin === "file"
      ? { origin, upload: value.upload, url: "" }
      : { origin, upload: null, url: value.url });
  };

  return (
    <>
      <Field label={labels.origin} hint={labels.exclusive}>
        <Chips<Origin>
          value={value.origin}
          onChange={switchOrigin}
          options={[{ value: "file", label: labels.fromFile },
                    { value: "url", label: labels.fromUrl }]}
        />
      </Field>

      {value.origin === "file" ? (
        <div className="grid" style={{ gap: 8 }}>
          <label
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              sendFile(e.dataTransfer.files);
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
            <span>{dragging ? labels.dropping : labels.drop}</span>
            <input
              ref={fileInput}
              type="file"
              accept={accept}
              onChange={(e) => sendFile(e.target.files)}
              style={{
                position: "absolute", width: 1, height: 1,
                opacity: 0, pointerEvents: "none",
              }}
            />
          </label>
          {uploading ? <span className="label">{labels.uploading}</span> : null}
          {value.upload ? (
            <div className="row spread wrap" style={{ gap: 8 }}>
              <span className="mono" style={{ fontSize: 12, minWidth: 0,
                           overflowWrap: "anywhere" }}>
                {value.upload.filename}
                {value.upload.size_bytes ? (
                  <span className="dimmer">
                    {" "}({(value.upload.size_bytes / 1048576).toFixed(0)} MB)
                  </span>
                ) : null}
              </span>
              <button className="btn sm ghost"
                      onClick={() => { clearInput(); onChange({ ...value, upload: null }); }}>
                {labels.swap}
              </button>
            </div>
          ) : null}
        </div>
      ) : (
        <Field label={labels.urlLabel} hint={labels.urlHint}>
          <input
            className="input"
            type="url"
            inputMode="url"
            placeholder={labels.urlPlaceholder}
            value={value.url}
            onChange={(e) => onChange({ ...value, url: e.target.value })}
          />
        </Field>
      )}
    </>
  );
}
