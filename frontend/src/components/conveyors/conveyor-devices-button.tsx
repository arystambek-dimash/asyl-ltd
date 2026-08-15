"use client";

import { useMemo, useState } from "react";
import type { AxiosError } from "axios";
import {
  AlertTriangle,
  Camera,
  Check,
  CheckCircle2,
  CircuitBoard,
  Clipboard,
  ClipboardCheck,
  Eye,
  EyeOff,
  Link2,
  LoaderCircle,
  LockKeyhole,
  Plus,
  Radio,
  ShieldCheck,
  Unplug,
  Wifi,
  WifiOff,
} from "lucide-react";
import type { CameraFeed } from "@/components/camera-wall";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import { showSuccess } from "@/lib/toast";
import type { ConveyorDevice, ConveyorDeviceEnrollment } from "@/lib/types";
import { cn, formatDateTime } from "@/lib/utils";

type PlayableCamera = CameraFeed & { src: string };

function fieldApiError(cause: unknown) {
  const data = (cause as AxiosError<Record<string, unknown>>).response?.data;
  if (data && typeof data === "object") {
    const messages = Object.entries(data).flatMap(([field, value]) => {
      if (!Array.isArray(value)) return [];
      const label = field === "camera_source" ? "Камера" : field === "name" ? "Название" : field;
      return value.filter((item): item is string => typeof item === "string").map((item) => `${label}: ${item}`);
    });
    if (messages.length) return messages.join(" ");
  }
  return apiError(cause);
}

function cameraTitle(camera: PlayableCamera | undefined, source: string) {
  return camera?.zone?.trim() || camera?.name?.trim() || source;
}

function defaultDeviceName(camera: PlayableCamera) {
  return `ESP32 · ${cameraTitle(camera, camera.src)}`;
}

function isRecentlyOnline(device: ConveyorDevice) {
  if (!device.last_seen_at) return false;
  const seenAt = Date.parse(device.last_seen_at);
  return Number.isFinite(seenAt) && Date.now() - seenAt < 5_000;
}

function DeviceStatus({ device }: { device: ConveyorDevice }) {
  const online = isRecentlyOnline(device);
  const faulted = Boolean(device.fault);

  return (
    <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] sm:grid-cols-3">
      <span
        className={cn(
          "flex items-center gap-1.5 rounded-lg px-2.5 py-2 font-semibold",
          online ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500",
        )}
      >
        {online ? <Wifi className="size-3.5" /> : <WifiOff className="size-3.5" />}
        {online ? "На связи" : "Не в сети"}
      </span>
      <span
        className={cn(
          "flex items-center gap-1.5 rounded-lg px-2.5 py-2 font-semibold",
          device.output_state === 1
            ? "bg-amber-50 text-amber-700"
            : device.output_state === 0
              ? "bg-blue-50 text-blue-700"
              : "bg-slate-100 text-slate-500",
        )}
      >
        <Radio className="size-3.5" />
        {device.output_state === 1 ? "Конвейер ON" : device.output_state === 0 ? "Конвейер OFF" : "Нет телеметрии"}
      </span>
      <span
        className={cn(
          "col-span-2 flex items-center gap-1.5 rounded-lg px-2.5 py-2 font-semibold sm:col-span-1",
          faulted
            ? "bg-red-50 text-red-700"
            : device.is_active
              ? "bg-emerald-50 text-emerald-700"
              : "bg-slate-100 text-slate-500",
        )}
      >
        {faulted ? <AlertTriangle className="size-3.5" /> : <ShieldCheck className="size-3.5" />}
        {faulted ? "Ошибка" : device.is_active ? "Активен" : "Отключён"}
      </span>
    </div>
  );
}

export function ConveyorDevicesButton({
  cameras,
  devices,
  reload,
}: {
  cameras: PlayableCamera[];
  devices: ConveyorDevice[];
  reload: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [cameraSource, setCameraSource] = useState("");
  const [active, setActive] = useState(true);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [issued, setIssued] = useState<ConveyorDeviceEnrollment | null>(null);
  const [showToken, setShowToken] = useState(false);
  const [copied, setCopied] = useState<"token" | "json" | null>(null);
  const [copyError, setCopyError] = useState("");
  const [discardOpen, setDiscardOpen] = useState(false);

  const byCamera = useMemo(() => new Map(devices.map((device) => [device.camera_source, device])), [devices]);
  const freeCameras = useMemo(() => cameras.filter((camera) => !byCamera.has(camera.src)), [byCamera, cameras]);
  const missingCameraDevices = useMemo(
    () => devices.filter((device) => !cameras.some((camera) => camera.src === device.camera_source)),
    [cameras, devices],
  );
  const onlineCount = devices.filter(isRecentlyOnline).length;

  function showForm(camera?: PlayableCamera) {
    const selected = camera ?? freeCameras[0];
    setCameraSource(selected?.src ?? "");
    setName(selected ? defaultDeviceName(selected) : "");
    setNameEdited(false);
    setActive(true);
    setFormError("");
    setFormOpen(true);
  }

  async function enroll() {
    const normalizedName = name.trim().replace(/\s+/g, " ");
    if (!normalizedName || !cameraSource) return;
    setSaving(true);
    setFormError("");
    try {
      const response = await api.post<ConveyorDeviceEnrollment>("/conveyors/devices/", {
        name: normalizedName,
        camera_source: cameraSource,
        is_active: active,
      });
      // Capture the one-time credential before any follow-up request. A failed
      // list refresh must never make the enrollment token disappear.
      setIssued(response.data);
      setShowToken(false);
      setCopied(null);
      setCopyError("");
      setFormOpen(false);
      showSuccess(`ESP32 закреплён за ${cameraSource}`);
      void reload().catch(() => undefined);
    } catch (cause) {
      setFormError(fieldApiError(cause));
    } finally {
      setSaving(false);
    }
  }

  const provisioningJson = issued
    ? JSON.stringify(
        {
          base_url: "https://asyl-ltd.kz/api",
          device_id: issued.credential.device_id,
          token: issued.credential.token,
        },
        null,
        2,
      )
    : "";

  async function copySecret(kind: "token" | "json", value: string) {
    setCopyError("");
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
    } catch {
      setCopyError("Не удалось скопировать автоматически. Откройте токен и скопируйте вручную.");
    }
  }

  function forgetCredential() {
    setIssued(null);
    setShowToken(false);
    setCopied(null);
    setCopyError("");
    setDiscardOpen(false);
  }

  return (
    <>
      <Button
        variant="outline"
        className="h-10 rounded-xl border-sky-200 bg-sky-50/70 text-sky-800 hover:bg-sky-100"
        onClick={() => {
          setOpen(true);
          void reload().catch(() => undefined);
        }}
      >
        <CircuitBoard className="size-4" /> ESP32
        <span className="rounded-full bg-white px-2 py-0.5 text-[11px] tabular-nums text-sky-700 shadow-sm">
          {devices.length}
        </span>
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        eyebrow="Только системный суперпользователь"
        title="ESP32 по камерам"
        description="Одна камера управляет только своим контроллером. Повторно занять камеру нельзя."
        className="max-w-4xl"
      >
        <div className="mb-5 grid gap-3 sm:grid-cols-3">
          <div className="rounded-2xl border border-sky-100 bg-sky-50/70 p-4">
            <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-sky-600">Привязано</p>
            <p className="mt-1 text-2xl font-black tabular-nums text-slate-900">{devices.length}</p>
            <p className="mt-0.5 text-xs text-slate-500">контроллеров</p>
          </div>
          <div className="rounded-2xl border border-emerald-100 bg-emerald-50/70 p-4">
            <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-emerald-600">На связи</p>
            <p className="mt-1 text-2xl font-black tabular-nums text-slate-900">{onlineCount}</p>
            <p className="mt-0.5 text-xs text-slate-500">видели за 5 секунд</p>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
            <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">Свободно</p>
            <p className="mt-1 text-2xl font-black tabular-nums text-slate-900">{freeCameras.length}</p>
            <p className="mt-0.5 text-xs text-slate-500">камер без ESP32</p>
          </div>
        </div>

        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-start gap-2.5 text-sm text-slate-500">
            <Link2 className="mt-0.5 size-4 shrink-0 text-sky-600" />
            <p>Привязка сама назначает этот ESP32 выбранной камере — дополнительных настроек режима не нужно.</p>
          </div>
          <Button disabled={!freeCameras.length} onClick={() => showForm()}>
            <Plus className="size-4" /> Привязать ESP32
          </Button>
        </div>

        <div className="grid gap-3 lg:grid-cols-2">
          {cameras.map((camera) => {
            const device = byCamera.get(camera.src);
            return (
              <article
                key={camera.src}
                className={cn(
                  "rounded-2xl border p-4 transition",
                  device ? "border-slate-200 bg-white" : "border-dashed border-sky-200 bg-sky-50/30",
                )}
              >
                <div className="flex items-start gap-3">
                  <span
                    className={cn(
                      "flex size-11 shrink-0 items-center justify-center rounded-2xl",
                      device ? "bg-slate-900 text-white" : "bg-white text-sky-600 shadow-sm ring-1 ring-sky-100",
                    )}
                  >
                    <Camera className="size-5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-bold text-slate-900">{cameraTitle(camera, camera.src)}</p>
                    <p className="mt-0.5 text-xs text-slate-400">{camera.src}</p>
                  </div>
                  {device ? (
                    <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-slate-600">
                      Привязана
                    </span>
                  ) : (
                    <span className="rounded-full bg-sky-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-sky-700">
                      Свободна
                    </span>
                  )}
                </div>

                {device ? (
                  <>
                    <div className="mt-4 flex items-center gap-3 rounded-xl bg-slate-50 px-3 py-3">
                      <CircuitBoard className="size-5 shrink-0 text-sky-600" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-bold text-slate-800">{device.name}</p>
                        <p className="mt-0.5 truncate font-mono text-[10px] text-slate-400">{device.public_id}</p>
                      </div>
                    </div>
                    <DeviceStatus device={device} />
                    <p className="mt-2 min-h-4 truncate text-[10px] text-slate-400">
                      {device.fault
                        ? `Fault: ${device.fault}`
                        : device.last_seen_at
                          ? `Последняя связь: ${formatDateTime(device.last_seen_at)}`
                          : "Контроллер ещё не подключался к API"}
                    </p>
                  </>
                ) : (
                  <div className="mt-4 flex items-center justify-between gap-3 rounded-xl border border-sky-100 bg-white p-3">
                    <div className="flex min-w-0 items-center gap-2.5 text-xs text-slate-500">
                      <Unplug className="size-4 shrink-0 text-slate-400" />
                      <span>У камеры пока нет контроллера</span>
                    </div>
                    <Button size="sm" onClick={() => showForm(camera)}>
                      <Link2 className="size-3.5" /> Привязать
                    </Button>
                  </div>
                )}
              </article>
            );
          })}
        </div>

        {!cameras.length && (
          <div className="rounded-2xl border border-dashed border-slate-200 p-10 text-center">
            <Camera className="mx-auto size-6 text-slate-300" />
            <p className="mt-2 text-sm font-semibold text-slate-600">Камеры пока не обнаружены</p>
            <p className="mt-1 text-xs text-slate-400">Сначала добавьте камеру в инвентарь моноблока.</p>
          </div>
        )}

        {missingCameraDevices.length > 0 && (
          <div className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 p-4">
            <div className="flex items-start gap-2.5">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600" />
              <div>
                <p className="text-sm font-bold text-amber-900">Есть ESP32 без камеры в текущем инвентаре</p>
                <p className="mt-1 text-xs text-amber-800/80">
                  {missingCameraDevices.map((device) => `${device.name} → ${device.camera_source}`).join(", ")}
                </p>
              </div>
            </div>
          </div>
        )}
      </Modal>

      <Modal
        open={formOpen}
        onClose={() => !saving && setFormOpen(false)}
        eyebrow="Новый контроллер"
        title="Привязать ESP32"
        description="После привязки заказы этой камеры будут управлять только её ESP32."
        className="max-w-lg"
        footer={
          <>
            <Button variant="ghost" disabled={saving} onClick={() => setFormOpen(false)}>
              Отмена
            </Button>
            <Button disabled={saving || !name.trim() || !cameraSource} onClick={() => void enroll()}>
              {saving ? <LoaderCircle className="size-4 animate-spin" /> : <Link2 className="size-4" />}
              {saving ? "Привязка…" : "Создать привязку"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="rounded-2xl border border-blue-100 bg-blue-50/70 p-3 text-sm text-blue-900">
            <div className="flex items-start gap-2.5">
              <ShieldCheck className="mt-0.5 size-4 shrink-0 text-blue-600" />
              <p>После создания device token будет показан только один раз. Сразу скопируйте его для BLE.</p>
            </div>
          </div>
          <div>
            <Label htmlFor="conveyor-device-name">Название ESP32</Label>
            <Input
              id="conveyor-device-name"
              data-autofocus
              value={name}
              onChange={(event) => {
                setName(event.target.value);
                setNameEdited(true);
              }}
              maxLength={80}
              placeholder="ESP32 · Камера погрузки"
            />
          </div>
          <div>
            <Label htmlFor="conveyor-camera">Камера</Label>
            <select
              id="conveyor-camera"
              value={cameraSource}
              onChange={(event) => {
                const source = event.target.value;
                setCameraSource(source);
                if (!nameEdited) {
                  const camera = cameras.find((item) => item.src === source);
                  setName(camera ? defaultDeviceName(camera) : "");
                }
              }}
              className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-800 outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-500/15"
            >
              <option value="">Выберите свободную камеру</option>
              {freeCameras.map((camera) => (
                <option key={camera.src} value={camera.src}>
                  {cameraTitle(camera, camera.src)} · {camera.src}
                </option>
              ))}
            </select>
            <p className="mt-1.5 text-[11px] text-slate-400">Занятые камеры исключены из списка.</p>
          </div>
          <label className="flex items-center justify-between gap-4 rounded-xl border border-slate-200 p-3">
            <span>
              <span className="block text-sm font-semibold text-slate-800">Активировать сразу</span>
              <span className="mt-0.5 block text-xs text-slate-400">
                ESP32 сможет авторизоваться после BLE-настройки
              </span>
            </span>
            <input
              type="checkbox"
              checked={active}
              onChange={(event) => setActive(event.target.checked)}
              className="size-4 accent-sky-600"
            />
          </label>
          {formError && (
            <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {formError}
            </p>
          )}
        </div>
      </Modal>

      <Modal
        open={issued !== null}
        onClose={() => setDiscardOpen(true)}
        eyebrow="Показано только один раз"
        title="ESP32 привязан"
        description={issued ? `${issued.name} → ${issued.camera_source}` : undefined}
        className="max-w-2xl"
        footer={
          <>
            <Button variant="outline" onClick={() => void copySecret("json", provisioningJson)}>
              {copied === "json" ? <ClipboardCheck className="size-4" /> : <Clipboard className="size-4" />}
              {copied === "json" ? "JSON скопирован" : "Копировать JSON"}
            </Button>
            <Button onClick={forgetCredential}>
              <Check className="size-4" /> Я сохранил
            </Button>
          </>
        }
      >
        {issued && (
          <div className="space-y-4">
            <div className="flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-amber-950">
              <LockKeyhole className="mt-0.5 size-5 shrink-0 text-amber-600" />
              <div>
                <p className="text-sm font-bold">Не закрывайте окно, пока не сохранили данные</p>
                <p className="mt-1 text-xs leading-5 text-amber-900/75">
                  После закрытия backend больше не покажет этот token. При потере понадобится ротация секрета.
                </p>
              </div>
            </div>

            <div className="rounded-2xl border border-emerald-100 bg-emerald-50/60 p-4">
              <div className="flex items-center gap-2 text-sm font-bold text-emerald-900">
                <CheckCircle2 className="size-4 text-emerald-600" /> Привязка создана
              </div>
              <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-[110px_1fr]">
                <dt className="text-emerald-800/65">Камера</dt>
                <dd className="font-semibold text-emerald-950">{issued.camera_source}</dd>
                <dt className="text-emerald-800/65">Device ID</dt>
                <dd className="break-all font-mono text-[11px] text-emerald-950">{issued.credential.device_id}</dd>
              </dl>
            </div>

            <div>
              <div className="mb-1.5 flex items-center justify-between gap-3">
                <Label className="mb-0">Device token</Label>
                <button
                  type="button"
                  onClick={() => setShowToken((current) => !current)}
                  className="flex items-center gap-1.5 text-xs font-semibold text-sky-700 hover:text-sky-900"
                >
                  {showToken ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                  {showToken ? "Скрыть" : "Показать"}
                </button>
              </div>
              <div className="flex gap-2">
                <Input
                  readOnly
                  aria-label="Device token"
                  value={showToken ? issued.credential.token : "•••••••••••••••••••••••••••••••••••••••••••"}
                  className="font-mono text-xs"
                />
                <Button
                  size="icon"
                  variant="outline"
                  aria-label="Копировать device token"
                  onClick={() => void copySecret("token", issued.credential.token)}
                >
                  {copied === "token" ? <ClipboardCheck className="size-4" /> : <Clipboard className="size-4" />}
                </Button>
              </div>
            </div>

            <div>
              <Label>JSON для BLE provisioning</Label>
              <pre className="max-h-52 overflow-auto rounded-2xl bg-slate-950 p-4 text-[11px] leading-5 text-sky-100">
                {provisioningJson}
              </pre>
            </div>

            <div className="flex items-start gap-2.5 rounded-xl border border-sky-100 bg-sky-50 p-3 text-xs leading-5 text-sky-900">
              <CircuitBoard className="mt-0.5 size-4 shrink-0 text-sky-600" />
              <p>Следующий шаг — передать этот JSON и пароль Wi-Fi в ESP32 через защищённый Bluetooth setup.</p>
            </div>
            {copyError && (
              <p role="alert" className="text-sm text-red-700">
                {copyError}
              </p>
            )}
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={discardOpen}
        onClose={() => setDiscardOpen(false)}
        title="Закрыть одноразовый token?"
        description="После закрытия увидеть его повторно нельзя. Убедитесь, что JSON сохранён для Bluetooth-настройки."
        confirmLabel="Закрыть без сохранения"
        onConfirm={forgetCredential}
      />
    </>
  );
}
