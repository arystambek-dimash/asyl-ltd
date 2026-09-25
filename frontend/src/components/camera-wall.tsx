"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Grid2x2, Pencil, RectangleHorizontal, ScanLine, Video, VideoOff } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { ensureCameraStreamToken } from "@/lib/camera-stream-auth";
import { isLogicalCamera, playableCameras, type PlayableCamera } from "@/lib/shipping-cameras";
import type { CameraFeed } from "@/lib/types";
import { CameraLineModal } from "@/components/camera-line-modal";
import { CameraStream } from "@/components/camera-stream";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { useAuth } from "@/store/auth";

const CAMERA_REFRESH_MS = 30 * 1000;
const RETRY_MAX_MS = 60 * 1000;

export function CameraTile({
  cam,
  onOnline,
  onClick,
  onRename,
  onConfigureLine,
}: {
  // Только играбельные камеры (с потоком); недоступные не показываем.
  cam: PlayableCamera;
  onOnline: (id: string, online: boolean) => void;
  onClick?: () => void;
  onRename?: (camera: PlayableCamera) => void;
  onConfigureLine?: (camera: PlayableCamera) => void;
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
      <CameraStream src={cam.src} onStateChange={handleState} className="absolute inset-0 h-full w-full object-cover" />

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

/** Переименование камеры; монтируется на время правки одной камеры. */
function CameraRenameModal({
  camera,
  onClose,
  onRenamed,
}: {
  camera: PlayableCamera;
  onClose: () => void;
  onRenamed: (src: string, name: string) => void;
}) {
  const [name, setName] = useState(camera.zone);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setSaving(true);
    setError("");
    try {
      const response = await api.patch<{ camera: string; name: string }>("/cameras/", {
        camera: camera.src,
        name,
      });
      onRenamed(response.data.camera, response.data.name);
      onClose();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      eyebrow="Настройка администратора"
      title="Название камеры"
      description="Новое имя будет использоваться во всех разделах и на всех устройствах."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button disabled={saving || !name.trim()} onClick={() => void save()}>
            <Check className="size-4" /> {saving ? "Сохранение…" : "Сохранить"}
          </Button>
        </>
      }
    >
      <label className="block">
        <span className="mb-2 block text-sm font-medium text-slate-700">Имя камеры</span>
        <Input
          autoFocus
          maxLength={80}
          value={name}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && name.trim() && !saving) {
              event.preventDefault();
              void save();
            }
          }}
          placeholder="Например, Главные ворота"
        />
      </label>
      <p className="mt-2 text-xs text-slate-400">
        Системная камера: {camera.name} · {camera.src}
      </p>
      {error && <p className="mt-3 text-sm text-[var(--destructive)]">{error}</p>}
    </Modal>
  );
}

const VIEW_MODES = [
  { mode: "grid", title: "Сетка", Icon: Grid2x2 },
  { mode: "single", title: "Одна камера", Icon: RectangleHorizontal },
] as const;

export function CameraWall() {
  const { me } = useAuth();
  const [cameras, setCameras] = useState<CameraFeed[]>([]);
  const [loading, setLoading] = useState(true);
  const [inventoryError, setInventoryError] = useState("");
  const [tokenError, setTokenError] = useState("");
  const [retryVersion, setRetryVersion] = useState(0);
  const inventoryFailures = useRef(0);
  const playable = playableCameras(cameras);
  const [mode, setMode] = useState<"grid" | "single">("grid");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [onlineIds, setOnlineIds] = useState<Set<string>>(new Set());
  const [renaming, setRenaming] = useState<PlayableCamera | null>(null);
  const [lineCamera, setLineCamera] = useState<PlayableCamera | null>(null);
  const canRename = can(me, "sys_permissions.manage");
  const canConfigureLine = !!me?.is_superuser;

  const updateCamera = useCallback((src: string, change: Partial<CameraFeed>) => {
    setCameras((current) => current.map((camera) => (camera.src === src ? { ...camera, ...change } : camera)));
  }, []);

  // Живой инвентарь: при ошибке сохраняем последнюю успешную выборку, а
  // повторяем запрос с ограниченным бэкоффом. Скрытая вкладка не опрашивает;
  // после восстановления сети и возврата во вкладку не ждём следующего таймера.
  useVisiblePolling(
    async ({ signal, first }) => {
      if (first) inventoryFailures.current = 0;
      try {
        const response = await api.get<CameraFeed[]>("/cameras/", { timeout: 10_000, signal });
        if (!Array.isArray(response.data)) throw new Error("invalid camera inventory");
        if (signal.aborted) return;
        setCameras(response.data);
        setInventoryError("");
        inventoryFailures.current = 0;
      } catch (cause) {
        if (signal.aborted) return;
        setInventoryError(apiError(cause));
        inventoryFailures.current += 1;
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    },
    () =>
      inventoryFailures.current
        ? Math.min(RETRY_MAX_MS, 1000 * 2 ** Math.min(inventoryFailures.current - 1, 6))
        : CAMERA_REFRESH_MS,
    true,
    { immediate: true, resetKey: retryVersion },
  );

  // Cookie go2rtc плитки получают и продлевают сами (CameraStream). Здесь
  // только показываем, что доступа к потокам нет, с кнопкой «Повторить».
  useEffect(() => {
    let disposed = false;
    ensureCameraStreamToken().then(
      () => {
        if (!disposed) setTokenError("");
      },
      (cause) => {
        if (!disposed) setTokenError(apiError(cause));
      },
    );
    return () => {
      disposed = true;
    };
  }, [retryVersion]);

  // Удалённая из нового инвентаря камера не должна оставаться в счётчике.
  useEffect(() => {
    const ids = new Set(cameras.map((camera) => camera.id));
    setOnlineIds((prev) => {
      const next = new Set([...prev].filter((id) => ids.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [cameras]);

  const handleOnline = useCallback((id: string, online: boolean) => {
    // Поток подключился — значит, cookie go2rtc уже есть.
    if (online) setTokenError("");
    setOnlineIds((prev) => {
      if (prev.has(id) === online) return prev;
      const next = new Set(prev);
      if (online) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  /** Общие действия плитки: онлайн-счётчик, переименование, линия подсчёта. */
  const tileActions = (camera: PlayableCamera) => ({
    onOnline: handleOnline,
    onRename: canRename ? setRenaming : undefined,
    onConfigureLine: canConfigureLine && isLogicalCamera(camera.src) ? setLineCamera : undefined,
  });

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
              {VIEW_MODES.map(({ mode: value, title, Icon }) => (
                <button
                  key={value}
                  onClick={() => setMode(value)}
                  title={title}
                  className={cn(
                    "rounded-md p-1.5 transition-colors",
                    mode === value
                      ? "bg-[var(--accent)] text-[var(--foreground)]"
                      : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
                  )}
                >
                  <Icon className="size-4" />
                </button>
              ))}
            </div>
          )}
        </div>

        {(inventoryError || tokenError) && (
          <ErrorAlert
            message={inventoryError || tokenError}
            onRetry={() => setRetryVersion((version) => version + 1)}
            className="mx-4 mt-4"
          />
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
                  {...tileActions(c)}
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
              <CameraTile key={active.id} cam={active} {...tileActions(active)} />
              <div className="grid grid-cols-4 gap-2 sm:grid-cols-6">
                {playable
                  .filter((c) => c.id !== active.id)
                  .map((c) => (
                    <CameraTile key={c.id} cam={c} {...tileActions(c)} onClick={() => setActiveId(c.id)} />
                  ))}
              </div>
            </div>
          ) : null}
        </div>
      </section>

      {renaming && (
        <CameraRenameModal
          key={renaming.src}
          camera={renaming}
          onClose={() => setRenaming(null)}
          onRenamed={(src, zone) => updateCamera(src, { zone })}
        />
      )}

      {lineCamera && (
        <CameraLineModal
          key={lineCamera.src}
          camera={lineCamera}
          onClose={() => setLineCamera(null)}
          onConfigChange={(src, lineConfig) => updateCamera(src, { line_config: lineConfig })}
        />
      )}
    </>
  );
}
