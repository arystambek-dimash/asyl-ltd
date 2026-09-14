"use client";

import { useMemo, useState } from "react";
import { Check, RefreshCw, ScanLine, Settings2, VideoOff } from "lucide-react";
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
import { WagonArchCameraPanel } from "./wagon-arch-camera";

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
      eyebrow="Приход и вывоз · Только суперадмин"
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
  return (
    <div className="flex flex-col gap-4">
      {(camerasError || settingsError) && (
        <ErrorAlert
          message={camerasError || settingsError || ""}
          onRetry={() => void Promise.all([reloadCameras(), reloadSettings()])}
        />
      )}
      <WagonArchCameraPanel
        assignedCamera={settings?.camera_source ?? null}
        syncStatus={settings?.sync_status ?? null}
        assignAction={
          canManage ? (
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
          ) : null
        }
      />
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
