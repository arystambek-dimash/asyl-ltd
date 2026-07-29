"use client";
import { useEffect, useRef, useState } from "react";
import { Mic, Square, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Секунды в «м:сс» — на длинной диктовке «93 с» читается хуже, чем «1:33». */
function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export interface VoiceRecorderProps {
  /** Отдаёт готовую запись наружу; null — запись удалили. */
  onChange: (file: File | null) => void;
  disabled?: boolean;
  /** Автостоп, чтобы случайно не записать сорокаминутный файл. */
  maxSeconds?: number;
}

export function VoiceRecorder({ onChange, disabled, maxSeconds = 300 }: VoiceRecorderProps) {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopTimer = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
  };

  // Микрофон и объектный URL — внешние ресурсы: гасим их при размонтировании,
  // иначе индикатор записи останется гореть после закрытия формы.
  useEffect(() => {
    return () => {
      stopTimer();
      recorderRef.current?.stream.getTracks().forEach((track) => track.stop());
      if (url) URL.revokeObjectURL(url);
    };
  }, [url]);

  async function start() {
    setError("");
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setError("Браузер не поддерживает запись звука");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
        // Расширение по типу: бэкенд принимает файл по content-type, но имя
        // должно оставаться узнаваемым в списке вложений.
        const ext = (recorder.mimeType || "audio/webm").includes("ogg") ? "ogg" : "webm";
        const file = new File([blob], `golos-${Date.now()}.${ext}`, { type: blob.type });
        setUrl((previous) => {
          if (previous) URL.revokeObjectURL(previous);
          return URL.createObjectURL(blob);
        });
        onChange(file);
        stream.getTracks().forEach((track) => track.stop());
      };
      recorder.start();
      recorderRef.current = recorder;
      setRecording(true);
      setSeconds(0);
      timerRef.current = setInterval(() => {
        setSeconds((value) => {
          if (value + 1 >= maxSeconds) stop();
          return value + 1;
        });
      }, 1000);
    } catch {
      setError("Нет доступа к микрофону — разрешите запись в браузере");
    }
  }

  function stop() {
    stopTimer();
    setRecording(false);
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
  }

  function discard() {
    if (url) URL.revokeObjectURL(url);
    setUrl(null);
    setSeconds(0);
    onChange(null);
  }

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {!recording && !url && (
          <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => void start()}>
            <Mic className="size-4" /> Записать голос
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
            <audio src={url} controls className="h-9 max-w-full" />
            <Button type="button" variant="ghost" size="sm" disabled={disabled} onClick={discard}>
              <Trash2 className="size-4" /> Удалить
            </Button>
          </>
        )}
      </div>
      {error && <p className="text-xs text-[var(--destructive)]">{error}</p>}
    </div>
  );
}
