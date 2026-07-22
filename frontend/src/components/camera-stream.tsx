"use client";

import { useEffect, useRef, useState } from "react";
import { ensureCameraStreamToken } from "@/lib/camera-stream-auth";

// Backwards-compatible export for existing camera-wall consumers. The cache
// itself lives in lib so authentication teardown can invalidate it.
export { ensureCameraStreamToken } from "@/lib/camera-stream-auth";

const TOKEN_CHECK_INTERVAL_MS = 10 * 60 * 1000;
const STARTUP_TIMEOUT_MS = 15 * 1000;
const DISCONNECTED_GRACE_MS = 4 * 1000;
const NO_MEDIA_TIMEOUT_MS = 20 * 1000;
const WATCHDOG_INTERVAL_MS = 5 * 1000;

type Go2RtcMessage = {
  type?: string;
  value?: string;
};

function isUdpCandidate(candidate: RTCIceCandidate | null): boolean {
  // `protocol` is available in current browsers. The string check keeps the
  // rule working in older Safari versions too.
  if (!candidate) return true;
  if (candidate.protocol) return candidate.protocol === "udp";
  return candidate.candidate.toLowerCase().includes(" udp ");
}

/**
 * Low-latency camera player. Signalling is authenticated through nginx over
 * WebSocket, while the encrypted media goes directly over WebRTC/SRTP UDP.
 * TCP ICE candidates are deliberately ignored: a slow/lost TCP packet must
 * not stall every following video frame.
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
  const [unsupported, setUnsupported] = useState(false);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (!("RTCPeerConnection" in window)) {
      setUnsupported(true);
      onStateChange?.(false);
      return;
    }

    let disposed = false;
    let ws: WebSocket | null = null;
    let pc: RTCPeerConnection | null = null;
    let remoteStream: MediaStream | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let startupTimer: ReturnType<typeof setTimeout> | null = null;
    let disconnectedTimer: ReturnType<typeof setTimeout> | null = null;
    let watchdogTimer: ReturnType<typeof setTimeout> | null = null;
    let tokenTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let generation = 0;
    let hadMedia = false;
    let lastMediaAt = Date.now();
    let lastBytesReceived = -1;

    const setState = (value: boolean) => {
      setOnline(value);
      onStateChange?.(value);
    };

    const clearConnectionTimers = () => {
      if (startupTimer) clearTimeout(startupTimer);
      if (disconnectedTimer) clearTimeout(disconnectedTimer);
      if (watchdogTimer) clearTimeout(watchdogTimer);
      startupTimer = null;
      disconnectedTimer = null;
      watchdogTimer = null;
    };

    const releaseConnection = () => {
      generation += 1;
      clearConnectionTimers();

      const oldWs = ws;
      ws = null;
      if (oldWs) {
        oldWs.onopen = null;
        oldWs.onmessage = null;
        oldWs.onerror = null;
        oldWs.onclose = null;
        oldWs.close();
      }

      const oldPc = pc;
      pc = null;
      if (oldPc) {
        oldPc.onicecandidate = null;
        oldPc.ontrack = null;
        oldPc.onconnectionstatechange = null;
        oldPc.close();
      }

      remoteStream?.getTracks().forEach((track) => track.stop());
      remoteStream = null;
      video.srcObject = null;
      delete video.dataset.transport;
    };

    const scheduleReconnect = () => {
      if (disposed || retryTimer) return;
      setState(false);
      releaseConnection();
      attempt += 1;
      const base = Math.min(15_000, 750 * 2 ** Math.min(attempt - 1, 5));
      // Multiple tiles should not reconnect in one burst after a common outage.
      const delay = Math.round(base * (0.8 + Math.random() * 0.4));
      retryTimer = setTimeout(() => {
        retryTimer = null;
        void connect();
      }, delay);
    };

    const markMediaPlaying = () => {
      if (disposed || !pc || pc.connectionState !== "connected") return;
      hadMedia = true;
      lastMediaAt = Date.now();
      attempt = 0;
      video.dataset.transport = "webrtc-udp";
      setState(true);
    };

    const connect = async () => {
      if (disposed) return;
      releaseConnection();
      const thisGeneration = generation;
      hadMedia = false;
      lastMediaAt = Date.now();
      lastBytesReceived = -1;

      try {
        await ensureCameraStreamToken();
        if (disposed || thisGeneration !== generation) return;

        const proto = location.protocol === "https:" ? "wss" : "ws";
        const thisWs = new WebSocket(`${proto}://${location.host}/go2rtc/api/ws?src=${encodeURIComponent(src)}`);
        ws = thisWs;

        const thisPc = new RTCPeerConnection({
          bundlePolicy: "max-bundle",
          iceServers: [{ urls: "stun:stun.cloudflare.com:3478" }, { urls: "stun:stun.l.google.com:19302" }],
        });
        pc = thisPc;
        remoteStream = new MediaStream();

        thisPc.addTransceiver("video", { direction: "recvonly" });

        thisPc.onicecandidate = (event) => {
          if (thisGeneration !== generation || thisWs.readyState !== WebSocket.OPEN) return;
          if (!isUdpCandidate(event.candidate)) return;
          thisWs.send(
            JSON.stringify({
              type: "webrtc/candidate",
              value: event.candidate?.candidate ?? "",
            }),
          );
        };

        thisPc.ontrack = (event) => {
          if (thisGeneration !== generation || !remoteStream) return;
          if (!remoteStream.getTracks().some((track) => track.id === event.track.id)) {
            remoteStream.addTrack(event.track);
          }
          video.srcObject = remoteStream;
          video.play().catch(() => {});
        };

        thisPc.onconnectionstatechange = () => {
          if (thisGeneration !== generation) return;
          if (thisPc.connectionState === "connected") {
            if (disconnectedTimer) clearTimeout(disconnectedTimer);
            disconnectedTimer = null;
            lastMediaAt = Date.now();
            video.play().catch(() => {});
          } else if (thisPc.connectionState === "disconnected") {
            if (!disconnectedTimer) {
              disconnectedTimer = setTimeout(scheduleReconnect, DISCONNECTED_GRACE_MS);
            }
          } else if (thisPc.connectionState === "failed") {
            scheduleReconnect();
          }
        };

        thisWs.onopen = async () => {
          if (thisGeneration !== generation) return;
          try {
            const offer = await thisPc.createOffer();
            await thisPc.setLocalDescription(offer);
            if (thisGeneration !== generation || thisWs.readyState !== WebSocket.OPEN) return;
            thisWs.send(JSON.stringify({ type: "webrtc/offer", value: offer.sdp }));
          } catch {
            scheduleReconnect();
          }
        };

        thisWs.onmessage = (event) => {
          if (thisGeneration !== generation || typeof event.data !== "string") return;
          try {
            const message = JSON.parse(event.data) as Go2RtcMessage;
            if (message.type === "webrtc/candidate" && message.value !== undefined) {
              // The server is configured UDP-only. Keep this client-side guard
              // too, so a future config regression cannot silently return TCP.
              if (message.value.toLowerCase().includes(" tcp ")) return;
              void thisPc
                .addIceCandidate(message.value ? { candidate: message.value, sdpMid: "0" } : null)
                .catch(() => {});
            } else if (message.type === "webrtc/answer" && message.value) {
              void thisPc.setRemoteDescription({ type: "answer", sdp: message.value }).catch(scheduleReconnect);
            } else if (message.type === "error" && message.value?.includes("webrtc/offer")) {
              scheduleReconnect();
            }
          } catch {
            scheduleReconnect();
          }
        };
        thisWs.onerror = () => thisWs.close();
        thisWs.onclose = () => {
          if (thisGeneration !== generation || disposed) return;
          // Once ICE is connected, media no longer depends on signalling WS.
          if (thisPc.connectionState !== "connected") {
            if (!hadMedia) void ensureCameraStreamToken(true).catch(() => {});
            scheduleReconnect();
          }
        };

        startupTimer = setTimeout(() => {
          if (!hadMedia) scheduleReconnect();
        }, STARTUP_TIMEOUT_MS);

        const runWatchdog = async () => {
          watchdogTimer = null;
          if (disposed || thisGeneration !== generation) return;
          if (document.visibilityState === "visible" && thisPc.connectionState === "connected") {
            try {
              const stats = await thisPc.getStats();
              let bytesReceived = 0;
              stats.forEach((report) => {
                if (report.type === "inbound-rtp" && report.kind === "video") {
                  bytesReceived += Number(report.bytesReceived ?? 0);
                }
              });
              if (bytesReceived > lastBytesReceived) {
                lastBytesReceived = bytesReceived;
                lastMediaAt = Date.now();
              }
              if (Date.now() - lastMediaAt > NO_MEDIA_TIMEOUT_MS) {
                scheduleReconnect();
                return;
              }
            } catch {
              scheduleReconnect();
              return;
            }
          }
          if (!disposed && thisGeneration === generation) {
            watchdogTimer = setTimeout(() => void runWatchdog(), WATCHDOG_INTERVAL_MS);
          }
        };
        watchdogTimer = setTimeout(() => void runWatchdog(), WATCHDOG_INTERVAL_MS);
      } catch {
        scheduleReconnect();
      }
    };

    video.addEventListener("loadeddata", markMediaPlaying);
    video.addEventListener("playing", markMediaPlaying);

    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      lastMediaAt = Date.now();
      if (!pc || pc.connectionState === "failed" || pc.connectionState === "closed") {
        scheduleReconnect();
      } else {
        video.play().catch(() => {});
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    void connect();
    const renewCameraToken = async () => {
      tokenTimer = null;
      try {
        await ensureCameraStreamToken();
      } catch {
        // A reconnect will force a renewal if the cached cookie is unusable.
      }
      if (!disposed) tokenTimer = setTimeout(() => void renewCameraToken(), TOKEN_CHECK_INTERVAL_MS);
    };
    tokenTimer = setTimeout(() => void renewCameraToken(), TOKEN_CHECK_INTERVAL_MS);

    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (tokenTimer) clearTimeout(tokenTimer);
      document.removeEventListener("visibilitychange", onVisible);
      video.removeEventListener("loadeddata", markMediaPlaying);
      video.removeEventListener("playing", markMediaPlaying);
      releaseConnection();
      try {
        video.removeAttribute("src");
        video.load();
      } catch {
        /* noop */
      }
    };
  }, [src, onStateChange]);

  if (unsupported) {
    return (
      <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/85 px-3 text-center">
        <span className="text-[11px] leading-snug text-white/60">
          Браузер не поддерживает WebRTC. Обновите браузер или откройте страницу с другого устройства.
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
