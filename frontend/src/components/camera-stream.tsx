"use client";
import { useEffect, useRef, useState } from "react";

/**
 * MSE-плеер потока go2rtc: WebSocket /go2rtc/api/ws?src=<name>,
 * fMP4-сегменты в MediaSource. Реконнект с бэкоффом.
 *
 * Логика доставки повторяет референсный плеер go2rtc (video-rtc.js):
 * на iOS Safari используется ManagedMediaSource через `srcObject`
 * (iOS 17.1+) с обработкой startstreaming/endstreaming — без этого
 * видео «замерзает картинкой». На десктопе — обычный MediaSource.
 */

interface ManagedMediaSourceCtor {
  new (): MediaSource;
  isTypeSupported(type: string): boolean;
}

type ManagedVideo = HTMLVideoElement & {
  disableRemotePlayback: boolean;
  srcObject: MediaProvider | MediaSource | null;
};

function resolveMediaSource(): { ctor: ManagedMediaSourceCtor; managed: boolean } | null {
  const w = window as unknown as {
    ManagedMediaSource?: ManagedMediaSourceCtor;
    MediaSource?: ManagedMediaSourceCtor;
  };
  if (w.ManagedMediaSource) return { ctor: w.ManagedMediaSource, managed: true };
  if (w.MediaSource) return { ctor: w.MediaSource, managed: false };
  return null;
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
    const video = videoRef.current as ManagedVideo | null;
    if (!video) return;

    const resolved = resolveMediaSource();
    if (!resolved) {
      setUnsupported(true);
      onStateChange?.(false);
      return;
    }
    const { ctor: MS, managed } = resolved;

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

        // Привязка источника: iOS требует srcObject + disableRemotePlayback,
        // десктоп — createObjectURL. Смешивать нельзя.
        let objectUrl: string | null = null;
        if (managed) {
          video.disableRemotePlayback = true;
          video.srcObject = ms;
        } else {
          objectUrl = URL.createObjectURL(ms);
          video.src = objectUrl;
        }

        let sb: SourceBuffer | null = null;
        // Активна ли доставка данных. ManagedMediaSource сам ставит на паузу
        // (endstreaming) когда буфера достаточно — тогда не докидываем.
        let streaming = !managed;

        // Буфер как в go2rtc: копим байты, пока SourceBuffer занят.
        const bufArr = new Uint8Array(4 * 1024 * 1024);
        let bufLen = 0;

        const append = (data: ArrayBuffer) => {
          if (!sb) return;
          const bytes = new Uint8Array(data);
          try {
            if (sb.updating || bufLen > 0) {
              bufArr.set(bytes, bufLen);
              bufLen += bytes.byteLength;
            } else {
              sb.appendBuffer(bytes);
            }
          } catch {
            ws?.close();
          }
        };

        const onUpdateEnd = () => {
          if (!sb) return;
          // Досыпаем накопленное
          if (bufLen > 0 && !sb.updating) {
            try {
              sb.appendBuffer(bufArr.slice(0, bufLen));
              bufLen = 0;
            } catch {
              ws?.close();
              return;
            }
          }
          // Держим окно ~5 c у живого края — экономим память iOS.
          if (!sb.updating && sb.buffered.length) {
            const end = sb.buffered.end(sb.buffered.length - 1);
            const start0 = sb.buffered.start(0);
            const start = end - 5;
            if (start > start0) {
              try {
                sb.remove(start0, start);
                ms.setLiveSeekableRange?.(start, end);
              } catch { /* не критично */ }
            }
          }
        };

        // Safari при просадке ставит видео на паузу — возвращаемся к краю.
        const onWaiting = () => {
          if (video.buffered.length) {
            const end = video.buffered.end(video.buffered.length - 1);
            if (end - video.currentTime > 1) video.currentTime = end - 0.5;
          }
          video.play().catch(() => {});
        };
        video.addEventListener("waiting", onWaiting);

        // ManagedMediaSource: сигналы «докидывай» / «хватит».
        const onStart = () => { streaming = true; };
        const onStop = () => { streaming = false; };
        (ms as unknown as EventTarget).addEventListener("startstreaming", onStart);
        (ms as unknown as EventTarget).addEventListener("endstreaming", onStop);

        const cleanup = () => {
          video.removeEventListener("waiting", onWaiting);
          (ms as unknown as EventTarget).removeEventListener("startstreaming", onStart);
          (ms as unknown as EventTarget).removeEventListener("endstreaming", onStop);
          if (objectUrl) URL.revokeObjectURL(objectUrl);
        };

        ms.addEventListener("sourceopen", () => {
          // Просим у сервера дорожки в H.264-профилях, что играет этот браузер.
          const codecs = ["avc1.640029", "avc1.64001F", "avc1.42E01F", "mp4a.40.2"]
            .filter((c) => !MS.isTypeSupported || MS.isTypeSupported(`video/mp4; codecs="${c}"`) || c.startsWith("mp4a"));
          ws?.send(JSON.stringify({ type: "mse", value: codecs.join(",") }));
        }, { once: true });

        ws.onmessage = (ev) => {
          try {
            if (typeof ev.data === "string") {
              const msg = JSON.parse(ev.data);
              if (msg.type === "mse" && !sb) {
                sb = ms.addSourceBuffer(msg.value);
                sb.mode = "segments";
                sb.addEventListener("updateend", onUpdateEnd);
                attempt = 0;
                setState(true);
                video.play().catch(() => {});
              }
            } else if (streaming || !managed) {
              append(ev.data);
            }
          } catch {
            ws?.close();
          }
        };
        ws.onclose = () => {
          cleanup();
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
      try {
        video.removeAttribute("src");
        video.srcObject = null;
        video.load();
      } catch { /* noop */ }
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
