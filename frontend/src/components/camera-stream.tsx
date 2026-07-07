"use client";
import { useEffect, useRef, useState } from "react";

/**
 * MSE-плеер потока go2rtc: WebSocket /go2rtc/api/ws?src=<name>,
 * fMP4-сегменты в MediaSource. Реконнект с бэкоффом.
 *
 * iOS Safari не имеет MediaSource — используется ManagedMediaSource
 * (iOS 17.1+); на более старых устройствах показывается подсказка.
 */

interface ManagedMediaSourceCtor {
  new (): MediaSource;
}

function mediaSourceCtor(): ManagedMediaSourceCtor | null {
  const w = window as unknown as {
    ManagedMediaSource?: ManagedMediaSourceCtor;
    MediaSource?: ManagedMediaSourceCtor;
  };
  // На iOS есть только ManagedMediaSource; на остальных — обычный MediaSource.
  return w.ManagedMediaSource ?? w.MediaSource ?? null;
}

export function CameraStream({
  src,
  className,
  onStateChange,
}: {
  src: string;
  className?: string;
  onStateChange?: (online: boolean) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [online, setOnline] = useState(false);
  const [unsupported, setUnsupported] = useState(false);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const MS = mediaSourceCtor();
    if (!MS) {
      setUnsupported(true);
      onStateChange?.(false);
      return;
    }

    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let disposed = false;

    const setState = (v: boolean) => {
      setOnline(v);
      onStateChange?.(v);
    };

    const connect = () => {
      if (disposed) return;
      try {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        ws = new WebSocket(`${proto}://${location.host}/go2rtc/api/ws?src=${encodeURIComponent(src)}`);
        ws.binaryType = "arraybuffer";

        const ms = new MS();
        // ManagedMediaSource стартует только с выключенным AirPlay-роутингом
        if ("disableRemotePlayback" in video) {
          (video as HTMLVideoElement & { disableRemotePlayback: boolean }).disableRemotePlayback = true;
        }
        video.src = URL.createObjectURL(ms as unknown as MediaSource);
        let sb: SourceBuffer | null = null;
        const queue: ArrayBuffer[] = [];

        const pump = () => {
          if (!sb || sb.updating || !queue.length) return;
          try {
            sb.appendBuffer(queue.shift()!);
          } catch {
            ws?.close(); // переполнение/ошибка буфера — переподключаемся
          }
        };

        ws.onopen = () => {
          ws!.send(JSON.stringify({ type: "mse", value: "avc1.640029,avc1.64001F,mp4a.40.2" }));
        };
        ws.onmessage = (ev) => {
          try {
            if (typeof ev.data === "string") {
              const msg = JSON.parse(ev.data);
              if (msg.type === "mse") {
                const init = () => {
                  sb = ms.addSourceBuffer(msg.value);
                  sb.mode = "segments";
                  sb.addEventListener("updateend", pump);
                  attempt = 0;
                  setState(true);
                  video.play().catch(() => {});
                };
                if (ms.readyState === "open") init();
                else ms.addEventListener("sourceopen", init, { once: true });
              }
            } else {
              queue.push(ev.data);
              // не даём буферу расти бесконечно — держимся у живого края
              if (video.buffered.length && video.buffered.end(video.buffered.length - 1) - video.currentTime > 5) {
                video.currentTime = video.buffered.end(video.buffered.length - 1) - 0.5;
              }
              pump();
            }
          } catch {
            ws?.close();
          }
        };
        ws.onclose = () => {
          if (disposed) return;
          setState(false);
          attempt += 1;
          retry = setTimeout(connect, Math.min(15000, 1000 * 2 ** attempt));
        };
        ws.onerror = () => ws?.close();
      } catch {
        // любой сбой инициализации — тихий реконнект, а не падение страницы
        setState(false);
        attempt += 1;
        retry = setTimeout(connect, Math.min(15000, 1000 * 2 ** attempt));
      }
    };

    connect();
    return () => {
      disposed = true;
      if (retry) clearTimeout(retry);
      ws?.close();
      video.removeAttribute("src");
      video.load();
    };
  }, [src, onStateChange]);

  if (unsupported) {
    return (
      <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/85 px-3 text-center">
        <span className="text-[11px] leading-snug text-white/60">
          Браузер не поддерживает просмотр камер.
          Обновите iOS до 17.1+ или откройте с компьютера.
        </span>
      </div>
    );
  }

  return (
    <video
      ref={videoRef}
      autoPlay
      muted
      playsInline
      className={className}
      style={online ? undefined : { visibility: "hidden" }}
    />
  );
}
