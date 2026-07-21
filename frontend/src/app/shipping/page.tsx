"use client";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import Image from "next/image";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { PlateBadge } from "@/components/ui/license-plate-input";
import { CameraStream } from "@/components/camera-stream";
import { ErrorAlert } from "@/components/ui/data-state";
import {
  ManualOrderStatusModal,
  type ManualOrderTarget,
} from "@/components/manual-order-status-modal";
import { ShipmentRollbackModal } from "@/components/shipment-rollback-modal";
import { playableCameras, type CameraFeed } from "@/components/camera-wall";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { cn, formatDateTime, formatMoney } from "@/lib/utils";
import {
  Activity, Archive, ArrowLeft, CalendarDays, Cctv, Check, Clock3,
  Film, GripVertical, Layers3, LockKeyhole, LogOut, Minus, Package, Phone,
  Plus, Radio, RotateCcw, Scale, Settings2, TrainFront, Truck, User, VideoOff,
} from "lucide-react";
import type {
  AiCountingHistory, AiCountingSession, AiRecording, Order, ShippingBoardSettings,
} from "@/lib/types";
import { useAiCounter, type AiCounter } from "@/lib/use-ai-counter";

const POLL_MS = 10_000; // борд и счётчики обновляются сами — пост «живой»

// Новый Windows AI-сервис возвращает machine-readable `online`, а старый
// сервис возвращал локализованное `онлайн`. Во время плавного обновления
// production принимаем оба контракта, чтобы готовый процессор не выглядел как
// бесконечно прогревающийся и пост сразу переключался на cam<N>ai.
function isAiOnlineStatus(status?: string): boolean {
  const normalized = status?.trim().toLowerCase();
  return normalized === "online" || normalized === "онлайн";
}

/* ── Этапы единого поста: заказ едет по остановкам слева направо ────────── */
const BOARD_STAGES = [
  { key: "waiting", label: "Ожидание въезда", color: "var(--ring)", statuses: ["confirmed"],
    hint: "Подтверждённые заказы", image: null, tint: "#f3f7ff", icon: Clock3,
    activeCircle: "border-blue-600 bg-blue-600 text-white shadow-[0_12px_28px_rgba(37,99,235,0.32)]",
    activeLabel: "text-blue-700" },
  { key: "loading", label: "Загружается", color: "var(--warning)", statuses: ["arrived", "loading"],
    hint: "Идёт погрузка", image: "/shipping/loading-forklift.jpg", tint: "#fffbf0", icon: Package,
    activeCircle: "border-amber-500 bg-amber-500 text-white shadow-[0_12px_28px_rgba(245,158,11,0.32)]",
    activeLabel: "text-amber-600" },
  { key: "done", label: "Отгружено", color: "var(--success)", statuses: ["loaded", "shipped"],
    hint: "Отгруженные заказы", image: "/shipping/completed-clipboard.jpg", tint: "#f4fbf5", icon: Check,
    activeCircle: "border-emerald-600 bg-emerald-600 text-white shadow-[0_12px_28px_rgba(5,150,105,0.32)]",
    activeLabel: "text-emerald-700" },
] as const;

const ACTIVE_STATUSES = ["confirmed", "arrived", "loading", "loaded"];
type BoardStageKey = (typeof BOARD_STAGES)[number]["key"];

function orderedBags(o: Order): number {
  return o.items.reduce((s, it) => s + Number(it.quantity), 0);
}

const STEPS = [
  { key: "arrive", label: "Прибытие", icon: Scale },
  { key: "load", label: "Погрузка", icon: Package },
  { key: "exit", label: "Выезд", icon: LogOut },
];
function stepIndex(status: string) {
  if (status === "confirmed") return 0;
  if (status === "arrived" || status === "loading") return 1;
  return 2; // loaded | shipped
}

/** Сегментированный прогресс этапов — как трекер статуса заказа в доставках. */
function StageTrack({ status }: { status: string }) {
  const current = stepIndex(status);
  return (
    <div className="flex w-full gap-1.5 sm:w-auto">
      {STEPS.map((s, i) => {
        const done = i < current;
        const active = i === current;
        const Icon = s.icon;
        return (
          <div key={s.key}
            className={cn(
              "flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-[13px] font-medium sm:flex-none",
              done && "bg-[var(--success)]/10 text-[var(--success)]",
              active && "bg-[var(--foreground)] text-[var(--background)]",
              !done && !active && "bg-[var(--muted)] text-[var(--muted-foreground)]"
            )}>
            {done ? <Check className="size-4" /> : <Icon className="size-4" />}
            <span className="whitespace-nowrap">{s.label}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Живая камера поста: зона по шагу, переключение — чипами поверх видео. */
function PostCamera({ cameras, zoneKeywords, preferId, ai }: {
  /** Только играбельные камеры (locked пост не показывает). */
  cameras: (CameraFeed & { src: string })[];
  zoneKeywords: string[];
  /** Камера, закреплённая за заказом под погрузку — показываем её первой. */
  preferId?: string | null;
  /** Работающий AI-подсчёт: на этой камере показываем аннотированный поток. */
  ai?: { camId: string; src: string } | null;
}) {
  const auto = useMemo(() => {
    const preferred = preferId ? cameras.find((c) => c.id === preferId) : null;
    if (preferred) return preferred;
    for (const kw of zoneKeywords) {
      const hit = cameras.find((c) => c.zone.toLowerCase().includes(kw));
      if (hit) return hit;
    }
    return cameras[0];
  }, [cameras, zoneKeywords, preferId]);
  const [manualId, setManualId] = useState<string | null>(null);
  const cam = cameras.find((c) => c.id === manualId) ?? auto;
  const [online, setOnline] = useState(false);

  const aiOn = !!ai && cam?.id === ai.camId;
  const src = aiOn ? ai.src : cam?.src;

  return (
    <div className="relative min-h-[260px] flex-1 overflow-hidden rounded-2xl bg-[#141416]">
      {cam && src && (
        <CameraStream key={src} src={src} onStateChange={setOnline}
          className="absolute inset-0 h-full w-full object-cover" />
      )}
      {!online && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-white/25">
          <VideoOff className="size-7" />
          <span className="text-xs">{cam ? "Нет сигнала" : "Камеры недоступны"}</span>
        </div>
      )}

      {/* зона и live-индикатор поверх видео — как в UniFi Protect */}
      {cam && (
        <div className="absolute left-3 top-3 flex items-center gap-1.5 rounded-md bg-black/55 px-2.5 py-1 backdrop-blur-sm">
          <span className={cn("size-1.5 rounded-full", online ? "bg-emerald-400" : "bg-white/40")} />
          <span className="text-xs font-medium text-white">{cam.zone}</span>
        </div>
      )}

      {/* AI-поток: боксы и линия подсчёта рисуются прямо в кадре */}
      {aiOn && (
        <div className="absolute right-3 top-3 flex items-center gap-1.5 rounded-md bg-emerald-600/90 px-2.5 py-1 backdrop-blur-sm">
          <span className="size-1.5 animate-pulse rounded-full bg-white" />
          <span className="text-xs font-semibold text-white">AI-подсчёт</span>
        </div>
      )}

      {/* переключение камер: чипы внизу видео */}
      {cameras.length > 1 && (
        <div className="absolute inset-x-3 bottom-3 flex gap-1.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {cameras.map((c) => (
            <button key={c.id} onClick={() => setManualId(c.id)}
              className={cn(
                "shrink-0 whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-medium backdrop-blur-sm transition-colors",
                c.id === cam?.id
                  ? "bg-white text-black"
                  : "bg-black/45 text-white/80 hover:bg-black/65"
              )}>
              {c.zone}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Крупный счётчик мешков под палец: −/+1/+5, автосохранение с дебаунсом.
 * Куда писать счёт (машина: /load/, поезд: train count) решает onSave. */
function BagCounter({ order, onSave }: {
  order: Order;
  onSave: (bags: number) => Promise<unknown>;
}) {
  const [bags, setBags] = useState(order.bags_loaded ?? 0);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSaved = useRef(order.bags_loaded ?? 0);
  // Для сброса на размонтировании: несохранённый счёт и актуальный onSave.
  const pending = useRef<number | null>(null);
  const onSaveRef = useRef(onSave);
  onSaveRef.current = onSave;

  // Чужие обновления (второй планшет, камера) подтягиваем при поллинге,
  // не перетирая то, что контролёр набирает прямо сейчас.
  useEffect(() => {
    const remote = order.bags_loaded ?? 0;
    if (remote !== lastSaved.current && timer.current === null) {
      lastSaved.current = remote;
      setBags(remote);
    }
  }, [order.bags_loaded]);

  const save = useCallback(async (value: number) => {
    setSaving(true); setError("");
    try {
      await onSave(value);
      lastSaved.current = value;
    } catch (e) { setError(apiError(e)); }
    finally { setSaving(false); }
  }, [onSave]);

  function change(delta: number) {
    setBags((prev) => {
      const next = Math.max(0, prev + delta);
      pending.current = next;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        timer.current = null;
        pending.current = null;
        save(next);
      }, 700);
      return next;
    });
  }

  // Уход со счётчика в пределах дебаунса не должен терять последние клики:
  // отменяем таймер и сохраняем несохранённое напрямую (без setState).
  useEffect(() => () => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    if (pending.current !== null) {
      void onSaveRef.current(pending.current).catch(() => {
        // экран уже закрыт — показать ошибку некому, счёт добьёт поллинг
      });
      pending.current = null;
    }
  }, []);

  const ordered = orderedBags(order);
  const pct = ordered > 0 ? Math.min(100, Math.round((bags / ordered) * 100)) : 0;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            Погружено мешков
          </span>
          <span className={cn("text-xs tabular-nums",
            saving ? "text-[var(--muted-foreground)]" : "opacity-0")}>
            сохранение…
          </span>
        </div>
        <div className="mt-1 flex items-baseline gap-2">
          <span className={cn("text-6xl font-bold tabular-nums leading-none tracking-tight sm:text-7xl",
            pct >= 100 && "text-[var(--success)]")}>
            {bags}
          </span>
          <span className="text-xl text-[var(--muted-foreground)]">/ {ordered}</span>
          <span className={cn("ml-auto text-lg font-semibold tabular-nums",
            pct >= 100 ? "text-[var(--success)]" : "text-[var(--muted-foreground)]")}>
            {pct}%
          </span>
        </div>
      </div>

      <div className="h-2.5 overflow-hidden rounded-full bg-[var(--muted)]">
        <div className="h-full rounded-full transition-all duration-300"
          style={{
            width: `${pct}%`,
            background: pct >= 100 ? "var(--success)" : "var(--warning)",
          }} />
      </div>

      {/* планшет: крупные кнопки */}
      <div className="grid grid-cols-[1fr_1.4fr_1.4fr] gap-2">
        <Button variant="outline" className="h-16 rounded-xl" disabled={bags <= 0}
          onClick={() => change(-1)} aria-label="Минус один мешок">
          <Minus className="size-6" />
        </Button>
        <Button variant="outline" className="h-16 rounded-xl text-xl font-semibold"
          onClick={() => change(1)}>
          <Plus className="size-5" /> 1
        </Button>
        <Button variant="outline" className="h-16 rounded-xl text-xl font-semibold"
          onClick={() => change(5)}>
          <Plus className="size-5" /> 5
        </Button>
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
    </div>
  );
}

// Цвет партии из ai_service (Blue_50, White…) → точка-индикатор в чипе.
const BAG_COLORS: [RegExp, string][] = [
  [/blue/i, "#3b82f6"], [/green/i, "#22c55e"], [/red/i, "#ef4444"],
  [/yellow/i, "#eab308"], [/orange/i, "#f97316"], [/black/i, "#27272a"],
  [/white/i, "#e4e4e7"],
];
function bagColor(name: string) {
  return BAG_COLORS.find(([re]) => re.test(name))?.[1] ?? "var(--muted-foreground)";
}

/** AI-подсчёт с камеры: живое число для сверки, «Принять» пишет его в ручной счёт. */
function AiCounterPanel({ ai, accepted, onAccept }: {
  ai: AiCounter;
  accepted: number;
  onAccept: (bags: number) => void;
}) {
  const st = ai.status;
  if (st?.busy && !st.owned_by_order) {
    return (
      <div className="rounded-xl border border-amber-500/30 bg-amber-500/[0.07] p-3.5">
        <div className="flex items-center gap-2 text-sm font-semibold text-amber-700 dark:text-amber-400">
          <Cctv className="size-4" /> AI-подсчёт занят
        </div>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          Сейчас считается заказ #{st.session_order_id}. Камеры можно смотреть,
          а новая сессия станет доступна сразу после завершения текущей.
        </p>
      </div>
    );
  }
  if (!st?.running) {
    return (
      <div className="flex flex-col gap-2">
        <Button variant="outline" className="h-12 rounded-xl" disabled={ai.busy || ai.occupied}
          onClick={() => ai.start().catch(() => {})}>
          <Cctv className="size-5" /> AI-подсчёт · заказ #{ai.orderId}
        </Button>
        {ai.error && <p className="text-sm text-[var(--destructive)]">{ai.error}</p>}
      </div>
    );
  }

  const warming = !isAiOnlineStatus(st.status);
  const total = st.total ?? 0;
  return (
    <div className="rounded-xl border border-emerald-600/25 bg-emerald-500/[0.06] p-3.5">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-emerald-600">
          <span className={cn("size-1.5 rounded-full bg-emerald-500", !warming && "animate-pulse")} />
          AI-подсчёт{warming && " · запуск модели…"}
        </span>
        {st.can_stop ? (
          <button onClick={() => ai.stop().catch(() => {})} disabled={ai.busy}
            className="text-xs text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]">
            Выключить
          </button>
        ) : (
          <span className="text-xs text-[var(--muted-foreground)]" title="Остановить может автор или администратор">
            запустил: {st.session_started_by_name || "другой сотрудник"}
          </span>
        )}
      </div>

      <div className="mt-1.5 flex items-baseline gap-2">
        <span className="text-4xl font-bold tabular-nums leading-none">{total}</span>
        <span className="text-sm text-[var(--muted-foreground)]">меш.</span>
        {(st.weight ?? 0) > 0 && (
          <span className="ml-auto text-sm tabular-nums text-[var(--muted-foreground)]">
            ≈ {formatMoney(st.weight!)} кг
          </span>
        )}
      </div>

      {st.per_color && Object.keys(st.per_color).length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {Object.entries(st.per_color).map(([color, n]) => (
            <span key={color}
              className="flex items-center gap-1.5 rounded-md border bg-[var(--card)] px-2 py-0.5 text-xs tabular-nums">
              <span className="size-2 rounded-full" style={{ background: bagColor(color) }} />
              {color.replace(/_/g, " ")} · {n}
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 grid grid-cols-[1fr_auto] gap-2">
        {/* на прогреве счёт ещё нулевой — принять его можно только по ошибке */}
        <Button className="h-11 rounded-lg" disabled={ai.busy || warming || total === accepted}
          onClick={() => onAccept(total)}>
          <Check className="size-4" /> Принять {total}
        </Button>
        <Button variant="outline" className="h-11 rounded-lg px-3" disabled={ai.busy || !st.can_stop}
          onClick={() => ai.reset().catch(() => {})} aria-label="Обнулить AI-счётчик"
          title="Начать счёт заново">
          <RotateCcw className="size-4" />
        </Button>
      </div>
      {ai.error && <p className="mt-2 text-sm text-[var(--destructive)]">{ai.error}</p>}
    </div>
  );
}

/** После назначения в «Моноблоке» камера фиксирована за заказом. */
function BoundCamera({ camera, source }: { camera?: CameraFeed; source: string }) {
  return (
    <div className="rounded-xl border border-blue-100 bg-blue-50/65 p-3">
      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-blue-700">
          <Cctv className="size-3.5" /> Камера погрузки
        </span>
        <span className="flex items-center gap-1 rounded-full bg-white px-2 py-0.5 text-[10px] font-semibold text-slate-500 shadow-sm">
          <LockKeyhole className="size-3" /> закреплена
        </span>
      </div>
      <div className="mt-2 flex items-center gap-2 rounded-lg border border-blue-100/80 bg-white px-3 py-2.5">
        <span className={cn(
          "size-2 shrink-0 rounded-full",
          camera?.online ? "bg-emerald-500" : "bg-amber-400",
        )} />
        <span className="min-w-0 flex-1 truncate text-sm font-bold text-slate-800">
          {camera?.zone || camera?.name || source}
        </span>
        <span className="text-[11px] text-slate-400">назначена в «Моноблоке»</span>
      </div>
    </div>
  );
}

/* ── Лайв-борд: заказы едут по этапам слева направо ─────────────────────── */
function TransportBadge({ order, size = "md" }: { order: Order; size?: "md" | "lg" }) {
  if (order.transport_type === "train") {
    return (
      <span className={cn(
        "flex items-center gap-1.5 rounded-md border bg-[var(--muted)] font-semibold",
        size === "lg" ? "px-3 py-1.5 text-sm" : "px-2 py-1 text-xs")}>
        <TrainFront className={size === "lg" ? "size-4" : "size-3.5"} /> Поезд
      </span>
    );
  }
  return <PlateBadge value={order.truck_number} size={size === "lg" ? "lg" : "md"} />;
}

function BoardCard({ order, stage, camera, session, history, onOpen, onHistory, draggable, moving, onDragStart, onDragEnd, quickTargets, onQuickMove }: {
  order: Order;
  stage: (typeof BOARD_STAGES)[number];
  camera?: CameraFeed;
  session?: AiCountingSession;
  history?: AiCountingHistory;
  onOpen?: (id: number) => void;
  onHistory?: (history: AiCountingHistory) => void;
  draggable?: boolean;
  moving?: boolean;
  onDragStart?: (event: DragEvent<HTMLDivElement>) => void;
  onDragEnd?: () => void;
  /** Этапы, куда заказ можно двинуть одним нажатием (замена drag&drop на планшете). */
  quickTargets?: BoardStageKey[];
  onQuickMove?: (target: BoardStageKey) => void;
}) {
  const ordered = orderedBags(order);
  const bags = order.bags_loaded ?? 0;
  const pct = ordered > 0 ? Math.min(100, Math.round((bags / ordered) * 100)) : 0;
  const clickable = !!onOpen || (!!history && !!onHistory);
  const cameraTotal = history?.final_total ?? history?.last_status?.total;
  return (
    <div
      draggable={draggable}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={() => {
        if (onOpen) onOpen(order.id);
        else if (history && onHistory) onHistory(history);
      }}
      onKeyDown={(event) => {
        if (clickable && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault();
          if (onOpen) onOpen(order.id);
          else if (history && onHistory) onHistory(history);
        }
      }}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      className={cn(
        "group relative flex w-full flex-col gap-2 overflow-hidden rounded-xl border bg-[var(--card)] p-3 text-left shadow-card outline-none transition-all",
        clickable && "cursor-pointer hover:-translate-y-px hover:shadow-md focus-visible:ring-2 focus-visible:ring-blue-500",
        draggable && "cursor-grab active:cursor-grabbing",
        moving && "pointer-events-none scale-[0.98] opacity-50",
      )}>
      <div className="flex items-center justify-between gap-2">
        <TransportBadge order={order} />
        <span className="flex items-center gap-1.5 text-xs font-semibold text-[var(--muted-foreground)]">
          {draggable && <GripVertical className="size-3.5 opacity-45" />}
          #{order.id}
        </span>
      </div>
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold">{order.client_name || "—"}</div>
        <div className="mt-0.5 text-xs tabular-nums text-[var(--muted-foreground)]">
          {stage.key === "waiting" && `${ordered} меш. к погрузке`}
          {stage.key === "loading" && `${bags} / ${ordered} меш.`}
          {stage.key === "done" && (order.shipped_at
            ? `выехал ${formatDateTime(order.shipped_at)}`
            : `${bags} меш.`)}
        </div>
      </div>
      {order.loading_camera && (
        <div className="flex items-center gap-1.5 rounded-lg border border-blue-100 bg-blue-50/80 px-2.5 py-1.5 text-[11px] font-semibold text-blue-700">
          <Cctv className="size-3.5 shrink-0" />
          <span className="truncate">{camera?.zone || camera?.name || order.loading_camera}</span>
          {stage.key === "loading" && (
            <span className="relative ml-auto flex size-2 shrink-0"
              title={session?.status === "active" ? "AI-подсчёт активен" : session ? "AI запускается" : "Камера закреплена, AI не запущен"}>
              {session?.status === "active" && (
                <span className="absolute inline-flex size-2 animate-ping rounded-full bg-emerald-400 opacity-50" />
              )}
              <span className={cn(
                "relative size-2 rounded-full",
                session?.status === "active" ? "bg-emerald-500"
                  : session?.status === "starting" ? "animate-pulse bg-amber-400"
                    : "bg-slate-300",
              )} />
            </span>
          )}
        </div>
      )}
      {stage.key === "loading" && (
        <div className="h-1.5 overflow-hidden rounded-full bg-[var(--muted)]">
          <div className="h-full rounded-full transition-all"
            style={{ width: `${pct}%`, background: pct >= 100 ? "var(--success)" : "var(--warning)" }} />
        </div>
      )}
      {onQuickMove && !!quickTargets?.length && (
        <div className="flex gap-1.5 pt-0.5"
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}>
          {quickTargets.includes("waiting") && (
            <Button size="sm" variant="ghost" className="px-2.5" title="Вернуть в ожидание"
              onClick={() => onQuickMove("waiting")}>
              <RotateCcw className="size-4" />
            </Button>
          )}
          {quickTargets.includes("done") && (
            <Button size="sm" variant="outline" className="flex-1"
              onClick={() => onQuickMove("done")}>
              <Check className="size-4" /> Завершить
            </Button>
          )}
        </div>
      )}
      {stage.key === "done" && history && (
        <div className="absolute inset-x-0 bottom-0 flex translate-y-[calc(100%-4px)] items-center justify-between gap-3 bg-slate-900/95 px-3 py-2.5 text-white backdrop-blur-sm transition-transform duration-200 group-hover:translate-y-0 group-focus-visible:translate-y-0">
          <span className="flex min-w-0 items-center gap-2 text-xs font-semibold">
            <Cctv className="size-4 shrink-0 text-cyan-300" />
            <span className="truncate">Камера насчитала</span>
          </span>
          <span className="flex shrink-0 items-center gap-2">
            <b className="text-lg tabular-nums">{cameraTotal ?? "—"}</b>
            {history.has_recording && <Film className="size-4 text-emerald-300" />}
          </span>
        </div>
      )}
    </div>
  );
}

/* ── Остановки этапов: иконки с дорогой, по которой едет грузовик ───────── */
function StageNav({ active, counts, onSelect, canDrop, overStage, onDragOverStage, onDragLeaveStage, onDropStage }: {
  active: BoardStageKey;
  counts: Record<BoardStageKey, number>;
  onSelect: (key: BoardStageKey) => void;
  /** Перетаскиваемую карточку можно бросить прямо на иконку этапа. */
  canDrop: (key: BoardStageKey) => boolean;
  overStage: BoardStageKey | null;
  onDragOverStage: (key: BoardStageKey) => void;
  onDragLeaveStage: () => void;
  onDropStage: (key: BoardStageKey) => void;
}) {
  const activeIndex = BOARD_STAGES.findIndex((stage) => stage.key === active);
  return (
    <div className="rounded-[22px] border bg-[var(--card)] px-3 pb-3 pt-5 shadow-[0_10px_30px_rgba(45,62,94,0.04)] sm:px-6">
      <div className="grid grid-cols-3">
        {BOARD_STAGES.map((stage) => {
          const Icon = stage.icon;
          const isActive = stage.key === active;
          const droppable = canDrop(stage.key);
          const isOver = droppable && overStage === stage.key;
          return (
            <button key={stage.key} type="button" onClick={() => onSelect(stage.key)}
              aria-pressed={isActive}
              onDragOver={(event) => {
                if (!droppable) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
                onDragOverStage(stage.key);
              }}
              onDragLeave={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node)) onDragLeaveStage();
              }}
              onDrop={(event) => {
                event.preventDefault();
                if (droppable) onDropStage(stage.key);
              }}
              className="group flex flex-col items-center gap-2 outline-none">
              <span className={cn(
                "relative flex size-14 items-center justify-center rounded-full border-2 border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)] transition-all duration-300 group-focus-visible:ring-2 group-focus-visible:ring-blue-500 group-focus-visible:ring-offset-2",
                isActive && stage.activeCircle,
                !isActive && "group-hover:border-slate-300 group-hover:text-slate-600",
                droppable && !isActive && "border-dashed border-blue-400 text-blue-600",
                isOver && "scale-110 border-solid border-blue-500 bg-blue-50",
              )}>
                <Icon className="size-6" />
                <span className={cn(
                  "absolute -right-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full border-2 border-[var(--card)] px-1 text-[11px] font-bold tabular-nums",
                  isActive ? "bg-slate-900 text-white" : "bg-[var(--muted)] text-[var(--muted-foreground)]",
                )}>
                  {counts[stage.key]}
                </span>
              </span>
              <span className={cn(
                "px-1 text-center text-[13px] font-semibold leading-tight",
                isActive ? stage.activeLabel : "text-[var(--muted-foreground)]",
              )}>
                {stage.label}
              </span>
              <span className="hidden text-[11px] text-slate-400 sm:block">{stage.hint}</span>
            </button>
          );
        })}
      </div>

      {/* дорога: грузовик подъезжает к выбранному этапу */}
      <div className="relative mx-[16.66%] mt-2 h-9">
        <div className="absolute inset-x-0 top-1/2 border-t-2 border-dashed border-slate-200" />
        {BOARD_STAGES.map((stage, index) => (
          <span key={stage.key}
            className={cn(
              "absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full transition-colors duration-300",
              index === activeIndex ? "bg-slate-700" : "bg-slate-300",
            )}
            style={{ left: `${(index / (BOARD_STAGES.length - 1)) * 100}%` }} />
        ))}
        <span className="absolute top-1/2 z-10 -translate-y-1/2 transition-[left] duration-700 ease-in-out"
          style={{ left: `${(activeIndex / (BOARD_STAGES.length - 1)) * 100}%` }}>
          <span className="flex size-8 -translate-x-1/2 items-center justify-center rounded-full bg-slate-900 text-white shadow-[0_6px_16px_rgba(15,23,42,0.35)]">
            <Truck className="size-4" />
          </span>
        </span>
      </div>
    </div>
  );
}

/** Карусель этапов: полотно едет к выбранной остановке, высота подстраивается. */
function StageCarousel({ active, slides }: { active: number; slides: ReactNode[] }) {
  const panes = useRef<(HTMLDivElement | null)[]>([]);
  const [height, setHeight] = useState<number>();
  useEffect(() => {
    const pane = panes.current[active];
    if (!pane) return;
    const update = () => setHeight(pane.offsetHeight);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(pane);
    return () => observer.disconnect();
  }, [active]);
  return (
    <div className="overflow-hidden transition-[height] duration-500 ease-in-out" style={{ height }}>
      <div className="flex items-start transition-transform duration-500 ease-in-out"
        style={{ transform: `translateX(-${active * 100}%)` }}>
        {slides.map((slide, index) => (
          <div key={BOARD_STAGES[index].key}
            ref={(el) => { panes.current[index] = el; }}
            aria-hidden={index !== active}
            className={cn("w-full shrink-0 pb-1", index !== active && "pointer-events-none")}>
            {slide}
          </div>
        ))}
      </div>
    </div>
  );
}

function LiveBoard({ orders, cameras, sessions, histories, completedDays, onOpen, onHistory, allowedStages, onMove, movingId, error }: {
  orders: Order[];
  cameras: CameraFeed[];
  sessions: AiCountingSession[];
  histories: AiCountingHistory[];
  completedDays: number;
  onOpen: (id: number) => void;
  onHistory: (history: AiCountingHistory) => void;
  allowedStages: (order: Order) => BoardStageKey[];
  onMove: (order: Order, stage: BoardStageKey) => void;
  movingId: number | null;
  error?: string;
}) {
  const [active, setActive] = useState<BoardStageKey>("waiting");
  const [draggedId, setDraggedId] = useState<number | null>(null);
  const [overStage, setOverStage] = useState<BoardStageKey | null>(null);
  const historyByOrder = useMemo(() => {
    const result = new Map<number, AiCountingHistory>();
    for (const item of histories) if (!result.has(item.order_id)) result.set(item.order_id, item);
    return result;
  }, [histories]);
  const dragged = orders.find((order) => order.id === draggedId);
  const draggedTargets = dragged ? allowedStages(dragged) : [];
  const stageRows = BOARD_STAGES.map((stage) =>
    orders.filter((order) => (stage.statuses as readonly string[]).includes(order.status)));
  const counts = Object.fromEntries(
    BOARD_STAGES.map((stage, index) => [stage.key, stageRows[index].length]),
  ) as Record<BoardStageKey, number>;
  const activeIndex = BOARD_STAGES.findIndex((stage) => stage.key === active);

  const completedLabel = completedDays === 1 ? "сегодня" : `за ${completedDays} дн.`;
  return (
    <section>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <span className="flex size-10 items-center justify-center rounded-xl border border-blue-100 bg-blue-50 text-blue-600 shadow-sm">
          <Layers3 className="size-5" />
        </span>
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-[20px] font-bold tracking-tight text-slate-800">Заказы на посту</h2>
            <span className="relative flex size-2">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-50" />
              <span className="relative size-2 rounded-full bg-emerald-500" />
            </span>
          </div>
          <span className="text-[12px] text-slate-400">обновляется автоматически · отгруженные {completedLabel}</span>
        </div>
      </div>
      {error && <div className="mb-3"><ErrorAlert message={error} /></div>}

      <div className="flex flex-col gap-4">
        <StageNav active={active} counts={counts} onSelect={setActive}
          canDrop={(key) => draggedTargets.includes(key)}
          overStage={overStage}
          onDragOverStage={setOverStage}
          onDragLeaveStage={() => setOverStage(null)}
          onDropStage={(key) => {
            if (dragged) {
              onMove(dragged, key);
              setActive(key);
            }
            setDraggedId(null);
            setOverStage(null);
          }} />

        <StageCarousel active={activeIndex} slides={BOARD_STAGES.map((stage, index) => {
          const rows = stageRows[index];
          const finished = stage.key === "done";
          return (
            <div key={stage.key}
              className="min-h-[300px] rounded-[22px] border p-3 shadow-[0_10px_30px_rgba(45,62,94,0.04)] sm:p-4"
              style={{ background: stage.tint }}>
              {rows.length === 0 ? (
                <div className="flex min-h-[280px] flex-col items-center justify-center rounded-2xl border border-white/80 bg-white/70 px-5 py-8 text-center shadow-sm">
                  {stage.image ? (
                    <div className="relative mb-4 size-32 overflow-hidden rounded-full">
                      <Image src={stage.image} alt="" fill sizes="128px" className="object-cover" />
                    </div>
                  ) : (
                    <span className="mb-4 flex size-20 items-center justify-center rounded-full bg-blue-50 text-blue-500">
                      <Truck className="size-9" strokeWidth={1.6} />
                    </span>
                  )}
                  <div className="text-[14px] font-semibold text-slate-600">{stage.hint}: пусто</div>
                  <p className="mt-1 max-w-[210px] text-[12px] leading-relaxed text-slate-400">
                    {stage.key === "loading" && "Здесь появятся заказы в процессе погрузки."}
                    {stage.key === "done" && `Нет отгруженных заказов ${completedDays === 1 ? "за сегодня" : `за последние ${completedDays} дней`}.`}
                    {stage.key === "waiting" && "Новые подтверждённые заказы появятся здесь."}
                  </p>
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                  {rows.map((o) => (
                    <BoardCard key={o.id} order={o} stage={stage}
                      camera={cameras.find((camera) => camera.src === o.loading_camera)}
                      session={sessions.find((session) => session.order_id === o.id)}
                      history={historyByOrder.get(o.id)}
                      onOpen={finished ? undefined : onOpen}
                      onHistory={finished ? onHistory : undefined}
                      draggable={allowedStages(o).length > 0 && movingId == null}
                      moving={movingId === o.id}
                      quickTargets={allowedStages(o)}
                      onQuickMove={movingId == null ? (target) => onMove(o, target) : undefined}
                      onDragStart={(event) => {
                        event.dataTransfer.effectAllowed = "move";
                        event.dataTransfer.setData("text/plain", String(o.id));
                        setDraggedId(o.id);
                      }}
                      onDragEnd={() => { setDraggedId(null); setOverStage(null); }} />
                  ))}
                </div>
              )}
            </div>
          );
        })} />
      </div>
    </section>
  );
}

function CompletedOrdersSettingsModal({ open, settings, onClose, onSaved }: {
  open: boolean;
  settings: ShippingBoardSettings | null;
  onClose: () => void;
  onSaved: () => Promise<unknown>;
}) {
  const [days, setDays] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (open) {
      setDays(settings?.completed_orders_days ?? 1);
      setError("");
    }
  }, [open, settings?.completed_orders_days]);

  async function save() {
    setBusy(true); setError("");
    try {
      await api.patch("/cameras/shipping-settings/", { completed_orders_days: days });
      await onSaved();
      onClose();
    } catch (e) { setError(apiError(e)); }
    finally { setBusy(false); }
  }

  return (
    <Modal open={open} onClose={onClose} eyebrow="Настройка администратора"
      title="Отгруженные заказы"
      description="Выберите, сколько дней отгруженные заказы остаются на живом посту."
      footer={<>
        <Button variant="ghost" onClick={onClose}>Отмена</Button>
        <Button disabled={busy || days < 1 || days > 90} onClick={save}>
          <Check className="size-4" /> Сохранить
        </Button>
      </>}>
      <div className="rounded-2xl border bg-slate-50 p-4">
        <div className="flex items-center gap-3">
          <span className="flex size-11 items-center justify-center rounded-xl bg-blue-600 text-white">
            <CalendarDays className="size-5" />
          </span>
          <div>
            <div className="text-sm font-bold text-slate-800">Период на доске</div>
            <div className="text-xs text-slate-500">От 1 до 90 дней</div>
          </div>
          <div className="relative ml-auto">
            <input type="number" min={1} max={90} value={days}
              onChange={(event) => setDays(Number(event.target.value))}
              className="h-12 w-24 rounded-xl border bg-white pr-9 text-center text-xl font-black tabular-nums outline-none focus:ring-2 focus:ring-blue-500" />
            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-400">дн.</span>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-5 gap-2">
          {[1, 3, 7, 14, 30].map((value) => (
            <button type="button" key={value} onClick={() => setDays(value)}
              className={cn(
                "rounded-lg border py-2 text-xs font-bold transition-colors",
                days === value ? "border-blue-600 bg-blue-600 text-white" : "bg-white text-slate-600 hover:border-blue-300",
              )}>
              {value === 1 ? "Сегодня" : value}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-4 flex items-start gap-3 rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
        <Archive className="mt-0.5 size-4 shrink-0" />
        Видео подсчёта хранится отдельно на компьютере камер {settings?.video_retention_days ?? 14} дней.
      </div>
      {error && <p className="mt-3 text-sm text-[var(--destructive)]">{error}</p>}
    </Modal>
  );
}

function CountingHistoryModal({ history, onClose }: {
  history: AiCountingHistory | null;
  onClose: () => void;
}) {
  const recordingUrl = history ? `/cameras/ai/history/${history.id}/recording/` : null;
  const { data: recording, loading, error } = useApi<AiRecording>(recordingUrl);
  const [segmentIndex, setSegmentIndex] = useState(0);
  useEffect(() => setSegmentIndex(0), [history?.id]);
  const segments = recording?.segments ?? [];
  const segment = segments[segmentIndex] ?? segments[0];
  const videoUrl = useMemo(() => {
    if (!segment?.video_url || typeof window === "undefined") return "";
    const configured = String(api.defaults.baseURL || "");
    const origin = /^https?:\/\//.test(configured)
      ? new URL(configured).origin
      : window.location.origin;
    return new URL(segment.video_url, origin).toString();
  }, [segment?.video_url]);
  const total = history?.final_total ?? history?.last_status?.total;

  return (
    <Modal open={!!history} onClose={onClose} className="max-w-3xl"
      eyebrow={history ? `История отгрузки · заказ #${history.order_id}` : undefined}
      title="Как камера считала мешки"
      description="Запись хранится на компьютере камер и не занимает место на сервере.">
      {history && (
        <div className="grid gap-4 md:grid-cols-[0.85fr_1.4fr]">
          <div className="flex flex-col gap-3">
            <div className="rounded-2xl bg-slate-950 p-5 text-white">
              <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-cyan-300">Итог модели</div>
              <div className="mt-2 text-6xl font-black tabular-nums tracking-[-0.06em]">{total ?? "—"}</div>
              <div className="text-sm text-white/55">мешков насчитано камерой</div>
            </div>
            <div className="rounded-2xl border p-4 text-sm">
              <div className="flex items-center gap-2 font-bold"><Cctv className="size-4 text-blue-600" /> {history.camera_name}</div>
              <div className="mt-3 grid gap-2 text-xs text-slate-500">
                <span><b className="text-slate-700">Клиент:</b> {history.order_client_name}</span>
                <span><b className="text-slate-700">Оператор:</b> {history.started_by_name || "—"}</span>
                <span><b className="text-slate-700">Начало:</b> {formatDateTime(history.started_at)}</span>
                {history.ended_at && <span><b className="text-slate-700">Завершение:</b> {formatDateTime(history.ended_at)}</span>}
              </div>
            </div>
          </div>
          <div className="min-h-[280px] overflow-hidden rounded-2xl border bg-[#111315]">
            {videoUrl ? (
              <div className="flex h-full flex-col">
                <video key={videoUrl} controls preload="metadata" src={videoUrl}
                  className="aspect-video w-full flex-1 bg-black object-contain" />
                {segments.length > 1 && (
                  <div className="flex gap-2 overflow-x-auto border-t border-white/10 p-3">
                    {segments.map((item, index) => (
                      <button key={`${item.start}-${index}`} type="button" onClick={() => setSegmentIndex(index)}
                        className={cn(
                          "shrink-0 rounded-lg px-3 py-2 text-xs font-semibold",
                          index === segmentIndex ? "bg-white text-black" : "bg-white/10 text-white/70 hover:bg-white/15",
                        )}>
                        Фрагмент {index + 1} · {Math.max(1, Math.round(item.duration / 60))} мин
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="flex h-full min-h-[280px] flex-col items-center justify-center px-8 text-center text-white/50">
                <Film className="size-10" />
                <div className="mt-3 text-sm font-bold text-white/75">
                  {loading ? "Ищем запись на компьютере камер…" : "Запись сейчас недоступна"}
                </div>
                <p className="mt-1 max-w-sm text-xs leading-relaxed">
                  {error || recording?.detail || (history.has_recording
                    ? "Компьютер камер выключен либо срок хранения записи истёк."
                    : "Эта сессия была завершена до включения локального архива.")}
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

/** Одна активная сессия из «Моноблока»: живой AI-счётчик прямо под бордом. */
function ActiveLoadingCard({ session, order, camera, onOpen }: {
  session: AiCountingSession;
  order?: Order;
  camera?: CameraFeed;
  onOpen: (id: number) => void;
}) {
  const ai = useAiCounter(session.camera, session.order_id, true);
  const counted = ai.status?.total ?? session.last_status?.total ?? 0;
  const expected = order ? orderedBags(order) : 0;
  const accepted = order?.bags_loaded ?? 0;
  const percent = expected > 0 ? Math.min(100, Math.round((counted / expected) * 100)) : 0;
  const isLive = !!ai.status?.running;

  return (
    <button type="button" onClick={() => onOpen(session.order_id)}
      className="group relative overflow-hidden rounded-[20px] border border-slate-200/80 bg-white p-4 text-left shadow-[0_12px_32px_rgba(48,70,108,0.07)] transition duration-200 hover:-translate-y-0.5 hover:border-blue-200 hover:shadow-[0_18px_42px_rgba(48,70,108,0.12)]">
      <div className="absolute inset-y-0 left-0 w-1 bg-gradient-to-b from-blue-500 via-cyan-400 to-emerald-400" />
      <div className="flex items-start gap-3 pl-1">
        <span className="relative flex size-11 shrink-0 items-center justify-center rounded-[14px] bg-slate-900 text-white shadow-sm">
          <Cctv className="size-5" />
          <span className={cn(
            "absolute -right-1 -top-1 size-3 rounded-full border-2 border-white",
            isLive ? "animate-pulse bg-emerald-400" : "bg-amber-400",
          )} />
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[15px] font-bold text-slate-800">Заказ #{session.order_id}</span>
            <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.12em] text-emerald-700">
              {isLive ? "камера считает" : "подключение"}
            </span>
          </div>
          <p className="mt-0.5 truncate text-[12px] text-slate-500">
            {order?.client_name || session.order_client_name || "Без клиента"}
          </p>
        </div>

        <div className="shrink-0 text-right">
          <div className="flex items-baseline justify-end gap-1 text-slate-900">
            <span className="text-4xl font-black tabular-nums leading-none tracking-[-0.05em]">{counted}</span>
            {expected > 0 && <span className="text-sm font-semibold text-slate-400">/ {expected}</span>}
          </div>
          <span className="text-[10px] font-bold uppercase tracking-[0.13em] text-slate-400">мешков камерой</span>
        </div>
      </div>

      <div className="mt-4 pl-1">
        <div className="h-2 overflow-hidden rounded-full bg-slate-100">
          <div className="h-full rounded-full bg-gradient-to-r from-blue-500 via-cyan-400 to-emerald-400 transition-all duration-500"
            style={{ width: `${percent}%` }} />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
          <span className="flex items-center gap-1.5 font-semibold text-blue-700">
            <Radio className="size-3.5" /> {camera?.zone || camera?.name || session.camera}
          </span>
          <span className="flex items-center gap-1.5">
            <User className="size-3.5" /> {session.started_by_name}
          </span>
          <span className="flex items-center gap-1.5">
            <Clock3 className="size-3.5" /> {formatDateTime(session.started_at)}
          </span>
          {accepted !== counted && (
            <span className="ml-auto tabular-nums text-slate-400">на посту принято: {accepted}</span>
          )}
        </div>
      </div>
    </button>
  );
}

function ActiveLoadings({ sessions, orders, cameras, onOpen }: {
  sessions: AiCountingSession[] | null;
  orders: Order[];
  cameras: CameraFeed[];
  onOpen: (id: number) => void;
}) {
  return (
    <section className="overflow-hidden rounded-[24px] border border-slate-200/80 bg-[linear-gradient(135deg,#f7faff_0%,#f8fbff_55%,#f4fbf8_100%)] p-4 shadow-[0_14px_40px_rgba(48,70,108,0.06)] sm:p-5">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <span className="flex size-10 items-center justify-center rounded-xl bg-slate-900 text-white shadow-sm">
          <Activity className="size-5" />
        </span>
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-[18px] font-bold tracking-tight text-slate-800">Сейчас на погрузке</h2>
            {!!sessions?.length && <span className="size-2 animate-pulse rounded-full bg-emerald-500" />}
          </div>
          <p className="text-[12px] text-slate-400">Активные заказы и живой счёт камер</p>
        </div>
        <span className="ml-auto rounded-full border border-white bg-white/90 px-3 py-1 text-[12px] font-semibold tabular-nums text-slate-600 shadow-sm">
          {sessions?.length ?? 0} активн.
        </span>
      </div>

      {!sessions?.length ? (
        <div className="flex min-h-28 flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-white/65 px-4 text-center">
          <Cctv className="size-6 text-slate-300" />
          <p className="mt-2 text-sm font-semibold text-slate-600">Камеры пока не считают</p>
          <p className="mt-0.5 text-xs text-slate-400">Активная отгрузка появится после запуска в «Моноблоке».</p>
        </div>
      ) : (
        <div className="grid gap-3 xl:grid-cols-2 2xl:grid-cols-3">
          {sessions.map((session) => (
            <ActiveLoadingCard key={session.id} session={session}
              order={orders.find((order) => order.id === session.order_id)}
              camera={cameras.find((camera) => camera.src === session.camera)}
              onOpen={onOpen} />
          ))}
        </div>
      )}
    </section>
  );
}

/* ── Страница ───────────────────────────────────────────────────────────── */
function ShippingPageInner() {
  const { me } = useAuth();
  const canLoad = can(me, "shipping.load");
  const canTrain = can(me, "train.load");
  const canEditStatus = can(me, "orders.edit");
  const canRollback = can(me, "shipping.rollback");
  const canManage = can(me, "rbac.manage");
  const canViewHistory = can(me, "shipping.view");
  const { data: orders, error: loadError, reload } = useApi<Order[]>("/orders/?post_board=1");
  const { data: cameras, reload: reloadCameras } = useApi<CameraFeed[]>("/cameras/");
  const { data: sessions, reload: reloadSessions } = useApi<AiCountingSession[]>(
    canLoad || canViewHistory ? "/cameras/ai/sessions/" : null,
  );
  const { data: boardSettings, reload: reloadBoardSettings } = useApi<ShippingBoardSettings>(
    canViewHistory || canManage ? "/cameras/shipping-settings/" : null,
  );
  const board = useMemo(() => (orders ?? [])
    .filter((order) => ACTIVE_STATUSES.includes(order.status) || order.status === "shipped")
    .sort((a, b) => a.id - b.id), [orders]);
  const historyOrderIds = useMemo(() => board
    .filter((order) => order.status === "loaded" || order.status === "shipped")
    .map((order) => order.id)
    .join(","), [board]);
  const historyUrl = canViewHistory && historyOrderIds
    ? `/cameras/ai/history/?order_ids=${historyOrderIds}`
    : null;
  const { data: histories, reload: reloadHistories } = useApi<AiCountingHistory[]>(historyUrl);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selectedHistory, setSelectedHistory] = useState<AiCountingHistory | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [movingId, setMovingId] = useState<number | null>(null);
  const [moveError, setMoveError] = useState("");
  const [rewindDropOrder, setRewindDropOrder] = useState<Order | null>(null);
  const [manualStatusOrder, setManualStatusOrder] = useState<Order | null>(null);
  const [manualStatusTarget, setManualStatusTarget] = useState<ManualOrderTarget | null>(null);
  const [rollbackOrder, setRollbackOrder] = useState<Order | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Инвентарь камер сам восстанавливается после сбоя MediaMTX/Tailscale.
  useEffect(() => {
    const refreshVisible = () => {
      if (!document.hidden) void reloadCameras();
    };
    const timer = setInterval(refreshVisible, 30_000);
    document.addEventListener("visibilitychange", refreshVisible);
    window.addEventListener("online", refreshVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
      window.removeEventListener("online", refreshVisible);
    };
  }, [reloadCameras]);

  // Пост — «живой» экран: борд и счётчики обновляются сами.
  useEffect(() => {
    const t = setInterval(() => {
      if (!document.hidden) {
        void reload();
        void reloadSessions();
        void reloadHistories();
      }
    }, POLL_MS);
    return () => clearInterval(t);
  }, [reload, reloadHistories, reloadSessions]);

  const active = board.filter((o) => ACTIVE_STATUSES.includes(o.status));
  const selected = selectedId != null ? active.find((o) => o.id === selectedId) ?? null : null;
  const isTrain = selected?.transport_type === "train";
  const rewindSession = rewindDropOrder
    ? sessions?.find((item) => item.order_id === rewindDropOrder.id) ?? null
    : null;

  // Пост работает только с играбельными камерами; locked — тема дашборда.
  const playable = useMemo(() => playableCameras(cameras), [cameras]);
  const aiCam = useMemo(() => {
    if (!selected?.loading_camera) return null;
    // Назначенная камера не должна молча подменяться другой, даже если
    // временно пропала из живого инвентаря.
    return playable.find((c) => c.src === selected.loading_camera) ?? null;
  }, [playable, selected?.loading_camera]);
  const orderCameras = useMemo(() => {
    if (!selected?.loading_camera) return [];
    const bound = playable.find((camera) => camera.src === selected.loading_camera);
    return bound ? [bound] : [];
  }, [playable, selected?.loading_camera]);
  const boundCamera = useMemo(
    () => selected?.loading_camera
      ? (cameras ?? []).find((camera) => camera.src === selected.loading_camera)
      : undefined,
    [cameras, selected?.loading_camera],
  );

  const isLoadStep = !!selected && canLoad
    && (isTrain ? selected.status === "loading"
      : selected.status === "arrived" || selected.status === "loading");
  const ai = useAiCounter(aiCam?.src ?? null, selected?.id ?? null, isLoadStep);
  const aiLive = ai.running && isAiOnlineStatus(ai.status?.status);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); await reload(); }
    catch (e) { setError(apiError(e)); }
    finally { setBusy(false); }
  }

  // Куда пишется счёт мешков: машина — /load/, поезд — train count.
  const saveBags = useCallback((order: Order) => (bags: number) =>
    order.transport_type === "train"
      ? api.post(`/orders/${order.id}/train/`, { action: "count", bags })
      : api.post(`/orders/${order.id}/load/`, { bags }),
  []);

  const openOrder = (id: number) => { setSelectedId(id); setError(""); };

  const allowedBoardStages = useCallback((order: Order): BoardStageKey[] => {
    const stages: BoardStageKey[] = [];
    if (order.status === "shipped") {
      if (canRollback) stages.push("waiting");
      return stages;
    }
    if (order.status === "confirmed") {
      // Фактический старт и назначение камеры выполняет только Моноблок.
      // Редактор заказа может закрыть/отменить ожидающий заказ вручную.
      if (canEditStatus) stages.push("done");
      return stages;
    }
    if ((order.status === "arrived" || order.status === "loading") && canLoad) {
      stages.push("waiting");
    }
    if (order.transport_type === "train" && order.status === "loading") {
      if (canTrain) stages.push("done");
      return stages;
    }
    if ((order.status === "arrived" || order.status === "loading") && canLoad) stages.push("done");
    return stages;
  }, [canEditStatus, canLoad, canRollback, canTrain]);

  const stopOrderAi = useCallback(async (order: Order, completeOrder = false) => {
    const session = sessions?.find((item) => item.order_id === order.id);
    if (!session) return false;
    await api.delete(`/cameras/${session.camera}/ai/`, {
      params: { order_id: order.id, complete_order: completeOrder ? 1 : 0 },
      data: { order_id: order.id, complete_order: completeOrder },
    });
    return true;
  }, [sessions]);

  const completeOrder = useCallback(async (order: Order) => {
    // При активной AI-сессии один backend-вызов фиксирует финальный кадр/счёт,
    // закрывает processor и сразу переводит заказ в `shipped`.
    if (await stopOrderAi(order, true)) return;
    if (order.transport_type === "train") {
      await api.post(`/orders/${order.id}/train/`, { action: "finish" });
      return;
    }
    if (order.status === "arrived") {
      await api.post(`/orders/${order.id}/load/`, { bags: order.bags_loaded ?? 0 });
    }
    if (order.status === "arrived" || order.status === "loading") {
      await api.post(`/orders/${order.id}/finish-loading/`, {});
      return;
    }
    // Совместимость с единичными заказами, оставшимися в старом `loaded`.
    if (order.status === "loaded") await api.post(`/orders/${order.id}/ship/`, {});
  }, [stopOrderAi]);

  const executeMove = useCallback(async (order: Order, target: BoardStageKey) => {
    if (!allowedBoardStages(order).includes(target)) return;
    setMovingId(order.id); setMoveError("");
    try {
      if (target === "waiting") {
        await stopOrderAi(order);
        await api.post(`/orders/${order.id}/rewind-loading/`, {});
      } else if (target === "done") {
        await completeOrder(order);
      }
      await Promise.all([reload(), reloadSessions(), reloadHistories()]);
    } catch (e) { setMoveError(apiError(e)); }
    finally { setMovingId(null); }
  }, [allowedBoardStages, completeOrder, reload, reloadHistories, reloadSessions, stopOrderAi]);

  const moveOrder = useCallback((order: Order, target: BoardStageKey) => {
    if (target === "waiting") {
      if (order.status === "shipped") {
        setRollbackOrder(order);
        return;
      }
      setRewindDropOrder(order);
      return;
    }
    if (target === "done" && order.status === "confirmed") {
      setManualStatusOrder(order);
      setManualStatusTarget("shipped");
      return;
    }
    void executeMove(order, target);
  }, [executeMove]);

  return (
    <AppShell title="Пост погрузки" section="Работа" actions={canManage ? (
      <Button variant="outline" onClick={() => setSettingsOpen(true)}>
        <Settings2 className="size-4" />
        <span className="hidden sm:inline">Отгруженные: {boardSettings?.completed_orders_days === 1 || !boardSettings ? "сегодня" : `${boardSettings.completed_orders_days} дн.`}</span>
      </Button>
    ) : undefined}>
      {loadError && !orders ? (
        <ErrorAlert message={loadError} onRetry={reload} />
      ) : !selected ? (
        <div className="flex flex-col gap-6">
          {/* Лайв-статус заказов по этапам */}
          <LiveBoard orders={board} cameras={cameras ?? []} sessions={sessions ?? []} histories={histories ?? []}
            completedDays={boardSettings?.completed_orders_days ?? 1}
            onOpen={openOrder} onHistory={setSelectedHistory}
            allowedStages={allowedBoardStages} onMove={moveOrder}
            movingId={movingId} error={moveError} />
          {canLoad && (
            <ActiveLoadings sessions={sessions} orders={board} cameras={cameras ?? []}
              onOpen={openOrder} />
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {/* назад к посту */}
          <button onClick={() => setSelectedId(null)}
            className="flex w-fit items-center gap-1.5 text-sm font-medium text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
            <ArrowLeft className="size-4" /> К посту
          </button>

          {/* Рабочая зона выбранного заказа */}
          <div className="overflow-hidden rounded-2xl border bg-[var(--card)] shadow-card">
            {/* шапка: номер как госзнак (или поезд) + этапы */}
            <div className="flex flex-wrap items-center justify-between gap-4 border-b px-5 py-4">
              <div className="flex flex-wrap items-center gap-4">
                <TransportBadge order={selected} size="lg" />
                <div className="leading-tight">
                  <div className="flex items-center gap-1.5 text-sm font-semibold">
                    <User className="size-3.5 text-[var(--muted-foreground)]" />
                    {selected.client_name || "—"}
                  </div>
                  {selected.client_phone && (
                    <a href={`tel:${selected.client_phone}`}
                      className="mt-0.5 flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
                      <Phone className="size-3" /> {selected.client_phone}
                    </a>
                  )}
                </div>
              </div>
              {!isTrain && <StageTrack status={selected.status} />}
            </div>

            {/* груз и вес */}
            <div className="flex flex-wrap items-center gap-2 border-b bg-[var(--muted)]/30 px-5 py-3">
              {selected.items.map((it, i) => (
                <span key={i} className="flex items-center gap-1.5 rounded-lg border bg-[var(--card)] px-2.5 py-1 text-sm">
                  <Package className="size-3.5 text-[var(--muted-foreground)]" />
                  {it.product_label ?? "Товар"}
                  <b className="tabular-nums">× {it.quantity}</b>
                </span>
              ))}
              {selected.weigh_in_kg && (
                <span className="flex items-center gap-1.5 rounded-lg border bg-[var(--card)] px-2.5 py-1 text-sm">
                  <Scale className="size-3.5 text-[var(--muted-foreground)]" />
                  На въезде <b className="tabular-nums">{formatMoney(selected.weigh_in_kg)} кг</b>
                </span>
              )}
            </div>

            {/* видео + действие шага */}
            <div className={cn(
              "grid gap-4 p-5",
              selected.loading_camera && "xl:grid-cols-[1.4fr_1fr]",
            )}>
              {selected.loading_camera && (
                <PostCamera cameras={orderCameras}
                  zoneKeywords={["загруз"]}
                  preferId={aiCam?.id ?? null}
                  ai={aiLive && aiCam
                    ? { camId: aiCam.id, src: ai.status?.stream ?? `${aiCam.src}ai` }
                    : null} />
              )}

              <div className="flex flex-col justify-center gap-4 rounded-2xl bg-[var(--muted)]/40 p-5">
                {/* ── Поезд: старт → счёт → финиш (без въезда и весов) ── */}
                {isTrain && selected.status === "confirmed" && (
                  <div>
                    <div className="text-base font-semibold">Ожидает запуска в Моноблоке</div>
                    <p className="mt-1 text-sm leading-relaxed text-[var(--muted-foreground)]">
                      Выберите заказ и свободную камеру в «Моноблоке». Только там создаётся активная отгрузка.
                    </p>
                  </div>
                )}
                {isTrain && selected.status === "loading" && (
                  canTrain ? (
                    <>
                      {selected.loading_camera && (
                        <BoundCamera camera={boundCamera} source={selected.loading_camera} />
                      )}
                      <BagCounter key={selected.id} order={selected} onSave={saveBags(selected)} />
                      {aiCam && (
                        <AiCounterPanel ai={ai} accepted={selected.bags_loaded ?? 0}
                          onAccept={(bags) => act(() => saveBags(selected)(bags))} />
                      )}
                      <Button className="h-14 rounded-xl text-base" disabled={busy}
                        onClick={() => act(async () => {
                          await completeOrder(selected);
                          setSelectedId(null);
                        })}>
                        <Check className="size-5" /> Завершить отгрузку
                      </Button>
                    </>
                  ) : <p className="text-sm text-[var(--muted-foreground)]">Идёт загрузка поезда.</p>
                )}

                {/* ── Машина: приём → погрузка → выезд ── */}
                {!isTrain && selected.status === "confirmed" && (
                  <div>
                    <div className="text-base font-semibold">Ожидает запуска в Моноблоке</div>
                    <p className="mt-1 text-sm leading-relaxed text-[var(--muted-foreground)]">
                      Приём машины, выбор камеры и запуск AI выполняются одним действием в «Моноблоке».
                    </p>
                  </div>
                )}

                {!isTrain && (selected.status === "arrived" || selected.status === "loading") && (
                  canLoad ? (
                    <>
                      {selected.loading_camera && (
                        <BoundCamera camera={boundCamera} source={selected.loading_camera} />
                      )}
                      <BagCounter key={selected.id} order={selected} onSave={saveBags(selected)} />
                      {aiCam && (
                        <AiCounterPanel ai={ai} accepted={selected.bags_loaded ?? 0}
                          onAccept={(bags) => act(() => saveBags(selected)(bags))} />
                      )}
                      <Button className="h-14 rounded-xl text-base" disabled={busy}
                        onClick={() => act(async () => {
                          await completeOrder(selected);
                          setSelectedId(null);
                        })}>
                        <Check className="size-5" /> Завершить отгрузку
                      </Button>
                    </>
                  ) : <p className="text-sm text-[var(--muted-foreground)]">Идёт погрузка.</p>
                )}

                {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
              </div>
            </div>
          </div>
        </div>
      )}
      <CompletedOrdersSettingsModal open={settingsOpen} settings={boardSettings}
        onClose={() => setSettingsOpen(false)}
        onSaved={async () => { await Promise.all([reloadBoardSettings(), reload()]); }} />
      <CountingHistoryModal history={selectedHistory} onClose={() => setSelectedHistory(null)} />
      <ManualOrderStatusModal
        order={manualStatusOrder}
        target={manualStatusTarget}
        onClose={() => { setManualStatusOrder(null); setManualStatusTarget(null); }}
        onChanged={async () => {
          await Promise.all([reload(), reloadSessions(), reloadHistories()]);
        }}
      />
      <ShipmentRollbackModal order={rollbackOrder}
        onClose={() => setRollbackOrder(null)}
        onChanged={async () => {
          await Promise.all([reload(), reloadSessions(), reloadHistories()]);
        }} />
      <Modal open={!!rewindDropOrder} onClose={() => setRewindDropOrder(null)}
        eyebrow={rewindDropOrder ? `Заказ #${rewindDropOrder.id}` : undefined}
        title="Вернуть в ожидание въезда?"
        description="Заказ вернётся в первую колонку, а назначенная камера снова станет свободной."
        footer={<>
          <Button variant="ghost" onClick={() => setRewindDropOrder(null)}>Отмена</Button>
          <Button disabled={movingId != null || (!!rewindSession && !rewindSession.can_stop)}
            onClick={() => {
              if (!rewindDropOrder) return;
              const order = rewindDropOrder;
              setRewindDropOrder(null);
              void executeMove(order, "waiting");
            }}>
            <RotateCcw className="size-4" /> Вернуть в ожидание
          </Button>
        </>}>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
              <div className="text-xs font-medium uppercase tracking-wide text-amber-700">Сбросится</div>
              <div className="mt-1 text-2xl font-black tabular-nums text-amber-950">
                {rewindDropOrder?.bags_loaded ?? 0} <span className="text-sm font-semibold">меш.</span>
              </div>
            </div>
            <div className="rounded-xl border bg-slate-50 p-4">
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Камера</div>
              <div className="mt-1 truncate text-sm font-bold text-slate-800">
                {rewindDropOrder?.loading_camera
                  ? (cameras ?? []).find((camera) => camera.src === rewindDropOrder.loading_camera)?.name
                    ?? rewindDropOrder.loading_camera
                  : "Не назначена"}
              </div>
            </div>
          </div>
          {rewindSession && (
            <div className={cn(
              "rounded-xl border px-4 py-3 text-sm",
              rewindSession.can_stop
                ? "border-blue-200 bg-blue-50 text-blue-900"
                : "border-red-200 bg-red-50 text-red-900",
            )}>
              {rewindSession.can_stop
                ? "Перед возвратом активный AI-подсчёт будет остановлен автоматически."
                : `AI-подсчёт запустил ${rewindSession.started_by_name || "другой сотрудник"}. Сначала он или администратор должен остановить сессию.`}
            </div>
          )}
          <p className="text-sm leading-relaxed text-slate-500">
            Вес въезда и текущий результат незавершённой погрузки будут очищены. Действие запишется в журнал.
          </p>
        </div>
      </Modal>
    </AppShell>
  );
}

export default function ShippingPage() {
  return (
    <RequirePerm perm={["shipping.view", "train.view"]} title="Пост погрузки">
      <ShippingPageInner />
    </RequirePerm>
  );
}
