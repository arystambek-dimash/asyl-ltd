"use client";

import { useMemo, useState } from "react";
import { Check, RefreshCw, ScanLine, Settings2, TrainFront, Video, VideoOff } from "lucide-react";
import { playableCameras, type CameraFeed } from "@/components/camera-wall";
import { CameraStream } from "@/components/camera-stream";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import { showSuccess } from "@/lib/toast";
import type { WagonNumberCameraSettings } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

function CameraChoice({
  camera,
  checked,
  onSelect,
}: {
  camera: CameraFeed & { src: string };
  checked: boolean;
  onSelect: () => void;
}) {
  const [streamOnline, setStreamOnline] = useState(false);

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={checked}
      className={cn(
        "group overflow-hidden rounded-2xl border text-left transition duration-200",
        checked
          ? "border-amber-400 bg-amber-50 shadow-[0_10px_28px_rgba(180,116,24,0.16)] ring-2 ring-amber-500/20"
          : "border-slate-200 bg-white hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-md",
      )}
    >
      <div className="relative aspect-video overflow-hidden bg-[#111318]">
        <CameraStream
          src={camera.src}
          onStateChange={setStreamOnline}
          className="absolute inset-0 size-full object-cover transition duration-300 group-hover:scale-[1.02]"
        />
        {!streamOnline && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 bg-slate-950/75 text-white/45">
            <VideoOff className="size-5" />
            <span className="text-[11px]">Нет изображения</span>
          </div>
        )}
        <div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/70 to-transparent px-3 pb-8 pt-2.5">
          <span className="flex items-center gap-1.5 rounded-full bg-black/40 px-2 py-1 text-[10px] font-semibold text-white backdrop-blur-md">
            <span className={cn("size-1.5 rounded-full", streamOnline ? "bg-emerald-400" : "bg-amber-400")} />
            {streamOnline ? "ОНЛАЙН" : "НЕТ СИГНАЛА"}
          </span>
          <span
            className={cn(
              "flex size-7 items-center justify-center rounded-full border backdrop-blur-md transition",
              checked ? "border-amber-300 bg-amber-500 text-white" : "border-white/35 bg-black/25 text-transparent",
            )}
          >
            <Check className="size-4" />
          </span>
        </div>
      </div>
      <div className="flex items-center gap-3 px-3.5 py-3">
        <span
          className={cn(
            "flex size-9 shrink-0 items-center justify-center rounded-xl",
            checked ? "bg-amber-500 text-white" : "bg-slate-100 text-slate-400",
          )}
        >
          <ScanLine className="size-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-bold text-slate-800">{camera.zone}</span>
          <span className="mt-0.5 block truncate text-[11px] text-slate-400">{camera.name}</span>
        </span>
      </div>
    </button>
  );
}

function AssignmentModal({
  cameras,
  settings,
  onSaved,
  onClose,
}: {
  cameras: (CameraFeed & { src: string })[];
  settings: WagonNumberCameraSettings | null;
  onSaved: (next: WagonNumberCameraSettings) => void;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(settings?.camera_source ?? null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setSaving(true);
    setError("");
    try {
      const { data } = await api.put<WagonNumberCameraSettings>("/cameras/wagon-number-settings/", {
        camera_source: selected,
      });
      onSaved(data);
      showSuccess(selected ? "Камера проходной назначена" : "Камера проходной отключена");
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
      eyebrow="Приход и проход · Только суперадмин"
      title="Камера номера вагона"
      description="Выберите одну камеру проходной. Её основной поток будет закреплён за круглосуточным распознаванием номеров."
      className="max-w-3xl"
      footer={
        <>
          {selected && (
            <Button variant="ghost" className="mr-auto text-slate-500" onClick={() => setSelected(null)}>
              Отключить назначение
            </Button>
          )}
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button disabled={saving} onClick={() => void save()}>
            <Check className="size-4" /> {saving ? "Сохранение…" : "Закрепить камеру"}
          </Button>
        </>
      }
    >
      <div className="mb-5 grid gap-2.5 sm:grid-cols-3">
        <div className="rounded-2xl border border-amber-200 bg-amber-50 p-3">
          <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-amber-700">Ответственность</p>
          <p className="mt-1 text-sm font-bold text-slate-900">Проходная вагонов</p>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-[#111318] p-3 text-white">
          <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-white/45">Поток</p>
          <p className="mt-1 text-sm font-bold">Основной · 24/7</p>
        </div>
        <div className="rounded-2xl border border-emerald-100 bg-emerald-50 p-3">
          <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-emerald-700">Результат</p>
          <p className="mt-1 text-sm font-bold text-slate-900">Номер вагона</p>
        </div>
      </div>

      {settings?.sync_status === "pending" && (
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
          <RefreshCw className="mt-0.5 size-4 shrink-0" />
          <p>{settings.detail || "ПК камер переподключается. Назначение применится автоматически."}</p>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {cameras.map((camera) => (
          <CameraChoice
            key={camera.id}
            camera={camera}
            checked={selected === camera.src}
            onSelect={() => {
              setSelected(camera.src);
              setError("");
            }}
          />
        ))}
      </div>
      {!cameras.length && (
        <div className="rounded-xl border border-dashed p-8 text-center text-sm text-slate-400">
          Подключённые камеры пока не обнаружены.
        </div>
      )}
      {error && <p className="mt-3 text-sm text-[var(--destructive)]">{error}</p>}
    </Modal>
  );
}

function CameraPanel({
  camera,
  settings,
}: {
  camera?: CameraFeed & { src: string };
  settings: WagonNumberCameraSettings | null;
}) {
  const [streamOnline, setStreamOnline] = useState(false);

  if (!settings?.camera_source) {
    return (
      <div className="relative flex min-h-72 flex-col items-center justify-center overflow-hidden rounded-[28px] border border-dashed border-amber-200 bg-[#111318] p-8 text-center text-white">
        <div className="absolute inset-0 opacity-30 [background-image:linear-gradient(rgba(255,255,255,.04)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,.04)_1px,transparent_1px)] [background-size:32px_32px]" />
        <span className="relative flex size-16 items-center justify-center rounded-2xl border border-amber-400/30 bg-amber-400/10 text-amber-300">
          <ScanLine className="size-8" />
        </span>
        <p className="relative mt-4 text-lg font-bold">Камера проходной не назначена</p>
        <p className="relative mt-1 max-w-md text-sm text-white/45">
          Выберите камеру, которая видит номер вагона при приходе и выходе с территории.
        </p>
      </div>
    );
  }

  return (
    <section className="overflow-hidden rounded-[28px] border border-slate-800 bg-[#111318] text-white shadow-[0_24px_60px_rgba(15,23,42,0.18)]">
      <div className="grid lg:grid-cols-[1.55fr_0.85fr]">
        <div className="relative aspect-video min-h-72 overflow-hidden bg-black">
          <CameraStream
            src={settings.camera_source}
            onStateChange={setStreamOnline}
            className="absolute inset-0 size-full object-cover"
          />
          {!streamOnline && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black text-white/40">
              <VideoOff className="size-7" />
              <span className="text-xs">Ожидаем видеопоток</span>
            </div>
          )}
          <div className="pointer-events-none absolute inset-x-[8%] top-1/2 h-px animate-pulse bg-amber-300 shadow-[0_0_18px_4px_rgba(251,191,36,0.55)]" />
          <div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/80 to-transparent p-4 pb-12">
            <span className="flex items-center gap-2 rounded-full border border-amber-300/25 bg-black/45 px-3 py-1.5 text-[10px] font-bold tracking-[0.14em] text-amber-200 backdrop-blur-md">
              <span className={cn("size-1.5 rounded-full", streamOnline ? "bg-emerald-400" : "bg-amber-400")} />
              ПРОХОДНАЯ · 24/7
            </span>
            <span className="rounded-full bg-black/45 px-3 py-1.5 text-[10px] font-semibold text-white/65 backdrop-blur-md">
              MAIN STREAM
            </span>
          </div>
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 to-transparent p-4 pt-14">
            <p className="text-lg font-bold">{camera?.zone || settings.camera_source}</p>
            <p className="mt-0.5 text-xs text-white/50">{camera?.name || settings.camera_source}</p>
          </div>
        </div>

        <div className="flex flex-col p-6">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-amber-300">Камера процесса</p>
              <h2 className="mt-2 text-2xl font-bold tracking-tight">Приход и проход</h2>
            </div>
            <span
              className={cn(
                "rounded-full border px-3 py-1 text-[10px] font-bold",
                settings.sync_status === "synced"
                  ? "border-emerald-400/25 bg-emerald-400/10 text-emerald-300"
                  : "border-amber-400/25 bg-amber-400/10 text-amber-200",
              )}
            >
              {settings.sync_status === "synced" ? "СИНХРОНИЗИРОВАНО" : "ОЖИДАЕТ СВЯЗЬ"}
            </span>
          </div>

          <div className="mt-7 grid gap-3">
            <div className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.04] p-4">
              <span className="flex size-10 items-center justify-center rounded-xl bg-amber-400/10 text-amber-300">
                <TrainFront className="size-5" />
              </span>
              <div>
                <p className="text-[11px] text-white/40">Ответственный участок</p>
                <p className="mt-0.5 text-sm font-bold">Проходная вагонов</p>
              </div>
            </div>
            <div className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.04] p-4">
              <span className="flex size-10 items-center justify-center rounded-xl bg-sky-400/10 text-sky-300">
                <Video className="size-5" />
              </span>
              <div>
                <p className="text-[11px] text-white/40">Закреплённый источник</p>
                <p className="mt-0.5 text-sm font-bold">{settings.live?.stream || settings.camera_source}</p>
              </div>
            </div>
          </div>

          <div className="mt-auto pt-6">
            <div className="rounded-2xl border border-amber-300/15 bg-amber-300/[0.06] p-4 text-xs leading-5 text-amber-50/65">
              Эта камера отвечает только за номера вагонов на проходной. Камеры погрузки остаются в моноблоке.
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

export function WagonNumberCameraWorkspace({ canManage = false }: { canManage?: boolean }) {
  const { data: cameraRows, error: camerasError, reload: reloadCameras } = useApi<CameraFeed[]>("/cameras/");
  const {
    data: settings,
    error: settingsError,
    reload: reloadSettings,
    setData: setSettings,
  } = useApi<WagonNumberCameraSettings>("/cameras/wagon-number-settings/");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const cameras = useMemo(
    () => playableCameras(cameraRows).filter((camera) => /^cam[1-9]\d*$/.test(camera.src)),
    [cameraRows],
  );
  const selectedCamera = cameras.find((camera) => camera.src === settings?.camera_source);

  return (
    <div className="flex flex-col gap-4">
      {(camerasError || settingsError) && (
        <ErrorAlert
          message={camerasError || settingsError || ""}
          onRetry={() => void Promise.all([reloadCameras(), reloadSettings()])}
        />
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-amber-700">Ответственная камера</p>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            Номер вагона фиксируется на входе и при выходе с территории.
          </p>
        </div>
        {canManage && (
          <Button
            variant="outline"
            className="h-10 rounded-xl border-amber-200 bg-amber-50/80 text-amber-800 hover:bg-amber-100"
            onClick={() => setSettingsOpen(true)}
          >
            <Settings2 className="size-4" /> Назначить камеру
            <span className="rounded-full bg-white px-2 py-0.5 text-[11px] text-amber-700 shadow-sm">
              {settings?.camera_source ? "1" : "0"}
            </span>
          </Button>
        )}
      </div>
      <CameraPanel camera={selectedCamera} settings={settings} />
      {canManage && settingsOpen && (
        <AssignmentModal
          cameras={cameras}
          settings={settings}
          onSaved={setSettings}
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </div>
  );
}
