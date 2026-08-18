"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Archive,
  BarChart3,
  Camera,
  CalendarDays,
  ChevronRight,
  Cpu,
  Check,
  Clock3,
  LockKeyhole,
  LoaderCircle,
  Minus,
  PackageCheck,
  Radio,
  RefreshCw,
  Settings2,
  ScanLine,
  ShieldCheck,
  Square,
  UserRound,
  Video,
  VideoOff,
  MonitorSmartphone,
  Plus,
  Pencil,
  Trash2,
  KeyRound,
  type LucideIcon,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { playableCameras, type CameraFeed } from "@/components/camera-wall";
import { CameraStream } from "@/components/camera-stream";
import { ConveyorDevicesButton } from "@/components/conveyors/conveyor-devices-button";
import { DetectionOverlay } from "@/components/detection-overlay";
import { AlwaysOnDayRunLog, AlwaysOnProductionPanel } from "@/components/monoblock/always-on-production-panel";
import { RequirePerm } from "@/components/require-perm";
import { ShipmentLauncher } from "@/components/shipping/shipment-launcher";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { motion } from "motion/react";
import { ColorDot, Eyebrow, Hairline, Metric, Panel, SectionHead } from "@/components/monoblock/ui";
import { colorMeta } from "@/lib/monoblock-colors";
import { api, apiError } from "@/lib/api";
import { orderedBagCount } from "@/lib/orders";
import { showSuccess } from "@/lib/toast";
import { can } from "@/lib/can";
import type {
  AiCountingSession,
  AlwaysOnCameraSettings,
  AlwaysOnCountArchive,
  AlwaysOnDailyAnalytics,
  AlwaysOnDailyCameraAnalytics,
  AlwaysOnDetection,
  AlwaysOnProcessorStatus,
  AlwaysOnProductMapping,
  AlwaysOnProductionPayload,
  AlwaysOnProductionRun,
  AlwaysOnStockBatch,
  ConveyorStatus,
  ConveyorDevice,
  MonoblockCameraSettings,
  MonoblockDevice,
  Order,
} from "@/lib/types";
import { dayColorBreakdown, fullDay, shortDay } from "@/lib/day-analytics";
import { useAiCounter } from "@/lib/use-ai-counter";
import { useApi } from "@/lib/use-api";
import { useRovingTabs } from "@/lib/use-roving-tabs";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { cn, formatDateTime } from "@/lib/utils";
import { useAuth } from "@/store/auth";

const SESSION_POLL_MS = 3_000;
// Рамки тянем чаще остального: мешок пересекает кадр за секунды, и на общем
// трёхсекундном опросе рамка заметно отставала от него.
const DETECTIONS_POLL_MS = 1_000;
// Рамка старше этого времени описывает уже уехавший мешок — гасим её, чтобы
// она не висела на пустом месте при обрыве связи или остановке модели.
const DETECTIONS_STALE_MS = 2_500;
// Заказы/камеры/настройки меняются редко — не гоняем полный список заказов
// каждые 3 секунды на экране, который висит открытым весь день.
const SLOW_POLL_MS = 30_000;
const ALWAYS_ON_MODAL_VIEWS = ["live", "production", "analytics", "archive"] as const;
const MONOBLOCK_PAGE_TABS = ["shipments", "monoblock"] as const;

const MODAL_TABS: { key: (typeof ALWAYS_ON_MODAL_VIEWS)[number]; label: string; icon: LucideIcon }[] = [
  { key: "live", label: "Прямой эфир", icon: Video },
  { key: "production", label: "Выпуск и склад", icon: PackageCheck },
  { key: "analytics", label: "Аналитика", icon: BarChart3 },
  { key: "archive", label: "Архив", icon: Archive },
];

function CameraChoice({
  camera,
  checked,
  onToggle,
}: {
  camera: CameraFeed & { src: string };
  checked: boolean;
  onToggle: () => void;
}) {
  const [streamOnline, setStreamOnline] = useState(false);

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={checked}
      className={cn(
        "group overflow-hidden rounded-2xl border text-left transition duration-200",
        checked
          ? "border-blue-400 bg-blue-50 shadow-[0_10px_28px_rgba(59,104,210,0.15)] ring-2 ring-blue-500/20"
          : "border-slate-200 bg-white hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-md",
      )}
    >
      <div className="relative aspect-video overflow-hidden bg-[#151821]">
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

        <div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/65 to-transparent px-3 pb-8 pt-2.5">
          <span className="flex items-center gap-1.5 rounded-full bg-black/35 px-2 py-1 text-[10px] font-semibold text-white backdrop-blur-md">
            <span className={cn("size-1.5 rounded-full", streamOnline ? "bg-emerald-400" : "bg-amber-400")} />
            {streamOnline ? "ОНЛАЙН" : "НЕТ СИГНАЛА"}
          </span>
          <span
            className={cn(
              "flex size-7 items-center justify-center rounded-full border backdrop-blur-md transition",
              checked ? "border-blue-300 bg-blue-600 text-white" : "border-white/35 bg-black/25 text-transparent",
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
            checked ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-400",
          )}
        >
          <Camera className="size-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-bold text-slate-800">{camera.zone}</span>
          <span className="mt-0.5 block truncate text-[11px] text-slate-400">{camera.name}</span>
        </span>
      </div>
    </button>
  );
}

function CameraSettingsButton({
  cameras,
  settings,
  reload,
}: {
  cameras: (CameraFeed & { src: string })[];
  settings: MonoblockCameraSettings | null;
  reload: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function show() {
    setSelected(settings?.camera_sources ?? []);
    setError("");
    setOpen(true);
  }

  function toggle(source: string) {
    setSelected((current) =>
      current.includes(source) ? current.filter((item) => item !== source) : [...current, source],
    );
  }

  async function save() {
    setSaving(true);
    setError("");
    try {
      await api.put("/cameras/monoblock-settings/", { camera_sources: selected });
      await reload();
      setOpen(false);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <Button variant="outline" className="h-10 rounded-xl bg-white" onClick={show}>
        <Settings2 className="size-4" /> Камеры моноблока
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] tabular-nums text-slate-500">
          {settings?.camera_sources.length ?? 0}
        </span>
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        eyebrow="Настройка администратора"
        title="Камеры моноблока"
        description="Отметьте камеры, которые оператор сможет назначать заказам."
        className="max-w-xl"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button disabled={saving} onClick={() => void save()}>
              <Check className="size-4" /> {saving ? "Сохранение…" : "Сохранить список"}
            </Button>
          </>
        }
      >
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-blue-100 bg-blue-50/70 p-3 text-sm text-blue-900">
          <ShieldCheck className="mt-0.5 size-5 shrink-0 text-blue-600" />
          <p>
            Изменение применяется для всех устройств. Активные отгрузки продолжат работу, но новые увидят только
            выбранные камеры.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {cameras.map((camera) => {
            const checked = selected.includes(camera.src);
            return (
              <CameraChoice key={camera.id} camera={camera} checked={checked} onToggle={() => toggle(camera.src)} />
            );
          })}
        </div>

        {!cameras.length && (
          <div className="rounded-xl border border-dashed p-8 text-center text-sm text-slate-400">
            Подключённые камеры пока не обнаружены.
          </div>
        )}
        {error && <p className="mt-3 text-sm text-[var(--destructive)]">{error}</p>}
      </Modal>
    </>
  );
}

function MonoblockDevicesButton({
  cameras,
  devices,
  reload,
}: {
  cameras: (CameraFeed & { src: string })[];
  devices: MonoblockDevice[];
  reload: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<MonoblockDevice | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [cameraSource, setCameraSource] = useState("");
  const [active, setActive] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function showForm(device?: MonoblockDevice) {
    setEditing(device ?? null);
    setName(device?.name ?? "");
    setUsername(device?.username ?? "");
    setPassword("");
    setCameraSource(device?.camera_source ?? "");
    setActive(device?.is_active ?? true);
    setError("");
    setFormOpen(true);
  }

  async function save() {
    setSaving(true);
    setError("");
    try {
      const body = {
        name,
        username,
        camera_source: cameraSource,
        is_active: active,
        ...(password ? { password } : {}),
      };
      if (editing) await api.patch(`/cameras/monoblock-devices/${editing.id}/`, body);
      else await api.post("/cameras/monoblock-devices/", body);
      await reload();
      setFormOpen(false);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setSaving(false);
    }
  }

  // Удаление уносит учётную запись поста: системный confirm не защищал от
  // повторного нажатия и выпадал из оформления остальных подтверждений.
  const [removing, setRemoving] = useState<MonoblockDevice | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const [removeError, setRemoveError] = useState("");

  async function confirmRemove() {
    if (!removing) return;
    setRemoveBusy(true);
    setRemoveError("");
    try {
      await api.delete(`/cameras/monoblock-devices/${removing.id}/`);
      await reload();
      setRemoving(null);
      showSuccess("Моноблок удалён");
    } catch (cause) {
      setRemoveError(apiError(cause));
    } finally {
      setRemoveBusy(false);
    }
  }

  const occupied = new Set(devices.filter((item) => item.id !== editing?.id).map((item) => item.camera_source));

  return (
    <>
      <Button
        variant="outline"
        className="h-10 rounded-xl bg-white"
        onClick={() => {
          setError("");
          setOpen(true);
        }}
      >
        <MonitorSmartphone className="size-4" /> Моноблоки
        <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[11px] tabular-nums text-blue-600">
          {devices.length}
        </span>
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        eyebrow="Устройства и доступ"
        title="Учётные записи моноблоков"
        description="У каждого физического моноблока свой логин и ровно одна закреплённая камера."
        className="max-w-2xl"
      >
        <div className="mb-4 flex items-center justify-between gap-3">
          <p className="text-sm text-slate-500">Оператор входит под этим логином — камера выбирается автоматически.</p>
          <Button onClick={() => showForm()}>
            <Plus className="size-4" /> Добавить
          </Button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {devices.map((device) => (
            <div
              key={device.id}
              className={cn(
                "rounded-2xl border p-4",
                device.is_active ? "border-slate-200 bg-white" : "border-slate-200 bg-slate-50 opacity-70",
              )}
            >
              <div className="flex items-start gap-3">
                <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
                  <MonitorSmartphone className="size-5" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-bold text-slate-800">{device.name}</div>
                  <div className="mt-0.5 truncate text-xs text-slate-400">Логин: {device.username}</div>
                </div>
                <span className={cn("size-2.5 rounded-full", device.is_active ? "bg-emerald-500" : "bg-slate-300")} />
              </div>
              <div className="mt-3 flex items-center gap-2 rounded-xl bg-slate-50 px-3 py-2 text-sm font-semibold text-slate-700">
                <Camera className="size-4 text-blue-600" /> {device.camera_name}
              </div>
              <div className="mt-3 flex justify-end gap-1">
                <Button size="icon" variant="ghost" aria-label="Изменить моноблок" onClick={() => showForm(device)}>
                  <Pencil className="size-4" />
                </Button>
                <Button size="icon" variant="ghost" aria-label="Удалить моноблок" onClick={() => setRemoving(device)}>
                  <Trash2 className="size-4 text-red-500" />
                </Button>
              </div>
            </div>
          ))}
        </div>
        {!devices.length && (
          <div className="rounded-2xl border border-dashed p-10 text-center text-sm text-slate-400">
            Моноблоки ещё не зарегистрированы.
          </div>
        )}
        {error && !formOpen && <p className="mt-3 text-sm text-[var(--destructive)]">{error}</p>}
      </Modal>

      <Modal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        eyebrow={editing ? "Изменение устройства" : "Новое устройство"}
        title={editing ? "Настроить моноблок" : "Зарегистрировать моноблок"}
        description="Эти данные используются только на физическом устройстве у камеры."
        className="max-w-lg"
        footer={
          <>
            <Button variant="ghost" onClick={() => setFormOpen(false)} disabled={saving}>
              Отмена
            </Button>
            <Button
              onClick={() => void save()}
              disabled={saving || !name || !username || !cameraSource || (!editing && !password)}
            >
              <Check className="size-4" /> {saving ? "Сохранение…" : "Сохранить"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <label className="grid gap-1.5">
            <Label>Название устройства</Label>
            <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Моноблок у конвейера" />
          </label>
          <label className="grid gap-1.5">
            <Label>Логин</Label>
            <Input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="monoblock-conveyor"
              autoComplete="off"
            />
          </label>
          <label className="grid gap-1.5">
            <Label>{editing ? "Новый пароль (необязательно)" : "Пароль"}</Label>
            <div className="relative">
              <KeyRound className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
              <Input
                type="password"
                className="pl-9"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder={editing ? "Оставьте пустым, чтобы не менять" : "Надёжный пароль"}
                autoComplete="new-password"
              />
            </div>
          </label>
          <label className="grid gap-1.5">
            <Label>Закреплённая камера</Label>
            <select
              value={cameraSource}
              onChange={(event) => setCameraSource(event.target.value)}
              className="h-10 rounded-lg border bg-white px-3 text-sm outline-none focus:ring-2 focus:ring-blue-500/20"
            >
              <option value="">Выберите камеру</option>
              {cameras
                .filter((camera) => !occupied.has(camera.src))
                .map((camera) => (
                  <option key={camera.src} value={camera.src}>
                    {camera.zone} · {camera.src}
                  </option>
                ))}
            </select>
          </label>
          <label className="flex items-center justify-between rounded-xl border p-3">
            <span>
              <span className="block text-sm font-semibold">Устройство активно</span>
              <span className="text-xs text-slate-400">Отключённый логин не сможет войти</span>
            </span>
            <input
              type="checkbox"
              checked={active}
              onChange={(event) => setActive(event.target.checked)}
              className="size-4 accent-blue-600"
            />
          </label>
          {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
        </div>
      </Modal>

      <ConfirmDialog
        open={removing !== null}
        onClose={() => !removeBusy && setRemoving(null)}
        title="Удалить моноблок?"
        description={
          removing ? `«${removing.name}» и его учётная запись будут удалены. Пост перестанет входить в систему.` : ""
        }
        busy={removeBusy}
        error={removeError}
        onConfirm={() => void confirmRemove()}
      />
    </>
  );
}

function AlwaysOnSettingsButton({
  cameras,
  settings,
  onSaved,
}: {
  cameras: (CameraFeed & { src: string })[];
  settings: AlwaysOnCameraSettings | null;
  onSaved: (next: AlwaysOnCameraSettings) => void;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function show() {
    setSelected(settings?.camera_sources ?? []);
    setError("");
    setOpen(true);
  }

  function toggle(source: string) {
    setSelected((current) => {
      if (current.includes(source)) return current.filter((item) => item !== source);
      if (settings?.capacity && current.length >= settings.capacity) {
        setError(`На ПК камер настроен лимит: ${settings.capacity} активных процессора.`);
        return current;
      }
      setError("");
      return [...current, source];
    });
  }

  async function save() {
    setSaving(true);
    setError("");
    try {
      // Ответ PUT авторитетен: сохранение в PostgreSQL уже произошло, даже
      // когда ПК цеха не ответил (202). Перечитывать список отдельным GET
      // нельзя — фоновый опрос мог стартовать до записи и вернуть прежнее
      // состояние уже после неё, из-за чего выбор «слетал» на экране.
      const { data } = await api.put<AlwaysOnCameraSettings>("/cameras/always-on-settings/", {
        camera_sources: selected,
      });
      onSaved(data);
      setOpen(false);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <Button
        variant="outline"
        className="h-10 rounded-xl border-blue-200 bg-blue-50/70 text-blue-700 hover:bg-blue-100"
        onClick={show}
      >
        <Settings2 className="size-4" /> Настроить
        <span className="rounded-full bg-white px-2 py-0.5 text-[11px] tabular-nums text-blue-600 shadow-sm">
          {settings?.camera_sources.length ?? 0}
        </span>
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        eyebrow="Требуется право «AI 24/7: Управление»"
        title="Постоянный AI-подсчёт"
        description="Модель остаётся прогретой и считает круглосуточно. В этом режиме видео не публикуется и не записывается."
        className="max-w-2xl"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button disabled={saving} onClick={() => void save()}>
              <Check className="size-4" /> {saving ? "Применение…" : "Применить режим"}
            </Button>
          </>
        }
      >
        <div className="mb-4 grid gap-2.5 sm:grid-cols-3">
          <div className="rounded-2xl border border-emerald-100 bg-emerald-50/70 p-3">
            <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-emerald-600">Модель</p>
            <p className="mt-1 text-sm font-bold text-slate-800">Всегда активна</p>
            {settings?.capacity && (
              <p className="mt-0.5 text-[10px] text-emerald-700/70">до {settings.capacity} камер одновременно</p>
            )}
          </div>
          <div className="rounded-2xl border border-sky-100 bg-sky-50/70 p-3">
            <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-sky-600">Отгрузка</p>
            <p className="mt-1 text-sm font-bold text-slate-800">Старт без прогрева</p>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3">
            <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">Диск камеры</p>
            <p className="mt-1 text-sm font-bold text-slate-800">Без фоновой записи</p>
          </div>
        </div>

        {settings?.sync_status === "pending" && (
          <div className="mb-4 flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            <RefreshCw className="mt-0.5 size-4 shrink-0" />
            <p>{settings.detail || "ПК камер переподключается. Настройка применится автоматически."}</p>
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          {cameras.map((camera) => {
            const checked = selected.includes(camera.src);
            const live = settings?.processors.find((item) => item.cam === camera.src);
            return (
              <button
                key={camera.id}
                type="button"
                onClick={() => toggle(camera.src)}
                aria-pressed={checked}
                className={cn(
                  "flex items-center gap-3 rounded-2xl border p-3 text-left transition",
                  checked
                    ? "border-blue-400 bg-blue-50 ring-2 ring-blue-500/15"
                    : "border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm",
                )}
              >
                <span
                  className={cn(
                    "flex size-11 shrink-0 items-center justify-center rounded-2xl",
                    checked ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-400",
                  )}
                >
                  <Cpu className="size-5" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-bold text-slate-800">{camera.zone}</span>
                  <span className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-400">
                    <span className={cn("size-1.5 rounded-full", live?.running ? "bg-emerald-400" : "bg-slate-300")} />
                    {live?.mode === "session" ? "занята отгрузкой" : live?.running ? "считает 24/7" : camera.src}
                  </span>
                </span>
                <span
                  className={cn(
                    "flex size-7 items-center justify-center rounded-full border",
                    checked ? "border-blue-600 bg-blue-600 text-white" : "border-slate-200 text-transparent",
                  )}
                >
                  <Check className="size-4" />
                </span>
              </button>
            );
          })}
        </div>
        {!cameras.length && (
          <div className="rounded-xl border border-dashed p-8 text-center text-sm text-slate-400">
            Подключённые AI-камеры пока не обнаружены.
          </div>
        )}
        {error && <p className="mt-3 text-sm text-[var(--destructive)]">{error}</p>}
      </Modal>
    </>
  );
}

function AlwaysOnCard({
  processor,
  camera,
  detail,
  daily,
  canManage,
  onAnalyticsChanged,
}: {
  processor: AlwaysOnProcessorStatus;
  camera?: CameraFeed & { src: string };
  detail?: string;
  daily?: AlwaysOnDailyCameraAnalytics;
  canManage: boolean;
  onAnalyticsChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [modalView, setModalView] = useState<(typeof ALWAYS_ON_MODAL_VIEWS)[number]>("live");
  const modalTabs = useRovingTabs({
    tabs: ALWAYS_ON_MODAL_VIEWS,
    active: modalView,
    onChange: setModalView,
    label: "Режим мониторинга камеры",
  });
  const [archives, setArchives] = useState<AlwaysOnCountArchive[] | null>(null);
  const [archivesError, setArchivesError] = useState("");
  const [openArchiveId, setOpenArchiveId] = useState<number | null>(null);
  const [archiveToDelete, setArchiveToDelete] = useState<AlwaysOnCountArchive | null>(null);
  const [deletingArchiveId, setDeletingArchiveId] = useState<number | null>(null);
  const [deleteArchiveError, setDeleteArchiveError] = useState("");
  const [correctionOpen, setCorrectionOpen] = useState(false);
  const [streamOnline, setStreamOnline] = useState(false);
  // Рамки модели можно скрыть: иногда оператору нужно посмотреть на сам кадр.
  const [showDetections, setShowDetections] = useState(true);
  // Рамки живут отдельно от остального состояния: их опрашиваем чаще, чтобы
  // они держались мешка, и помечаем временем — устаревшие гасим.
  const [liveBoxes, setLiveBoxes] = useState<{
    detections?: AlwaysOnDetection[];
    frame?: { width?: number; height?: number } | null;
    at: number;
  } | null>(null);
  const [liveProcessor, setLiveProcessor] = useState(processor);
  const [liveDaily, setLiveDaily] = useState<AlwaysOnDailyCameraAnalytics | undefined>(daily);
  const [liveDetail, setLiveDetail] = useState(detail || "");
  const [production, setProduction] = useState<AlwaysOnProductionPayload | null>(null);
  const [productionLoading, setProductionLoading] = useState(false);
  const [productionError, setProductionError] = useState<string | null>(null);
  const [productionSaving, setProductionSaving] = useState(false);
  const [selectedProductionRuns, setSelectedProductionRuns] = useState<AlwaysOnProductionRun[] | null>(null);
  const [selectedProductionTimezone, setSelectedProductionTimezone] = useState("Asia/Almaty");
  const [selectedProductionLoading, setSelectedProductionLoading] = useState(false);
  const [selectedProductionError, setSelectedProductionError] = useState<string | null>(null);
  const [selectedProductionReload, setSelectedProductionReload] = useState(0);
  const [correctionAmount, setCorrectionAmount] = useState("");
  const [correctionColor, setCorrectionColor] = useState("");
  const [correctionReason, setCorrectionReason] = useState("");
  const [correctionError, setCorrectionError] = useState("");
  const [correcting, setCorrecting] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiveNote, setArchiveNote] = useState("");
  const [archiveError, setArchiveError] = useState("");
  const [archiving, setArchiving] = useState(false);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const current = open ? liveProcessor : processor;
  const currentDaily = open ? liveDaily : daily;
  const todayTotal = currentDaily?.total ?? 0;
  const allTimeTotal = currentDaily?.all_time_total ?? todayTotal;
  const inSession = current.mode === "session";
  const chartMax = Math.max(1, ...(currentDaily?.history ?? []).map((item) => item.total));
  const dominant = currentDaily?.colors?.[0];
  // Разбор одного дня: сам столбик уже несёт полную статистику, поэтому
  // выбранный день хранится ключом, а не копией — опрос обновляет данные,
  // не закрывая панель.
  const selectedPoint = (currentDaily?.history ?? []).find((item) => item.day === selectedDay);
  // Разбивку за день считает бэкенд — тем же кодом, что и общую, поэтому
  // цифры сходятся. Локальный расчёт остаётся на случай старого ответа.
  const selectedColors = selectedPoint?.colors?.length ? selectedPoint.colors : dayColorBreakdown(selectedPoint);
  const correctionAvailable = currentDaily?.colors?.find((item) => item.color === correctionColor)?.total ?? 0;

  useEffect(() => {
    setLiveProcessor(processor);
    setLiveDaily(daily);
    setLiveDetail(detail || "");
  }, [daily, detail, processor]);

  // Разбор дня — состояние одного просмотра: закрыли окно, выбор снят.
  useEffect(() => {
    if (!open) setSelectedDay(null);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refresh = async () => {
      try {
        const [settingsResponse, analyticsResponse] = await Promise.all([
          api.get<AlwaysOnCameraSettings>("/cameras/always-on-settings/"),
          api.get<AlwaysOnDailyAnalytics>("/cameras/always-on-analytics/"),
        ]);
        if (disposed) return;
        const next = settingsResponse.data.processors.find((item) => item.cam === processor.cam);
        if (next) setLiveProcessor(next);
        setLiveDaily(analyticsResponse.data.cameras.find((item) => item.camera === processor.cam));
        setLiveDetail(settingsResponse.data.detail || "");
      } catch (cause) {
        if (!disposed) setLiveDetail(apiError(cause));
      } finally {
        if (!disposed) timer = setTimeout(() => void refresh(), SESSION_POLL_MS);
      }
    };
    void refresh();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [open, processor.cam]);

  // Быстрый опрос только рамок. Отдельно от тяжёлого снимка: аналитику и
  // настройки незачем перечитывать раз в секунду, а рамка на общем интервале
  // отставала от мешка и висела после его ухода.
  useEffect(() => {
    if (!open || modalView !== "live" || !showDetections) {
      setLiveBoxes(null);
      return;
    }
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const pull = async () => {
      try {
        const { data } = await api.get<{ processors: AlwaysOnProcessorStatus[] }>("/cameras/always-on-detections/");
        if (disposed) return;
        const row = data.processors.find((item) => item.cam === processor.cam);
        setLiveBoxes({
          detections: row?.detections,
          frame: row?.detection_frame,
          at: Date.now(),
        });
      } catch {
        // Обрыв связи — не повод оставлять рамку на экране: она уже неверна.
        if (!disposed) setLiveBoxes(null);
      } finally {
        if (!disposed) timer = setTimeout(() => void pull(), DETECTIONS_POLL_MS);
      }
    };
    void pull();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [open, modalView, showDetections, processor.cam]);

  const loadProduction = useCallback(
    async (showLoader = false) => {
      if (showLoader) setProductionLoading(true);
      setProductionError(null);
      try {
        const response = await api.get<AlwaysOnProductionPayload>(
          `/cameras/always-on-production/?camera=${encodeURIComponent(processor.cam)}`,
        );
        setProduction(response.data);
        return response.data;
      } catch (cause) {
        setProductionError(apiError(cause));
        return null;
      } finally {
        if (showLoader) setProductionLoading(false);
      }
    },
    [processor.cam],
  );

  // Журнал обновляем отдельно и заметно реже live-рамок. Приходы и настройки
  // не должны раздувать уже существующий трёхсекундный polling аналитики.
  useEffect(() => {
    if (!open || modalView !== "production") return;
    void loadProduction(true);
    const timer = window.setInterval(() => void loadProduction(false), 15_000);
    return () => window.clearInterval(timer);
  }, [loadProduction, modalView, open]);

  // Исторический день запрашиваем отдельно: полный ответ вкладки «Выпуск и
  // склад» нельзя подменять дневным срезом. Текущий выбранный день обновляем,
  // пока окно открыто — так строка «идёт сейчас» и количество не замирают.
  useEffect(() => {
    if (!open || modalView !== "analytics" || !selectedDay) {
      setSelectedProductionRuns(null);
      setSelectedProductionError(null);
      setSelectedProductionLoading(false);
      return;
    }

    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const pollCurrentDay = selectedDay === currentDaily?.day;

    const pull = async (showLoader: boolean) => {
      if (showLoader) {
        setSelectedProductionRuns(null);
        setSelectedProductionLoading(true);
      }
      setSelectedProductionError(null);
      try {
        const response = await api.get<AlwaysOnProductionPayload>(
          `/cameras/always-on-production/?camera=${encodeURIComponent(processor.cam)}&day=${encodeURIComponent(selectedDay)}`,
        );
        if (disposed) return;
        setSelectedProductionRuns(response.data.day_runs);
        setSelectedProductionTimezone(response.data.timezone || "Asia/Almaty");
      } catch (cause) {
        if (!disposed) setSelectedProductionError(apiError(cause));
      } finally {
        if (!disposed) {
          setSelectedProductionLoading(false);
          if (pollCurrentDay) timer = setTimeout(() => void pull(false), 15_000);
        }
      }
    };

    void pull(true);
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [currentDaily?.day, modalView, open, processor.cam, selectedDay, selectedProductionReload]);

  async function saveProductionMappings(mappings: AlwaysOnProductMapping[]) {
    if (!canManage) return;
    setProductionSaving(true);
    setProductionError(null);
    try {
      const response = await api.put<AlwaysOnProductionPayload>("/cameras/always-on-production/", {
        camera: processor.cam,
        mappings: mappings.map(({ color, product }) => ({ color, product })),
      });
      setProduction(response.data);
      showSuccess("Привязки цветов к товарам сохранены");
    } catch (cause) {
      setProductionError(apiError(cause));
    } finally {
      setProductionSaving(false);
    }
  }

  async function retryProductionBatch(batch: AlwaysOnStockBatch) {
    if (!canManage) return;
    setProductionError(null);
    try {
      await api.post(`/cameras/always-on-production/batches/${batch.id}/retry/`);
      await loadProduction(false);
      showSuccess("Приёмка повторно проверена");
    } catch (cause) {
      setProductionError(apiError(cause));
    }
  }

  function showStream() {
    setStreamOnline(false);
    setModalView("live");
    setOpen(true);
  }

  function closeStream() {
    setOpen(false);
    setStreamOnline(false);
  }

  function showCorrection() {
    if (!canManage) return;
    setCorrectionAmount("");
    setCorrectionColor(currentDaily?.colors?.[0]?.color ?? "");
    setCorrectionReason("");
    setCorrectionError("");
    setCorrectionOpen(true);
  }

  async function subtractCount() {
    if (!canManage) return;
    setCorrecting(true);
    setCorrectionError("");
    try {
      await api.post<AlwaysOnDailyCameraAnalytics>(`/cameras/always-on-analytics/${processor.cam}/subtract/`, {
        amount: Number(correctionAmount),
        color: correctionColor,
        reason: correctionReason.trim(),
      });
      const analyticsResponse = await api.get<AlwaysOnDailyAnalytics>("/cameras/always-on-analytics/");
      setLiveDaily(analyticsResponse.data.cameras.find((item) => item.camera === processor.cam));
      await onAnalyticsChanged();
      await loadProduction(false);
      setCorrectionOpen(false);
    } catch (cause) {
      setCorrectionError(apiError(cause));
    } finally {
      setCorrecting(false);
    }
  }

  const loadArchives = useCallback(async () => {
    setArchivesError("");
    try {
      const response = await api.get<AlwaysOnCountArchive[]>(
        `/cameras/always-on-analytics/archives/?camera=${encodeURIComponent(processor.cam)}`,
      );
      setArchives(response.data);
      return response.data;
    } catch (cause) {
      setArchivesError(apiError(cause));
      return null;
    }
  }, [processor.cam]);

  // Архив не меняется сам по себе — грузим при первом открытии вкладки.
  useEffect(() => {
    if (open && modalView === "archive" && archives === null) void loadArchives();
  }, [open, modalView, archives, loadArchives]);

  async function deleteArchive(row: AlwaysOnCountArchive) {
    if (!canManage) return;
    setDeletingArchiveId(row.id);
    setDeleteArchiveError("");
    try {
      await api.delete(`/cameras/always-on-analytics/archives/${row.id}/`);
      // Мешки возвращаются в текущий счёт, поэтому обновляем и аналитику.
      const analyticsResponse = await api.get<AlwaysOnDailyAnalytics>("/cameras/always-on-analytics/");
      setLiveDaily(analyticsResponse.data.cameras.find((item) => item.camera === processor.cam));
      await onAnalyticsChanged();
      await loadArchives();
      setArchiveToDelete(null);
    } catch (cause) {
      setDeleteArchiveError(apiError(cause));
    } finally {
      setDeletingArchiveId(null);
    }
  }

  async function archiveCount() {
    if (!canManage) return;
    setArchiving(true);
    setArchiveError("");
    try {
      await api.post(`/cameras/always-on-analytics/${processor.cam}/archive/`, {
        note: archiveNote.trim(),
      });
      const analyticsResponse = await api.get<AlwaysOnDailyAnalytics>("/cameras/always-on-analytics/");
      setLiveDaily(analyticsResponse.data.cameras.find((item) => item.camera === processor.cam));
      await onAnalyticsChanged();
      const fresh = await loadArchives();
      setArchiveOpen(false);
      setArchiveNote("");
      // Сразу показываем, куда уехали мешки, и раскрываем свежую запись.
      setModalView("archive");
      if (fresh?.length) setOpenArchiveId(fresh[0].id);
    } catch (cause) {
      setArchiveError(apiError(cause));
    } finally {
      setArchiving(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={showStream}
        aria-label={`Открыть прямой эфир камеры ${camera?.zone || processor.cam}`}
        className="group relative w-full overflow-hidden rounded-[20px] border border-slate-200 bg-white p-4 text-left shadow-[0_10px_32px_rgba(44,65,103,0.06)] transition duration-200 hover:-translate-y-0.5 hover:border-blue-200 hover:shadow-[0_16px_38px_rgba(44,65,103,0.12)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40"
      >
        <span className="absolute inset-y-0 left-0 w-1 bg-gradient-to-b from-blue-500 to-emerald-400" />
        <span className="flex items-start gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-2xl bg-blue-50 text-blue-600 transition group-hover:bg-blue-600 group-hover:text-white">
            <Cpu className="size-5" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center justify-between gap-2">
              <span className="truncate text-sm font-bold text-slate-800">{camera?.zone || processor.cam}</span>
              <span className="text-right">
                <span className="block text-2xl font-black tabular-nums tracking-tight text-slate-900">
                  {todayTotal}
                </span>
                <span className="block text-[9px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                  сегодня
                </span>
              </span>
            </span>
            <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
              <span className="flex items-center gap-1.5">
                <span
                  className={cn(
                    "size-1.5 rounded-full",
                    current.running ? "animate-pulse bg-emerald-400" : "bg-amber-400",
                  )}
                />
                {inSession ? "режим отгрузки" : current.running ? "фоновый подсчёт" : "переподключение"}
              </span>
              <span>{inSession ? "видео записывается" : "без записи видео"}</span>
              <span className="ml-auto font-semibold text-slate-500">Всего: {allTimeTotal}</span>
            </span>
          </span>
        </span>
      </button>

      <Modal
        open={open}
        onClose={closeStream}
        eyebrow="AI 24/7 · мониторинг"
        title={camera?.zone || processor.cam}
        description="Прямой эфир, журнал цветовых смен, аналитика и автоматический приход на склад. Фоновое видео не записывается."
        className="max-w-5xl"
        mobileFullscreen
      >
        <div
          {...modalTabs.tabListProps}
          className="mb-4 flex w-full gap-1 overflow-x-auto rounded-xl border border-slate-200 bg-slate-100 p-1 sm:w-auto sm:inline-flex"
        >
          {MODAL_TABS.map((tab) => {
            const active = modalView === tab.key;
            const Icon = tab.icon;
            return (
              <button
                type="button"
                key={tab.key}
                {...modalTabs.getTabProps(tab.key)}
                className={cn(
                  "relative flex shrink-0 flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold transition sm:flex-none sm:px-4",
                  active ? "text-slate-900" : "text-slate-500 hover:text-slate-800",
                )}
              >
                {active && (
                  <motion.span
                    layoutId="modal-tab-pill"
                    transition={{ type: "spring", stiffness: 420, damping: 34 }}
                    className="absolute inset-0 rounded-lg bg-white shadow-sm motion-reduce:transition-none"
                  />
                )}
                <span className="relative z-10 flex items-center gap-2">
                  <Icon className="size-4" /> {tab.label}
                </span>
              </button>
            );
          })}
        </div>

        {modalView === "live" ? (
          <div
            {...modalTabs.getTabPanelProps("live")}
            className="grid overflow-hidden rounded-2xl border border-slate-200 bg-slate-950 shadow-[0_24px_70px_rgba(15,23,42,0.22)] sm:rounded-[22px] lg:grid-cols-[minmax(0,1fr)_260px]"
          >
            <div className="relative aspect-video min-h-0 overflow-hidden bg-[#111827] lg:aspect-auto lg:min-h-[460px]">
              {camera?.src ? (
                <CameraStream
                  src={camera.src}
                  onStateChange={setStreamOnline}
                  className="absolute inset-0 size-full object-contain"
                />
              ) : null}
              {/* Всегда-включённый поток идёт без вжатых рамок, поэтому
                  показываем работу модели оверлеем поверх видео. */}
              {streamOnline && showDetections && (
                <DetectionOverlay
                  detections={liveBoxes?.detections ?? current.detections}
                  frame={liveBoxes?.frame ?? current.detection_frame}
                  staleAfterMs={DETECTIONS_STALE_MS}
                  updatedAt={liveBoxes?.at}
                />
              )}
              {!streamOnline && (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-slate-950 text-white/45">
                  <VideoOff className="size-8" />
                  <span className="text-sm">Подключаем прямой поток…</span>
                </div>
              )}
              <div className="absolute left-2.5 top-2.5 flex items-center gap-2 rounded-full border border-white/15 bg-black/45 px-2.5 py-1 text-[10px] font-semibold text-white backdrop-blur-md sm:left-4 sm:top-4 sm:px-3 sm:py-1.5 sm:text-xs">
                <span
                  className={cn("size-2 rounded-full", streamOnline ? "animate-pulse bg-emerald-400" : "bg-amber-400")}
                />
                {streamOnline ? "ПРЯМОЙ ЭФИР" : "ПОДКЛЮЧЕНИЕ"}
              </div>
              {streamOnline && (
                <button
                  type="button"
                  onClick={() => setShowDetections((current) => !current)}
                  aria-pressed={showDetections}
                  className={cn(
                    "absolute right-2.5 top-2.5 flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-semibold backdrop-blur-md transition sm:right-4 sm:top-4 sm:px-3 sm:py-1.5 sm:text-xs",
                    showDetections
                      ? "border-emerald-400/40 bg-emerald-500/20 text-emerald-100"
                      : "border-white/15 bg-black/45 text-white/60 hover:text-white",
                  )}
                >
                  <ScanLine className="size-3.5" />
                  {showDetections ? "Рамки модели" : "Рамки скрыты"}
                  {showDetections && current.detections?.length ? (
                    <span className="tabular-nums">· {current.detections.length}</span>
                  ) : null}
                </button>
              )}
              {/* Старая версия ПК цеха не присылает координаты рамок. Молчать
                  нельзя: оператор видит включённую кнопку и пустое видео и
                  считает, что сломалась модель, хотя счёт при этом идёт. */}
              {streamOnline && showDetections && current.running && current.detections === undefined && (
                <div className="absolute bottom-2.5 left-2.5 right-2.5 rounded-lg border border-amber-400/30 bg-black/70 px-3 py-2 text-[11px] text-amber-100 backdrop-blur-md sm:bottom-4 sm:left-4 sm:right-auto sm:max-w-md">
                  Рамки недоступны: на ПК цеха стоит версия AI-сервиса без их передачи. Счёт мешков при этом работает —
                  обновите сервис, чтобы увидеть распознавание.
                </div>
              )}
            </div>

            <aside className="flex flex-col justify-between border-t border-white/10 bg-slate-900 p-4 text-white sm:p-5 lg:border-l lg:border-t-0">
              <div>
                <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-white/45">
                  <CalendarDays className="size-3.5" /> Реальный итог за сегодня
                </div>
                <div className="mt-1 text-5xl font-black tabular-nums tracking-tight sm:mt-2 sm:text-7xl">
                  {todayTotal}
                </div>
                <div className="mt-1 text-sm text-white/45">мешков · накоплено CRM</div>

                <div className="mt-4 grid grid-cols-2 gap-2 text-xs sm:mt-7 sm:block sm:space-y-2.5 sm:text-sm">
                  <div className="flex items-center justify-between rounded-xl bg-white/[0.06] px-3 py-2.5">
                    <span className="text-white/55">За всё время</span>
                    <span className="font-semibold tabular-nums">{allTimeTotal}</span>
                  </div>
                  <div className="flex items-center justify-between rounded-xl bg-white/[0.06] px-3 py-2.5">
                    <span className="text-white/55">Текущий цикл</span>
                    <span className="font-semibold tabular-nums">{current.total ?? 0}</span>
                  </div>
                  <div className="flex items-center justify-between rounded-xl bg-white/[0.06] px-3 py-2.5">
                    <span className="text-white/55">Модель</span>
                    <span className={cn("font-semibold", current.running ? "text-emerald-400" : "text-amber-300")}>
                      {current.running ? "работает" : "ожидает связь"}
                    </span>
                  </div>
                  <div className="flex items-center justify-between rounded-xl bg-white/[0.06] px-3 py-2.5">
                    <span className="text-white/55">Режим</span>
                    <span className="font-semibold">{inSession ? "отгрузка" : "24/7"}</span>
                  </div>
                  {(currentDaily?.adjustment ?? 0) < 0 && (
                    <div className="col-span-2 flex items-center justify-between rounded-xl border border-amber-300/15 bg-amber-300/10 px-3 py-2.5">
                      <span className="text-amber-100/65">Корректировка</span>
                      <span className="font-semibold tabular-nums text-amber-200">{currentDaily?.adjustment}</span>
                    </div>
                  )}
                </div>
              </div>
              <div className="mt-5 space-y-3">
                {(current.error || liveDetail) && (
                  <p className="rounded-xl border border-amber-300/15 bg-amber-300/10 px-3 py-2.5 text-xs leading-relaxed text-amber-100/80">
                    {current.error || liveDetail}
                  </p>
                )}
                {canManage && (
                  <button
                    type="button"
                    disabled={todayTotal <= 0}
                    onClick={showCorrection}
                    className="flex w-full items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2.5 text-xs font-semibold text-white/75 transition hover:bg-white/[0.1] hover:text-white disabled:cursor-not-allowed disabled:opacity-35"
                  >
                    <Minus className="size-3.5" /> Уменьшить итог
                  </button>
                )}
              </div>
            </aside>
          </div>
        ) : modalView === "production" ? (
          <div {...modalTabs.getTabPanelProps("production")}>
            <AlwaysOnProductionPanel
              payload={production}
              loading={productionLoading}
              error={productionError}
              saving={productionSaving}
              canManage={canManage}
              onSave={saveProductionMappings}
              onRetry={retryProductionBatch}
            />
          </div>
        ) : modalView === "analytics" ? (
          <div {...modalTabs.getTabPanelProps("analytics")} className="space-y-4">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              <Panel className="p-5">
                <Metric label="Сегодня" value={todayTotal} unit="меш." size="lg" />
              </Panel>
              <Panel className="p-5">
                <Metric label="За всё время" value={allTimeTotal} size="lg" accent="blue" />
              </Panel>
              <Panel className="col-span-2 p-5 sm:col-span-1">
                <Eyebrow>Чаще всего</Eyebrow>
                {dominant ? (
                  <div className="mt-2 flex items-center gap-2">
                    <ColorDot className={colorMeta(dominant.color).dot} />
                    <span className="text-2xl font-black tracking-tight text-slate-900">
                      {colorMeta(dominant.color).label}
                    </span>
                    <span className="ml-auto text-sm font-semibold tabular-nums text-slate-400">{dominant.total}</span>
                  </div>
                ) : (
                  <div className="mt-2 text-2xl font-bold text-slate-300">—</div>
                )}
              </Panel>
            </div>

            <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
              <Panel className="p-5 sm:p-6">
                <SectionHead
                  title="Подсчёт по дням"
                  aside={<span className="text-[11px] font-medium tabular-nums text-slate-400">макс. {chartMax}</span>}
                />
                <div className="mt-5 overflow-x-auto pb-1">
                  <div className="h-56 min-w-[520px] sm:h-64">
                    <div className="flex h-[188px] items-end gap-2 sm:h-[216px]">
                      {(currentDaily?.history ?? []).map((item) => {
                        const active = item.day === selectedDay;
                        return (
                          <button
                            type="button"
                            key={item.day}
                            aria-pressed={active}
                            aria-label={`Аналитика за ${fullDay(item.day)}: ${item.total} мешков`}
                            onClick={() => setSelectedDay(active ? null : item.day)}
                            className="group flex h-full min-w-0 flex-1 cursor-pointer flex-col justify-end rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                          >
                            <div className="relative flex flex-1 items-end justify-center">
                              <span className="pointer-events-none absolute -top-7 z-10 hidden whitespace-nowrap rounded-md bg-slate-900 px-2 py-1 text-[10px] font-semibold text-white shadow-lg group-hover:block">
                                {item.total} меш.
                              </span>
                              <div
                                className={cn(
                                  "w-full max-w-8 rounded-md transition-all duration-500 group-hover:brightness-105",
                                  active ? "bg-blue-600" : "bg-slate-200 group-hover:bg-slate-300",
                                  selectedDay && !active && "opacity-60",
                                )}
                                style={{ height: item.total ? `${Math.max(4, (item.total * 100) / chartMax)}%` : 0 }}
                              />
                            </div>
                            <span
                              className={cn(
                                "mt-2 block truncate text-center text-[9px] font-medium",
                                active ? "font-bold text-blue-600" : "text-slate-400",
                              )}
                            >
                              {shortDay(item.day)}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>
                {!selectedPoint && (
                  <p className="mt-4 text-center text-xs text-slate-400">Нажмите на столбик, чтобы раскрыть день</p>
                )}
              </Panel>

              <Panel className="flex flex-col p-5 sm:p-6">
                <SectionHead title="Цвета продукции" hint="За всё время по данным модели." />
                <div className="mt-5 space-y-4">
                  {(currentDaily?.colors ?? []).map((item) => (
                    <div key={item.color}>
                      <div className="mb-1.5 flex items-center gap-2 text-sm">
                        <ColorDot className={colorMeta(item.color).dot} />
                        <span className="font-medium text-slate-600">{colorMeta(item.color).label}</span>
                        <span className="ml-auto font-bold tabular-nums text-slate-900">{item.total}</span>
                        <span className="w-9 text-right text-xs tabular-nums text-slate-400">{item.percent}%</span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                        <div
                          className={cn("h-full rounded-full transition-all duration-500", colorMeta(item.color).bar)}
                          style={{ width: `${item.percent}%` }}
                        />
                      </div>
                    </div>
                  ))}
                  {!currentDaily?.colors?.length && (
                    <div className="py-10 text-center text-sm text-slate-400">Цветов пока нет</div>
                  )}
                </div>
                {canManage && (
                  <div className="mt-auto flex flex-col gap-2 pt-6">
                    <button
                      type="button"
                      disabled={todayTotal <= 0}
                      onClick={showCorrection}
                      className="flex w-full items-center justify-center gap-2 rounded-xl border border-slate-200 px-3 py-2.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-35"
                    >
                      <Minus className="size-3.5" /> Уменьшить за сегодня
                    </button>
                    <button
                      type="button"
                      disabled={allTimeTotal <= 0 || archiving}
                      onClick={() => setArchiveOpen(true)}
                      className="flex w-full items-center justify-center gap-2 rounded-xl border border-slate-200 px-3 py-2.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-35"
                    >
                      <Archive className="size-3.5" /> Сдать в архив
                    </button>
                  </div>
                )}
              </Panel>
            </div>

            {selectedPoint && (
              <Panel className="p-5 sm:p-6">
                <div className="flex flex-wrap items-center gap-2">
                  <h4 className="text-[15px] font-semibold tracking-tight text-slate-900">
                    {fullDay(selectedPoint.day)}
                  </h4>
                  <button
                    type="button"
                    onClick={() => setSelectedDay(null)}
                    className="ml-auto rounded-lg px-2 py-1 text-xs font-semibold text-slate-400 transition hover:bg-slate-50 hover:text-slate-700"
                  >
                    Закрыть
                  </button>
                </div>

                <div className="mt-4 grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
                  <Metric label="Итог" value={selectedPoint.total} size="sm" />
                  <Metric label="Модель" value={selectedPoint.model_total} size="sm" />
                  <Metric
                    label="Поправка"
                    value={selectedPoint.adjustment || 0}
                    size="sm"
                    accent={selectedPoint.adjustment < 0 ? "amber" : "slate"}
                  />
                  <Metric
                    label="Доля дня"
                    value={`${chartMax > 0 ? Math.round((selectedPoint.total * 100) / chartMax) : 0}%`}
                    size="sm"
                  />
                </div>

                {selectedColors.length > 0 && (
                  <>
                    <Hairline className="my-5" />
                    <div className="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-3">
                      {selectedColors.map((item) => (
                        <div key={item.color}>
                          <div className="flex items-center gap-2">
                            <ColorDot className={colorMeta(item.color).dot} />
                            <span className="text-xs font-medium text-slate-500">{colorMeta(item.color).label}</span>
                            <span className="ml-auto text-xs tabular-nums text-slate-400">{item.percent}%</span>
                          </div>
                          <div className="mt-1 text-2xl font-black tabular-nums tracking-tight text-slate-900">
                            {item.total}
                          </div>
                        </div>
                      ))}
                    </div>
                  </>
                )}

                <Hairline className="my-5" />

                <AlwaysOnDayRunLog
                  day={selectedPoint.day}
                  runs={selectedProductionRuns}
                  timezone={selectedProductionTimezone}
                  loading={selectedProductionLoading}
                  error={selectedProductionError}
                  onRetry={() => setSelectedProductionReload((value) => value + 1)}
                />
              </Panel>
            )}
          </div>
        ) : null}

        {modalView === "archive" && (
          <Panel {...modalTabs.getTabPanelProps("archive")} className="p-5 sm:p-6">
            <SectionHead
              title="Закрытые периоды"
              hint="Каждая строка — счёт, сданный в архив. Данные не меняются."
              aside={
                archives !== null && archives.length > 0 ? (
                  <Metric
                    label="Всего"
                    value={archives.reduce((sum, row) => sum + row.total, 0)}
                    size="sm"
                    className="text-right"
                  />
                ) : undefined
              }
            />

            {archivesError && (
              <p className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-[var(--destructive)]">
                {archivesError}
              </p>
            )}

            {archives === null && !archivesError && (
              <div className="flex min-h-40 items-center justify-center text-slate-400">
                <LoaderCircle className="size-5 animate-spin" />
              </div>
            )}

            {archives !== null && archives.length === 0 && (
              <div className="mt-4 flex min-h-40 flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 text-center text-slate-400">
                <Archive className="mb-2 size-7 text-slate-300" />
                <span className="text-sm font-semibold">Архив пуст</span>
                <span className="mt-1 max-w-64 text-xs">
                  Здесь появятся закрытые периоды после нажатия «Обнулить и сдать в архив».
                </span>
              </div>
            )}

            <div className="mt-4 space-y-2.5">
              {(archives ?? []).map((row) => {
                const expanded = openArchiveId === row.id;
                const dayMax = Math.max(1, ...row.day_rows.map((d) => d.total));
                return (
                  <div
                    key={row.id}
                    className={cn(
                      "overflow-hidden rounded-xl border transition",
                      expanded ? "border-blue-300 bg-blue-50/40" : "border-slate-200",
                    )}
                  >
                    <div className="flex items-stretch pr-2">
                      <button
                        type="button"
                        onClick={() => setOpenArchiveId(expanded ? null : row.id)}
                        aria-expanded={expanded}
                        className="flex min-w-0 flex-1 items-center gap-3 p-3 text-left transition hover:bg-slate-50/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40 sm:p-4"
                      >
                        <ChevronRight
                          className={cn("size-4 shrink-0 text-slate-400 transition-transform", expanded && "rotate-90")}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                            <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-400">
                              Период
                            </span>
                            <span className="font-bold text-slate-800">
                              {fullDay(row.period_start)}
                              {row.period_end !== row.period_start && ` — ${fullDay(row.period_end)}`}
                            </span>
                            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-500">
                              {row.days} дн.
                            </span>
                          </div>
                          <div className="mt-1 text-xs text-slate-400">
                            Заархивирован {formatDateTime(row.created_at)} · {row.archived_by_name || "—"}
                          </div>
                        </div>
                        <div className="shrink-0 text-right">
                          <div className="text-2xl font-black tabular-nums text-slate-900">{row.total}</div>
                          <div className="text-[10px] text-slate-400">мешков</div>
                        </div>
                      </button>
                      {/* Корзинка прямо на строке: удаление, спрятанное внутри
                        раскрытой карточки, никто не находил. */}
                      {canManage && (
                        <button
                          type="button"
                          aria-label={`Удалить архив за ${fullDay(row.period_start)}`}
                          title="Удалить запись — мешки вернутся в счёт"
                          disabled={deletingArchiveId === row.id}
                          onClick={() => setArchiveToDelete(row)}
                          className="flex size-9 shrink-0 items-center justify-center self-center rounded-lg text-slate-300 transition hover:bg-red-50 hover:text-[var(--destructive)] disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          {deletingArchiveId === row.id ? (
                            <LoaderCircle className="size-4 animate-spin" />
                          ) : (
                            <Trash2 className="size-4" />
                          )}
                        </button>
                      )}
                    </div>

                    {expanded && (
                      <div className="border-t border-slate-100 p-4 sm:p-5">
                        <div className="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
                          <Metric label="Итог" value={row.total} size="sm" />
                          <Metric label="Модель" value={row.model_total} size="sm" />
                          <Metric
                            label="Поправка"
                            value={row.adjustment || 0}
                            size="sm"
                            accent={row.adjustment < 0 ? "amber" : "slate"}
                          />
                          <Metric
                            label="В среднем"
                            value={row.days > 0 ? Math.round(row.total / row.days) : 0}
                            size="sm"
                          />
                        </div>

                        {row.colors.length > 0 && (
                          <>
                            <Hairline className="my-4" />
                            <div className="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-3">
                              {row.colors.map((item) => (
                                <div key={item.color}>
                                  <div className="flex items-center gap-2">
                                    <ColorDot className={colorMeta(item.color).dot} />
                                    <span className="text-xs font-medium text-slate-500">
                                      {colorMeta(item.color).label}
                                    </span>
                                    <span className="ml-auto text-xs tabular-nums text-slate-400">{item.percent}%</span>
                                  </div>
                                  <div className="mt-1 text-xl font-black tabular-nums tracking-tight text-slate-900">
                                    {item.total}
                                  </div>
                                </div>
                              ))}
                            </div>
                          </>
                        )}

                        {row.day_rows.length > 0 && (
                          <>
                            <Hairline className="my-4" />
                            <Eyebrow>По дням</Eyebrow>
                            <div className="mt-2 space-y-1.5">
                              {row.day_rows.map((entry) => (
                                <div key={entry.day} className="flex items-center gap-3 text-xs">
                                  <span className="w-20 shrink-0 font-medium text-slate-500">{fullDay(entry.day)}</span>
                                  <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-slate-100">
                                    <div
                                      className="h-full rounded-full bg-slate-300"
                                      style={{ width: `${Math.max(2, (entry.total * 100) / dayMax)}%` }}
                                    />
                                  </div>
                                  <span className="w-14 shrink-0 text-right font-bold tabular-nums text-slate-900">
                                    {entry.total}
                                  </span>
                                  <span className="hidden w-28 shrink-0 justify-end gap-1.5 sm:flex">
                                    {entry.colors.map((c) => (
                                      <span key={c.color} className="flex items-center gap-1 text-[10px]">
                                        <span className={cn("size-2 rounded-full", colorMeta(c.color).dot)} />
                                        <span className="tabular-nums text-slate-500">{c.total}</span>
                                      </span>
                                    ))}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </>
                        )}

                        {row.note && (
                          <p className="mt-4 rounded-lg bg-slate-50 px-3 py-2 text-xs italic text-slate-500">
                            {row.note}
                          </p>
                        )}

                        {canManage && (
                          <button
                            type="button"
                            disabled={deletingArchiveId === row.id}
                            onClick={() => setArchiveToDelete(row)}
                            className="mt-4 flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-slate-400 transition hover:bg-red-50 hover:text-[var(--destructive)] disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            {deletingArchiveId === row.id ? (
                              <LoaderCircle className="size-3.5 animate-spin" />
                            ) : (
                              <Trash2 className="size-3.5" />
                            )}
                            Удалить запись
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </Panel>
        )}
      </Modal>

      <Modal
        open={canManage && correctionOpen}
        onClose={() => !correcting && setCorrectionOpen(false)}
        eyebrow={`AI 24/7 · управление · ${camera?.zone || processor.cam}`}
        title="Уменьшить итог за сегодня"
        description="Используйте только для ложных срабатываний. Сырой результат модели не меняется, корректировка навсегда останется в журнале."
        className="max-w-lg"
        footer={
          <>
            <Button variant="ghost" disabled={correcting} onClick={() => setCorrectionOpen(false)}>
              Отмена
            </Button>
            <Button
              variant="destructive"
              disabled={
                correcting ||
                !correctionColor ||
                Number(correctionAmount) <= 0 ||
                Number(correctionAmount) > correctionAvailable ||
                correctionReason.trim().length < 5
              }
              onClick={() => void subtractCount()}
            >
              {correcting ? <LoaderCircle className="size-4 animate-spin" /> : <Minus className="size-4" />}
              Вычесть {Number(correctionAmount) > 0 ? correctionAmount : ""}
            </Button>
          </>
        }
      >
        <div className="space-y-5">
          <div className="flex items-end justify-between rounded-2xl border border-blue-100 bg-blue-50/70 p-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.12em] text-blue-500">Сейчас за сегодня</div>
              <div className="mt-1 text-4xl font-black tabular-nums text-slate-900">{todayTotal}</div>
            </div>
            <div className="text-right text-xs text-slate-500">
              модель: {currentDaily?.model_total ?? 0}
              <br />
              поправка: {currentDaily?.adjustment ?? 0}
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`correction-color-${processor.cam}`}>Цвет продукции</Label>
            <select
              id={`correction-color-${processor.cam}`}
              value={correctionColor}
              onChange={(event) => {
                setCorrectionColor(event.target.value);
                setCorrectionAmount("");
              }}
              className="h-10 w-full rounded-xl border bg-[var(--background)] px-3 text-sm outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/15"
            >
              <option value="">Выберите цвет</option>
              {(currentDaily?.colors ?? [])
                .filter((item) => item.total > 0)
                .map((item) => (
                  <option key={item.color} value={item.color}>
                    {colorMeta(item.color).label} · {item.total} меш.
                  </option>
                ))}
            </select>
            <span className="text-xs text-[var(--muted-foreground)]">
              Цвет нужен, чтобы складская корректировка попала в правильный товар.
            </span>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`correction-amount-${processor.cam}`}>Сколько вычесть</Label>
            <Input
              id={`correction-amount-${processor.cam}`}
              type="number"
              inputMode="numeric"
              min={1}
              max={correctionAvailable}
              autoFocus
              value={correctionAmount}
              onChange={(event) => setCorrectionAmount(event.target.value)}
              placeholder="Например, 2"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`correction-reason-${processor.cam}`}>Причина</Label>
            <textarea
              id={`correction-reason-${processor.cam}`}
              value={correctionReason}
              onChange={(event) => setCorrectionReason(event.target.value)}
              maxLength={500}
              placeholder="Например: два ложных пересечения линии"
              className="min-h-24 w-full resize-y rounded-xl border bg-[var(--background)] px-3 py-2 text-sm outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/15"
            />
            <span className="text-xs text-[var(--muted-foreground)]">Обязательно, минимум 5 символов.</span>
          </div>
          {correctionError && (
            <p className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-[var(--destructive)]">
              {correctionError}
            </p>
          )}
        </div>
      </Modal>

      <Modal
        open={canManage && archiveOpen}
        onClose={() => !archiving && setArchiveOpen(false)}
        eyebrow={`AI 24/7 · управление · ${camera?.zone || processor.cam}`}
        title="Обнулить счётчик и сдать в архив"
        description="Накопленное переносится в архив целиком: счётчик начнётся с нуля, а дни останутся в истории и на графике."
        className="max-w-lg"
        footer={
          <>
            <Button variant="ghost" disabled={archiving} onClick={() => setArchiveOpen(false)}>
              Отмена
            </Button>
            <Button disabled={archiving || allTimeTotal <= 0} onClick={() => void archiveCount()}>
              {archiving ? <LoaderCircle className="size-4 animate-spin" /> : <Archive className="size-4" />}
              Архивировать {allTimeTotal}
            </Button>
          </>
        }
      >
        <div className="space-y-5">
          <div className="flex items-end justify-between rounded-2xl border border-blue-100 bg-blue-50/70 p-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.12em] text-blue-500">Уйдёт в архив</div>
              <div className="mt-1 text-4xl font-black tabular-nums text-slate-900">{allTimeTotal}</div>
            </div>
            <div className="text-right text-xs text-slate-500">
              станет: 0
              <br />
              за сегодня: {todayTotal}
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`archive-note-${processor.cam}`}>Примечание</Label>
            <textarea
              id={`archive-note-${processor.cam}`}
              value={archiveNote}
              onChange={(event) => setArchiveNote(event.target.value)}
              maxLength={500}
              placeholder="Например: закрытие месяца, сдано в CRM"
              className="min-h-20 w-full resize-y rounded-xl border bg-[var(--background)] px-3 py-2 text-sm outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/15"
            />
            <span className="text-xs text-[var(--muted-foreground)]">
              Необязательно — попадёт в журнал вместе с суммой.
            </span>
          </div>
          {archiveError && (
            <p className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-[var(--destructive)]">
              {archiveError}
            </p>
          )}
        </div>
      </Modal>

      <Modal
        open={canManage && archiveToDelete !== null}
        onClose={() => deletingArchiveId === null && setArchiveToDelete(null)}
        eyebrow={`AI 24/7 · управление · ${camera?.zone || processor.cam}`}
        title="Удалить запись архива?"
        description="Мешки не пропадут — они вернутся в текущий счёт, как будто период не закрывали."
        className="max-w-lg"
        footer={
          <>
            <Button variant="ghost" disabled={deletingArchiveId !== null} onClick={() => setArchiveToDelete(null)}>
              Отмена
            </Button>
            <Button
              variant="destructive"
              disabled={deletingArchiveId !== null}
              onClick={() => archiveToDelete && void deleteArchive(archiveToDelete)}
            >
              {deletingArchiveId !== null ? (
                <LoaderCircle className="size-4 animate-spin" />
              ) : (
                <Trash2 className="size-4" />
              )}
              Удалить
            </Button>
          </>
        }
      >
        {archiveToDelete && (
          <div className="space-y-4">
            <div className="flex items-end justify-between rounded-2xl border border-amber-200 bg-amber-50/70 p-4">
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.12em] text-amber-600">Вернётся в счёт</div>
                <div className="mt-1 text-4xl font-black tabular-nums text-slate-900">{archiveToDelete.total}</div>
              </div>
              <div className="text-right text-xs text-slate-500">
                {fullDay(archiveToDelete.period_start)}
                {archiveToDelete.period_end !== archiveToDelete.period_start &&
                  ` — ${fullDay(archiveToDelete.period_end)}`}
                <br />
                {archiveToDelete.days} дн.
              </div>
            </div>
            <p className="text-sm text-slate-500">
              Запись исчезнет из архива, а её дни снова попадут в «за всё время» и на график. Действие попадёт в журнал.
            </p>
            {deleteArchiveError && (
              <p className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-[var(--destructive)]">
                {deleteArchiveError}
              </p>
            )}
          </div>
        )}
      </Modal>
    </>
  );
}

function conveyorEnabled(conveyor: ConveyorStatus | null | undefined): boolean {
  return conveyor?.enabled ?? conveyor?.configured ?? false;
}

function conveyorFeedbackValues(conveyor: ConveyorStatus | null | undefined): number[] {
  const values: number[] = [];
  if (conveyor?.feedback === 0 || conveyor?.feedback === 1) values.push(conveyor.feedback);
  if (conveyor?.feedback === false) values.push(0);
  if (conveyor?.feedback === true) values.push(1);
  if (conveyor?.feedback_on === false) values.push(0);
  if (conveyor?.feedback_on === true) values.push(1);
  if (conveyor?.verified_off === true) values.push(0);
  return values;
}

function conveyorFeedbackConflict(conveyor: ConveyorStatus | null | undefined): boolean {
  const values = conveyorFeedbackValues(conveyor);
  return conveyor?.feedback_conflict === true || (values.length > 1 && values.some((value) => value !== values[0]));
}

function conveyorFeedbackOff(conveyor: ConveyorStatus | null | undefined): boolean {
  const values = conveyorFeedbackValues(conveyor);
  return values.length > 0 && !conveyorFeedbackConflict(conveyor) && values.every((value) => value === 0);
}

function conveyorFeedbackLabel(conveyor: ConveyorStatus | null | undefined): string {
  if (conveyorFeedbackConflict(conveyor)) return "feedback конфликт";
  if (conveyorFeedbackOff(conveyor)) return "feedback OFF";
  const values = conveyorFeedbackValues(conveyor);
  if (values.length > 0 && values.every((value) => value === 1)) {
    return "feedback ON";
  }
  return "feedback —";
}

function conveyorStateLabel(state: ConveyorStatus["state"]): string {
  switch (state) {
    case "off":
      return "Остановлен";
    case "prepared":
    case "armed":
      return "Готов к запуску";
    case "arming":
    case "starting":
      return "Подготовка к запуску";
    case "running":
      return "Работает";
    case "stopping":
      return "Останавливается";
    case "goal_reached":
      return "Цель достигнута";
    case "fault":
      return "Авария";
    case "unknown":
      return "Состояние неизвестно";
    default:
      return "Автоматика не настроена";
  }
}

function conveyorStopMessage(conveyor: ConveyorStatus | null): string | null {
  if (!conveyor) return null;
  switch (conveyor.stop_reason) {
    case "target_reached":
      return "Цель достигнута — конвейер остановлен.";
    case "stale_ai":
      return "Нет свежих данных камеры — выполнен безопасный стоп. Нужна ручная сверка.";
    case "no_progress":
      return `Счётчик не увеличивался ${conveyor.no_progress_timeout_seconds ?? 15} с — выполнен безопасный стоп. Нужна ручная сверка.`;
    case "max_runtime":
      return `Достигнут предел непрерывной работы ${conveyor.max_run_seconds ?? 300} с — выполнен безопасный стоп.`;
    case "controller_fault":
      return conveyor.error
        ? `Ошибка ESP32 или feedback: ${conveyor.error}`
        : "Ошибка ESP32 или feedback — используйте аппаратный E-stop.";
    case "emergency_stop":
      return "Выполнен аварийный стоп. Автозапуск этой сессии заблокирован.";
    case "manual_stop":
      return "Конвейер остановлен оператором. Для заказа нужна ручная сверка.";
    case "start_timeout":
      return "ESP32 не подтвердил запуск — выход защёлкнут в OFF.";
    case "shutdown":
      return "Сервис камеры завершил работу и перевёл выход в OFF.";
    default:
      return conveyor.error || conveyor.stop_reason || null;
  }
}

function SessionCard({
  session,
  order,
  camera,
  onStopped,
}: {
  session: AiCountingSession;
  order?: Order;
  camera?: CameraFeed & { src: string };
  onStopped: () => void;
}) {
  const ai = useAiCounter(session.camera, session.order_id, true);
  const live = !!ai.status?.running;
  const total = ai.status?.total ?? session.last_status?.total ?? 0;
  const reportedTarget = ai.status?.target_total ?? session.target_total ?? session.last_status?.target_total ?? null;
  // Старый backend ещё не возвращает frozen target — до завершения раскатки
  // показываем текущий заказ, но автоматика всё равно опирается только на server target.
  const orderTarget = order ? orderedBagCount(order) : null;
  // target_total=0 — legacy sentinel у старых/пустых заказов, не рабочая цель.
  const target =
    typeof reportedTarget === "number" && reportedTarget > 0
      ? reportedTarget
      : orderTarget !== null && orderTarget > 0
        ? orderTarget
        : null;
  const reportedRemaining = ai.status?.remaining ?? session.remaining ?? session.last_status?.remaining ?? null;
  const remaining =
    typeof reportedRemaining === "number"
      ? Math.max(0, reportedRemaining)
      : target !== null
        ? Math.max(0, target - total)
        : null;
  const reportedGoalReached = ai.status?.goal_reached ?? session.goal_reached ?? session.last_status?.goal_reached;
  const liveConveyor = ai.status?.conveyor ?? null;
  const conveyor = liveConveyor ?? session.conveyor ?? session.last_status?.conveyor ?? null;
  const conveyorMessage = conveyorStopMessage(conveyor);
  const isConveyorEnabled = conveyorEnabled(conveyor) || session.conveyor_enabled === true;
  const serverGoalReached = reportedGoalReached ?? conveyor?.goal_reached;
  // При включённой автоматике решение о цели принимает только backend: браузерный
  // счёт может быть отложенным или пропустить пакет обновлений.
  const goalReached = isConveyorEnabled
    ? serverGoalReached === true
    : (serverGoalReached ?? (target !== null && total >= target));
  const feedbackOff = conveyorFeedbackOff(conveyor);
  const conveyorState = conveyor?.state ?? (isConveyorEnabled ? "unknown" : "unconfigured");
  const conveyorFault = conveyorState === "fault" || !!conveyor?.error || conveyorFeedbackConflict(conveyor);
  const controllerOnline = conveyor?.online;
  const canStop = ai.status?.can_stop ?? session.can_stop;
  const stream = ai.status?.stream ?? (live ? `${session.camera}ai` : camera?.src);
  const isStarting = session.status === "starting";
  const needsRecovery =
    isStarting || ai.status?.code === "ai_reconciliation_required" || ai.status?.code === "ai_processor_stopped";
  const progress = target && target > 0 ? Math.min(100, Math.round((total / target) * 100)) : 0;
  const overrun = target !== null && total > target;
  // Для подключённого контроллера завершение погрузки допустимо только после server goal
  // и физического read-back OFF. Старая конфигурация без ESP сохраняет прежний flow.
  const freshControlledStatus =
    ai.status !== null &&
    !ai.stale &&
    ai.status.owned_by_order === true &&
    String(ai.status.session_id) === String(session.id) &&
    liveConveyor !== null;
  const liveState = liveConveyor?.state;
  const canComplete =
    !isConveyorEnabled ||
    (freshControlledStatus &&
      ai.status?.goal_reached === true &&
      conveyorFeedbackOff(liveConveyor) &&
      liveConveyor?.online === true &&
      (liveState === "off" || liveState === "goal_reached") &&
      !liveConveyor?.error &&
      !conveyorFeedbackConflict(liveConveyor));
  const requiresManualReconciliation =
    isConveyorEnabled &&
    !canComplete &&
    (conveyor?.terminal === true ||
      conveyorFault ||
      needsRecovery ||
      ai.stale ||
      conveyorState === "off" ||
      conveyorState === "goal_reached");
  const liveLabel = ai.stale
    ? "СВЯЗЬ ПОТЕРЯНА"
    : goalReached
      ? "ЦЕЛЬ ДОСТИГНУТА"
      : live
        ? "СЧИТЫВАНИЕ"
        : needsRecovery
          ? "ТРЕБУЕТ ЗАПУСКА"
          : "ЗАПУСК";

  async function runCommand(command: () => Promise<void>) {
    try {
      await command();
    } catch {
      // useAiCounter показывает нормализованную ошибку внутри карточки.
    } finally {
      onStopped();
    }
  }

  async function closeForManualReconciliation() {
    const confirmed = window.confirm(
      "Конвейер будет подтверждённо остановлен, AI-сессия закроется, но погрузка не будет завершена. " +
        "После сверки завершите погрузку или верните заказ вручную. Продолжить?",
    );
    if (!confirmed) return;
    await runCommand(() => ai.stop(false, session.id));
  }

  function completionHint(): string {
    if (!isConveyorEnabled) return "Итог AI-подсчёта будет сохранён, а заказ станет готов к оформлению выезда.";
    if (!freshControlledStatus) return "Получите свежее состояние текущей AI-сессии перед завершением погрузки.";
    if (ai.stale) return "Сначала восстановите связь и подтвердите остановку конвейера.";
    if (conveyorFault) return "Устраните аварию контроллера перед завершением погрузки.";
    if (controllerOnline !== true) {
      return controllerOnline === false
        ? "ESP32 не на связи — физическая остановка не подтверждена."
        : "Связь ESP32 ещё не подтверждена.";
    }
    if (!goalReached) return remaining !== null ? `До цели осталось ${remaining} меш.` : "Цель ещё не достигнута.";
    if (!feedbackOff) return "Ждём физическое подтверждение feedback OFF.";
    return "Конвейер подтверждённо остановлен — итог подсчёта можно сохранить.";
  }

  return (
    <article className="group overflow-hidden rounded-[22px] border border-slate-200/80 bg-white shadow-[0_12px_38px_rgba(44,65,103,0.07)] transition hover:-translate-y-0.5 hover:shadow-[0_18px_48px_rgba(44,65,103,0.11)]">
      <div className="relative aspect-[16/8] overflow-hidden bg-[#172033]">
        {stream ? (
          <CameraStream src={stream} className="absolute inset-0 size-full object-cover" />
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-white/35">
            <VideoOff className="size-6" />
            <span className="text-xs">Поток запускается</span>
          </div>
        )}
        <div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/60 to-transparent px-4 pb-8 pt-3">
          <span className="flex items-center gap-2 rounded-full bg-black/40 px-2.5 py-1 text-[11px] font-semibold text-white backdrop-blur-md">
            <span
              className={cn(
                "size-2 rounded-full",
                ai.stale
                  ? "bg-red-400"
                  : goalReached
                    ? "bg-emerald-400"
                    : live
                      ? "animate-pulse bg-emerald-400"
                      : "bg-amber-400",
              )}
            />
            {liveLabel}
          </span>
          <span className="rounded-full bg-black/40 px-2.5 py-1 text-[11px] text-white/90 backdrop-blur-md">
            {camera?.zone || session.camera}
          </span>
        </div>
        <div className="absolute bottom-3 right-3 rounded-2xl border border-white/20 bg-slate-950/70 px-4 py-2 text-right text-white backdrop-blur-lg">
          <div className="text-[10px] uppercase tracking-[0.14em] text-white/55">мешков камерой</div>
          <div className="flex items-baseline justify-end gap-1 tabular-nums">
            <span className="text-3xl font-bold leading-none">{total}</span>
            {target !== null && <span className="text-sm font-semibold text-white/55">/ {target}</span>}
          </div>
        </div>
      </div>

      <div className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <PackageCheck className="size-4 shrink-0 text-blue-600" />
              <h3 className="truncate text-[15px] font-bold text-slate-800">
                Заказ #{session.order_id} · {session.order_client_name || "Без клиента"}
              </h3>
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-slate-500">
              <span className="flex items-center gap-1.5">
                <UserRound className="size-3.5" /> {session.started_by_name}
              </span>
              <span className="flex items-center gap-1.5">
                <Clock3 className="size-3.5" /> {formatDateTime(session.started_at)}
              </span>
              <span className="flex items-center gap-1.5">
                <Camera className="size-3.5" /> {session.camera}
              </span>
            </div>
          </div>
        </div>

        {target !== null && (
          <div className="mt-4">
            <div className="mb-1.5 flex items-center justify-between text-[11px] font-semibold">
              <span className={overrun ? "text-red-600" : goalReached ? "text-emerald-700" : "text-slate-500"}>
                {overrun
                  ? `Превышение цели на ${total - target}`
                  : goalReached
                    ? "Цель достигнута"
                    : `Осталось ${remaining ?? Math.max(0, target - total)} меш.`}
              </span>
              <span className="tabular-nums text-slate-400">{progress}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-100">
              <div
                className={cn(
                  "h-full rounded-full transition-[width] duration-500",
                  overrun ? "bg-red-500" : goalReached ? "bg-emerald-500" : "bg-blue-600",
                )}
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        )}

        <div
          className={cn(
            "mt-4 rounded-2xl border px-3.5 py-3",
            conveyorFault
              ? "border-red-200 bg-red-50/80"
              : conveyorState === "off" || conveyorState === "goal_reached"
                ? "border-emerald-200 bg-emerald-50/70"
                : conveyorState === "running"
                  ? "border-blue-200 bg-blue-50/75"
                  : "border-amber-200 bg-amber-50/70",
          )}
        >
          <div className="flex items-center gap-3">
            <span
              className={cn(
                "flex size-9 shrink-0 items-center justify-center rounded-xl",
                conveyorFault
                  ? "bg-red-600 text-white"
                  : conveyorState === "off" || conveyorState === "goal_reached"
                    ? "bg-emerald-600 text-white"
                    : conveyorState === "running"
                      ? "bg-blue-600 text-white"
                      : "bg-amber-500 text-white",
              )}
            >
              <Radio className={cn("size-4", conveyorState === "running" && "animate-pulse")} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">Конвейер</div>
              <div className="truncate text-sm font-bold text-slate-800">{conveyorStateLabel(conveyorState)}</div>
            </div>
            <div className="shrink-0 text-right">
              <div
                className={cn(
                  "text-[11px] font-bold",
                  feedbackOff ? "text-emerald-700" : conveyorFault ? "text-red-700" : "text-slate-600",
                )}
              >
                {isConveyorEnabled ? conveyorFeedbackLabel(conveyor) : "не подключён"}
              </div>
              {isConveyorEnabled && (
                <div
                  className={cn("mt-0.5 text-[10px]", controllerOnline === false ? "text-red-600" : "text-slate-400")}
                >
                  {controllerOnline === true
                    ? "ESP32 онлайн"
                    : controllerOnline === false
                      ? "ESP32 нет связи"
                      : "связь не подтверждена"}
                </div>
              )}
            </div>
          </div>
          {conveyorMessage && (
            <p className={cn("mt-2 text-xs", conveyor?.error ? "text-red-700" : "text-slate-500")}>{conveyorMessage}</p>
          )}
        </div>

        {ai.stale && (
          <div className="mt-3 flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-xs text-red-700">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            Последнее состояние устарело. Не завершайте погрузку, пока связь и feedback OFF не подтверждены.
          </div>
        )}

        <div className="mt-4">
          {canStop ? (
            isStarting ? (
              <div className="grid gap-2 sm:grid-cols-2">
                <Button
                  className="h-10 rounded-xl"
                  disabled={ai.busy}
                  onClick={() => void runCommand(() => ai.start(session.id))}
                >
                  <RefreshCw className={cn("size-3.5", ai.busy && "animate-spin")} />
                  Повторить запуск
                </Button>
                <Button
                  variant="outline"
                  className="h-10 rounded-xl border-red-200 text-[var(--destructive)] hover:bg-red-50 hover:text-red-700"
                  disabled={ai.busy}
                  onClick={() => void runCommand(() => ai.stop(false, session.id))}
                >
                  <Square className="size-3.5 fill-current" /> Отменить запуск
                </Button>
                {isConveyorEnabled && (
                  <Button
                    variant="destructive"
                    className="h-10 rounded-xl sm:col-span-2"
                    disabled={ai.stoppingConveyor}
                    onClick={() => void runCommand(() => ai.stopConveyor(session.id))}
                  >
                    <Square className="size-3.5 fill-current" /> Аварийно остановить конвейер
                  </Button>
                )}
              </div>
            ) : (
              <div className={cn("grid gap-2", isConveyorEnabled && "sm:grid-cols-2")}>
                {needsRecovery && (
                  <Button
                    className={cn("h-10 rounded-xl", isConveyorEnabled && "sm:col-span-2")}
                    disabled={ai.busy}
                    onClick={() => void runCommand(() => ai.start(session.id))}
                  >
                    <RefreshCw className={cn("size-3.5", ai.busy && "animate-spin")} />
                    Восстановить AI-счётчик
                  </Button>
                )}
                {isConveyorEnabled && (
                  <Button
                    variant="destructive"
                    className="h-10 rounded-xl"
                    disabled={ai.stoppingConveyor}
                    onClick={() => void runCommand(() => ai.stopConveyor(session.id))}
                  >
                    <Square className="size-3.5 fill-current" /> Остановить конвейер
                  </Button>
                )}
                <Button
                  className="h-10 rounded-xl"
                  disabled={ai.busy || !canComplete}
                  onClick={() => void runCommand(() => ai.stop(true, session.id))}
                >
                  <Check className="size-3.5" /> Завершить погрузку
                </Button>
                {requiresManualReconciliation && (
                  <Button
                    variant="outline"
                    className="h-10 rounded-xl border-amber-300 text-amber-800 sm:col-span-2"
                    disabled={ai.busy}
                    onClick={() => void closeForManualReconciliation()}
                  >
                    <Square className="size-3.5" /> Закрыть AI для ручной сверки
                  </Button>
                )}
                <p
                  className={cn(
                    "text-center text-[11px] sm:col-span-2",
                    canComplete ? "text-emerald-700" : "text-slate-500",
                  )}
                >
                  {completionHint()} После этого оформите фактический выезд отдельно на «Посту погрузки».
                </p>
              </div>
            )
          ) : (
            <div className="grid gap-2">
              {isConveyorEnabled && (
                <Button
                  variant="destructive"
                  className="h-10 rounded-xl"
                  disabled={ai.stoppingConveyor}
                  onClick={() => void runCommand(() => ai.stopConveyor(session.id))}
                >
                  <Square className="size-3.5 fill-current" /> Аварийно остановить конвейер
                </Button>
              )}
              <div className="flex items-center justify-center gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-[12px] text-slate-500">
                <LockKeyhole className="size-3.5" /> Управлять сессией может {session.started_by_name} или администратор
              </div>
            </div>
          )}
          {ai.error && <p className="mt-2 text-center text-xs text-[var(--destructive)]">{ai.error}</p>}
        </div>
      </div>
    </article>
  );
}

function MonoblockPageInner() {
  const { me } = useAuth();
  const isSuper = !!me?.is_superuser;
  const canManageSystem = can(me, "sys_permissions.manage");
  // Техническая учётная запись физического моноблока работает только с
  // отгрузкой своей камеры. Общий производственный мониторинг предназначен
  // сотрудникам, которые входят на эту же страницу по shipping.load.
  const canViewAlwaysOn = can(me, "shipping.load") && !me?.is_monoblock;
  const canManageAlwaysOn = canViewAlwaysOn && can(me, "ai_247.manage");
  const { data: orders, error, reload: reloadOrders } = useApi<Order[]>("/orders/?post_board=1");
  const { data: cameras, error: camerasError, reload: reloadCameras } = useApi<CameraFeed[]>("/cameras/");
  const {
    data: sessions,
    error: sessionsError,
    reload: reloadSessions,
  } = useApi<AiCountingSession[]>("/cameras/ai/sessions/");
  const {
    data: cameraSettings,
    error: cameraSettingsError,
    reload: reloadCameraSettings,
  } = useApi<MonoblockCameraSettings>("/cameras/monoblock-settings/");
  const {
    data: monoblockDevices,
    error: monoblockDevicesError,
    reload: reloadMonoblockDevices,
  } = useApi<MonoblockDevice[]>(me?.is_superuser ? "/cameras/monoblock-devices/" : null);
  const {
    data: conveyorDevices,
    error: conveyorDevicesError,
    reload: reloadConveyorDevices,
  } = useApi<ConveyorDevice[]>(me?.is_superuser ? "/conveyors/devices/" : null);
  const {
    data: alwaysOnSettings,
    error: alwaysOnSettingsError,
    reload: reloadAlwaysOnSettings,
    setData: setAlwaysOnSettings,
  } = useApi<AlwaysOnCameraSettings>(canViewAlwaysOn ? "/cameras/always-on-settings/" : null);
  const {
    data: alwaysOnAnalytics,
    error: alwaysOnAnalyticsError,
    reload: reloadAlwaysOnAnalytics,
  } = useApi<AlwaysOnDailyAnalytics>(canViewAlwaysOn ? "/cameras/always-on-analytics/" : null);
  // Страница разделена на вкладки: «Отгрузки» (по умолчанию) — запуск сессий
  // и активные отгрузки, «AI 24/7» — сам моноблок с бесконечным циклом подсчёта.
  // Технический аккаунт моноблока остаётся только на вкладке отгрузки.
  const [tab, setTab] = useState<(typeof MONOBLOCK_PAGE_TABS)[number]>("shipments");
  const activeTab = canViewAlwaysOn ? tab : "shipments";
  const pageTabs = useRovingTabs({
    tabs: MONOBLOCK_PAGE_TABS,
    active: activeTab,
    onChange: setTab,
    label: "Режим моноблока",
  });
  const playable = useMemo(
    () => playableCameras(cameras).filter((camera) => /^cam[1-9]\d*$/.test(camera.src)),
    [cameras],
  );
  const monoblockCameras = useMemo(() => {
    const allowed = new Set(cameraSettings?.camera_sources ?? []);
    return playable.filter((camera) => allowed.has(camera.src));
  }, [cameraSettings?.camera_sources, playable]);

  useVisiblePolling(reloadSessions, SESSION_POLL_MS);
  useVisiblePolling(
    () =>
      Promise.all([
        reloadOrders(),
        reloadCameras(),
        reloadCameraSettings(),
        ...(canViewAlwaysOn ? [reloadAlwaysOnSettings(), reloadAlwaysOnAnalytics()] : []),
        ...(isSuper ? [reloadConveyorDevices()] : []),
      ]),
    SLOW_POLL_MS,
  );
  const auxiliaryError =
    camerasError ||
    sessionsError ||
    cameraSettingsError ||
    monoblockDevicesError ||
    conveyorDevicesError ||
    alwaysOnSettingsError ||
    alwaysOnAnalyticsError;
  const reloadAll = () =>
    Promise.all([
      reloadOrders(),
      reloadCameras(),
      reloadSessions(),
      reloadCameraSettings(),
      reloadMonoblockDevices(),
      reloadConveyorDevices(),
      reloadAlwaysOnSettings(),
      reloadAlwaysOnAnalytics(),
    ]);

  const sessionOrderIds = new Set((sessions ?? []).map((session) => session.order_id));
  const startable = (orders ?? []).filter((order) => {
    if (sessionOrderIds.has(order.id)) return false;
    // Новая сессия начинается для готового к погрузке заказа. `arrived`
    // поддерживаем для старых/ручных записей, но въезд и весы здесь не нужны.
    // После привязки камеры backend сразу переводит заказ в `loading`,
    // поэтому активный заказ больше не должен оставаться в этом списке.
    return order.status === "confirmed" || order.status === "arrived";
  });
  const cameraOwners = useMemo(() => {
    const result: Record<string, number> = {};
    for (const order of orders ?? []) {
      if (order.loading_camera && ["confirmed", "arrived", "loading"].includes(order.status)) {
        result[order.loading_camera] ??= order.id;
      }
    }
    for (const session of sessions ?? []) result[session.camera] = session.order_id;
    return result;
  }, [orders, sessions]);

  async function start(order: Order, camera: CameraFeed & { src: string }) {
    try {
      await api.post(
        `/cameras/${camera.src}/ai/`,
        { order_id: order.id },
        {
          params: { order_id: order.id },
        },
      );
    } finally {
      // Даже если ПК камеры не ответил, сервер мог уже безопасно закрепить
      // слот и перевести заказ в загрузку — сразу показываем реальное состояние.
      await Promise.all([reloadOrders(), reloadSessions()]);
    }
  }

  return (
    <AppShell title="Моноблок" section="Работа">
      {error && !orders ? (
        <ErrorAlert message={error} onRetry={() => void reloadAll()} />
      ) : (
        <div className="flex flex-col gap-7">
          {(error || auxiliaryError) && (
            <ErrorAlert message={error || auxiliaryError} onRetry={() => void reloadAll()} />
          )}
          {(canViewAlwaysOn || canManageSystem) && (
            <div className="flex flex-wrap items-center gap-3">
              {canViewAlwaysOn && (
                <div
                  {...pageTabs.tabListProps}
                  className="grid w-full min-w-0 grid-cols-2 rounded-2xl border border-slate-200 bg-slate-100 p-1 xl:w-auto xl:grid-flow-col xl:grid-cols-none"
                >
                  <button
                    type="button"
                    {...pageTabs.getTabProps("shipments")}
                    className={cn(
                      "flex min-w-0 items-center justify-center gap-2 rounded-xl px-2 py-2.5 text-xs font-semibold transition sm:px-4 sm:text-sm",
                      activeTab === "shipments"
                        ? "bg-white text-slate-900 shadow-sm"
                        : "text-slate-500 hover:text-slate-800",
                    )}
                  >
                    <Radio className="size-4" /> Отгрузки
                    <span
                      className={cn(
                        "rounded-full px-2 py-0.5 text-[11px] tabular-nums",
                        activeTab === "shipments" ? "bg-blue-50 text-blue-600" : "bg-white/70 text-slate-500",
                      )}
                    >
                      {sessions?.length ?? 0}
                    </span>
                  </button>
                  <button
                    type="button"
                    {...pageTabs.getTabProps("monoblock")}
                    className={cn(
                      "flex min-w-0 items-center justify-center gap-2 rounded-xl px-2 py-2.5 text-xs font-semibold transition sm:px-4 sm:text-sm",
                      activeTab === "monoblock"
                        ? "bg-white text-slate-900 shadow-sm"
                        : "text-slate-500 hover:text-slate-800",
                    )}
                  >
                    <Cpu className="size-4" /> AI 24/7
                    <span
                      className={cn(
                        "rounded-full px-2 py-0.5 text-[11px] tabular-nums",
                        activeTab === "monoblock" ? "bg-blue-50 text-blue-600" : "bg-white/70 text-slate-500",
                      )}
                    >
                      {alwaysOnSettings?.camera_sources.length ?? 0}
                    </span>
                  </button>
                </div>
              )}
              <div className="ml-auto flex items-center gap-2">
                {activeTab === "monoblock" ? (
                  canManageAlwaysOn ? (
                    <AlwaysOnSettingsButton
                      cameras={playable}
                      settings={alwaysOnSettings}
                      onSaved={setAlwaysOnSettings}
                    />
                  ) : null
                ) : canManageSystem ? (
                  <>
                    {isSuper && (
                      <>
                        <ConveyorDevicesButton
                          cameras={playable}
                          devices={conveyorDevices ?? []}
                          reload={reloadConveyorDevices}
                        />
                        <MonoblockDevicesButton
                          cameras={playable}
                          devices={monoblockDevices ?? []}
                          reload={reloadMonoblockDevices}
                        />
                      </>
                    )}
                    <CameraSettingsButton cameras={playable} settings={cameraSettings} reload={reloadCameraSettings} />
                  </>
                ) : null}
              </div>
            </div>
          )}

          <div {...(canViewAlwaysOn ? pageTabs.getTabPanelProps(activeTab) : {})} className="flex flex-col gap-7">
            {activeTab === "monoblock" ? (
              !alwaysOnSettings?.camera_sources.length ? (
                <div className="flex min-h-56 flex-col items-center justify-center rounded-[24px] border border-dashed border-slate-200 bg-slate-50/70 p-8 text-center">
                  <span className="flex size-14 items-center justify-center rounded-full bg-white text-slate-300 shadow-sm">
                    <Cpu className="size-6" />
                  </span>
                  <p className="mt-3 text-sm font-semibold text-slate-600">Бесконечный цикл пока не запущен</p>
                  <p className="mt-1 max-w-sm text-xs text-slate-400">
                    {canManageAlwaysOn
                      ? "Выберите камеры в настройке «AI 24/7» — модель начнёт считать круглосуточно, без публикации и записи видео."
                      : "Камеры для постоянного подсчёта пока не настроены. Обратитесь к сотруднику с правом управления AI 24/7."}
                  </p>
                </div>
              ) : (
                <section className="rounded-[24px] border border-blue-100 bg-gradient-to-br from-blue-50/80 via-white to-emerald-50/40 p-5">
                  <div className="mb-4 flex items-center gap-3">
                    <span className="flex size-10 items-center justify-center rounded-xl bg-blue-600 text-white shadow-[0_8px_22px_rgba(37,99,235,0.25)]">
                      <Cpu className="size-5" />
                    </span>
                    <div>
                      <h2 className="text-[18px] font-bold tracking-tight text-slate-800">Постоянный AI-контур</h2>
                      <p className="text-[12px] text-slate-400">
                        Бесконечный цикл: модель считает круглосуточно, без публикации и записи фонового видео
                      </p>
                    </div>
                    <div className="ml-auto flex items-center gap-2">
                      <span className="flex items-center gap-2 rounded-full border border-blue-100 bg-white px-3 py-1 text-[11px] font-semibold text-blue-700 shadow-sm">
                        <CalendarDays className="size-3.5" /> Сегодня: {alwaysOnAnalytics?.total ?? 0}
                        <span className="text-slate-300">·</span>
                        Всего: {alwaysOnAnalytics?.all_time_total ?? alwaysOnAnalytics?.total ?? 0}
                      </span>
                      <span
                        className={cn(
                          "rounded-full border bg-white px-3 py-1 text-[11px] font-semibold shadow-sm",
                          alwaysOnSettings.sync_status === "synced" ? "text-emerald-600" : "text-amber-600",
                        )}
                      >
                        {alwaysOnSettings.sync_status === "synced" ? "синхронизировано" : "ожидает связь"}
                      </span>
                    </div>
                  </div>
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {alwaysOnSettings.camera_sources.map((source) => {
                      const processor = alwaysOnSettings.processors.find((item) => item.cam === source) ?? {
                        cam: source,
                        running: false,
                        mode: "always_on" as const,
                        recording: false,
                        total: 0,
                      };
                      return (
                        <AlwaysOnCard
                          key={source}
                          processor={processor}
                          camera={playable.find((item) => item.src === source)}
                          detail={alwaysOnSettings.detail}
                          daily={alwaysOnAnalytics?.cameras.find((item) => item.camera === source)}
                          canManage={canManageAlwaysOn}
                          onAnalyticsChanged={reloadAlwaysOnAnalytics}
                        />
                      );
                    })}
                  </div>
                </section>
              )
            ) : (
              <>
                <ShipmentLauncher
                  orders={startable}
                  cameras={monoblockCameras}
                  busyCameras={(sessions ?? []).map((session) => session.camera)}
                  cameraOwners={cameraOwners}
                  activeSessionCount={sessions?.length ?? 0}
                  cameraLocked={!!cameraSettings?.locked || !!me?.is_monoblock}
                  onStart={start}
                />

                <section>
                  <div className="mb-4 flex items-center gap-3">
                    <span className="flex size-10 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
                      <Radio className="size-5" />
                    </span>
                    <div>
                      <h2 className="text-[20px] font-bold tracking-tight text-slate-800">Активные отгрузки</h2>
                      <p className="text-[12px] text-slate-400">Каждая сессия закреплена за отдельной камерой</p>
                    </div>
                    <span className="ml-auto rounded-full border bg-white px-3 py-1 text-[12px] font-semibold text-slate-600 shadow-sm">
                      {sessions?.length ?? 0} активн.
                    </span>
                  </div>

                  {!sessions?.length ? (
                    <div className="flex min-h-48 flex-col items-center justify-center rounded-[22px] border border-dashed border-slate-200 bg-slate-50/70 text-center">
                      <span className="flex size-14 items-center justify-center rounded-full bg-white text-slate-300 shadow-sm">
                        <Radio className="size-6" />
                      </span>
                      <p className="mt-3 text-sm font-semibold text-slate-600">Активных сессий пока нет</p>
                      <p className="mt-1 text-xs text-slate-400">Выберите заказ и камеру выше, чтобы начать.</p>
                    </div>
                  ) : (
                    <div className="grid gap-4 xl:grid-cols-2 2xl:grid-cols-3">
                      {sessions.map((session) => (
                        <SessionCard
                          key={session.id}
                          session={session}
                          order={(orders ?? []).find((order) => order.id === session.order_id)}
                          camera={playable.find((camera) => camera.src === session.camera)}
                          onStopped={() => {
                            void Promise.all([reloadOrders(), reloadSessions()]);
                          }}
                        />
                      ))}
                    </div>
                  )}
                </section>
              </>
            )}
          </div>
        </div>
      )}
    </AppShell>
  );
}

export default function MonoblockPage() {
  return (
    <RequirePerm perm="shipping.load" title="Моноблок">
      <MonoblockPageInner />
    </RequirePerm>
  );
}
