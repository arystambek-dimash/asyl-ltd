"use client";

import { useMemo, useState } from "react";
import { Check, ScanLine, Settings2 } from "lucide-react";
import { CameraChoice } from "@/components/camera-choice";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import { isLogicalCamera, playableCameras, type PlayableCamera } from "@/lib/shipping-cameras";
import { showSuccess } from "@/lib/toast";
import type { CameraFeed, WagonNumberCameraSettings } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { WagonArchCameraPanel } from "./wagon-arch-camera";

function AssignmentModal({
  cameras,
  settings,
  onSaved,
  onClose,
}: {
  cameras: PlayableCamera[];
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

      <div className="grid gap-3 sm:grid-cols-2">
        {cameras.map((camera) => (
          <CameraChoice
            key={camera.id}
            camera={camera}
            checked={selected === camera.src}
            accent="amber"
            icon={ScanLine}
            onToggle={() => {
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

export function WagonNumberCameraWorkspace() {
  const canManage = useAuth((state) => Boolean(state.me?.is_superuser));
  // Список камер нужен только окну назначения, а оно есть только у суперадмина.
  const {
    data: cameraRows,
    error: camerasError,
    reload: reloadCameras,
  } = useApi<CameraFeed[]>(canManage ? "/cameras/" : null);
  const {
    data: settings,
    error: settingsError,
    reload: reloadSettings,
    setData: setSettings,
  } = useApi<WagonNumberCameraSettings>("/cameras/wagon-number-settings/");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const cameras = useMemo(
    () => playableCameras(cameraRows).filter((camera) => isLogicalCamera(camera.src)),
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
