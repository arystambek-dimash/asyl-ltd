"use client";
import { useEffect, useRef, useState } from "react";

/**
 * MSE-плеер потока go2rtc: WebSocket /go2rtc/api/ws?src=<name>,
 * fMP4-сегменты в MediaSource. Реконнект с бэкоффом.
 */
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

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
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
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/go2rtc/api/ws?src=${encodeURIComponent(src)}`);
      ws.binaryType = "arraybuffer";

      const ms = new MediaSource();
      video.src = URL.createObjectURL(ms);
      let sb: SourceBuffer | null = null;
      const queue: ArrayBuffer[] = [];

      const pump = () => {
        if (sb && !sb.updating && queue.length) sb.appendBuffer(queue.shift()!);
      };

      ws.onopen = () => {
        ws!.send(JSON.stringify({ type: "mse", value: "avc1.640029,avc1.64001F,mp4a.40.2" }));
      };
      ws.onmessage = (ev) => {
        if (typeof ev.data === "string") {
          const msg = JSON.parse(ev.data);
          if (msg.type === "mse") {
            const init = () => {
              sb = ms.addSourceBuffer(msg.value);
              sb.mode = "segments";
              sb.addEventListener("updateend", pump);
              attempt = 0;
              setState(true);
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
      };
      ws.onclose = () => {
        if (disposed) return;
        setState(false);
        attempt += 1;
        retry = setTimeout(connect, Math.min(15000, 1000 * 2 ** attempt));
      };
      ws.onerror = () => ws?.close();
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
