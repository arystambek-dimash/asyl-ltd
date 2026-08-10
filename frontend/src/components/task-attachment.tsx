"use client";

import Image from "next/image";
import { Paperclip } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api, apiError, blobApiError } from "@/lib/api";

export function AttachmentChip({
  taskId,
  attachmentId,
  kind,
  url,
  name,
}: {
  taskId: number;
  attachmentId: number;
  kind: string;
  url: string | null;
  name: string;
}) {
  const [currentUrl, setCurrentUrl] = useState(url);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [loadingAttachment, setLoadingAttachment] = useState(false);
  const [attachmentError, setAttachmentError] = useState("");
  const renewalRef = useRef<Promise<string | null> | null>(null);
  const imageRenewedAfterError = useRef(false);

  useEffect(() => {
    setCurrentUrl(url);
    imageRenewedAfterError.current = false;
  }, [url]);

  useEffect(
    () => () => {
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    },
    [audioUrl],
  );

  async function renewUrl(): Promise<string | null> {
    if (renewalRef.current) return renewalRef.current;
    const request = api
      .get<{ url: string | null }>(`/tasks/${taskId}/attachments/${attachmentId}/url/`)
      .then((response) => response.data.url)
      .finally(() => {
        renewalRef.current = null;
      });
    renewalRef.current = request;
    return request;
  }

  async function renewImageAfterError() {
    if (imageRenewedAfterError.current) return;
    imageRenewedAfterError.current = true;
    try {
      const freshUrl = await renewUrl();
      if (freshUrl) setCurrentUrl(freshUrl);
    } catch (cause) {
      setAttachmentError(apiError(cause));
    }
  }

  async function openFreshUrl() {
    const newTab = window.open("about:blank", "_blank");
    if (newTab) newTab.opener = null;
    setLoadingAttachment(true);
    setAttachmentError("");
    try {
      const freshUrl = await renewUrl();
      if (!freshUrl) throw new Error("Attachment is unavailable");
      setCurrentUrl(freshUrl);
      if (newTab) newTab.location.replace(freshUrl);
      else window.open(freshUrl, "_blank", "noopener,noreferrer");
    } catch (cause) {
      newTab?.close();
      setAttachmentError(apiError(cause));
    } finally {
      setLoadingAttachment(false);
    }
  }

  async function loadVoice() {
    setLoadingAttachment(true);
    setAttachmentError("");
    try {
      const freshUrl = await renewUrl();
      if (!freshUrl) throw new Error("Attachment is unavailable");
      let response;
      try {
        response = await api.get<Blob>(freshUrl, { responseType: "blob" });
      } catch (cause) {
        setAttachmentError(await blobApiError(cause));
        return;
      }
      setAudioUrl(URL.createObjectURL(response.data));
    } catch (cause) {
      setAttachmentError(apiError(cause));
    } finally {
      setLoadingAttachment(false);
    }
  }

  if (!url) {
    return (
      <span
        className="flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs text-[var(--muted-foreground)]"
        title="Файл недоступен"
      >
        <Paperclip className="size-3.5" />
        {name || "файл"}
        <span aria-hidden="true">·</span>
        <span>недоступен</span>
      </span>
    );
  }
  if (kind === "voice") {
    if (audioUrl) {
      return <audio src={audioUrl} controls autoPlay className="h-8 max-w-[220px]" />;
    }
    return (
      <div className="grid gap-1">
        <button
          type="button"
          disabled={loadingAttachment}
          onClick={() => void loadVoice()}
          className="flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs hover:bg-[var(--muted)] disabled:cursor-wait disabled:opacity-60"
        >
          <Paperclip className="size-3.5" />
          {loadingAttachment ? "Загрузка…" : "Прослушать голосовое"}
        </button>
        {attachmentError && <span className="text-xs text-[var(--destructive)]">{attachmentError}</span>}
      </div>
    );
  }
  if (kind === "photo") {
    return (
      <div className="grid gap-1">
        <button
          type="button"
          disabled={loadingAttachment}
          onClick={() => void openFreshUrl()}
          className="group relative disabled:cursor-wait disabled:opacity-60"
          aria-label={`Открыть ${name || "фото"}`}
        >
          <Image
            src={currentUrl ?? url}
            alt={name}
            width={64}
            height={64}
            unoptimized
            onError={() => void renewImageAfterError()}
            className="size-16 rounded-lg border object-cover transition group-hover:opacity-80"
          />
        </button>
        {attachmentError && <span className="text-xs text-[var(--destructive)]">{attachmentError}</span>}
      </div>
    );
  }
  return (
    <button
      type="button"
      disabled={loadingAttachment}
      onClick={() => void openFreshUrl()}
      className="flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs hover:bg-[var(--muted)]"
    >
      <Paperclip className="size-3.5" /> {loadingAttachment ? "Загрузка…" : name || "файл"}
    </button>
  );
}
