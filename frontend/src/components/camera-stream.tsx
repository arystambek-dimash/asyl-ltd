"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

// Cookie живёт 12 часов. Обновляем её заранее, чтобы долгоживущий экран не
// обнаруживал истечение только в момент следующего реконнекта WebSocket.
const TOKEN_RENEW_AFTER_MS = 10 * 60 * 60 * 1000;
const TOKEN_CHECK_INTERVAL_MS = 10 * 60 * 1000;
const AUTH_RENEW_COOLDOWN_MS = 60 * 1000;
const STARTUP_TIMEOUT_MS = 20 * 1000;
const NO_DATA_TIMEOUT_MS = 20 * 1000;
const NO_FRAME_TIMEOUT_MS = 25 * 1000;
const WATCHDOG_INTERVAL_MS = 5 * 1000;

let tokenIssuedAt = 0;
let tokenRequest: Promise<void> | null = null;
let lastForcedTokenRenewal = 0;

/**
 * Получение cam_token дедуплицировано для всей страницы: сетка из десяти
 * камер делает один запрос, а не десять. `force` используется после отказа
 * WebSocket до первого кадра (nginx при 403 не сообщает браузеру HTTP-код).
 */
export function ensureCameraStreamToken(force = false): Promise<void> {
  const now = Date.now();
  if (tokenRequest) return tokenRequest;
  if (!force && tokenIssuedAt && now - tokenIssuedAt < TOKEN_RENEW_AFTER_MS) {
    return Promise.resolve();
  }
  // Все offline-камеры также закрываются до первого кадра. Ограничиваем
  // принудительное обновление, чтобы такой сбой не создавал request storm.
  if (force && lastForcedTokenRenewal && now - lastForcedTokenRenewal < AUTH_RENEW_COOLDOWN_MS) {
    return Promise.resolve();
  }
  if (force) lastForcedTokenRenewal = now;

  tokenRequest = api
    .post("/cameras/token/", undefined, { timeout: 10_000 })
    .then(() => {
      tokenIssuedAt = Date.now();
    })
    .finally(() => {
      tokenRequest = null;
    });
  return tokenRequest;
}

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
    let tokenCheck: ReturnType<typeof setInterval> | null = null;
    let cleanupCurrent: (() => void) | null = null;
    let attempt = 0;
    let disposed = false;

    const setState = (v: boolean) => {
      setOnline(v);
      onStateChange?.(v);
    };

    const scheduleReconnect = () => {
      if (disposed || retry) return;
      setState(false);
      attempt += 1;
      const base = Math.min(30_000, 1000 * 2 ** Math.min(attempt - 1, 5));
      // Камеры не должны одновременно штурмовать go2rtc после общего сбоя.
      const delay = Math.round(base * (0.8 + Math.random() * 0.4));
      retry = setTimeout(() => {
        retry = null;
        void connect();
      }, delay);
    };

    const connect = async () => {
      if (disposed) return;
      try {
        // Нельзя открывать WS до установки cookie. Ошибка токена проходит тем
        // же ограниченным экспоненциальным бэкоффом, что и ошибка потока.
        await ensureCameraStreamToken();
        if (disposed) return;

        const proto = location.protocol === "https:" ? "wss" : "ws";
        ws = new WebSocket(`${proto}://${location.host}/go2rtc/api/ws?src=${encodeURIComponent(src)}`);
        ws.binaryType = "arraybuffer";
        const thisWs = ws;
        const connectedAt = Date.now();
        let startupDeadlineAt = connectedAt + STARTUP_TIMEOUT_MS;

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
        let hadMedia = false;
        let lastMediaAt = connectedAt;
        let lastFrameAt = connectedAt;
        let lastVideoTime = video.currentTime;

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
            thisWs.close();
          }
        };

        // Возврат к живому краю (как в референсном video-rtc.js): без него
        // MSE копит буфер и картинка отстаёт от реального времени.
        const seekLive = (maxLag: number) => {
          if (!video.buffered.length) return;
          const end = video.buffered.end(video.buffered.length - 1);
          if (end - video.currentTime > maxLag) video.currentTime = end - 0.5;
        };

        const onUpdateEnd = () => {
          if (!sb) return;
          // Досыпаем накопленное
          if (bufLen > 0 && !sb.updating) {
            try {
              sb.appendBuffer(bufArr.slice(0, bufLen));
              bufLen = 0;
            } catch {
              thisWs.close();
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
          // Дрейф-гард: задержка выросла (фоновая вкладка, просадка
          // декодера) — догоняем край, не дожидаясь опустошения буфера.
          seekLive(3);
        };

        // Safari при просадке ставит видео на паузу — возвращаемся к краю.
        const onWaiting = () => {
          seekLive(1);
          video.play().catch(() => {});
        };
        video.addEventListener("waiting", onWaiting);

        const onFrameProgress = () => {
          if (video.currentTime > lastVideoTime + 0.01) {
            lastVideoTime = video.currentTime;
            lastFrameAt = Date.now();
          }
        };
        video.addEventListener("timeupdate", onFrameProgress);
        video.addEventListener("playing", onFrameProgress);

        // Вернулись во вкладку — сразу свежая картинка, не догоняющая.
        const onVisible = () => {
          if (document.visibilityState !== "visible") return;
          // После сна вкладки даём декодеру полное окно watchdog на запуск.
          const now = Date.now();
          lastMediaAt = now;
          lastFrameAt = now;
          lastVideoTime = video.currentTime;
          if (!hadMedia) startupDeadlineAt = now + STARTUP_TIMEOUT_MS;
          seekLive(1);
          video.play().catch(() => {});
        };
        document.addEventListener("visibilitychange", onVisible);

        // ManagedMediaSource: сигналы «докидывай» / «хватит».
        const onStart = () => { streaming = true; };
        const onStop = () => { streaming = false; };
        (ms as unknown as EventTarget).addEventListener("startstreaming", onStart);
        (ms as unknown as EventTarget).addEventListener("endstreaming", onStop);

        const watchdog = setInterval(() => {
          if (document.visibilityState !== "visible") return;
          const now = Date.now();
          if (thisWs.readyState === WebSocket.CONNECTING) {
            if (now > startupDeadlineAt) thisWs.close();
            return;
          }
          if (thisWs.readyState !== WebSocket.OPEN) return;
          onFrameProgress();
          if (!hadMedia) {
            if (now > startupDeadlineAt) thisWs.close();
            return;
          }
          // Различаем зависший сокет и зависший декодер: постоянный поток
          // байтов не должен маскировать картинку, застывшую на одном кадре.
          if (now - lastMediaAt > NO_DATA_TIMEOUT_MS || now - lastFrameAt > NO_FRAME_TIMEOUT_MS) {
            thisWs.close();
          } else if (video.paused) {
            video.play().catch(() => {});
          }
        }, WATCHDOG_INTERVAL_MS);

        let cleaned = false;
        const cleanup = () => {
          if (cleaned) return;
          cleaned = true;
          clearInterval(watchdog);
          video.removeEventListener("waiting", onWaiting);
          video.removeEventListener("timeupdate", onFrameProgress);
          video.removeEventListener("playing", onFrameProgress);
          document.removeEventListener("visibilitychange", onVisible);
          (ms as unknown as EventTarget).removeEventListener("startstreaming", onStart);
          (ms as unknown as EventTarget).removeEventListener("endstreaming", onStop);
          sb?.removeEventListener("updateend", onUpdateEnd);
          if (objectUrl) URL.revokeObjectURL(objectUrl);
          if (cleanupCurrent === cleanup) cleanupCurrent = null;
        };
        cleanupCurrent = cleanup;

        // Запрос дорожек шлём, только когда открыты И WebSocket, И MediaSource
        // (порядок этих событий не гарантирован — иначе send() теряется).
        let wsOpen = false;
        let msOpen = false;
        let codecsSent = false;
        const requestCodecs = () => {
          if (!wsOpen || !msOpen || codecsSent) return;
          codecsSent = true;
          thisWs.send(JSON.stringify({ type: "mse", value: "avc1.640029,avc1.64001F,avc1.42E01F,mp4a.40.2" }));
        };

        thisWs.onopen = () => { wsOpen = true; requestCodecs(); };
        ms.addEventListener("sourceopen", () => { msOpen = true; requestCodecs(); }, { once: true });

        thisWs.onmessage = (ev) => {
          try {
            if (typeof ev.data === "string") {
              const msg = JSON.parse(ev.data);
              if (msg.type === "mse" && !sb) {
                sb = ms.addSourceBuffer(msg.value);
                sb.mode = "segments";
                sb.addEventListener("updateend", onUpdateEnd);
                video.play().catch(() => {});
              }
            } else if ((streaming || !managed) && sb) {
              const now = Date.now();
              lastMediaAt = now;
              if (!hadMedia) {
                hadMedia = true;
                lastFrameAt = now;
                attempt = 0;
                setState(true);
              }
              append(ev.data);
            }
          } catch {
            thisWs.close();
          }
        };
        thisWs.onclose = () => {
          cleanup();
          if (disposed) return;
          setState(false);
          // 403 на WebSocket недоступен через браузерный API и выглядит как
          // закрытие до первого сообщения. Сразу обновляем cookie; глобальный
          // cooldown не даст offline-камере делать это на каждом реконнекте.
          if (!hadMedia) void ensureCameraStreamToken(true).catch(() => {});
          scheduleReconnect();
        };
        thisWs.onerror = () => thisWs.close();
      } catch {
        // любой сбой инициализации — тихий реконнект, а не падение страницы
        scheduleReconnect();
      }
    };

    void connect();
    // Проверка дешёвая (обычно Promise.resolve), сеть используется только
    // после достижения порога TOKEN_RENEW_AFTER_MS.
    tokenCheck = setInterval(() => {
      void ensureCameraStreamToken().catch(() => {});
    }, TOKEN_CHECK_INTERVAL_MS);
    return () => {
      disposed = true;
      if (retry) clearTimeout(retry);
      if (tokenCheck) clearInterval(tokenCheck);
      cleanupCurrent?.();
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
