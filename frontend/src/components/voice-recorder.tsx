"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { LoaderCircle, Mic, Square, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Секунды в «м:сс» — на длинной диктовке «93 с» читается хуже, чем «1:33». */
function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function stopStream(stream: MediaStream | null | undefined) {
  stream?.getTracks().forEach((track) => track.stop());
}

type RecorderStatus = "idle" | "starting" | "recording" | "stopping";

export interface VoiceRecorderProps {
  /** Отдаёт готовую запись наружу; null — запись удалили. */
  onChange: (file: File | null) => void;
  disabled?: boolean;
  /** Автостоп, чтобы случайно не записать сорокаминутный файл. */
  maxSeconds?: number;
}

export function VoiceRecorder({ onChange, disabled, maxSeconds = 300 }: VoiceRecorderProps) {
  const [status, setStatus] = useState<RecorderStatus>("idle");
  const [seconds, setSeconds] = useState(0);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState("");
  const mountedRef = useRef(false);
  const statusRef = useRef<RecorderStatus>("idle");
  const requestRef = useRef(0);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const finishRef = useRef<(() => void) | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const secondsRef = useRef(0);
  const urlRef = useRef<string | null>(null);
  const onChangeRef = useRef(onChange);
  const maxSecondsRef = useRef(Math.max(1, Math.floor(maxSeconds)));

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  useEffect(() => {
    maxSecondsRef.current = Math.max(1, Math.floor(maxSeconds));
  }, [maxSeconds]);

  const stopTimer = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
  }, []);

  const updateStatus = useCallback((next: RecorderStatus) => {
    statusRef.current = next;
    if (mountedRef.current) setStatus(next);
  }, []);

  const replaceUrl = useCallback((next: string | null) => {
    if (urlRef.current && urlRef.current !== next) URL.revokeObjectURL(urlRef.current);
    urlRef.current = next;
    if (mountedRef.current) setUrl(next);
  }, []);

  // Микрофон и объектный URL — внешние ресурсы: гасим их при размонтировании,
  // иначе индикатор записи останется гореть после закрытия формы. Идентификатор
  // запроса также инвалидирует ещё не завершившийся запрос разрешения.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestRef.current += 1;
      stopTimer();
      finishRef.current = null;
      const recorder = recorderRef.current;
      recorderRef.current = null;
      if (recorder) {
        recorder.ondataavailable = null;
        recorder.onstop = null;
        if (recorder.state !== "inactive") {
          try {
            recorder.stop();
          } catch {
            // Поток всё равно будет остановлен ниже.
          }
        }
      }
      stopStream(streamRef.current ?? recorder?.stream);
      streamRef.current = null;
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    };
  }, [stopTimer]);

  async function start() {
    if (disabled || statusRef.current !== "idle" || urlRef.current) return;
    setError("");
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setError("Браузер не поддерживает запись звука");
      return;
    }

    const requestId = ++requestRef.current;
    updateStatus("starting");

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      if (mountedRef.current && requestRef.current === requestId) {
        updateStatus("idle");
        setError("Нет доступа к микрофону — разрешите запись в браузере");
      }
      return;
    }

    if (!mountedRef.current || requestRef.current !== requestId) {
      stopStream(stream);
      return;
    }

    streamRef.current = stream;
    try {
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      let finalized = false;

      const finish = () => {
        if (finalized) return;
        finalized = true;
        stopTimer();
        stopStream(stream);
        if (streamRef.current === stream) streamRef.current = null;
        if (finishRef.current === finish) finishRef.current = null;
        if (recorderRef.current !== recorder) return;
        recorderRef.current = null;
        if (!mountedRef.current || requestRef.current !== requestId) return;

        try {
          const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
          // Расширение по типу: бэкенд принимает файл по content-type, но имя
          // должно оставаться узнаваемым в списке вложений.
          const ext = (recorder.mimeType || "audio/webm").includes("ogg") ? "ogg" : "webm";
          const file = new File([blob], `golos-${Date.now()}.${ext}`, { type: blob.type });
          replaceUrl(URL.createObjectURL(blob));
          onChangeRef.current(file);
          setError("");
        } catch {
          setError("Не удалось подготовить голосовую запись");
        } finally {
          updateStatus("idle");
        }
      };

      recorder.ondataavailable = (event) => {
        if (recorderRef.current === recorder && event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = finish;
      recorderRef.current = recorder;
      finishRef.current = finish;
      recorder.start();
      updateStatus("recording");
      secondsRef.current = 0;
      setSeconds(0);
      timerRef.current = setInterval(() => {
        const next = Math.min(secondsRef.current + 1, maxSecondsRef.current);
        secondsRef.current = next;
        if (mountedRef.current) setSeconds(next);
        if (next >= maxSecondsRef.current) stop();
      }, 1000);
    } catch {
      const recorder = recorderRef.current;
      finishRef.current = null;
      recorderRef.current = null;
      if (recorder) {
        recorder.ondataavailable = null;
        recorder.onstop = null;
      }
      stopStream(stream);
      if (streamRef.current === stream) streamRef.current = null;
      if (mountedRef.current && requestRef.current === requestId) {
        updateStatus("idle");
        setError("Не удалось начать запись звука");
      }
    }
  }

  function stop() {
    if (statusRef.current !== "recording") return;
    stopTimer();
    updateStatus("stopping");
    const recorder = recorderRef.current;
    if (!recorder) {
      stopStream(streamRef.current);
      streamRef.current = null;
      updateStatus("idle");
      return;
    }
    try {
      if (recorder.state === "inactive") finishRef.current?.();
      else recorder.stop();
    } catch {
      recorder.ondataavailable = null;
      recorder.onstop = null;
      recorderRef.current = null;
      finishRef.current = null;
      stopStream(streamRef.current ?? recorder.stream);
      streamRef.current = null;
      updateStatus("idle");
      setError("Не удалось завершить запись звука");
    }
  }

  function discard() {
    replaceUrl(null);
    secondsRef.current = 0;
    setSeconds(0);
    setError("");
    onChangeRef.current(null);
  }

  const recording = status === "recording";

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {!recording && !url && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled || status !== "idle"}
            onClick={() => void start()}
          >
            {status === "starting" || status === "stopping" ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Mic className="size-4" />
            )}
            {status === "starting" ? "Подключение…" : status === "stopping" ? "Обработка…" : "Записать голос"}
          </Button>
        )}
        {recording && (
          <>
            <Button type="button" variant="destructive" size="sm" onClick={stop}>
              <Square className="size-4" /> Остановить
            </Button>
            <span className="flex items-center gap-1.5 text-sm font-medium tabular-nums text-[var(--destructive)]">
              <span className="size-2 animate-pulse rounded-full bg-[var(--destructive)]" />
              {clock(seconds)}
            </span>
          </>
        )}
        {url && !recording && (
          <>
            <audio src={url} controls aria-label="Записанное голосовое сообщение" className="h-9 max-w-full" />
            <Button type="button" variant="ghost" size="sm" disabled={disabled} onClick={discard}>
              <Trash2 className="size-4" /> Удалить
            </Button>
          </>
        )}
      </div>
      {error && (
        <p role="alert" className="text-xs text-[var(--destructive)]">
          {error}
        </p>
      )}
    </div>
  );
}
