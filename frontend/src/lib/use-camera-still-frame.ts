"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, blobApiError } from "@/lib/api";

/** «Подключение…» that never ends must not leave the operator without a picture. */
export const LIVE_FALLBACK_MS = 6_000;

/**
 * Live stream by default; the camera PC's latest still frame as a fallback.
 * The automatic fallback keeps the stream running until a frame has actually
 * arrived, and fires once: after that the operator chooses the source.
 */
export function useCameraStillFrame(src: string) {
  const [mode, setMode] = useState<"live" | "still">("live");
  const [online, setOnline] = useState(false);
  const [autoFallback, setAutoFallback] = useState(true);
  const [url, setUrl] = useState<string | null>(null);
  const [takenAt, setTakenAt] = useState<Date | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const requestId = useRef(0);
  const urlRef = useRef<string | null>(null);
  const onlineRef = useRef(false);

  /** A stream only reports changes, so a freshly mounted one starts offline. */
  const reportOnline = useCallback((value: boolean) => {
    onlineRef.current = value;
    setOnline(value);
  }, []);

  const enter = useCallback(
    (next: "live" | "still") => {
      reportOnline(false);
      setMode(next);
    },
    [reportOnline],
  );

  /** True when this request is still current and delivered a frame. */
  const load = useCallback(async () => {
    const id = ++requestId.current;
    setLoading(true);
    setError("");
    try {
      const response = await api.get<Blob>(`/cameras/${encodeURIComponent(src)}/counting-line/frame`, {
        responseType: "blob",
        timeout: 15_000,
      });
      if (id !== requestId.current) return false;
      const next = URL.createObjectURL(response.data);
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = next;
      setUrl(next);
      setTakenAt(new Date());
      return true;
    } catch (cause) {
      const message = await blobApiError(cause);
      if (id === requestId.current) setError(message || "Не удалось получить кадр камеры.");
      return false;
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [src]);

  const showStill = useCallback(() => {
    setAutoFallback(false);
    enter("still");
    void load();
  }, [enter, load]);

  const showLive = useCallback(() => {
    requestId.current += 1;
    setLoading(false);
    setError("");
    setAutoFallback(false);
    enter("live");
  }, [enter]);

  useEffect(() => {
    if (mode !== "live" || online || !autoFallback) return;
    const timer = setTimeout(() => {
      setAutoFallback(false);
      void load().then((loaded) => {
        // A stream that connected meanwhile wins; a failure stays a hint.
        if (loaded && !onlineRef.current) enter("still");
      });
    }, LIVE_FALLBACK_MS);
    return () => clearTimeout(timer);
  }, [mode, online, autoFallback, load, enter]);

  useEffect(
    () => () => {
      requestId.current += 1;
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    },
    [],
  );

  return {
    mode,
    online,
    autoFallback,
    url,
    takenAt,
    loading,
    error,
    reportOnline,
    showStill,
    showLive,
    refresh: load,
  };
}
