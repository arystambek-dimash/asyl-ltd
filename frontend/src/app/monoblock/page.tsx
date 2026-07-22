"use client";

import { useEffect, useMemo, useState } from "react";
import {
  BarChart3,
  Camera,
  CalendarDays,
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
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { playableCameras, type CameraFeed } from "@/components/camera-wall";
import { CameraStream } from "@/components/camera-stream";
import { RequirePerm } from "@/components/require-perm";
import { ShipmentLauncher } from "@/components/shipping/shipment-launcher";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import type {
  AiCountingSession,
  AlwaysOnCameraSettings,
  AlwaysOnDailyAnalytics,
  AlwaysOnDailyCameraAnalytics,
  AlwaysOnProcessorStatus,
  MonoblockCameraSettings,
  MonoblockDevice,
  Order,
} from "@/lib/types";
import { useAiCounter } from "@/lib/use-ai-counter";
import { useApi } from "@/lib/use-api";
import { cn, formatDateTime } from "@/lib/utils";
import { useAuth } from "@/store/auth";

const SESSION_POLL_MS = 3_000;
// Заказы/камеры/настройки меняются редко — не гоняем полный список заказов
// каждые 3 секунды на экране, который висит открытым весь день.
const SLOW_POLL_MS = 30_000;

const COLOR_META: Record<string, { label: string; bar: string; dot: string }> = {
  red: { label: "Красный", bar: "bg-[#dc604d]", dot: "bg-[#dc604d]" },
  blue: { label: "Синий", bar: "bg-[#4169d8]", dot: "bg-[#4169d8]" },
  green: { label: "Зелёный", bar: "bg-[#42a779]", dot: "bg-[#42a779]" },
  white: { label: "Белый", bar: "border border-slate-300 bg-slate-100", dot: "border border-slate-300 bg-white" },
};

function colorMeta(color: string) {
  return COLOR_META[color.toLowerCase()] ?? {
    label: color,
    bar: "bg-slate-500",
    dot: "bg-slate-500",
  };
}

function shortDay(day: string) {
  const [, month, date] = day.split("-");
  return `${date}.${month}`;
}

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
    <button type="button" onClick={onToggle} aria-pressed={checked}
      className={cn(
        "group overflow-hidden rounded-2xl border text-left transition duration-200",
        checked
          ? "border-blue-400 bg-blue-50 shadow-[0_10px_28px_rgba(59,104,210,0.15)] ring-2 ring-blue-500/20"
          : "border-slate-200 bg-white hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-md",
      )}>
      <div className="relative aspect-video overflow-hidden bg-[#151821]">
        <CameraStream src={camera.src} onStateChange={setStreamOnline}
          className="absolute inset-0 size-full object-cover transition duration-300 group-hover:scale-[1.02]" />

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
          <span className={cn(
            "flex size-7 items-center justify-center rounded-full border backdrop-blur-md transition",
            checked
              ? "border-blue-300 bg-blue-600 text-white"
              : "border-white/35 bg-black/25 text-transparent",
          )}>
            <Check className="size-4" />
          </span>
        </div>
      </div>

      <div className="flex items-center gap-3 px-3.5 py-3">
        <span className={cn(
          "flex size-9 shrink-0 items-center justify-center rounded-xl",
          checked ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-400",
        )}>
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
    setSelected((current) => current.includes(source)
      ? current.filter((item) => item !== source)
      : [...current, source]);
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

      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Настройка администратора"
        title="Камеры моноблока"
        description="Отметьте камеры, которые оператор сможет назначать заказам."
        className="max-w-xl"
        footer={(
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>Отмена</Button>
            <Button disabled={saving} onClick={() => void save()}>
              <Check className="size-4" /> {saving ? "Сохранение…" : "Сохранить список"}
            </Button>
          </>
        )}>
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-blue-100 bg-blue-50/70 p-3 text-sm text-blue-900">
          <ShieldCheck className="mt-0.5 size-5 shrink-0 text-blue-600" />
          <p>Изменение применяется для всех устройств. Активные отгрузки продолжат работу, но новые увидят только выбранные камеры.</p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {cameras.map((camera) => {
            const checked = selected.includes(camera.src);
            return (
              <CameraChoice key={camera.id} camera={camera} checked={checked}
                onToggle={() => toggle(camera.src)} />
            );
          })}
        </div>

        {!cameras.length && (
          <div className="rounded-xl border border-dashed p-8 text-center text-sm text-slate-400">
            Подключённые камеры пока не обнаружены.
          </div>
        )}
        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
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
    setSaving(true); setError("");
    try {
      const body = {
        name, username, camera_source: cameraSource, is_active: active,
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

  async function remove(device: MonoblockDevice) {
    if (!window.confirm(`Удалить моноблок «${device.name}» и его учётную запись?`)) return;
    setError("");
    try {
      await api.delete(`/cameras/monoblock-devices/${device.id}/`);
      await reload();
    } catch (cause) {
      setError(apiError(cause));
    }
  }

  const occupied = new Set(devices.filter((item) => item.id !== editing?.id).map((item) => item.camera_source));

  return (
    <>
      <Button variant="outline" className="h-10 rounded-xl bg-white" onClick={() => { setError(""); setOpen(true); }}>
        <MonitorSmartphone className="size-4" /> Моноблоки
        <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[11px] tabular-nums text-blue-600">{devices.length}</span>
      </Button>
      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Устройства и доступ"
        title="Учётные записи моноблоков"
        description="У каждого физического моноблока свой логин и ровно одна закреплённая камера."
        className="max-w-2xl">
        <div className="mb-4 flex items-center justify-between gap-3">
          <p className="text-sm text-slate-500">Оператор входит под этим логином — камера выбирается автоматически.</p>
          <Button onClick={() => showForm()}><Plus className="size-4" /> Добавить</Button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {devices.map((device) => (
            <div key={device.id} className={cn(
              "rounded-2xl border p-4",
              device.is_active ? "border-slate-200 bg-white" : "border-slate-200 bg-slate-50 opacity-70",
            )}>
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
                <Button size="icon" variant="ghost" aria-label="Удалить моноблок" onClick={() => void remove(device)}>
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
        {error && !formOpen && <p className="mt-3 text-sm text-red-600">{error}</p>}
      </Modal>

      <Modal open={formOpen} onClose={() => setFormOpen(false)}
        eyebrow={editing ? "Изменение устройства" : "Новое устройство"}
        title={editing ? "Настроить моноблок" : "Зарегистрировать моноблок"}
        description="Эти данные используются только на физическом устройстве у камеры."
        className="max-w-lg"
        footer={(
          <>
            <Button variant="ghost" onClick={() => setFormOpen(false)} disabled={saving}>Отмена</Button>
            <Button onClick={() => void save()} disabled={saving || !name || !username || !cameraSource || (!editing && !password)}>
              <Check className="size-4" /> {saving ? "Сохранение…" : "Сохранить"}
            </Button>
          </>
        )}>
        <div className="space-y-4">
          <label className="grid gap-1.5"><Label>Название устройства</Label>
            <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Моноблок у конвейера" />
          </label>
          <label className="grid gap-1.5"><Label>Логин</Label>
            <Input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="monoblock-conveyor" autoComplete="off" />
          </label>
          <label className="grid gap-1.5"><Label>{editing ? "Новый пароль (необязательно)" : "Пароль"}</Label>
            <div className="relative">
              <KeyRound className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
              <Input type="password" className="pl-9" value={password} onChange={(event) => setPassword(event.target.value)}
                placeholder={editing ? "Оставьте пустым, чтобы не менять" : "Надёжный пароль"} autoComplete="new-password" />
            </div>
          </label>
          <label className="grid gap-1.5"><Label>Закреплённая камера</Label>
            <select value={cameraSource} onChange={(event) => setCameraSource(event.target.value)}
              className="h-10 rounded-lg border bg-white px-3 text-sm outline-none focus:ring-2 focus:ring-blue-500/20">
              <option value="">Выберите камеру</option>
              {cameras.filter((camera) => !occupied.has(camera.src)).map((camera) => (
                <option key={camera.src} value={camera.src}>{camera.zone} · {camera.src}</option>
              ))}
            </select>
          </label>
          <label className="flex items-center justify-between rounded-xl border p-3">
            <span><span className="block text-sm font-semibold">Устройство активно</span>
              <span className="text-xs text-slate-400">Отключённый логин не сможет войти</span></span>
            <input type="checkbox" checked={active} onChange={(event) => setActive(event.target.checked)} className="size-4 accent-blue-600" />
          </label>
          {error && <p className="text-sm text-red-600">{error}</p>}
        </div>
      </Modal>
    </>
  );
}

function AlwaysOnSettingsButton({
  cameras,
  settings,
  reload,
}: {
  cameras: (CameraFeed & { src: string })[];
  settings: AlwaysOnCameraSettings | null;
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
      await api.put("/cameras/always-on-settings/", { camera_sources: selected });
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
      <Button variant="outline" className="h-10 rounded-xl border-blue-200 bg-blue-50/70 text-blue-700 hover:bg-blue-100" onClick={show}>
        <Settings2 className="size-4" /> Настроить
        <span className="rounded-full bg-white px-2 py-0.5 text-[11px] tabular-nums text-blue-600 shadow-sm">
          {settings?.camera_sources.length ?? 0}
        </span>
      </Button>

      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Только системный суперпользователь"
        title="Постоянный AI-подсчёт"
        description="Модель остаётся прогретой и считает круглосуточно. В этом режиме видео не публикуется и не записывается."
        className="max-w-2xl"
        footer={(
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>Отмена</Button>
            <Button disabled={saving} onClick={() => void save()}>
              <Check className="size-4" /> {saving ? "Применение…" : "Применить режим"}
            </Button>
          </>
        )}>
        <div className="mb-4 grid gap-2.5 sm:grid-cols-3">
          <div className="rounded-2xl border border-emerald-100 bg-emerald-50/70 p-3">
            <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-emerald-600">Модель</p>
            <p className="mt-1 text-sm font-bold text-slate-800">Всегда активна</p>
            {settings?.capacity && <p className="mt-0.5 text-[10px] text-emerald-700/70">до {settings.capacity} камер одновременно</p>}
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
              <button key={camera.id} type="button" onClick={() => toggle(camera.src)}
                aria-pressed={checked}
                className={cn(
                  "flex items-center gap-3 rounded-2xl border p-3 text-left transition",
                  checked
                    ? "border-blue-400 bg-blue-50 ring-2 ring-blue-500/15"
                    : "border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm",
                )}>
                <span className={cn(
                  "flex size-11 shrink-0 items-center justify-center rounded-2xl",
                  checked ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-400",
                )}>
                  <Cpu className="size-5" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-bold text-slate-800">{camera.zone}</span>
                  <span className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-400">
                    <span className={cn("size-1.5 rounded-full", live?.running ? "bg-emerald-400" : "bg-slate-300")} />
                    {live?.mode === "session" ? "занята отгрузкой" : live?.running ? "считает 24/7" : camera.src}
                  </span>
                </span>
                <span className={cn(
                  "flex size-7 items-center justify-center rounded-full border",
                  checked ? "border-blue-600 bg-blue-600 text-white" : "border-slate-200 text-transparent",
                )}>
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
        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
      </Modal>
    </>
  );
}

function AlwaysOnCard({
  processor,
  camera,
  detail,
  daily,
  onAnalyticsChanged,
}: {
  processor: AlwaysOnProcessorStatus;
  camera?: CameraFeed & { src: string };
  detail?: string;
  daily?: AlwaysOnDailyCameraAnalytics;
  onAnalyticsChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [modalView, setModalView] = useState<"live" | "analytics">("live");
  const [correctionOpen, setCorrectionOpen] = useState(false);
  const [streamOnline, setStreamOnline] = useState(false);
  const [liveProcessor, setLiveProcessor] = useState(processor);
  const [liveDaily, setLiveDaily] = useState<AlwaysOnDailyCameraAnalytics | undefined>(daily);
  const [liveDetail, setLiveDetail] = useState(detail || "");
  const [correctionAmount, setCorrectionAmount] = useState("");
  const [correctionReason, setCorrectionReason] = useState("");
  const [correctionError, setCorrectionError] = useState("");
  const [correcting, setCorrecting] = useState(false);
  const current = open ? liveProcessor : processor;
  const currentDaily = open ? liveDaily : daily;
  const todayTotal = currentDaily?.total ?? 0;
  const allTimeTotal = currentDaily?.all_time_total ?? todayTotal;
  const inSession = current.mode === "session";
  const chartMax = Math.max(1, ...(currentDaily?.history ?? []).map((item) => item.total));
  const dominant = currentDaily?.colors?.[0];

  useEffect(() => {
    setLiveProcessor(processor);
    setLiveDaily(daily);
    setLiveDetail(detail || "");
  }, [daily, detail, processor]);

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
    setCorrectionAmount("");
    setCorrectionReason("");
    setCorrectionError("");
    setCorrectionOpen(true);
  }

  async function subtractCount() {
    setCorrecting(true);
    setCorrectionError("");
    try {
      await api.post<AlwaysOnDailyCameraAnalytics>(
        `/cameras/always-on-analytics/${processor.cam}/subtract/`,
        { amount: Number(correctionAmount), reason: correctionReason.trim() },
      );
      const analyticsResponse = await api.get<AlwaysOnDailyAnalytics>("/cameras/always-on-analytics/");
      setLiveDaily(analyticsResponse.data.cameras.find((item) => item.camera === processor.cam));
      await onAnalyticsChanged();
      setCorrectionOpen(false);
    } catch (cause) {
      setCorrectionError(apiError(cause));
    } finally {
      setCorrecting(false);
    }
  }

  return (
    <>
      <button type="button" onClick={showStream}
        aria-label={`Открыть прямой эфир камеры ${camera?.zone || processor.cam}`}
        className="group relative w-full overflow-hidden rounded-[20px] border border-slate-200 bg-white p-4 text-left shadow-[0_10px_32px_rgba(44,65,103,0.06)] transition duration-200 hover:-translate-y-0.5 hover:border-blue-200 hover:shadow-[0_16px_38px_rgba(44,65,103,0.12)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40">
        <span className="absolute inset-y-0 left-0 w-1 bg-gradient-to-b from-blue-500 to-emerald-400" />
        <span className="flex items-start gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-2xl bg-blue-50 text-blue-600 transition group-hover:bg-blue-600 group-hover:text-white">
            <Cpu className="size-5" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center justify-between gap-2">
              <span className="truncate text-sm font-bold text-slate-800">{camera?.zone || processor.cam}</span>
              <span className="text-right">
                <span className="block text-2xl font-black tabular-nums tracking-tight text-slate-900">{todayTotal}</span>
                <span className="block text-[9px] font-semibold uppercase tracking-[0.12em] text-slate-400">сегодня</span>
              </span>
            </span>
            <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
              <span className="flex items-center gap-1.5">
                <span className={cn("size-1.5 rounded-full", current.running ? "animate-pulse bg-emerald-400" : "bg-amber-400")} />
                {inSession ? "режим отгрузки" : current.running ? "фоновый подсчёт" : "переподключение"}
              </span>
              <span>{inSession ? "видео записывается" : "без записи видео"}</span>
              <span className="ml-auto font-semibold text-slate-500">Всего: {allTimeTotal}</span>
            </span>
          </span>
        </span>
      </button>

      <Modal open={open} onClose={closeStream}
        eyebrow="AI 24/7 · мониторинг"
        title={camera?.zone || processor.cam}
        description="Прямой эфир, накопленный результат и аналитика цветов модели. Фоновое видео не записывается."
        className="max-w-5xl" mobileFullscreen>
        <div className="mb-4 flex w-full rounded-xl border border-slate-200 bg-slate-100 p-1 sm:w-auto sm:inline-flex">
          <button type="button" onClick={() => setModalView("live")}
            className={cn("flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold transition sm:flex-none sm:px-4", modalView === "live" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800")}>
            <Video className="size-4" /> Прямой эфир
          </button>
          <button type="button" onClick={() => setModalView("analytics")}
            className={cn("flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold transition sm:flex-none sm:px-4", modalView === "analytics" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800")}>
            <BarChart3 className="size-4" /> Аналитика
          </button>
        </div>

        {modalView === "live" ? (
          <div className="grid overflow-hidden rounded-2xl border border-slate-200 bg-slate-950 shadow-[0_24px_70px_rgba(15,23,42,0.22)] sm:rounded-[22px] lg:grid-cols-[minmax(0,1fr)_260px]">
            <div className="relative aspect-video min-h-0 overflow-hidden bg-[#111827] lg:aspect-auto lg:min-h-[460px]">
              {camera?.src ? (
                <CameraStream src={camera.src} onStateChange={setStreamOnline}
                  className="absolute inset-0 size-full object-contain" />
              ) : null}
              {!streamOnline && (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-slate-950 text-white/45">
                  <VideoOff className="size-8" />
                  <span className="text-sm">Подключаем прямой поток…</span>
                </div>
              )}
              <div className="absolute left-2.5 top-2.5 flex items-center gap-2 rounded-full border border-white/15 bg-black/45 px-2.5 py-1 text-[10px] font-semibold text-white backdrop-blur-md sm:left-4 sm:top-4 sm:px-3 sm:py-1.5 sm:text-xs">
                <span className={cn("size-2 rounded-full", streamOnline ? "animate-pulse bg-emerald-400" : "bg-amber-400")} />
                {streamOnline ? "ПРЯМОЙ ЭФИР" : "ПОДКЛЮЧЕНИЕ"}
              </div>
            </div>

            <aside className="flex flex-col justify-between border-t border-white/10 bg-slate-900 p-4 text-white sm:p-5 lg:border-l lg:border-t-0">
              <div>
                <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-white/45">
                  <CalendarDays className="size-3.5" /> Реальный итог за сегодня
                </div>
                <div className="mt-1 text-5xl font-black tabular-nums tracking-tight sm:mt-2 sm:text-7xl">{todayTotal}</div>
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
                <button type="button" disabled={todayTotal <= 0} onClick={showCorrection}
                  className="flex w-full items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2.5 text-xs font-semibold text-white/75 transition hover:bg-white/[0.1] hover:text-white disabled:cursor-not-allowed disabled:opacity-35">
                  <Minus className="size-3.5" /> Уменьшить итог
                </button>
              </div>
            </aside>
          </div>
        ) : (
          <div className="overflow-hidden rounded-[22px] border border-slate-200 bg-[#f8fafc] shadow-[0_20px_55px_rgba(15,23,42,0.09)]">
            <div className="grid grid-cols-2 border-b border-slate-200 bg-white sm:grid-cols-3">
              <div className="border-r border-slate-200 p-3 sm:p-5">
                <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">Сегодня</div>
                <div className="mt-1 text-3xl font-black tabular-nums tracking-tight text-slate-900 sm:text-4xl">{todayTotal}</div>
                <div className="mt-1 text-xs text-slate-400">мешков за текущий день</div>
              </div>
              <div className="p-3 sm:border-r sm:p-5">
                <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">За всё время</div>
                <div className="mt-1 text-3xl font-black tabular-nums tracking-tight text-blue-600 sm:text-4xl">{allTimeTotal}</div>
                <div className="mt-1 text-xs text-slate-400">накоплено CRM</div>
              </div>
              <div className="col-span-2 border-t border-slate-200 p-3 sm:col-span-1 sm:border-t-0 sm:p-5">
                <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">Чаще всего</div>
                {dominant ? (
                  <div className="mt-2 flex items-center gap-2">
                    <span className={cn("size-4 rounded-full", colorMeta(dominant.color).dot)} />
                    <span className="text-xl font-black text-slate-900">{colorMeta(dominant.color).label}</span>
                    <span className="ml-auto text-sm font-bold tabular-nums text-slate-500">{dominant.total}</span>
                  </div>
                ) : <div className="mt-2 text-xl font-bold text-slate-300">Нет данных</div>}
                <div className="mt-1 text-xs text-slate-400">по всем распознанным цветам</div>
              </div>
            </div>

            <div className="grid gap-3 p-3 sm:gap-5 sm:p-5 lg:grid-cols-[minmax(0,1.5fr)_minmax(260px,0.8fr)]">
              <section className="min-w-0 rounded-2xl border border-slate-200 bg-white p-3 sm:p-5">
                <div className="flex items-end justify-between gap-3">
                  <div>
                    <h3 className="font-bold text-slate-800">Подсчёт по дням</h3>
                    <p className="mt-0.5 text-xs text-slate-400">Последние 14 календарных дней</p>
                  </div>
                  <span className="text-xs font-semibold text-slate-400">макс. {chartMax}</span>
                </div>
                <div className="mt-4 overflow-x-auto pb-1 sm:mt-5">
                <div className="h-56 min-w-[560px] rounded-xl border border-slate-100 bg-[linear-gradient(to_bottom,transparent_24%,#e2e8f0_25%,transparent_26%,transparent_49%,#e2e8f0_50%,transparent_51%,transparent_74%,#e2e8f0_75%,transparent_76%)] px-3 pt-4 sm:h-64">
                  <div className="flex h-[173px] items-end gap-2 sm:h-[205px]">
                    {(currentDaily?.history ?? []).map((item) => (
                      <div key={item.day} className="group flex h-full min-w-0 flex-1 flex-col justify-end">
                        <div className="relative flex flex-1 items-end justify-center">
                          <span className="pointer-events-none absolute -top-7 z-10 hidden whitespace-nowrap rounded-md bg-slate-900 px-2 py-1 text-[10px] font-semibold text-white shadow-lg group-hover:block">
                            {item.total} меш.
                          </span>
                          <div className="w-full max-w-9 rounded-t-md bg-gradient-to-t from-[#cf4f3e] to-[#e8755f] transition-all duration-500 group-hover:brightness-110"
                            style={{ height: item.total ? `${Math.max(4, item.total * 100 / chartMax)}%` : 0 }} />
                        </div>
                        <span className="mt-2 block truncate text-center text-[9px] font-medium text-slate-400">{shortDay(item.day)}</span>
                      </div>
                    ))}
                  </div>
                </div>
                </div>
              </section>

              <section className="rounded-2xl border border-slate-200 bg-white p-3 sm:p-5">
                <h3 className="font-bold text-slate-800">Цвета продукции</h3>
                <p className="mt-0.5 text-xs text-slate-400">За всё время по данным модели</p>
                <div className="mt-5 space-y-4">
                  {(currentDaily?.colors ?? []).map((item) => (
                    <div key={item.color}>
                      <div className="mb-1.5 flex items-center gap-2 text-sm">
                        <span className={cn("size-2.5 rounded-full", colorMeta(item.color).dot)} />
                        <span className="font-semibold text-slate-700">{colorMeta(item.color).label}</span>
                        <span className="ml-auto font-bold tabular-nums text-slate-900">{item.total}</span>
                        <span className="w-10 text-right text-xs tabular-nums text-slate-400">{item.percent}%</span>
                      </div>
                      <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                        <div className={cn("h-full rounded-full transition-all duration-500", colorMeta(item.color).bar)} style={{ width: `${item.percent}%` }} />
                      </div>
                    </div>
                  ))}
                  {!currentDaily?.colors?.length && (
                    <div className="flex min-h-40 flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 text-center text-slate-400">
                      <BarChart3 className="mb-2 size-7 text-slate-300" />
                      <span className="text-sm font-semibold">Цветов пока нет</span>
                      <span className="mt-1 max-w-48 text-xs">Они появятся после первых распознаваний модели.</span>
                    </div>
                  )}
                </div>
                <button type="button" disabled={todayTotal <= 0} onClick={showCorrection}
                  className="mt-5 flex w-full items-center justify-center gap-2 rounded-xl border border-slate-200 px-3 py-2.5 text-xs font-semibold text-slate-600 transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-35">
                  <Minus className="size-3.5" /> Уменьшить итог за сегодня
                </button>
              </section>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={correctionOpen} onClose={() => !correcting && setCorrectionOpen(false)}
        eyebrow={`Суперадмин · ${camera?.zone || processor.cam}`}
        title="Уменьшить итог за сегодня"
        description="Используйте только для ложных срабатываний. Сырой результат модели не меняется, корректировка навсегда останется в журнале."
        className="max-w-lg"
        footer={(
          <>
            <Button variant="ghost" disabled={correcting} onClick={() => setCorrectionOpen(false)}>Отмена</Button>
            <Button variant="destructive"
              disabled={correcting || Number(correctionAmount) <= 0 || correctionReason.trim().length < 5}
              onClick={() => void subtractCount()}>
              {correcting ? <LoaderCircle className="size-4 animate-spin" /> : <Minus className="size-4" />}
              Вычесть {Number(correctionAmount) > 0 ? correctionAmount : ""}
            </Button>
          </>
        )}>
        <div className="space-y-5">
          <div className="flex items-end justify-between rounded-2xl border border-blue-100 bg-blue-50/70 p-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.12em] text-blue-500">Сейчас за сегодня</div>
              <div className="mt-1 text-4xl font-black tabular-nums text-slate-900">{todayTotal}</div>
            </div>
            <div className="text-right text-xs text-slate-500">модель: {currentDaily?.model_total ?? 0}<br />поправка: {currentDaily?.adjustment ?? 0}</div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`correction-amount-${processor.cam}`}>Сколько вычесть</Label>
            <Input id={`correction-amount-${processor.cam}`} type="number" inputMode="numeric"
              min={1} max={todayTotal} autoFocus value={correctionAmount}
              onChange={(event) => setCorrectionAmount(event.target.value)} placeholder="Например, 2" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`correction-reason-${processor.cam}`}>Причина</Label>
            <textarea id={`correction-reason-${processor.cam}`} value={correctionReason}
              onChange={(event) => setCorrectionReason(event.target.value)} maxLength={500}
              placeholder="Например: два ложных пересечения линии"
              className="min-h-24 w-full resize-y rounded-xl border bg-[var(--background)] px-3 py-2 text-sm outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/15" />
            <span className="text-xs text-[var(--muted-foreground)]">Обязательно, минимум 5 символов.</span>
          </div>
          {correctionError && <p className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-red-600">{correctionError}</p>}
        </div>
      </Modal>
    </>
  );
}

function SessionCard({
  session,
  camera,
  onStopped,
}: {
  session: AiCountingSession;
  camera?: CameraFeed & { src: string };
  onStopped: () => void;
}) {
  const ai = useAiCounter(session.camera, session.order_id, true);
  const live = ai.status?.running;
  const total = ai.status?.total ?? session.last_status?.total ?? 0;
  const canStop = ai.status?.can_stop ?? session.can_stop;
  const stream = ai.status?.stream ?? (live ? `${session.camera}ai` : camera?.src);

  async function stop() {
    try {
      // Моноблок закрывает бизнес-операцию целиком: backend сначала сохраняет
      // финальный счёт модели, затем переводит заказ в `shipped`.
      await ai.stop(true);
    } catch {
      // ошибка уже показана через ai.error — карточку всё равно обновляем
    } finally {
      onStopped();
    }
  }

  return (
    <article className="group overflow-hidden rounded-[22px] border border-slate-200/80 bg-white shadow-[0_12px_38px_rgba(44,65,103,0.07)] transition hover:-translate-y-0.5 hover:shadow-[0_18px_48px_rgba(44,65,103,0.11)]">
      <div className="relative aspect-[16/8] overflow-hidden bg-[#172033]">
        {stream ? (
          <CameraStream
            src={stream}
            className="absolute inset-0 size-full object-cover"
          />
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-white/35">
            <VideoOff className="size-6" />
            <span className="text-xs">Поток запускается</span>
          </div>
        )}
        <div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/55 to-transparent px-4 pb-8 pt-3">
          <span className="flex items-center gap-2 rounded-full bg-black/35 px-2.5 py-1 text-[11px] font-semibold text-white backdrop-blur-md">
            <span className={cn("size-2 rounded-full", live ? "animate-pulse bg-emerald-400" : "bg-amber-400")} />
            {live ? "СЧИТЫВАНИЕ" : "ЗАПУСК"}
          </span>
          <span className="rounded-full bg-black/35 px-2.5 py-1 text-[11px] text-white/90 backdrop-blur-md">
            {camera?.zone || session.camera}
          </span>
        </div>
        <div className="absolute bottom-3 right-3 rounded-2xl border border-white/20 bg-slate-950/65 px-4 py-2 text-right text-white backdrop-blur-lg">
          <div className="text-[10px] uppercase tracking-[0.14em] text-white/55">мешков</div>
          <div className="text-3xl font-bold tabular-nums leading-none">{total}</div>
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

        <div className="mt-4">
          {canStop ? (
            <Button
              variant="outline"
              className="h-10 w-full rounded-xl border-red-200 text-red-600 hover:bg-red-50 hover:text-red-700"
              disabled={ai.busy}
              onClick={() => void stop()}
            >
              <Square className="size-3.5 fill-current" /> Остановить и завершить
            </Button>
          ) : (
            <div className="flex items-center justify-center gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-[12px] text-slate-500">
              <LockKeyhole className="size-3.5" /> Остановить может {session.started_by_name} или администратор
            </div>
          )}
          {ai.error && <p className="mt-2 text-center text-xs text-red-600">{ai.error}</p>}
        </div>
      </div>
    </article>
  );
}

function MonoblockPageInner() {
  const { me } = useAuth();
  const { data: orders, error, reload: reloadOrders } = useApi<Order[]>("/orders/");
  const { data: cameras, reload: reloadCameras } = useApi<CameraFeed[]>("/cameras/");
  const { data: sessions, reload: reloadSessions } = useApi<AiCountingSession[]>("/cameras/ai/sessions/");
  const { data: cameraSettings, reload: reloadCameraSettings } = useApi<MonoblockCameraSettings>(
    "/cameras/monoblock-settings/",
  );
  const { data: monoblockDevices, reload: reloadMonoblockDevices } = useApi<MonoblockDevice[]>(
    me?.is_superuser ? "/cameras/monoblock-devices/" : null,
  );
  const { data: alwaysOnSettings, reload: reloadAlwaysOnSettings } = useApi<AlwaysOnCameraSettings>(
    me?.is_superuser ? "/cameras/always-on-settings/" : null,
  );
  const { data: alwaysOnAnalytics, reload: reloadAlwaysOnAnalytics } = useApi<AlwaysOnDailyAnalytics>(
    me?.is_superuser ? "/cameras/always-on-analytics/" : null,
  );
  const isSuper = !!me?.is_superuser;
  // Страница разделена на вкладки: «Отгрузки» (по умолчанию) — запуск сессий
  // и активные отгрузки, «AI 24/7» — сам моноблок с бесконечным циклом подсчёта.
  // Вкладка AI видна только суперпользователю, остальным — сразу отгрузки.
  const [tab, setTab] = useState<"monoblock" | "shipments">("shipments");
  const activeTab = isSuper && tab === "monoblock" ? "monoblock" : "shipments";
  const playable = useMemo(
    () => playableCameras(cameras).filter((camera) => /^cam[1-9]\d*$/.test(camera.src)),
    [cameras],
  );
  const monoblockCameras = useMemo(() => {
    const allowed = new Set(cameraSettings?.camera_sources ?? []);
    return playable.filter((camera) => allowed.has(camera.src));
  }, [cameraSettings?.camera_sources, playable]);

  useEffect(() => {
    const refreshSessions = () => {
      if (document.hidden) return;
      void reloadSessions();
    };
    const refreshRest = () => {
      if (document.hidden) return;
      void reloadOrders();
      void reloadCameras();
      void reloadCameraSettings();
      if (me?.is_superuser) {
        void reloadAlwaysOnSettings();
        void reloadAlwaysOnAnalytics();
      }
    };
    const refreshAll = () => { refreshSessions(); refreshRest(); };
    const fast = setInterval(refreshSessions, SESSION_POLL_MS);
    const slow = setInterval(refreshRest, SLOW_POLL_MS);
    document.addEventListener("visibilitychange", refreshAll);
    window.addEventListener("online", refreshAll);
    return () => {
      clearInterval(fast);
      clearInterval(slow);
      document.removeEventListener("visibilitychange", refreshAll);
      window.removeEventListener("online", refreshAll);
    };
  }, [me?.is_superuser, reloadAlwaysOnAnalytics, reloadAlwaysOnSettings, reloadCameraSettings, reloadCameras, reloadOrders, reloadSessions]);

  const sessionOrderIds = new Set((sessions ?? []).map((session) => session.order_id));
  const startable = (orders ?? []).filter((order) => {
    if (sessionOrderIds.has(order.id)) return false;
    // Новая сессия начинается только из колонки «Ожидание въезда».
    // После привязки камеры backend сразу переводит заказ в `loading`,
    // поэтому активный заказ больше не должен оставаться в этом списке.
    return order.status === "confirmed";
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
      await api.post(`/cameras/${camera.src}/ai/`, { order_id: order.id }, {
        params: { order_id: order.id },
      });
    } finally {
      // Даже если ПК камеры не ответил, сервер мог уже безопасно закрепить
      // слот и перевести заказ в загрузку — сразу показываем реальное состояние.
      await Promise.all([reloadOrders(), reloadSessions()]);
    }
  }

  return (
    <AppShell title="Моноблок" section="Работа">
      {error && !orders ? (
        <ErrorAlert message={error} onRetry={reloadOrders} />
      ) : (
        <div className="flex flex-col gap-7">
          {(isSuper || can(me, "rbac.manage")) && (
            <div className="flex flex-wrap items-center gap-3">
              {isSuper && (
                <div className="flex w-full rounded-2xl border border-slate-200 bg-slate-100 p-1 sm:w-auto sm:inline-flex">
                  <button type="button" onClick={() => setTab("shipments")}
                    className={cn(
                      "flex flex-1 items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold transition sm:flex-none",
                      activeTab === "shipments" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800",
                    )}>
                    <Radio className="size-4" /> Отгрузки
                    <span className={cn(
                      "rounded-full px-2 py-0.5 text-[11px] tabular-nums",
                      activeTab === "shipments" ? "bg-blue-50 text-blue-600" : "bg-white/70 text-slate-500",
                    )}>
                      {sessions?.length ?? 0}
                    </span>
                  </button>
                  <button type="button" onClick={() => setTab("monoblock")}
                    className={cn(
                      "flex flex-1 items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold transition sm:flex-none",
                      activeTab === "monoblock" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800",
                    )}>
                    <Cpu className="size-4" /> AI 24/7
                    <span className={cn(
                      "rounded-full px-2 py-0.5 text-[11px] tabular-nums",
                      activeTab === "monoblock" ? "bg-blue-50 text-blue-600" : "bg-white/70 text-slate-500",
                    )}>
                      {alwaysOnSettings?.camera_sources.length ?? 0}
                    </span>
                  </button>
                </div>
              )}
              <div className="ml-auto flex items-center gap-2">
                {isSuper && activeTab === "monoblock" ? (
                  <AlwaysOnSettingsButton cameras={playable} settings={alwaysOnSettings}
                    reload={reloadAlwaysOnSettings} />
                ) : can(me, "rbac.manage") ? (
                  <>
                    {isSuper && (
                      <MonoblockDevicesButton cameras={playable} devices={monoblockDevices ?? []}
                        reload={reloadMonoblockDevices} />
                    )}
                    <CameraSettingsButton cameras={playable} settings={cameraSettings}
                      reload={reloadCameraSettings} />
                  </>
                ) : null}
              </div>
            </div>
          )}

          {activeTab === "monoblock" ? (
            !alwaysOnSettings?.camera_sources.length ? (
              <div className="flex min-h-56 flex-col items-center justify-center rounded-[24px] border border-dashed border-slate-200 bg-slate-50/70 p-8 text-center">
                <span className="flex size-14 items-center justify-center rounded-full bg-white text-slate-300 shadow-sm">
                  <Cpu className="size-6" />
                </span>
                <p className="mt-3 text-sm font-semibold text-slate-600">Бесконечный цикл пока не запущен</p>
                <p className="mt-1 max-w-sm text-xs text-slate-400">
                  Выберите камеры в настройке «AI 24/7» — модель начнёт считать круглосуточно, без публикации и записи видео.
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
                  <p className="text-[12px] text-slate-400">Бесконечный цикл: модель считает круглосуточно, без публикации и записи фонового видео</p>
                </div>
                <div className="ml-auto flex items-center gap-2">
                  <span className="flex items-center gap-2 rounded-full border border-blue-100 bg-white px-3 py-1 text-[11px] font-semibold text-blue-700 shadow-sm">
                    <CalendarDays className="size-3.5" /> Сегодня: {alwaysOnAnalytics?.total ?? 0}
                    <span className="text-slate-300">·</span>
                    Всего: {alwaysOnAnalytics?.all_time_total ?? alwaysOnAnalytics?.total ?? 0}
                  </span>
                  <span className={cn(
                    "rounded-full border bg-white px-3 py-1 text-[11px] font-semibold shadow-sm",
                    alwaysOnSettings.sync_status === "synced" ? "text-emerald-600" : "text-amber-600",
                  )}>
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
                  return <AlwaysOnCard key={source} processor={processor}
                    camera={playable.find((item) => item.src === source)}
                    detail={alwaysOnSettings.detail}
                    daily={alwaysOnAnalytics?.cameras.find((item) => item.camera === source)}
                    onAnalyticsChanged={reloadAlwaysOnAnalytics} />;
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
                        camera={playable.find((camera) => camera.src === session.camera)}
                        onStopped={() => { void Promise.all([reloadOrders(), reloadSessions()]); }}
                      />
                    ))}
                  </div>
                )}
              </section>
            </>
          )}
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
