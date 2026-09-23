"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { AxiosError } from "axios";
import {
  Check,
  Grid2x2,
  LoaderCircle,
  Pencil,
  RectangleHorizontal,
  ScanLine,
  ShieldCheck,
  Video,
  VideoOff,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import {
  CameraLineEditor,
  defaultCountingLine,
  type LineDirection,
  type NormalizedLine,
  type VerificationLine,
} from "@/components/camera-line-editor";
import { CameraStream, ensureCameraStreamToken } from "@/components/camera-stream";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import {
  countingLineSaveBody,
  lineSetupError,
  normalizeVerificationLines,
  verificationLinesSupported,
} from "@/lib/camera-counting-line";
import { useAuth } from "@/store/auth";

const CAMERA_REFRESH_MS = 30 * 1000;
const RETRY_MAX_MS = 60 * 1000;
const LINE_SYNC_MS = 3 * 1000;

export interface CameraCountingLine {
  configured: boolean;
  coordinate_space: "normalized";
  line: NormalizedLine | null;
  line_spec?: string | null;
  direction: LineDirection;
  updated_at?: string | null;
  /** Линии проверки мешков: классифицируют, но не считают. */
  verification_lines?: VerificationLine[] | null;
  /** false — AI-сервис старый и не хранит линии проверки. */
  verification_lines_supported?: boolean;
  /** Только в ответе на «Обновить статус»: работает ли камера с этими линиями. */
  line_applied?: "applied" | "not_applied" | "not_running";
}

interface CameraCountingLineSave extends CameraCountingLine {
  saved?: boolean;
  applied_to_processor?: boolean;
  detail?: string;
  code?: string;
}

const SAVED_NOT_APPLIED = "Сохранено, но не применено к камере — обновите статус";
const STILL_NOT_APPLIED = "Камера всё ещё работает со старыми линиями. Сохраните ещё раз.";

function countingLineSignature(config: CameraCountingLine | null | undefined) {
  const line = config?.line;
  const checks = normalizeVerificationLines(config?.verification_lines).map((item) =>
    [item.id, item.name, item.line.x1, item.line.y1, item.line.x2, item.line.y2].join(":"),
  );
  return [
    config?.updated_at ?? "",
    config?.direction ?? "any",
    // An AI-service upgrade changes only this: the editor must follow it.
    verificationLinesSupported(config),
    line?.x1 ?? "",
    line?.y1 ?? "",
    line?.x2 ?? "",
    line?.y2 ?? "",
    ...checks,
  ].join("|");
}

/** Камера из живого инвентаря сети (бэкенд строит его из ai_service). */
export interface CameraFeed {
  /** Стабильный ключ: kind + MAC (не меняется при перетасовке каналов NVR). */
  id: string;
  name: string;
  zone: string;
  /** Имя потока в go2rtc (cam2, cam_8c26); null у locked-камер. */
  src: string | null;
  kind: "nvr-channel" | "direct" | "locked";
  /** Живость источника по данным инвентаря (у locked всегда false). */
  online: boolean;
  /** Пояснение для locked: обнаружена, но пароль неизвестен. */
  note?: string;
  /** Сохранённая AI-сервисом линия подсчёта, если камера её поддерживает. */
  line_config?: CameraCountingLine | null;
}

/** Камеры, у которых есть поток для просмотра (locked не играют). */
export function playableCameras(cams: CameraFeed[] | null | undefined) {
  return (cams ?? []).filter((c): c is CameraFeed & { src: string } => !!c.src);
}

export function CameraTile({
  cam,
  ready,
  onOnline,
  onClick,
  onRename,
  onConfigureLine,
}: {
  // Только играбельные камеры (с потоком); недоступные не показываем.
  cam: CameraFeed & { src: string };
  ready: boolean;
  onOnline: (id: string, online: boolean) => void;
  onClick?: () => void;
  onRename?: (camera: CameraFeed & { src: string }) => void;
  onConfigureLine?: (camera: CameraFeed & { src: string }) => void;
}) {
  const [online, setOnline] = useState(false);
  const handleState = useCallback(
    (v: boolean) => {
      setOnline(v);
      onOnline(cam.id, v);
    },
    [cam.id, onOnline],
  );
  const accessibleName = cam.zone.trim() || cam.name;

  return (
    <div className="group relative aspect-video overflow-hidden rounded-lg bg-[#1c1c1e]">
      {ready && (
        <CameraStream
          src={cam.src}
          onStateChange={handleState}
          className="absolute inset-0 h-full w-full object-cover"
        />
      )}

      {!online && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 text-white/30">
          <VideoOff className="size-5" />
          <span className="text-[11px]">Нет сигнала</span>
        </div>
      )}

      {onClick && (
        <button
          type="button"
          aria-label={`Открыть камеру «${accessibleName}»`}
          onClick={onClick}
          className="absolute inset-0 z-[5] cursor-pointer rounded-lg border-0 bg-transparent p-0 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white/90"
        />
      )}

      {(onRename || onConfigureLine) && (
        <div
          className={cn(
            "absolute right-2 top-2 z-10 flex gap-1.5 transition focus-within:opacity-100",
            onConfigureLine ? "opacity-100" : "opacity-0 group-hover:opacity-100",
          )}
        >
          {onConfigureLine && (
            <button
              type="button"
              title="Настроить линию подсчёта"
              onClick={(event) => {
                event.stopPropagation();
                onConfigureLine(cam);
              }}
              className={cn(
                "flex size-8 items-center justify-center rounded-lg border border-white/15 bg-black/55 text-white/85 shadow-sm backdrop-blur-md transition hover:bg-sky-500 hover:text-white",
                cam.line_config?.configured && "border-sky-300/50 bg-sky-500/80 text-white",
              )}
            >
              <ScanLine className="size-3.5" />
            </button>
          )}
          {onRename && (
            <button
              type="button"
              title="Изменить название камеры"
              onClick={(event) => {
                event.stopPropagation();
                onRename(cam);
              }}
              className="flex size-8 items-center justify-center rounded-lg border border-white/15 bg-black/55 text-white/80 shadow-sm backdrop-blur-md transition hover:bg-white hover:text-slate-900"
            >
              <Pencil className="size-3.5" />
            </button>
          )}
        </div>
      )}

      {/* Нижний скрим с именем камеры и статусом — как в UniFi Protect */}
      <div className="absolute inset-x-0 bottom-0 flex items-center justify-between bg-gradient-to-t from-black/70 to-transparent px-2.5 pb-1.5 pt-5">
        <span className="text-xs font-medium text-white drop-shadow-sm">{cam.zone}</span>
        <span className={cn("size-1.5 rounded-full", online ? "bg-emerald-400" : "bg-white/30")} />
      </div>
    </div>
  );
}

export function CameraWall() {
  const { me } = useAuth();
  const [cameras, setCameras] = useState<CameraFeed[]>([]);
  const [loading, setLoading] = useState(true);
  const [inventoryError, setInventoryError] = useState("");
  const [tokenError, setTokenError] = useState("");
  const [retryVersion, setRetryVersion] = useState(0);
  const playable = playableCameras(cameras);
  const [mode, setMode] = useState<"grid" | "single">("grid");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tokenReady, setTokenReady] = useState(false);
  const [onlineIds, setOnlineIds] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<(CameraFeed & { src: string }) | null>(null);
  const [cameraName, setCameraName] = useState("");
  const [savingName, setSavingName] = useState(false);
  const [renameError, setRenameError] = useState("");
  const canRename = can(me, "sys_permissions.manage");
  const canConfigureLine = !!me?.is_superuser;
  const lineRequestId = useRef(0);
  const lineDirty = useRef(false);
  const lineServerSignature = useRef("");
  const [lineCamera, setLineCamera] = useState<(CameraFeed & { src: string }) | null>(null);
  const [linePendingRemote, setLinePendingRemote] = useState<CameraCountingLine | null>(null);
  const [lineDraft, setLineDraft] = useState<NormalizedLine>(defaultCountingLine());
  const [lineDirection, setLineDirection] = useState<LineDirection>("any");
  const [verificationDraft, setVerificationDraft] = useState<VerificationLine[]>([]);
  const [verificationSupported, setVerificationSupported] = useState(false);
  const [lineAuthoritative, setLineAuthoritative] = useState(false);
  const [loadingLine, setLoadingLine] = useState(false);
  const [savingLine, setSavingLine] = useState(false);
  const [lineError, setLineError] = useState("");
  const [lineNotice, setLineNotice] = useState("");
  // Saved on the AI service, but not (verifiably) live on the camera yet.
  const [lineWarning, setLineWarning] = useState("");
  const lineCameraSource = lineCamera?.src ?? null;
  const lineSetupProblem = lineSetupError(lineDraft, verificationDraft);

  const updateCameraLine = useCallback((src: string, config: CameraCountingLine) => {
    setCameras((current) =>
      current.map((camera) => (camera.src === src ? { ...camera, line_config: config } : camera)),
    );
  }, []);

  const acceptLineConfig = useCallback(
    (src: string, config: CameraCountingLine) => {
      setLineDraft(config.line ? { ...config.line } : defaultCountingLine());
      setLineDirection(config.direction ?? "any");
      setVerificationDraft(normalizeVerificationLines(config.verification_lines));
      setVerificationSupported(verificationLinesSupported(config));
      setLineAuthoritative(true);
      lineDirty.current = false;
      lineServerSignature.current = countingLineSignature(config);
      setLinePendingRemote(null);
      updateCameraLine(src, config);
    },
    [updateCameraLine],
  );

  /**
   * Read the authoritative saved lines; null when superseded or failed.
   * ``checkApplied`` also asks whether the running camera uses them.
   */
  async function loadLineConfig(src: string, checkApplied = false) {
    const requestId = ++lineRequestId.current;
    setLoadingLine(true);
    try {
      const response = await api.get<CameraCountingLine>(`/cameras/${encodeURIComponent(src)}/counting-line`, {
        timeout: 10_000,
        ...(checkApplied ? { params: { applied: 1 } } : {}),
      });
      if (lineRequestId.current !== requestId) return null;
      acceptLineConfig(src, response.data);
      return response.data;
    } catch (cause) {
      if (lineRequestId.current === requestId) setLineError(apiError(cause));
      return null;
    } finally {
      if (lineRequestId.current === requestId) setLoadingLine(false);
    }
  }

  async function configureLine(camera: CameraFeed & { src: string }) {
    if (!canConfigureLine || !/^cam[1-9]\d*$/.test(camera.src)) return;
    const current = camera.line_config;
    setLineCamera(camera);
    setLineDraft(current?.line ? { ...current.line } : defaultCountingLine());
    setLineDirection(current?.direction ?? "any");
    setVerificationDraft(normalizeVerificationLines(current?.verification_lines));
    setVerificationSupported(verificationLinesSupported(current));
    setLineAuthoritative(false);
    lineDirty.current = false;
    lineServerSignature.current = countingLineSignature(current);
    setLinePendingRemote(null);
    setLineError("");
    setLineNotice("");
    setLineWarning("");
    await loadLineConfig(camera.src);
  }

  /** The saved file alone proves nothing: ask the running camera. */
  async function refreshLineStatus() {
    if (!lineCamera) return;
    setLineError("");
    setLineNotice("");
    const config = await loadLineConfig(lineCamera.src, true);
    if (!config) return;
    if (config.line_applied === "applied" || config.line_applied === "not_running") {
      setLineWarning("");
      setLineNotice(
        config.line_applied === "applied"
          ? "Линии применены к камере."
          : "Модель на камере не запущена — линии применятся при следующем запуске.",
      );
    } else {
      setLineWarning(STILL_NOT_APPLIED);
    }
  }

  function editLineDraft() {
    lineDirty.current = true;
    setLineNotice("");
    setLineError("");
    setLineWarning("");
  }

  // An editor can stay open while another administrator calibrates the same
  // camera. Poll the exact lightweight endpoint: clean drafts follow remote
  // changes automatically, while dirty drafts surface a conflict instead of
  // being silently overwritten.
  useEffect(() => {
    if (!lineCameraSource) return;
    let disposed = false;
    let inFlight = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const schedule = () => {
      if (disposed) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void pull(), LINE_SYNC_MS);
    };
    const pull = async () => {
      if (disposed || document.visibilityState !== "visible" || inFlight) return;
      inFlight = true;
      const requestId = ++lineRequestId.current;
      try {
        const response = await api.get<CameraCountingLine>(
          `/cameras/${encodeURIComponent(lineCameraSource)}/counting-line`,
          { timeout: 10_000 },
        );
        if (disposed || lineRequestId.current !== requestId) return;
        const remote = response.data;
        const remoteSignature = countingLineSignature(remote);
        if (remoteSignature === lineServerSignature.current) return;
        updateCameraLine(lineCameraSource, remote);
        if (lineDirty.current) {
          setLinePendingRemote((current) => (countingLineSignature(current) === remoteSignature ? current : remote));
        } else {
          acceptLineConfig(lineCameraSource, remote);
          setLineError("");
          setLineNotice("Линия обновлена из настроек камеры.");
        }
      } catch {
        // The initial load already exposes connectivity errors. Background
        // sync is best-effort and retries without replacing a usable draft.
      } finally {
        inFlight = false;
        schedule();
      }
    };
    const pullNow = () => {
      if (document.visibilityState !== "visible") return;
      if (timer) clearTimeout(timer);
      timer = null;
      void pull();
    };

    timer = setTimeout(() => void pull(), LINE_SYNC_MS);
    document.addEventListener("visibilitychange", pullNow);
    window.addEventListener("online", pullNow);
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", pullNow);
      window.removeEventListener("online", pullNow);
    };
  }, [acceptLineConfig, lineCameraSource, updateCameraLine]);

  function closeLineEditor() {
    lineRequestId.current += 1;
    lineDirty.current = false;
    lineServerSignature.current = "";
    setLineCamera(null);
    setLinePendingRemote(null);
    setLineAuthoritative(false);
    setLineError("");
    setLineNotice("");
    setLineWarning("");
  }

  function savedConfig(payload?: Partial<CameraCountingLine>): CameraCountingLine {
    return {
      configured: payload?.configured ?? true,
      coordinate_space: "normalized",
      line: payload?.line ? { ...payload.line } : { ...lineDraft },
      line_spec: payload?.line_spec ?? null,
      direction: payload?.direction ?? lineDirection,
      updated_at: payload?.updated_at ?? new Date().toISOString(),
      verification_lines: payload?.verification_lines ?? (verificationSupported ? verificationDraft : []),
      verification_lines_supported: payload?.verification_lines_supported ?? verificationSupported,
    };
  }

  async function saveCountingLine() {
    if (!lineCamera || !lineAuthoritative || linePendingRemote || !canConfigureLine || lineSetupProblem) return;
    lineRequestId.current += 1; // an older sync GET may not roll this save back
    const sentVerification = verificationSupported ? verificationDraft : null;
    const several = !!sentVerification?.length;
    setSavingLine(true);
    setLineError("");
    setLineNotice("");
    setLineWarning("");
    try {
      const response = await api.put<CameraCountingLineSave>(
        `/cameras/${encodeURIComponent(lineCamera.src)}/counting-line`,
        countingLineSaveBody(lineDraft, lineDirection, sentVerification),
        { timeout: 12_000 },
      );
      acceptLineConfig(lineCamera.src, savedConfig(response.data));
      if (several && response.data.verification_lines_supported === false) {
        setLineWarning("Основная линия сохранена, но AI-сервис не сохранил линии проверки. Обновите AI-сервис.");
      } else {
        const pending = response.data.applied_to_processor === false;
        setLineNotice(
          several
            ? pending
              ? "Линии сохранены. Они применятся при следующем запуске модели."
              : "Линии сохранены и готовы к подсчёту."
            : pending
              ? "Линия сохранена. Она применится при следующем запуске модели."
              : "Линия сохранена и готова к подсчёту.",
        );
      }
    } catch (cause) {
      const payload = (cause as AxiosError<CameraCountingLineSave>).response?.data;
      if (payload?.saved) {
        // The PUT reply is the saved state; the processor has not confirmed it.
        acceptLineConfig(lineCamera.src, savedConfig(payload));
        setLineWarning(SAVED_NOT_APPLIED);
      } else {
        setLineError(apiError(cause));
      }
    } finally {
      setSavingLine(false);
    }
  }

  function editCamera(camera: CameraFeed & { src: string }) {
    setEditing(camera);
    setCameraName(camera.zone);
    setRenameError("");
  }

  async function saveCameraName() {
    if (!editing) return;
    setSavingName(true);
    setRenameError("");
    try {
      const response = await api.patch<{ camera: string; name: string }>("/cameras/", {
        camera: editing.src,
        name: cameraName,
      });
      setCameras((current) =>
        current.map((camera) =>
          camera.src === response.data.camera ? { ...camera, zone: response.data.name } : camera,
        ),
      );
      setEditing(null);
    } catch (cause) {
      setRenameError(apiError(cause));
    } finally {
      setSavingName(false);
    }
  }

  // Живой инвентарь: при ошибке сохраняем последнюю успешную выборку, а
  // повторяем запрос с ограниченным бэкоффом. После восстановления сети и
  // возврата во вкладку не ждём следующего таймера.
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let inFlight = false;
    let rerun = false;
    let failures = 0;

    const schedule = (delay: number) => {
      if (disposed) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        void refresh();
      }, delay);
    };

    const refresh = async () => {
      if (disposed) return;
      if (inFlight) {
        rerun = true;
        return;
      }
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      inFlight = true;
      let nextDelay = CAMERA_REFRESH_MS;
      try {
        const response = await api.get<CameraFeed[]>("/cameras/", { timeout: 10_000 });
        if (!Array.isArray(response.data)) throw new Error("invalid camera inventory");
        if (!disposed) {
          setCameras(response.data);
          setInventoryError("");
        }
        failures = 0;
      } catch (cause) {
        if (!disposed) setInventoryError(apiError(cause));
        failures += 1;
        nextDelay = Math.min(RETRY_MAX_MS, 1000 * 2 ** Math.min(failures - 1, 6));
      } finally {
        inFlight = false;
        if (!disposed) setLoading(false);
        if (rerun) {
          rerun = false;
          void refresh();
        } else {
          schedule(nextDelay);
        }
      }
    };

    const refreshNow = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", refreshNow);
    window.addEventListener("online", refreshNow);
    void refresh();

    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", refreshNow);
      window.removeEventListener("online", refreshNow);
    };
  }, [retryVersion]);

  // cookie-доступ к потокам go2rtc; без неё nginx отдаст 403. Ошибка не
  // оставляет стену навсегда пустой: повторяем с тем же capped backoff.
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;

    const acquire = async () => {
      try {
        await ensureCameraStreamToken();
        if (!disposed) {
          failures = 0;
          setTokenReady(true);
          setTokenError("");
        }
      } catch (cause) {
        if (disposed) return;
        setTokenReady(false);
        setTokenError(apiError(cause));
        failures += 1;
        const delay = Math.min(30_000, 1000 * 2 ** Math.min(failures - 1, 5));
        timer = setTimeout(() => void acquire(), delay);
      }
    };

    const acquireNow = () => {
      if (document.visibilityState !== "visible" || tokenReady) return;
      if (timer) clearTimeout(timer);
      timer = null;
      void acquire();
    };
    document.addEventListener("visibilitychange", acquireNow);
    window.addEventListener("online", acquireNow);
    void acquire();

    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", acquireNow);
      window.removeEventListener("online", acquireNow);
    };
  }, [retryVersion, tokenReady]);

  // Удалённая из нового инвентаря камера не должна оставаться в счётчике.
  useEffect(() => {
    const ids = new Set(cameras.map((camera) => camera.id));
    setOnlineIds((prev) => {
      const next = new Set([...prev].filter((id) => ids.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [cameras]);

  const handleOnline = useCallback((id: string, online: boolean) => {
    setOnlineIds((prev) => {
      if (prev.has(id) === online) return prev;
      const next = new Set(prev);
      if (online) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  // Показываем только играбельные камеры; недоступные (locked) не выводим вовсе.
  const active = playable.find((c) => c.id === activeId) ?? playable[0];

  return (
    <>
      <section className="rounded-xl border bg-[var(--card)] shadow-sm">
        <div className="flex items-center gap-2.5 border-b px-4 py-3">
          <Video className="size-4 text-[var(--muted-foreground)]" />
          <span className="text-sm font-semibold">Камеры</span>
          <span className="text-xs text-[var(--muted-foreground)]">
            {onlineIds.size} из {playable.length} онлайн
          </span>
          {playable.length > 1 && (
            <div className="ml-auto flex items-center gap-0.5">
              <button
                onClick={() => setMode("grid")}
                title="Сетка"
                className={cn(
                  "rounded-md p-1.5 transition-colors",
                  mode === "grid"
                    ? "bg-[var(--accent)] text-[var(--foreground)]"
                    : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
                )}
              >
                <Grid2x2 className="size-4" />
              </button>
              <button
                onClick={() => setMode("single")}
                title="Одна камера"
                className={cn(
                  "rounded-md p-1.5 transition-colors",
                  mode === "single"
                    ? "bg-[var(--accent)] text-[var(--foreground)]"
                    : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
                )}
              >
                <RectangleHorizontal className="size-4" />
              </button>
            </div>
          )}
        </div>

        {(inventoryError || tokenError) && (
          <div
            role="alert"
            className="mx-4 mt-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[var(--destructive)]/20 bg-[var(--destructive)]/5 px-3 py-2 text-sm"
          >
            <span className="text-[var(--destructive)]">{inventoryError || tokenError}</span>
            <button
              type="button"
              onClick={() => setRetryVersion((version) => version + 1)}
              className="font-medium text-[var(--primary)] hover:underline"
            >
              Повторить
            </button>
          </div>
        )}

        <div className="p-4">
          {playable.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed py-12 text-[var(--muted-foreground)]">
              <VideoOff className="size-6" />
              <div className="text-sm font-medium">
                {loading ? "Загрузка…" : inventoryError ? "Не удалось загрузить камеры" : "Камеры недоступны"}
              </div>
              {!loading && !inventoryError && <div className="text-xs">NVR не в сети или потоки ещё не настроены</div>}
            </div>
          ) : mode === "grid" || playable.length === 1 ? (
            <div
              className={cn(
                "grid gap-3",
                playable.length === 1
                  ? "mx-auto max-w-2xl grid-cols-1"
                  : playable.length <= 4
                    ? "grid-cols-2"
                    : "grid-cols-2 lg:grid-cols-3",
              )}
            >
              {playable.map((c) => (
                <CameraTile
                  key={c.id}
                  cam={c}
                  ready={tokenReady}
                  onOnline={handleOnline}
                  onRename={canRename ? editCamera : undefined}
                  onConfigureLine={canConfigureLine && /^cam[1-9]\d*$/.test(c.src) ? configureLine : undefined}
                  onClick={
                    playable.length > 1
                      ? () => {
                          setActiveId(c.id);
                          setMode("single");
                        }
                      : undefined
                  }
                />
              ))}
            </div>
          ) : active ? (
            <div className="flex flex-col gap-3">
              <CameraTile
                key={active.id}
                cam={active}
                ready={tokenReady}
                onOnline={handleOnline}
                onRename={canRename ? editCamera : undefined}
                onConfigureLine={canConfigureLine && /^cam[1-9]\d*$/.test(active.src) ? configureLine : undefined}
              />
              <div className="grid grid-cols-4 gap-2 sm:grid-cols-6">
                {playable
                  .filter((c) => c.id !== active.id)
                  .map((c) => (
                    <CameraTile
                      key={c.id}
                      cam={c}
                      ready={tokenReady}
                      onOnline={handleOnline}
                      onClick={() => setActiveId(c.id)}
                      onRename={canRename ? editCamera : undefined}
                      onConfigureLine={canConfigureLine && /^cam[1-9]\d*$/.test(c.src) ? configureLine : undefined}
                    />
                  ))}
              </div>
            </div>
          ) : null}
        </div>
      </section>

      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        eyebrow="Настройка администратора"
        title="Название камеры"
        description="Новое имя будет использоваться во всех разделах и на всех устройствах."
        footer={
          <>
            <Button variant="ghost" onClick={() => setEditing(null)}>
              Отмена
            </Button>
            <Button disabled={savingName || !cameraName.trim()} onClick={() => void saveCameraName()}>
              <Check className="size-4" /> {savingName ? "Сохранение…" : "Сохранить"}
            </Button>
          </>
        }
      >
        <label className="block">
          <span className="mb-2 block text-sm font-medium text-slate-700">Имя камеры</span>
          <Input
            autoFocus
            maxLength={80}
            value={cameraName}
            onChange={(event) => setCameraName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && cameraName.trim() && !savingName) {
                event.preventDefault();
                void saveCameraName();
              }
            }}
            placeholder="Например, Главные ворота"
          />
        </label>
        {editing && (
          <p className="mt-2 text-xs text-slate-400">
            Системная камера: {editing.name} · {editing.src}
          </p>
        )}
        {renameError && <p className="mt-3 text-sm text-[var(--destructive)]">{renameError}</p>}
      </Modal>

      <Modal
        open={!!lineCamera}
        onClose={closeLineEditor}
        eyebrow="Только для суперпользователя"
        title="Линии подсчёта и проверки"
        description={
          lineCamera ? `${lineCamera.zone} · проведите линии на живом видео или на снимке кадра.` : undefined
        }
        className="max-w-4xl"
        footer={
          <>
            <div className="mr-auto hidden items-center gap-2 text-xs text-[var(--muted-foreground)] sm:flex">
              <ShieldCheck className="size-4 text-emerald-600" />
              Настройка защищена правами superuser
            </div>
            <Button variant="ghost" onClick={closeLineEditor}>
              Закрыть
            </Button>
            <Button
              disabled={loadingLine || savingLine || !lineAuthoritative || !!linePendingRemote || !!lineSetupProblem}
              onClick={() => void saveCountingLine()}
              className="min-w-36 bg-sky-600 text-white hover:bg-sky-700"
            >
              {savingLine ? (
                <>
                  <LoaderCircle className="size-4 animate-spin" /> Сохранение…
                </>
              ) : (
                <>
                  <Check className="size-4" /> Сохранить линии
                </>
              )}
            </Button>
          </>
        }
      >
        {lineCamera && (
          <div className="space-y-4">
            <CameraLineEditor
              key={lineCamera.src}
              src={lineCamera.src}
              line={lineDraft}
              direction={lineDirection}
              ready={tokenReady}
              disabled={loadingLine || savingLine || !lineAuthoritative}
              verificationLines={verificationDraft}
              verificationSupported={verificationSupported}
              onLineChange={(line) => {
                editLineDraft();
                setLineDraft(line);
              }}
              onDirectionChange={(direction) => {
                editLineDraft();
                setLineDirection(direction);
              }}
              onVerificationLinesChange={(lines) => {
                editLineDraft();
                setVerificationDraft(lines);
              }}
            />
            {loadingLine && (
              <div className="flex items-center gap-2 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800">
                <LoaderCircle className="size-4 animate-spin" /> Загружаем сохранённую линию…
              </div>
            )}
            {lineSetupProblem && !loadingLine && (
              <p className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                {lineSetupProblem}
              </p>
            )}
            {linePendingRemote && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                <p className="font-medium">Линию изменил другой пользователь.</p>
                <p className="mt-1 text-xs text-amber-800/80">
                  Ваш черновик сохранён на экране. Выберите, какую версию продолжить редактировать.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      if (!lineCamera) return;
                      acceptLineConfig(lineCamera.src, linePendingRemote);
                      setLineNotice("Загружена новая настройка камеры.");
                    }}
                  >
                    Загрузить новую
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      lineServerSignature.current = countingLineSignature(linePendingRemote);
                      setLinePendingRemote(null);
                      setLineNotice("Оставлен ваш черновик. Сохранение заменит новую настройку.");
                    }}
                  >
                    Оставить мой вариант
                  </Button>
                </div>
              </div>
            )}
            {lineWarning && (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                <span className="font-medium">{lineWarning}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={loadingLine}
                  onClick={() => void refreshLineStatus()}
                >
                  Обновить статус
                </Button>
              </div>
            )}
            {lineNotice && (
              <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800">
                {lineNotice}
              </p>
            )}
            {lineError && (
              <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{lineError}</p>
            )}
          </div>
        )}
      </Modal>
    </>
  );
}
