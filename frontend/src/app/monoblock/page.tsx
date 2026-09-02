"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  Camera,
  CalendarDays,
  Cpu,
  Check,
  Clock3,
  LockKeyhole,
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
import { CameraCountingLineOverlay } from "@/components/camera-counting-line-overlay";
import { DetectionOverlay } from "@/components/detection-overlay";
import {
  AlwaysOnDayColorViewToggle,
  AlwaysOnDayRunLog,
  AlwaysOnProductionPanel,
  AlwaysOnReceiptDestinationLabel,
  resolveAlwaysOnReceiptDestination,
  type AlwaysOnDayColorView,
  type AlwaysOnReceiptMappingContext,
} from "@/components/monoblock/always-on-production-panel";
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
import { brandMeta } from "@/lib/monoblock-brands";
import { colorMeta, normalizedColor } from "@/lib/monoblock-colors";
import { api, apiError } from "@/lib/api";
import { orderedBagCount } from "@/lib/orders";
import { resolveCountingLine } from "@/lib/camera-counting-line";
import { showSuccess } from "@/lib/toast";
import { can } from "@/lib/can";
import type {
  AiCountingSession,
  AlwaysOnCameraSettings,
  AlwaysOnDailyAnalytics,
  AlwaysOnDailyCameraAnalytics,
  AlwaysOnDetection,
  AlwaysOnProcessorStatus,
  AlwaysOnProductMapping,
  AlwaysOnProductionPayload,
  AlwaysOnStockBatch,
  CameraContinuousReadiness,
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
const DETECTIONS_POLL_MS = 250;
// Рамка старше этого времени описывает уже уехавший мешок — гасим её, чтобы
// она не висела на пустом месте при обрыве связи или остановке модели.
const DETECTIONS_STALE_MS = 2_500;
// Заказы/камеры/настройки меняются редко — не гоняем полный список заказов
// каждые 3 секунды на экране, который висит открытым весь день.
const SLOW_POLL_MS = 30_000;
const ALWAYS_ON_MODAL_VIEWS = ["live", "production", "analytics"] as const;
const SHIPPING_MODAL_VIEWS: readonly (typeof ALWAYS_ON_MODAL_VIEWS)[number][] = ["live", "analytics"];
const MONOBLOCK_PAGE_TABS = ["shipments", "monoblock"] as const;

const MODAL_TABS: { key: (typeof ALWAYS_ON_MODAL_VIEWS)[number]; label: string; icon: LucideIcon }[] = [
  { key: "live", label: "Прямой эфир", icon: Video },
  { key: "production", label: "Выпуск и склад", icon: PackageCheck },
  { key: "analytics", label: "Аналитика", icon: BarChart3 },
];

function CameraChoice({
  camera,
  checked,
  onToggle,
  disabled = false,
  disabledReason,
}: {
  camera: CameraFeed & { src: string };
  checked: boolean;
  onToggle: () => void;
  disabled?: boolean;
  disabledReason?: string;
}) {
  const [streamOnline, setStreamOnline] = useState(false);

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={checked}
      disabled={disabled}
      aria-label={disabledReason ? `${camera.zone}: ${disabledReason}` : undefined}
      className={cn(
        "group overflow-hidden rounded-2xl border text-left transition duration-200",
        checked
          ? "border-blue-400 bg-blue-50 shadow-[0_10px_28px_rgba(59,104,210,0.15)] ring-2 ring-blue-500/20"
          : "border-slate-200 bg-white hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-md",
        disabled &&
          "cursor-not-allowed border-amber-200 bg-amber-50/60 opacity-75 hover:translate-y-0 hover:shadow-none",
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
          {disabledReason && (
            <span className="mt-1 flex items-center gap-1 text-[10px] font-semibold text-amber-700">
              <LockKeyhole className="size-3" /> {disabledReason}
            </span>
          )}
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
    if ((settings?.blocked_camera_sources ?? []).includes(source)) return;
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
        description="Отметьте логические камеры camN. Камера-ПК подключает их напрямую к substream и готовит непрерывный AI-процессор до начала заказа."
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
            Выбранные камеры остаются в отдельном контуре отгрузки и считают круглосуточно через sub. В AI 24/7 они не
            перемещаются. Камеру с активной отгрузкой нельзя добавить или убрать до завершения сессии.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {cameras.map((camera) => {
            const checked = selected.includes(camera.src);
            const blocked = (settings?.blocked_camera_sources ?? []).includes(camera.src);
            return (
              <CameraChoice
                key={camera.id}
                camera={camera}
                checked={checked}
                disabled={blocked}
                disabledReason={blocked ? "занята контуром AI 24/7" : undefined}
                onToggle={() => toggle(camera.src)}
              />
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
  blockedCameraSources = [],
  reload,
}: {
  cameras: (CameraFeed & { src: string })[];
  devices: MonoblockDevice[];
  blockedCameraSources?: string[];
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
  const [policyNotice, setPolicyNotice] = useState("");

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
      const response = editing
        ? await api.patch<MonoblockDevice>(`/cameras/monoblock-devices/${editing.id}/`, body)
        : await api.post<MonoblockDevice>("/cameras/monoblock-devices/", body);
      await reload();
      setFormOpen(false);
      setPolicyNotice(
        response.status === 202 || response.data.always_on_sync_status === "pending"
          ? response.data.always_on_detail ||
              "Настройка сохранена, но камера-ПК ещё не подтвердила непрерывный контур отгрузки. Запуск будет недоступен до синхронизации."
          : "",
      );
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
      const response = await api.delete<{
        deleted?: boolean;
        always_on_sync_status?: "synced" | "pending";
        always_on_detail?: string;
      }>(`/cameras/monoblock-devices/${removing.id}/`);
      await reload();
      setRemoving(null);
      if (response.status === 202 || response.data?.always_on_sync_status === "pending") {
        const detail =
          response.data?.always_on_detail || "Моноблок удалён, но камера-ПК ещё не подтвердила новый контур отгрузки.";
        setPolicyNotice(detail);
        showSuccess("Моноблок удалён; камеры отгрузки ожидают синхронизации");
      } else {
        setPolicyNotice("");
        showSuccess("Моноблок удалён");
      }
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
        description="У каждого физического моноблока свой логин и ровно одна логическая камера camN. Активная камера автоматически работает 24/7 через прямое сопоставление substream на камера-ПК."
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
        {policyNotice && (
          <p className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            {policyNotice}
          </p>
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
            <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Моноблок в цехе" />
          </label>
          <label className="grid gap-1.5">
            <Label>Логин</Label>
            <Input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="monoblock-workshop"
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
              {cameras.map((camera) => {
                const occupiedByDevice = occupied.has(camera.src);
                const ownedByAi247 = blockedCameraSources.includes(camera.src);
                return (
                  <option key={camera.src} value={camera.src} disabled={occupiedByDevice || ownedByAi247}>
                    {camera.zone} · {camera.src}
                    {ownedByAi247 ? " · занята AI 24/7" : occupiedByDevice ? " · занята другим моноблоком" : ""}
                  </option>
                );
              })}
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
    if ((settings?.blocked_camera_sources ?? []).includes(source)) return;
    setSelected((current) => {
      if (current.includes(source)) return current.filter((item) => item !== source);
      const activeOtherSources = settings?.active_other_camera_sources ?? settings?.blocked_camera_sources ?? [];
      const availableCapacity = settings?.capacity ? Math.max(0, settings.capacity - activeOtherSources.length) : null;
      if (availableCapacity !== null && current.length >= availableCapacity) {
        setError(
          `На ПК камер настроен общий лимит ${settings?.capacity}; активные камеры отгрузки уже занимают ${activeOtherSources.length}.`,
        );
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
        description="Отдельный контур AI 24/7 через прямой camN/sub. Камеры отгрузки сюда не переносятся и недоступны для выбора."
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
            <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-sky-600">Контур</p>
            <p className="mt-1 text-sm font-bold text-slate-800">Отдельно от отгрузки</p>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3">
            <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">Диск камеры</p>
            <p className="mt-1 text-sm font-bold text-slate-800">Технический архив 48 ч</p>
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
            const blocked = (settings?.blocked_camera_sources ?? []).includes(camera.src);
            const live = settings?.processors.find((item) => item.cam === camera.src);
            return (
              <button
                key={camera.id}
                type="button"
                onClick={() => toggle(camera.src)}
                aria-pressed={checked}
                disabled={blocked}
                aria-label={blocked ? `${camera.zone}: принадлежит контуру отгрузки` : undefined}
                className={cn(
                  "flex items-center gap-3 rounded-2xl border p-3 text-left transition",
                  checked
                    ? "border-blue-400 bg-blue-50 ring-2 ring-blue-500/15"
                    : "border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm",
                  blocked && "cursor-not-allowed border-amber-300 bg-amber-50/70 ring-amber-500/10",
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
                  {blocked && (
                    <span className="mt-1 flex items-center gap-1 text-[10px] font-semibold text-amber-700">
                      <LockKeyhole className="size-3" /> Камера отгрузки · {camera.src}/sub
                    </span>
                  )}
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
  readiness,
  daily,
  analyticsError,
  canManage,
  scope = "ai_247",
}: {
  processor: AlwaysOnProcessorStatus;
  camera?: CameraFeed & { src: string };
  detail?: string;
  readiness?: CameraContinuousReadiness;
  daily?: AlwaysOnDailyCameraAnalytics;
  analyticsError?: string;
  canManage: boolean;
  scope?: "shipping" | "ai_247";
}) {
  const isShipping = scope === "shipping";
  const runtimeSettingsUrl = isShipping ? "/cameras/shipping-continuous-settings/" : "/cameras/always-on-settings/";
  const detectionsUrl = isShipping ? "/cameras/shipping-continuous-detections/" : "/cameras/always-on-detections/";
  const analyticsUrl = isShipping ? "/cameras/shipping-continuous-analytics/" : "/cameras/always-on-analytics/";
  const modalViews = isShipping ? SHIPPING_MODAL_VIEWS : ALWAYS_ON_MODAL_VIEWS;
  const visibleModalTabs = MODAL_TABS.filter((tab) => modalViews.includes(tab.key));
  const [open, setOpen] = useState(false);
  const [modalView, setModalView] = useState<(typeof ALWAYS_ON_MODAL_VIEWS)[number]>("live");
  const modalTabs = useRovingTabs({
    tabs: modalViews,
    active: modalView,
    onChange: setModalView,
    label: "Режим мониторинга камеры",
  });
  const [streamOnline, setStreamOnline] = useState(false);
  // Рамки модели можно скрыть: иногда оператору нужно посмотреть на сам кадр.
  const [showDetections, setShowDetections] = useState(true);
  // Рамки живут отдельно от остального состояния: их опрашиваем чаще, чтобы
  // они держались мешка, и помечаем временем — устаревшие гасим.
  const [liveBoxes, setLiveBoxes] = useState<{
    detections: AlwaysOnDetection[];
    bagsPresent?: boolean | null;
    frame?: { width?: number; height?: number } | null;
    line?: AlwaysOnProcessorStatus["line"];
    direction?: AlwaysOnProcessorStatus["direction"];
    revision?: string | null;
    at: number;
  } | null>(null);
  const [liveProcessor, setLiveProcessor] = useState(processor);
  const [liveReadiness, setLiveReadiness] = useState(readiness);
  const [liveDaily, setLiveDaily] = useState<AlwaysOnDailyCameraAnalytics | undefined>(daily);
  const [liveDetail, setLiveDetail] = useState(detail || "");
  const [liveAnalyticsError, setLiveAnalyticsError] = useState(analyticsError || "");
  const [production, setProduction] = useState<AlwaysOnProductionPayload | null>(null);
  const [productionLoading, setProductionLoading] = useState(false);
  const [productionError, setProductionError] = useState<string | null>(null);
  const [productionSaving, setProductionSaving] = useState(false);
  const productionRequestSequence = useRef(0);
  const productionMutationInFlight = useRef(false);
  const [selectedProductionDay, setSelectedProductionDay] = useState<AlwaysOnProductionPayload | null>(null);
  const [selectedProductionLoading, setSelectedProductionLoading] = useState(false);
  const [selectedProductionError, setSelectedProductionError] = useState<string | null>(null);
  const [selectedProductionReload, setSelectedProductionReload] = useState(0);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [selectedDayColorView, setSelectedDayColorView] = useState<AlwaysOnDayColorView>("algorithm");
  const current = open ? liveProcessor : processor;
  const currentReadiness = open ? liveReadiness : readiness;
  const bagsPresent = open && liveBoxes ? liveBoxes.bagsPresent : current.bags_present;
  const countingLine = resolveCountingLine(
    {
      line: liveBoxes?.line ?? current.line,
      direction: liveBoxes?.direction ?? current.direction,
    },
    camera?.line_config,
  );
  const currentDaily = open ? liveDaily : daily;
  const todayTotal = currentDaily?.total ?? 0;
  const allTimeTotal = currentDaily?.all_time_total ?? todayTotal;
  const analyticsTransportError = open ? liveAnalyticsError : analyticsError;
  const analyticsAvailable = !analyticsTransportError && currentDaily?.analytics_sync?.available === true;
  const analyticsDetail =
    analyticsTransportError || currentDaily?.analytics_sync?.detail || "Аналитика событий ещё не синхронизирована";
  const todayDisplay = analyticsAvailable ? todayTotal : "—";
  const allTimeDisplay = analyticsAvailable ? allTimeTotal : "—";
  const liveCounterAvailable =
    current.running === true &&
    current.processor_alive === true &&
    current.source === "sub" &&
    current.analytics_scope === scope &&
    currentReadiness?.status === "synced" &&
    Number.isFinite(current.total) &&
    current.total >= 0;
  const currentCycleDisplay = liveCounterAvailable ? current.total : "—";
  const inSession = current.mode === "session";
  const chartMax = Math.max(1, ...(currentDaily?.history ?? []).map((item) => item.total));
  const dominant = currentDaily?.colors?.[0];
  const currentReceiptMappings = selectedProductionDay?.mappings ?? production?.mappings ?? null;
  const currentReceiptError = selectedProductionError || productionError;
  const receiptMapping = useMemo<AlwaysOnReceiptMappingContext>(
    () => ({
      status: currentReceiptError
        ? "unavailable"
        : currentReceiptMappings
          ? "ready"
          : selectedProductionDay || production
            ? "unavailable"
            : "loading",
      mappings: currentReceiptMappings,
      products: selectedProductionDay?.products ?? production?.products,
      warehouse: selectedProductionDay?.warehouse ?? production?.warehouse,
      warehouseName: selectedProductionDay?.warehouse_name ?? production?.warehouse_name,
    }),
    [currentReceiptError, currentReceiptMappings, production, selectedProductionDay],
  );
  // Разбор одного дня: сам столбик уже несёт полную статистику, поэтому
  // выбранный день хранится ключом, а не копией — опрос обновляет данные,
  // не закрывая панель.
  const selectedPoint = (currentDaily?.history ?? []).find((item) => item.day === selectedDay);
  // Разбивку за день считает бэкенд — тем же кодом, что и общую, поэтому
  // цифры сходятся. Локальный расчёт остаётся на случай старого ответа.
  const selectedColors = selectedPoint?.colors?.length ? selectedPoint.colors : dayColorBreakdown(selectedPoint);
  // Дневная детализация приходит отдельным запросом. Проверка даты не даёт
  // на один рендер показать ответ предыдущего столбика после быстрого клика.
  const selectedDayProduction =
    selectedProductionDay?.selected_day === selectedPoint?.day ? selectedProductionDay : null;
  const selectedRawRuns = selectedDayProduction?.day_runs ?? null;
  const smoothing = selectedDayProduction?.run_smoothing;
  const serverAlgorithmRuns = selectedDayProduction?.algorithm_day_runs;
  const algorithmViewAvailable = Boolean(serverAlgorithmRuns && smoothing);
  const selectedAlgorithmRuns = serverAlgorithmRuns && smoothing ? serverAlgorithmRuns : selectedRawRuns;
  const rawRunsTotal = selectedRawRuns?.reduce((sum, run) => sum + run.model_bags, 0);
  const runsMatchSelectedAnalytics = Boolean(
    selectedPoint &&
    selectedRawRuns &&
    !selectedRawRuns.some((run) => run.is_partial_for_day) &&
    rawRunsTotal === selectedPoint.model_total &&
    (!smoothing ||
      (smoothing.raw_model_total === selectedPoint.model_total &&
        smoothing.algorithm_model_total === selectedPoint.model_total)),
  );
  const selectedVisibleRuns = selectedDayProduction
    ? runsMatchSelectedAnalytics
      ? selectedDayColorView === "algorithm"
        ? selectedAlgorithmRuns
        : selectedRawRuns
      : []
    : null;
  const runMismatchMessage =
    selectedDayProduction && !runsMatchSelectedAnalytics
      ? "Периоды не показаны: журнал не совпадает с выбранным срезом аналитики — например, часть дня уже перенесена в архив."
      : null;
  // Старые интервалы могут пересекать границу дня, а append-only журнал —
  // границу переноса в архив. В обоих случаях не смешиваем разные срезы.
  const selectedVisibleColors =
    runsMatchSelectedAnalytics && smoothing
      ? selectedDayColorView === "algorithm" && algorithmViewAvailable
        ? smoothing.algorithm_colors
        : smoothing.raw_colors
      : selectedColors;
  const selectedMappings = selectedDayProduction?.mappings ?? production?.mappings ?? null;
  const selectedReceiptMapping = useMemo<AlwaysOnReceiptMappingContext>(
    () => ({
      status:
        selectedProductionError || productionError
          ? "unavailable"
          : selectedMappings
            ? "ready"
            : selectedDayProduction || production
              ? "unavailable"
              : "loading",
      mappings: selectedMappings,
      products: selectedDayProduction?.products ?? production?.products,
      warehouse: selectedDayProduction?.warehouse ?? production?.warehouse,
      warehouseName: selectedDayProduction?.warehouse_name ?? production?.warehouse_name,
    }),
    [selectedDayProduction, selectedMappings, selectedProductionError, production, productionError],
  );
  const selectedBrandsByColor = selectedDayProduction?.dominant_brand_by_color;
  const selectedBrandByColor = new Map(
    Object.entries(selectedBrandsByColor ?? {}).map(([color, brand]) => [normalizedColor(color), brand]),
  );
  const selectedBrandStatus = selectedBrandsByColor
    ? "ready"
    : selectedProductionError || selectedDayProduction
      ? "unavailable"
      : "loading";
  useEffect(() => {
    setLiveProcessor(processor);
    setLiveReadiness(readiness);
    setLiveDaily(daily);
    setLiveDetail(detail || "");
    setLiveAnalyticsError(analyticsError || "");
  }, [analyticsError, daily, detail, processor, readiness]);

  // Разбор дня — состояние одного просмотра: закрыли окно, выбор снят.
  useEffect(() => {
    if (!open) setSelectedDay(null);
  }, [open]);

  useEffect(() => {
    setSelectedDayColorView("algorithm");
  }, [selectedDay]);

  useEffect(() => {
    if (!open) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refresh = async () => {
      try {
        const [settingsResponse, analyticsResponse] = await Promise.all([
          api.get<AlwaysOnCameraSettings>(runtimeSettingsUrl),
          api.get<AlwaysOnDailyAnalytics>(analyticsUrl),
        ]);
        if (disposed) return;
        const next = settingsResponse.data.processors.find((item) => item.cam === processor.cam);
        setLiveProcessor(
          next ?? {
            cam: processor.cam,
            running: false,
            processor_alive: false,
            mode: "always_on",
            analytics_scope: scope,
            source: "sub",
            recording: false,
            total: 0,
          },
        );
        setLiveReadiness(settingsResponse.data.camera_readiness?.[processor.cam]);
        setLiveDaily(analyticsResponse.data.cameras.find((item) => item.camera === processor.cam));
        setLiveDetail(settingsResponse.data.detail || "");
        setLiveAnalyticsError("");
      } catch (cause) {
        if (!disposed) {
          const message = apiError(cause);
          setLiveDetail(message);
          setLiveAnalyticsError(message);
        }
      } finally {
        if (!disposed) timer = setTimeout(() => void refresh(), SESSION_POLL_MS);
      }
    };
    void refresh();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [analyticsUrl, open, processor.cam, runtimeSettingsUrl, scope]);

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
        const { data } = await api.get<{ processors: AlwaysOnProcessorStatus[] }>(detectionsUrl);
        if (disposed) return;
        const row = data.processors.find((item) => item.cam === processor.cam);
        const revision = row?.last_frame_at ?? (row ? null : "processor-missing");
        setLiveBoxes((previous) => {
          // Four quick browser polls can hit the same one-second backend
          // snapshot. Do not rerender the whole card until that snapshot (or
          // its applied line) actually changes.
          if (
            revision &&
            revision === previous?.revision &&
            row?.line === previous.line &&
            row?.direction === previous.direction &&
            row?.bags_present === previous.bagsPresent
          ) {
            return previous;
          }
          return {
            // A successful snapshot without this processor is authoritative.
            // An explicit empty list prevents the initial, now-stale settings
            // snapshot from reappearing through a nullish fallback.
            detections: row?.detections ?? [],
            bagsPresent: row?.bags_present ?? null,
            frame: row?.detection_frame,
            line: row?.line,
            direction: row?.direction,
            revision,
            at: Date.now(),
          };
        });
      } catch {
        // Null means that the first fast poll has not completed yet. Once a
        // poll fails, keep an explicit empty snapshot so initial detections do
        // not reappear behind an unavailable endpoint.
        if (!disposed) {
          setLiveBoxes((previous) =>
            previous?.revision === "unavailable"
              ? previous
              : { detections: [], bagsPresent: null, revision: "unavailable", at: Date.now() },
          );
        }
      } finally {
        if (!disposed) timer = setTimeout(() => void pull(), DETECTIONS_POLL_MS);
      }
    };
    void pull();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [detectionsUrl, open, modalView, showDetections, processor.cam]);

  const loadProduction = useCallback(
    async (showLoader = false) => {
      if (productionMutationInFlight.current) return null;
      const requestSequence = ++productionRequestSequence.current;
      if (showLoader) setProductionLoading(true);
      setProductionError(null);
      try {
        const response = await api.get<AlwaysOnProductionPayload>(
          `/cameras/always-on-production/?camera=${encodeURIComponent(processor.cam)}`,
        );
        if (requestSequence !== productionRequestSequence.current || productionMutationInFlight.current) {
          return null;
        }
        setProduction(response.data);
        return response.data;
      } catch (cause) {
        if (requestSequence === productionRequestSequence.current && !productionMutationInFlight.current) {
          setProductionError(apiError(cause));
        }
        return null;
      } finally {
        if (requestSequence === productionRequestSequence.current) {
          setProductionLoading(false);
        }
      }
    },
    [processor.cam],
  );

  // Полный производственный журнал обновляем только на его собственной
  // вкладке. Этот endpoint также закрывает устаревшие периоды, поэтому не
  // дублируем его polling поверх выбранного дня аналитики.
  useEffect(() => {
    if (!open || modalView !== "production") return;
    void loadProduction(true);
    const timer = window.setInterval(() => void loadProduction(false), 15_000);
    return () => window.clearInterval(timer);
  }, [loadProduction, modalView, open]);

  // Сводной аналитике нужен текущий маршрут цвет → товар → склад. Пока день
  // не выбран, обновляем его отдельно; после выбора дневной запрос становится
  // единственным polling-источником и не создаёт двойных блокировок на backend.
  useEffect(() => {
    if (!open || isShipping || modalView !== "analytics" || selectedDay) return;
    void loadProduction(false);
    const timer = window.setInterval(() => void loadProduction(false), 15_000);
    return () => window.clearInterval(timer);
  }, [isShipping, loadProduction, modalView, open, selectedDay]);

  // Исторический день запрашиваем отдельно: полный ответ вкладки «Выпуск и
  // склад» нельзя подменять дневным срезом. Текущий выбранный день обновляем,
  // пока окно открыто — так строка «идёт сейчас» и количество не замирают.
  useEffect(() => {
    if (isShipping || !open || modalView !== "analytics" || !selectedDay) {
      setSelectedProductionDay(null);
      setSelectedProductionError(null);
      setSelectedProductionLoading(false);
      return;
    }

    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const pollCurrentDay = selectedDay === currentDaily?.day;

    const pull = async (showLoader: boolean) => {
      if (showLoader) {
        setSelectedProductionDay(null);
        setSelectedProductionLoading(true);
      }
      setSelectedProductionError(null);
      try {
        const response = await api.get<AlwaysOnProductionPayload>(
          `/cameras/always-on-production/?camera=${encodeURIComponent(processor.cam)}&day=${encodeURIComponent(selectedDay)}`,
        );
        if (disposed) return;
        setProductionError(null);
        setSelectedProductionDay(response.data);
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
  }, [currentDaily?.day, isShipping, modalView, open, processor.cam, selectedDay, selectedProductionReload]);

  async function saveProductionMappings(mappings: AlwaysOnProductMapping[], warehouse: number | null) {
    if (!canManage) return;
    productionMutationInFlight.current = true;
    productionRequestSequence.current += 1;
    setProductionLoading(false);
    setProductionSaving(true);
    setProductionError(null);
    try {
      const response = await api.put<AlwaysOnProductionPayload>("/cameras/always-on-production/", {
        camera: processor.cam,
        ...(warehouse !== null ? { warehouse } : {}),
        mappings: mappings.map(({ color, product }) => ({ color, product })),
      });
      setProduction(response.data);
      showSuccess("Привязки цветов к товарам сохранены");
    } catch (cause) {
      setProductionError(apiError(cause));
    } finally {
      productionMutationInFlight.current = false;
      setProductionSaving(false);
    }
  }

  async function retryProductionBatch(batch: AlwaysOnStockBatch) {
    if (!canManage) return;
    productionMutationInFlight.current = true;
    productionRequestSequence.current += 1;
    setProductionLoading(false);
    setProductionError(null);
    try {
      await api.post(`/cameras/always-on-production/batches/${batch.id}/retry/`);
      productionMutationInFlight.current = false;
      await loadProduction(false);
      showSuccess("Приёмка повторно проверена");
    } catch (cause) {
      setProductionError(apiError(cause));
    } finally {
      productionMutationInFlight.current = false;
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
                  {todayDisplay}
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
              <span>{inSession ? "AI-видео отгрузки" : "технический архив 48 ч"}</span>
              <span
                className={cn(
                  "font-semibold",
                  bagsPresent === true
                    ? "text-emerald-600"
                    : bagsPresent === false
                      ? "text-slate-500"
                      : "text-amber-600",
                )}
              >
                Мешки в кадре: {bagsPresent === true ? "есть" : bagsPresent === false ? "нет" : "нет данных"}
              </span>
              <span className="ml-auto font-semibold text-slate-500">Всего: {allTimeDisplay}</span>
            </span>
          </span>
        </span>
      </button>

      <Modal
        open={open}
        onClose={closeStream}
        eyebrow={isShipping ? "Отгрузки · камеры работают 24/7" : "AI 24/7 · мониторинг"}
        title={camera?.zone || processor.cam}
        description={
          isShipping
            ? "Прямой эфир и отдельная непрерывная аналитика камеры отгрузки. Заказ подключается к уже работающей модели без переноса камеры в AI 24/7."
            : "Прямой эфир, журнал цветовых смен, аналитика и автоматический приход на склад. Фоновый AI-overlay не публикуется; исходный substream хранится в техническом архиве 48 часов."
        }
        className="max-w-5xl"
        mobileFullscreen
      >
        <div
          {...modalTabs.tabListProps}
          className="mb-4 flex w-full gap-1 overflow-x-auto rounded-xl border border-slate-200 bg-slate-100 p-1 sm:w-auto sm:inline-flex"
        >
          {visibleModalTabs.map((tab) => {
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
              {/* Всегда-включённый поток идёт без вжатых рамок и линии,
                  поэтому весь слой модели собирает браузер поверх видео. */}
              {streamOnline && showDetections && (
                <>
                  <DetectionOverlay
                    detections={liveBoxes ? liveBoxes.detections : current.detections}
                    frame={liveBoxes?.frame ?? current.detection_frame}
                    staleAfterMs={DETECTIONS_STALE_MS}
                    updatedAt={liveBoxes?.at}
                  />
                  {countingLine && (
                    <CameraCountingLineOverlay line={countingLine.line} direction={countingLine.direction} />
                  )}
                </>
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
                  {showDetections ? "Рамки и линия" : "Слой скрыт"}
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
                  {todayDisplay}
                </div>
                <div className="mt-1 text-sm text-white/45">
                  {analyticsAvailable ? "мешков · накоплено CRM" : "аналитика не синхронизирована"}
                </div>

                <div className="mt-4 grid grid-cols-2 gap-2 text-xs sm:mt-7 sm:block sm:space-y-2.5 sm:text-sm">
                  <div className="flex items-center justify-between rounded-xl bg-white/[0.06] px-3 py-2.5">
                    <span className="text-white/55">За всё время</span>
                    <span className="font-semibold tabular-nums">{allTimeDisplay}</span>
                  </div>
                  <div className="flex items-center justify-between rounded-xl bg-white/[0.06] px-3 py-2.5">
                    <span className="text-white/55">Текущий цикл</span>
                    <span className="font-semibold tabular-nums">{currentCycleDisplay}</span>
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
              {(!analyticsAvailable || current.error || liveDetail) && (
                <p className="mt-5 rounded-xl border border-amber-300/15 bg-amber-300/10 px-3 py-2.5 text-xs leading-relaxed text-amber-100/80">
                  {!analyticsAvailable ? analyticsDetail : current.error || liveDetail}
                </p>
              )}
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
            {!analyticsAvailable && (
              <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                <p>
                  <b>Аналитика не синхронизирована.</b> {analyticsDetail} Живой счётчик модели может продолжать
                  увеличиваться, но неподтверждённые события не показываются как ноль.
                </p>
              </div>
            )}
            <div className="grid gap-4 sm:grid-cols-3">
              <Panel className="p-5">
                <Metric label="Сегодня" value={todayDisplay} unit={analyticsAvailable ? "меш." : undefined} size="lg" />
              </Panel>
              <Panel className="p-5">
                <Metric label="За всё время" value={allTimeDisplay} size="lg" accent="blue" />
              </Panel>
              <Panel className="p-5">
                <Eyebrow>Основной цвет</Eyebrow>
                {dominant ? (
                  <>
                    <div className="mt-2 flex items-center gap-2">
                      <ColorDot className={colorMeta(dominant.color).dot} />
                      <span className="text-2xl font-black tracking-tight text-slate-900">
                        {colorMeta(dominant.color).label}
                      </span>
                      <span className="ml-auto text-sm font-semibold tabular-nums text-slate-400">
                        {dominant.total}
                      </span>
                    </div>
                    {!isShipping && (
                      <AlwaysOnReceiptDestinationLabel
                        destination={resolveAlwaysOnReceiptDestination(receiptMapping, dominant.color)}
                        colorLabel={colorMeta(dominant.color).label}
                        className="mt-2"
                      />
                    )}
                  </>
                ) : (
                  <div className="mt-2 text-2xl font-bold text-slate-300">—</div>
                )}
              </Panel>
            </div>

            <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
              <Panel className="p-5 sm:p-6">
                <SectionHead
                  title="Учтено по дням"
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
                                style={{
                                  height: item.total ? `${Math.max(4, (item.total * 100) / chartMax)}%` : 0,
                                }}
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
                <SectionHead
                  title={isShipping ? "Цвета мешков" : "Цвета продукции"}
                  hint={
                    isShipping
                      ? "За всё время в контуре отгрузки."
                      : "За всё время по данным модели. Под цветом показаны текущие товар и склад прихода."
                  }
                />
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
                      {!isShipping && (
                        <AlwaysOnReceiptDestinationLabel
                          destination={resolveAlwaysOnReceiptDestination(receiptMapping, item.color)}
                          colorLabel={colorMeta(item.color).label}
                          className="mt-2"
                        />
                      )}
                    </div>
                  ))}
                  {!currentDaily?.colors?.length && (
                    <div className="py-10 text-center text-sm text-slate-400">Цветов пока нет</div>
                  )}
                </div>
              </Panel>
            </div>

            {selectedPoint && (
              <Panel className="p-5 sm:p-6">
                <div className="flex flex-wrap items-center gap-2">
                  <h4 className="text-[15px] font-semibold tracking-tight text-slate-900">
                    {fullDay(selectedPoint.day)}
                  </h4>
                  <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
                    {!isShipping && (
                      <AlwaysOnDayColorViewToggle
                        view={selectedDayColorView}
                        nMin={smoothing?.n_min ?? 10}
                        onChange={setSelectedDayColorView}
                      />
                    )}
                    <button
                      type="button"
                      onClick={() => setSelectedDay(null)}
                      className="rounded-lg px-2 py-1 text-xs font-semibold text-slate-400 transition hover:bg-slate-50 hover:text-slate-700"
                    >
                      Закрыть
                    </button>
                  </div>
                </div>

                <div className="mt-4 grid max-w-xl grid-cols-2 gap-x-8 gap-y-4">
                  <Metric label="Учтено за день" value={selectedPoint.total} size="sm" />
                  <Metric
                    label="От максимума"
                    value={`${Math.round((selectedPoint.total * 100) / chartMax)}%`}
                    size="sm"
                  />
                </div>

                {selectedVisibleColors.length > 0 && (
                  <>
                    <Hairline className="my-5" />
                    <SectionHead
                      title={isShipping ? "Цвета мешков за день" : "Цвета и продукция за день"}
                      hint={
                        isShipping
                          ? "Отдельная аналитика камеры отгрузки; эти данные не создают выпуск или приход на склад."
                          : "Количество по цветам распознано камерой; товар показан по текущему сопоставлению в разделе «Куда приходовать»."
                      }
                    />
                    <div className="mt-3 grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-3">
                      {selectedVisibleColors.map((item) => {
                        if (isShipping) {
                          return (
                            <div
                              key={item.color}
                              role="group"
                              aria-label={`${colorMeta(item.color).label}: ${item.total} мешков`}
                            >
                              <div className="flex items-center gap-2">
                                <ColorDot className={colorMeta(item.color).dot} />
                                <span className="min-w-0 truncate text-xs font-medium text-slate-600">
                                  {colorMeta(item.color).label}
                                </span>
                                <span className="ml-auto text-xs tabular-nums text-slate-400">{item.percent}%</span>
                              </div>
                              <div className="mt-1 text-2xl font-black tabular-nums tracking-tight text-slate-900">
                                {item.total}
                              </div>
                            </div>
                          );
                        }
                        const brand = selectedBrandByColor.get(normalizedColor(item.color));
                        const brandLabel = brand
                          ? brandMeta(brand).label
                          : selectedBrandStatus === "ready"
                            ? "Бренд не определён"
                            : selectedBrandStatus === "unavailable"
                              ? "Бренд недоступен"
                              : "Загрузка бренда…";
                        const colorAndBrandLabel = `${colorMeta(item.color).label} · ${brandLabel}`;
                        const destination = resolveAlwaysOnReceiptDestination(selectedReceiptMapping, item.color);
                        return (
                          <div
                            key={item.color}
                            role="group"
                            aria-label={`${colorMeta(item.color).label}: ${item.total} мешков`}
                          >
                            <div className="flex min-w-0 items-start gap-2">
                              <AlwaysOnReceiptDestinationLabel
                                destination={destination}
                                colorLabel={colorMeta(item.color).label}
                              />
                              <span className="ml-auto text-xs tabular-nums text-slate-400">{item.percent}%</span>
                            </div>
                            <div className="mt-1 text-2xl font-black tabular-nums tracking-tight text-slate-900">
                              {item.total}
                            </div>
                            <div className="mt-1.5 flex min-w-0 items-center gap-2">
                              <ColorDot className={colorMeta(item.color).dot} />
                              <span
                                title={colorAndBrandLabel}
                                className={cn(
                                  "truncate text-[11px] font-medium",
                                  brand ? "text-slate-500" : "text-slate-400",
                                )}
                              >
                                {colorAndBrandLabel}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </>
                )}

                {!isShipping && (
                  <>
                    <Hairline className="my-5" />
                    <AlwaysOnDayRunLog
                      day={selectedPoint.day}
                      runs={selectedVisibleRuns}
                      timezone={selectedDayProduction?.timezone || "Asia/Almaty"}
                      loading={selectedProductionLoading}
                      error={selectedProductionError}
                      unavailableReason={runMismatchMessage}
                      receiptMapping={selectedReceiptMapping}
                      onRetry={() => setSelectedProductionReload((value) => value + 1)}
                    />
                  </>
                )}
              </Panel>
            )}
          </div>
        ) : null}
      </Modal>
    </>
  );
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
  const [streamOnline, setStreamOnline] = useState(false);
  const live = !!ai.status?.running;
  const total = ai.status?.total ?? session.last_status?.total ?? 0;
  const orderTarget = order ? orderedBagCount(order) : null;
  const target = orderTarget !== null && orderTarget > 0 ? orderTarget : null;
  const remaining = target !== null ? Math.max(0, target - total) : null;
  const goalReached = target !== null && total >= target;
  const canStop = ai.status?.can_stop ?? session.can_stop;
  // camNai зависит от отдельного RTSP publisher на ПК цеха. Счётчик может
  // продолжать работать, когда этот publisher переподключается, и тогда
  // карточка становилась полностью чёрной. Базовый camN уже контролируется
  // сервером камер; рамки и линию собираем браузером поверх него.
  const stream = camera?.src ?? session.camera;
  const countingLine = resolveCountingLine(ai.status, camera?.line_config);
  const detectionRevision = ai.status?.last_frame_at ?? null;
  const [detectionFreshness, setDetectionFreshness] = useState<{
    revision: string | null;
    at?: number;
  }>({ revision: null });
  // last_frame_at приходит с часов ПК камеры. Для таймера устаревания важен
  // локальный момент, когда браузер увидел новую ревизию, иначе clock skew
  // может сразу скрыть свежую рамку или оставить старую висеть навсегда.
  useEffect(() => {
    setDetectionFreshness((current) =>
      current.revision === detectionRevision
        ? current
        : { revision: detectionRevision, at: detectionRevision ? Date.now() : undefined },
    );
  }, [detectionRevision]);
  const isStarting = session.status === "starting";
  const needsRecovery =
    isStarting || ai.status?.code === "ai_reconciliation_required" || ai.status?.code === "ai_processor_stopped";
  const progress = target && target > 0 ? Math.min(100, Math.round((total / target) * 100)) : 0;
  const overrun = target !== null && total > target;
  const aiLabel = ai.stale
    ? "СВЯЗЬ ПОТЕРЯНА"
    : goalReached
      ? "ЦЕЛЬ ДОСТИГНУТА"
      : live
        ? "СЧИТЫВАНИЕ"
        : needsRecovery
          ? "ТРЕБУЕТ ЗАПУСКА"
          : "ЗАПУСК";
  const liveLabel = streamOnline ? aiLabel : "ПОДКЛЮЧЕНИЕ ВИДЕО";

  async function runCommand(command: () => Promise<void>) {
    try {
      await command();
    } catch {
      // useAiCounter показывает нормализованную ошибку внутри карточки.
    } finally {
      onStopped();
    }
  }

  return (
    <article className="group overflow-hidden rounded-[22px] border border-slate-200/80 bg-white shadow-[0_12px_38px_rgba(44,65,103,0.07)] transition hover:-translate-y-0.5 hover:shadow-[0_18px_48px_rgba(44,65,103,0.11)]">
      <div className="relative aspect-[16/8] overflow-hidden bg-[#172033]">
        <CameraStream
          src={stream}
          onStateChange={setStreamOnline}
          className="absolute inset-0 size-full object-cover"
        />
        {streamOnline ? (
          <>
            <DetectionOverlay
              detections={ai.status?.detections}
              frame={ai.status?.detection_frame}
              staleAfterMs={DETECTIONS_STALE_MS}
              updatedAt={detectionFreshness.at}
            />
            {countingLine && <CameraCountingLineOverlay line={countingLine.line} direction={countingLine.direction} />}
          </>
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-white/35">
            <VideoOff className="size-6" />
            <span className="text-xs">Подключаем прямой поток…</span>
          </div>
        )}
        <div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/60 to-transparent px-4 pb-8 pt-3">
          <span className="flex items-center gap-2 rounded-full bg-black/40 px-2.5 py-1 text-[11px] font-semibold text-white backdrop-blur-md">
            <span
              className={cn(
                "size-2 rounded-full",
                !streamOnline
                  ? "bg-amber-400"
                  : ai.stale
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

        {ai.stale && (
          <div className="mt-3 flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-xs text-red-700">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            Последнее состояние AI устарело. Проверьте связь с сервисом перед завершением погрузки.
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
              </div>
            ) : (
              <div className="grid gap-2">
                {needsRecovery && (
                  <Button
                    className="h-10 rounded-xl"
                    disabled={ai.busy}
                    onClick={() => void runCommand(() => ai.start(session.id))}
                  >
                    <RefreshCw className={cn("size-3.5", ai.busy && "animate-spin")} />
                    Восстановить AI-счётчик
                  </Button>
                )}
                <Button
                  className="h-10 rounded-xl"
                  disabled={ai.busy}
                  onClick={() => void runCommand(() => ai.stop(true, session.id))}
                >
                  <Check className="size-3.5" /> Завершить погрузку
                </Button>
                <p className="text-center text-[11px] text-slate-500">
                  Итог AI-подсчёта будет сохранён, а заказ станет готов к оформлению выезда. После этого оформите
                  фактический выезд отдельно на «Посту погрузки».
                </p>
              </div>
            )
          ) : (
            <div className="grid gap-2">
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
  const {
    data: shippingContinuousSettings,
    error: shippingContinuousSettingsError,
    reload: reloadShippingContinuousSettings,
  } = useApi<AlwaysOnCameraSettings>("/cameras/shipping-continuous-settings/");
  const {
    data: shippingContinuousAnalytics,
    error: shippingContinuousAnalyticsError,
    reload: reloadShippingContinuousAnalytics,
  } = useApi<AlwaysOnDailyAnalytics>("/cameras/shipping-continuous-analytics/");
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
        reloadShippingContinuousSettings(),
        reloadShippingContinuousAnalytics(),
        ...(canViewAlwaysOn ? [reloadAlwaysOnSettings(), reloadAlwaysOnAnalytics()] : []),
      ]),
    SLOW_POLL_MS,
  );
  const auxiliaryError =
    camerasError ||
    sessionsError ||
    cameraSettingsError ||
    monoblockDevicesError ||
    alwaysOnSettingsError ||
    alwaysOnAnalyticsError ||
    shippingContinuousSettingsError ||
    shippingContinuousAnalyticsError;
  const alwaysOnAnalyticsAvailable = !alwaysOnAnalyticsError && alwaysOnAnalytics?.analytics_sync?.available === true;
  const shippingAnalyticsAvailable =
    !shippingContinuousAnalyticsError && shippingContinuousAnalytics?.analytics_sync?.available === true;
  const reloadAll = () =>
    Promise.all([
      reloadOrders(),
      reloadCameras(),
      reloadSessions(),
      reloadCameraSettings(),
      reloadMonoblockDevices(),
      reloadShippingContinuousSettings(),
      reloadShippingContinuousAnalytics(),
      reloadAlwaysOnSettings(),
      reloadAlwaysOnAnalytics(),
    ]);
  const reloadMonoblockPolicy = async () => {
    await Promise.all([
      reloadCameraSettings(),
      reloadMonoblockDevices(),
      reloadShippingContinuousSettings(),
      reloadShippingContinuousAnalytics(),
      reloadAlwaysOnSettings(),
    ]);
  };

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
                      <MonoblockDevicesButton
                        cameras={playable}
                        devices={monoblockDevices ?? []}
                        blockedCameraSources={
                          cameraSettings?.blocked_camera_sources ?? alwaysOnSettings?.camera_sources ?? []
                        }
                        reload={reloadMonoblockPolicy}
                      />
                    )}
                    <CameraSettingsButton cameras={playable} settings={cameraSettings} reload={reloadMonoblockPolicy} />
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
                      ? "Выберите камеры в настройке «AI 24/7» — модель начнёт считать круглосуточно; исходный substream будет храниться в техническом архиве 48 часов, а фоновый AI-overlay не публикуется."
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
                        Бесконечный цикл: модель считает круглосуточно, исходный substream хранится 48 часов; фоновый
                        AI-overlay не публикуется
                      </p>
                    </div>
                    <div className="ml-auto flex items-center gap-2">
                      <span className="flex items-center gap-2 rounded-full border border-blue-100 bg-white px-3 py-1 text-[11px] font-semibold text-blue-700 shadow-sm">
                        <CalendarDays className="size-3.5" /> Сегодня:{" "}
                        {alwaysOnAnalyticsAvailable ? (alwaysOnAnalytics?.total ?? 0) : "—"}
                        <span className="text-slate-300">·</span>
                        Всего:{" "}
                        {alwaysOnAnalyticsAvailable
                          ? (alwaysOnAnalytics?.all_time_total ?? alwaysOnAnalytics?.total ?? 0)
                          : "—"}
                      </span>
                      <span
                        className={cn(
                          "rounded-full border bg-white px-3 py-1 text-[11px] font-semibold shadow-sm",
                          alwaysOnSettings.sync_status === "synced" && alwaysOnAnalyticsAvailable
                            ? "text-emerald-600"
                            : "text-amber-600",
                        )}
                      >
                        {alwaysOnSettings.sync_status !== "synced"
                          ? "ожидает связь"
                          : alwaysOnAnalyticsAvailable
                            ? "синхронизировано"
                            : "журнал не синхронизирован"}
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
                          readiness={alwaysOnSettings.camera_readiness?.[source]}
                          daily={alwaysOnAnalytics?.cameras.find((item) => item.camera === source)}
                          analyticsError={alwaysOnAnalyticsError}
                          canManage={canManageAlwaysOn}
                        />
                      );
                    })}
                  </div>
                </section>
              )
            ) : (
              <>
                <section className="rounded-[24px] border border-indigo-100 bg-gradient-to-br from-indigo-50/80 via-white to-sky-50/50 p-5">
                  <div className="mb-4 flex flex-wrap items-center gap-3">
                    <span className="flex size-10 items-center justify-center rounded-xl bg-indigo-600 text-white shadow-[0_8px_22px_rgba(79,70,229,0.22)]">
                      <Camera className="size-5" />
                    </span>
                    <div>
                      <h2 className="text-[18px] font-bold tracking-tight text-slate-800">
                        Камеры отгрузки · работают 24/7
                      </h2>
                      <p className="text-[12px] text-slate-400">
                        Отдельный непрерывный контур отгрузок; эти камеры и их аналитика не переходят в AI 24/7
                      </p>
                    </div>
                    <div className="ml-auto flex items-center gap-2">
                      <span className="rounded-full border border-indigo-100 bg-white px-3 py-1 text-[11px] font-semibold text-indigo-700 shadow-sm">
                        Сегодня: {shippingAnalyticsAvailable ? (shippingContinuousAnalytics?.total ?? 0) : "—"}
                      </span>
                      <span
                        className={cn(
                          "rounded-full border bg-white px-3 py-1 text-[11px] font-semibold shadow-sm",
                          shippingContinuousSettings?.sync_status === "synced" && shippingAnalyticsAvailable
                            ? "text-emerald-600"
                            : "text-amber-600",
                        )}
                      >
                        {shippingContinuousSettings?.sync_status !== "synced"
                          ? "ожидает готовности"
                          : shippingAnalyticsAvailable
                            ? "синхронизировано"
                            : "журнал не синхронизирован"}
                      </span>
                    </div>
                  </div>

                  {!shippingContinuousSettings?.camera_sources.length ? (
                    <div className="rounded-2xl border border-dashed border-indigo-100 bg-white/70 px-5 py-8 text-center">
                      <p className="text-sm font-semibold text-slate-600">Камеры отгрузки пока не назначены</p>
                      <p className="mt-1 text-xs text-slate-400">
                        Выберите их в «Камеры моноблока». Одна камера может принадлежать только одному контуру.
                      </p>
                    </div>
                  ) : (
                    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                      {shippingContinuousSettings.camera_sources.map((source) => {
                        const processor = shippingContinuousSettings.processors.find((item) => item.cam === source) ?? {
                          cam: source,
                          running: false,
                          mode: "always_on" as const,
                          recording: false,
                          total: 0,
                          analytics_scope: "shipping" as const,
                        };
                        return (
                          <AlwaysOnCard
                            key={source}
                            scope="shipping"
                            processor={processor}
                            camera={playable.find((item) => item.src === source)}
                            detail={
                              shippingContinuousSettings.camera_readiness?.[source]?.detail ||
                              shippingContinuousSettings.detail
                            }
                            readiness={shippingContinuousSettings.camera_readiness?.[source]}
                            daily={shippingContinuousAnalytics?.cameras.find((item) => item.camera === source)}
                            analyticsError={shippingContinuousAnalyticsError}
                            canManage={false}
                          />
                        );
                      })}
                    </div>
                  )}
                </section>

                <ShipmentLauncher
                  orders={startable}
                  cameras={monoblockCameras}
                  busyCameras={(sessions ?? []).map((session) => session.camera)}
                  shippingProcessors={shippingContinuousSettings?.processors}
                  cameraOwners={cameraOwners}
                  activeSessionCount={sessions?.length ?? 0}
                  cameraLocked={!!cameraSettings?.locked || !!me?.is_monoblock}
                  continuousReady={
                    (shippingContinuousSettings?.sync_status ??
                      cameraSettings?.continuous_sync_status ??
                      cameraSettings?.always_on_sync_status) === "synced"
                  }
                  cameraReadiness={shippingContinuousSettings?.camera_readiness ?? cameraSettings?.camera_readiness}
                  continuousDetail={
                    shippingContinuousSettings?.detail ??
                    cameraSettings?.continuous_detail ??
                    cameraSettings?.always_on_detail ??
                    ""
                  }
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
