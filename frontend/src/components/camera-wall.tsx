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
  validCountingLine,
  type LineDirection,
  type NormalizedLine,
} from "@/components/camera-line-editor";
import { CameraStream, ensureCameraStreamToken } from "@/components/camera-stream";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { useAuth } from "@/store/auth";

const CAMERA_REFRESH_MS = 30 * 1000;
const RETRY_MAX_MS = 60 * 1000;

export interface CameraCountingLine {
  configured: boolean;
  coordinate_space: "normalized";
  line: NormalizedLine | null;
  line_spec?: string | null;
  direction: LineDirection;
  updated_at?: string | null;
}

interface CameraCountingLineSave extends CameraCountingLine {
  saved?: boolean;
  applied_to_processor?: boolean;
  detail?: string;
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

function CameraTile({
  cam,
  ready,
  onOnline,
  onClick,
  onRename,
  onConfigureLine,
  active = false,
}: {
  // Только играбельные камеры (с потоком); недоступные не показываем.
  cam: CameraFeed & { src: string };
  ready: boolean;
  onOnline: (id: string, online: boolean) => void;
  onClick?: () => void;
  onRename?: (camera: CameraFeed & { src: string }) => void;
  onConfigureLine?: (camera: CameraFeed & { src: string }) => void;
  active?: boolean;
}) {
  const [online, setOnline] = useState(false);
  const handleState = useCallback(
    (v: boolean) => {
      setOnline(v);
      onOnline(cam.id, v);
    },
    [cam.id, onOnline]
  );

  return (
    <div
      onClick={onClick}
      className={cn(
        "group relative aspect-video overflow-hidden rounded-lg bg-[#1c1c1e]",
        onClick && "cursor-pointer",
        active && "ring-2 ring-[var(--ring)]",
      )}
    >
      {ready && (
        <CameraStream src={cam.src} onStateChange={handleState}
          className="absolute inset-0 h-full w-full object-cover" />
      )}

      {!online && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 text-white/30">
          <VideoOff className="size-5" />
          <span className="text-[11px]">Нет сигнала</span>
        </div>
      )}

      {(onRename || onConfigureLine) && (
        <div className={cn(
          "absolute right-2 top-2 z-10 flex gap-1.5 transition focus-within:opacity-100",
          onConfigureLine ? "opacity-100" : "opacity-0 group-hover:opacity-100",
        )}>
          {onConfigureLine && (
            <button type="button" title="Настроить линию подсчёта"
              onClick={(event) => {
                event.stopPropagation();
                onConfigureLine(cam);
              }}
              className={cn(
                "flex size-8 items-center justify-center rounded-lg border border-white/15 bg-black/55 text-white/85 shadow-sm backdrop-blur-md transition hover:bg-sky-500 hover:text-white",
                cam.line_config?.configured && "border-sky-300/50 bg-sky-500/80 text-white",
              )}>
              <ScanLine className="size-3.5" />
            </button>
          )}
          {onRename && (
            <button type="button" title="Изменить название камеры"
              onClick={(event) => {
                event.stopPropagation();
                onRename(cam);
              }}
              className="flex size-8 items-center justify-center rounded-lg border border-white/15 bg-black/55 text-white/80 shadow-sm backdrop-blur-md transition hover:bg-white hover:text-slate-900">
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
  const playable = playableCameras(cameras);
  const [mode, setMode] = useState<"grid" | "single">("grid");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tokenReady, setTokenReady] = useState(false);
  const [onlineIds, setOnlineIds] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<(CameraFeed & { src: string }) | null>(null);
  const [cameraName, setCameraName] = useState("");
  const [savingName, setSavingName] = useState(false);
  const [renameError, setRenameError] = useState("");
  const canRename = can(me, "rbac.manage");
  const canConfigureLine = !!me?.is_superuser;
  const lineRequestId = useRef(0);
  const [lineCamera, setLineCamera] = useState<(CameraFeed & { src: string }) | null>(null);
  const [lineDraft, setLineDraft] = useState<NormalizedLine>(defaultCountingLine());
  const [lineDirection, setLineDirection] = useState<LineDirection>("any");
  const [loadingLine, setLoadingLine] = useState(false);
  const [savingLine, setSavingLine] = useState(false);
  const [lineError, setLineError] = useState("");
  const [lineNotice, setLineNotice] = useState("");

  const updateCameraLine = useCallback((src: string, config: CameraCountingLine) => {
    setCameras((current) => current.map((camera) =>
      camera.src === src ? { ...camera, line_config: config } : camera));
  }, []);

  async function configureLine(camera: CameraFeed & { src: string }) {
    if (!canConfigureLine || !/^cam[1-9]\d*$/.test(camera.src)) return;
    const requestId = ++lineRequestId.current;
    const current = camera.line_config;
    setLineCamera(camera);
    setLineDraft(current?.line ? { ...current.line } : defaultCountingLine());
    setLineDirection(current?.direction ?? "any");
    setLineError("");
    setLineNotice("");
    setLoadingLine(true);
    try {
      const response = await api.get<CameraCountingLine>(
        `/cameras/${encodeURIComponent(camera.src)}/counting-line`,
        { timeout: 10_000 },
      );
      if (lineRequestId.current !== requestId) return;
      const config = response.data;
      setLineDraft(config.line ? { ...config.line } : defaultCountingLine());
      setLineDirection(config.direction ?? "any");
      updateCameraLine(camera.src, config);
    } catch (cause) {
      if (lineRequestId.current === requestId) setLineError(apiError(cause));
    } finally {
      if (lineRequestId.current === requestId) setLoadingLine(false);
    }
  }

  function closeLineEditor() {
    lineRequestId.current += 1;
    setLineCamera(null);
    setLineError("");
    setLineNotice("");
  }

  function savedConfig(payload?: Partial<CameraCountingLine>): CameraCountingLine {
    return {
      configured: payload?.configured ?? true,
      coordinate_space: "normalized",
      line: payload?.line ? { ...payload.line } : { ...lineDraft },
      line_spec: payload?.line_spec ?? null,
      direction: payload?.direction ?? lineDirection,
      updated_at: payload?.updated_at ?? new Date().toISOString(),
    };
  }

  async function saveCountingLine() {
    if (!lineCamera || !canConfigureLine || !validCountingLine(lineDraft)) return;
    setSavingLine(true);
    setLineError("");
    setLineNotice("");
    try {
      const response = await api.put<CameraCountingLineSave>(
        `/cameras/${encodeURIComponent(lineCamera.src)}/counting-line`,
        { line: lineDraft, direction: lineDirection },
        { timeout: 12_000 },
      );
      const config = savedConfig(response.data);
      updateCameraLine(lineCamera.src, config);
      setLineDraft(config.line ?? lineDraft);
      setLineDirection(config.direction);
      setLineNotice(
        response.data.applied_to_processor === false
          ? "Линия сохранена. Она применится при следующем запуске модели."
          : "Линия сохранена и готова к подсчёту.",
      );
    } catch (cause) {
      const payload = (cause as AxiosError<CameraCountingLineSave>).response?.data;
      if (payload?.saved) {
        const config = savedConfig(payload);
        updateCameraLine(lineCamera.src, config);
        setLineDraft(config.line ?? lineDraft);
        setLineDirection(config.direction);
        setLineNotice("Линия сохранена. Работающая модель получит её после перезапуска.");
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
      setCameras((current) => current.map((camera) =>
        camera.src === response.data.camera
          ? { ...camera, zone: response.data.name }
          : camera));
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
        if (!disposed) setCameras(response.data);
        failures = 0;
      } catch {
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
  }, []);

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
        }
      } catch {
        if (disposed) return;
        setTokenReady(false);
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
  }, [tokenReady]);

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
                className={cn("rounded-md p-1.5 transition-colors",
                  mode === "grid" ? "bg-[var(--accent)] text-[var(--foreground)]"
                    : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]")}>
                <Grid2x2 className="size-4" />
              </button>
              <button
                onClick={() => setMode("single")}
                title="Одна камера"
                className={cn("rounded-md p-1.5 transition-colors",
                  mode === "single" ? "bg-[var(--accent)] text-[var(--foreground)]"
                    : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]")}>
                <RectangleHorizontal className="size-4" />
              </button>
            </div>
          )}
        </div>

        <div className="p-4">
          {playable.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed py-12 text-[var(--muted-foreground)]">
              <VideoOff className="size-6" />
              <div className="text-sm font-medium">{loading ? "Загрузка…" : "Камеры недоступны"}</div>
              {!loading && <div className="text-xs">NVR не в сети или потоки ещё не настроены</div>}
            </div>
          ) : mode === "grid" || playable.length === 1 ? (
            <div className={cn(
              "grid gap-3",
              playable.length === 1 ? "mx-auto max-w-2xl grid-cols-1" :
              playable.length <= 4 ? "grid-cols-2" : "grid-cols-2 lg:grid-cols-3",
            )}>
              {playable.map((c) => (
                <CameraTile key={c.id} cam={c} ready={tokenReady} onOnline={handleOnline}
                  onRename={canRename ? editCamera : undefined}
                  onConfigureLine={canConfigureLine && /^cam[1-9]\d*$/.test(c.src)
                    ? configureLine : undefined}
                  onClick={playable.length > 1
                    ? () => { setActiveId(c.id); setMode("single"); } : undefined} />
              ))}
            </div>
          ) : active ? (
            <div className="flex flex-col gap-3">
              <CameraTile key={active.id} cam={active} ready={tokenReady} onOnline={handleOnline}
                onRename={canRename ? editCamera : undefined}
                onConfigureLine={canConfigureLine && /^cam[1-9]\d*$/.test(active.src)
                  ? configureLine : undefined} />
              <div className="grid grid-cols-4 gap-2 sm:grid-cols-6">
                {playable.map((c) => (
                  <CameraTile key={c.id} cam={c} ready={tokenReady} onOnline={handleOnline}
                    active={c.id === active.id} onClick={() => setActiveId(c.id)}
                    onRename={canRename ? editCamera : undefined}
                    onConfigureLine={canConfigureLine && /^cam[1-9]\d*$/.test(c.src)
                      ? configureLine : undefined} />
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </section>

      <Modal open={!!editing} onClose={() => setEditing(null)}
        eyebrow="Настройка администратора"
        title="Название камеры"
        description="Новое имя будет использоваться во всех разделах и на всех устройствах."
        footer={(
          <>
            <Button variant="ghost" onClick={() => setEditing(null)}>Отмена</Button>
            <Button disabled={savingName || !cameraName.trim()} onClick={() => void saveCameraName()}>
              <Check className="size-4" /> {savingName ? "Сохранение…" : "Сохранить"}
            </Button>
          </>
        )}>
        <label className="block">
          <span className="mb-2 block text-sm font-medium text-slate-700">Имя камеры</span>
          <Input autoFocus maxLength={80} value={cameraName}
            onChange={(event) => setCameraName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && cameraName.trim() && !savingName) {
                event.preventDefault();
                void saveCameraName();
              }
            }}
            placeholder="Например, Главные ворота" />
        </label>
        {editing && (
          <p className="mt-2 text-xs text-slate-400">
            Системная камера: {editing.name} · {editing.src}
          </p>
        )}
        {renameError && <p className="mt-3 text-sm text-red-600">{renameError}</p>}
      </Modal>

      <Modal
        open={!!lineCamera}
        onClose={closeLineEditor}
        eyebrow="Только для суперпользователя"
        title="Линия подсчёта"
        description={lineCamera
          ? `${lineCamera.zone} · проведите линию непосредственно на живом изображении.`
          : undefined}
        className="max-w-4xl"
        footer={(
          <>
            <div className="mr-auto hidden items-center gap-2 text-xs text-[var(--muted-foreground)] sm:flex">
              <ShieldCheck className="size-4 text-emerald-600" />
              Настройка защищена правами superuser
            </div>
            <Button variant="ghost" onClick={closeLineEditor}>Закрыть</Button>
            <Button
              disabled={loadingLine || savingLine || !validCountingLine(lineDraft)}
              onClick={() => void saveCountingLine()}
              className="min-w-36 bg-sky-600 text-white hover:bg-sky-700"
            >
              {savingLine ? (
                <><LoaderCircle className="size-4 animate-spin" /> Сохранение…</>
              ) : (
                <><Check className="size-4" /> Сохранить линию</>
              )}
            </Button>
          </>
        )}
      >
        {lineCamera && (
          <div className="space-y-4">
            <CameraLineEditor
              src={lineCamera.src}
              line={lineDraft}
              direction={lineDirection}
              ready={tokenReady}
              disabled={loadingLine || savingLine}
              onLineChange={(line) => {
                setLineDraft(line);
                setLineNotice("");
                setLineError("");
              }}
              onDirectionChange={(direction) => {
                setLineDirection(direction);
                setLineNotice("");
                setLineError("");
              }}
            />
            {loadingLine && (
              <div className="flex items-center gap-2 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800">
                <LoaderCircle className="size-4 animate-spin" /> Загружаем сохранённую линию…
              </div>
            )}
            {!validCountingLine(lineDraft) && !loadingLine && (
              <p className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                Линия слишком короткая. Протяните её между двумя разными точками.
              </p>
            )}
            {lineNotice && (
              <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800">
                {lineNotice}
              </p>
            )}
            {lineError && (
              <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {lineError}
              </p>
            )}
          </div>
        )}
      </Modal>
    </>
  );
}
