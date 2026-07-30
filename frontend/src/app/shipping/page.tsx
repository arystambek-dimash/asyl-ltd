"use client";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { PlateBadge } from "@/components/ui/license-plate-input";
import { CameraStream } from "@/components/camera-stream";
import { BagCounter, type BagCounterHandle } from "@/components/shipping/bag-counter";
import { ErrorAlert } from "@/components/ui/data-state";
import { ManualOrderStatusModal, type ManualOrderTarget } from "@/components/manual-order-status-modal";
import { ShipmentRollbackModal } from "@/components/shipment-rollback-modal";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { formatPlate } from "@/components/ui/license-plate-input";
import { playableCameras, type CameraFeed } from "@/components/camera-wall";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { resolveApiMediaUrl } from "@/lib/safe-url";
import { orderedBagCount } from "@/lib/orders";
import { cn, formatDateTime, formatMoney } from "@/lib/utils";
import {
  Activity,
  Archive,
  ArrowLeft,
  CalendarDays,
  Cctv,
  Check,
  Clock3,
  Film,
  GripVertical,
  Layers3,
  LockKeyhole,
  LogOut,
  Package,
  Phone,
  Radio,
  RotateCcw,
  Scale,
  Settings2,
  TrainFront,
  User,
  VideoOff,
} from "lucide-react";
import type { AiCountingHistory, AiCountingSession, AiRecording, Order, ShippingBoardSettings } from "@/lib/types";
import { useAiCounter, type AiCounter } from "@/lib/use-ai-counter";
import { useVisiblePolling } from "@/lib/use-visible-polling";

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
  {
    key: "waiting",
    label: "Ожидание въезда",
    statuses: ["confirmed"],
    hint: "Подтверждённые заказы",
    icon: Clock3,
    surface: "border-[var(--soft-blue-border)] bg-[var(--soft-blue)]",
    activeItem: "border-[var(--soft-blue-border)] bg-[var(--soft-blue)]",
    activeIcon: "border-[var(--soft-blue-border)] bg-[var(--card)] text-[var(--soft-blue-foreground)]",
    activeLabel: "text-[var(--soft-blue-foreground)]",
  },
  {
    key: "loading",
    label: "Загружается",
    statuses: ["arrived", "loading"],
    hint: "Идёт погрузка",
    icon: Package,
    surface: "border-[var(--soft-amber-border)] bg-[var(--soft-amber)]",
    activeItem: "border-[var(--soft-amber-border)] bg-[var(--soft-amber)]",
    activeIcon: "border-[var(--soft-amber-border)] bg-[var(--card)] text-[var(--soft-amber-foreground)]",
    activeLabel: "text-[var(--soft-amber-foreground)]",
  },
  {
    key: "done",
    label: "Отгружено",
    statuses: ["loaded", "shipped"],
    hint: "Отгруженные заказы",
    icon: Check,
    surface: "border-[var(--soft-green-border)] bg-[var(--soft-green)]",
    activeItem: "border-[var(--soft-green-border)] bg-[var(--soft-green)]",
    activeIcon: "border-[var(--soft-green-border)] bg-[var(--card)] text-[var(--soft-green-foreground)]",
    activeLabel: "text-[var(--soft-green-foreground)]",
  },
] as const;

const ACTIVE_STATUSES = ["confirmed", "arrived", "loading", "loaded"];
type BoardStageKey = (typeof BOARD_STAGES)[number]["key"];

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
          <div
            key={s.key}
            className={cn(
              "flex min-h-11 flex-1 items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-[13px] font-semibold sm:flex-none",
              done && "bg-[var(--success)]/10 text-[var(--success)]",
              active && "bg-[var(--foreground)] text-[var(--background)]",
              !done && !active && "bg-[var(--muted)] text-[var(--muted-foreground)]",
            )}
          >
            {done ? <Check className="size-4" /> : <Icon className="size-4" />}
            <span className="whitespace-nowrap">{s.label}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Живая камера поста: зона по шагу, переключение — чипами поверх видео. */
function PostCamera({
  cameras,
  zoneKeywords,
  preferId,
  ai,
}: {
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
        <CameraStream
          key={src}
          src={src}
          onStateChange={setOnline}
          className="absolute inset-0 h-full w-full object-cover"
        />
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
        <div className="absolute right-3 top-3 flex items-center gap-1.5 rounded-md bg-[var(--success)]/90 px-2.5 py-1 backdrop-blur-sm">
          <span className="size-1.5 animate-pulse rounded-full bg-white" />
          <span className="text-xs font-semibold text-white">AI-подсчёт</span>
        </div>
      )}

      {/* переключение камер: чипы внизу видео */}
      {cameras.length > 1 && (
        <div className="absolute inset-x-3 bottom-3 flex gap-1.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {cameras.map((c) => (
            <button
              type="button"
              key={c.id}
              onClick={() => setManualId(c.id)}
              aria-pressed={c.id === cam?.id}
              className={cn(
                "min-h-11 shrink-0 whitespace-nowrap rounded-lg px-3 py-2 text-xs font-semibold backdrop-blur-sm transition-colors",
                c.id === cam?.id ? "bg-white text-black" : "bg-black/45 text-white/80 hover:bg-black/65",
              )}
            >
              {c.zone}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// Цвет партии из ai_service (Blue_50, White…) → точка-индикатор в чипе.
const BAG_COLORS: [RegExp, string][] = [
  [/blue/i, "#3b82f6"],
  [/green/i, "#22c55e"],
  [/red/i, "#ef4444"],
  [/yellow/i, "#eab308"],
  [/orange/i, "#f97316"],
  [/black/i, "#27272a"],
  [/white/i, "#e4e4e7"],
];
function bagColor(name: string) {
  return BAG_COLORS.find(([re]) => re.test(name))?.[1] ?? "var(--muted-foreground)";
}

/** AI-подсчёт с камеры: живое число для сверки, «Принять» пишет его в ручной счёт. */
function AiCounterPanel({
  ai,
  accepted,
  onAccept,
}: {
  ai: AiCounter;
  accepted: number;
  onAccept: (bags: number) => void;
}) {
  const st = ai.status;
  if (st?.busy && !st.owned_by_order) {
    return (
      <div className="rounded-xl border border-[var(--soft-amber-border)] bg-[var(--soft-amber)] p-3.5">
        <div className="flex items-center gap-2 text-sm font-semibold text-[var(--soft-amber-foreground)]">
          <Cctv className="size-4" /> AI-подсчёт занят
        </div>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          Сейчас считается заказ #{st.session_order_id}. Камеры можно смотреть, а новая сессия станет доступна сразу
          после завершения текущей.
        </p>
      </div>
    );
  }
  if (!st?.running) {
    return (
      <div className="flex flex-col gap-2">
        <Button
          variant="outline"
          className="h-12 rounded-xl"
          disabled={ai.busy || ai.occupied}
          onClick={() => ai.start().catch(() => {})}
        >
          <Cctv className="size-5" /> AI-подсчёт · заказ #{ai.orderId}
        </Button>
        {ai.error && <p className="text-sm text-[var(--destructive)]">{ai.error}</p>}
      </div>
    );
  }

  const warming = !isAiOnlineStatus(st.status);
  const total = st.total ?? 0;
  return (
    <div className="rounded-xl border border-[var(--soft-green-border)] bg-[var(--soft-green)] p-3.5">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--soft-green-foreground)]">
          <span className={cn("size-1.5 rounded-full bg-[var(--success)]", !warming && "animate-pulse")} />
          AI-подсчёт{warming && " · запуск модели…"}
        </span>
        {st.can_stop ? (
          <button
            type="button"
            onClick={() => ai.stop().catch(() => {})}
            disabled={ai.busy}
            className="min-h-11 rounded-lg px-3 text-xs font-semibold text-[var(--muted-foreground)] transition-colors hover:bg-[var(--card)] hover:text-[var(--foreground)]"
          >
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
            <span
              key={color}
              className="flex items-center gap-1.5 rounded-md border bg-[var(--card)] px-2 py-0.5 text-xs tabular-nums"
            >
              <span className="size-2 rounded-full" style={{ background: bagColor(color) }} />
              {color.replace(/_/g, " ")} · {n}
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 grid grid-cols-[1fr_auto] gap-2">
        {/* на прогреве счёт ещё нулевой — принять его можно только по ошибке */}
        <Button
          className="h-11 rounded-lg"
          disabled={ai.busy || warming || total === accepted}
          onClick={() => onAccept(total)}
        >
          <Check className="size-4" /> Принять {total}
        </Button>
        <Button
          variant="outline"
          className="h-11 rounded-lg px-3"
          disabled={ai.busy || !st.can_stop}
          onClick={() => ai.reset().catch(() => {})}
          aria-label="Обнулить AI-счётчик"
          title="Начать счёт заново"
        >
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
    <div className="rounded-xl border border-[var(--soft-blue-border)] bg-[var(--soft-blue)] p-3">
      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--soft-blue-foreground)]">
          <Cctv className="size-3.5" /> Камера погрузки
        </span>
        <span className="flex items-center gap-1 rounded-full bg-[var(--card)] px-2 py-0.5 text-[10px] font-semibold text-[var(--muted-foreground)] shadow-sm">
          <LockKeyhole className="size-3" /> закреплена
        </span>
      </div>
      <div className="mt-2 flex min-h-11 items-center gap-2 rounded-lg border border-[var(--soft-blue-border)] bg-[var(--card)] px-3 py-2.5">
        <span
          className={cn("size-2 shrink-0 rounded-full", camera?.online ? "bg-[var(--success)]" : "bg-[var(--warning)]")}
        />
        <span className="min-w-0 flex-1 truncate text-sm font-bold text-[var(--foreground)]">
          {camera?.zone || camera?.name || source}
        </span>
        <span className="text-[11px] text-[var(--muted-foreground)]">назначена в «Моноблоке»</span>
      </div>
    </div>
  );
}

/* ── Лайв-борд: заказы едут по этапам слева направо ─────────────────────── */
function TransportBadge({ order, size = "md" }: { order: Order; size?: "md" | "lg" }) {
  if (order.transport_type === "train") {
    return (
      <span
        className={cn(
          "flex items-center gap-1.5 rounded-md border bg-[var(--muted)] font-semibold",
          size === "lg" ? "px-3 py-1.5 text-sm" : "px-2 py-1 text-xs",
        )}
      >
        <TrainFront className={size === "lg" ? "size-4" : "size-3.5"} /> Вагон
      </span>
    );
  }
  return <PlateBadge value={order.truck_number} size={size === "lg" ? "lg" : "md"} />;
}

function BoardCard({
  order,
  stage,
  camera,
  session,
  history,
  onOpen,
  onHistory,
  draggable,
  moving,
  onDragStart,
  onDragEnd,
  quickTargets,
  onQuickMove,
}: {
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
  const ordered = orderedBagCount(order);
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
        clickable &&
          "cursor-pointer hover:-translate-y-px hover:shadow-md focus-visible:ring-2 focus-visible:ring-[var(--ring)]",
        draggable && "cursor-grab active:cursor-grabbing",
        moving && "pointer-events-none scale-[0.98] opacity-50",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <TransportBadge order={order} />
        <span className="flex items-center gap-1.5 text-xs font-semibold text-[var(--muted-foreground)]">
          {draggable && <GripVertical className="size-3.5 opacity-45" />}#{order.id}
        </span>
      </div>
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold">{order.client_name || "—"}</div>
        <div className="mt-0.5 text-xs tabular-nums text-[var(--muted-foreground)]">
          {stage.key === "waiting" && `${ordered} меш. к погрузке`}
          {stage.key === "loading" && `${bags} / ${ordered} меш.`}
          {stage.key === "done" && (order.shipped_at ? `выехал ${formatDateTime(order.shipped_at)}` : `${bags} меш.`)}
        </div>
      </div>
      {order.loading_camera && (
        <div className="flex items-center gap-1.5 rounded-lg border border-[var(--soft-blue-border)] bg-[var(--soft-blue)] px-2.5 py-1.5 text-[11px] font-semibold text-[var(--soft-blue-foreground)]">
          <Cctv className="size-3.5 shrink-0" />
          <span className="truncate">{camera?.zone || camera?.name || order.loading_camera}</span>
          {stage.key === "loading" && (
            <span
              className="relative ml-auto flex size-2 shrink-0"
              title={
                session?.status === "active"
                  ? "AI-подсчёт активен"
                  : session
                    ? "AI запускается"
                    : "Камера закреплена, AI не запущен"
              }
            >
              {session?.status === "active" && (
                <span className="absolute inline-flex size-2 animate-ping rounded-full bg-[var(--success)] opacity-40" />
              )}
              <span
                className={cn(
                  "relative size-2 rounded-full",
                  session?.status === "active"
                    ? "bg-[var(--success)]"
                    : session?.status === "starting"
                      ? "animate-pulse bg-[var(--warning)]"
                      : "bg-[var(--input)]",
                )}
              />
            </span>
          )}
        </div>
      )}
      {stage.key === "loading" && (
        <div className="h-1.5 overflow-hidden rounded-full bg-[var(--muted)]">
          <div
            className="h-full rounded-full transition-all"
            style={{ width: `${pct}%`, background: pct >= 100 ? "var(--success)" : "var(--warning)" }}
          />
        </div>
      )}
      {onQuickMove && !!quickTargets?.length && (
        <div
          className="flex gap-1.5 pt-0.5"
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
        >
          {quickTargets.includes("waiting") && (
            <Button
              size="sm"
              variant="ghost"
              className="h-11 px-3"
              aria-label="Вернуть заказ в ожидание"
              title="Вернуть в ожидание"
              onClick={() => onQuickMove("waiting")}
            >
              <RotateCcw className="size-4" />
            </Button>
          )}
          {quickTargets.includes("done") && (
            <Button size="sm" variant="outline" className="h-11 flex-1" onClick={() => onQuickMove("done")}>
              <Check className="size-4" /> Завершить
            </Button>
          )}
        </div>
      )}
      {stage.key === "done" && history && (
        <div className="absolute inset-x-0 bottom-0 flex translate-y-[calc(100%-4px)] items-center justify-between gap-3 bg-[var(--foreground)] px-3 py-2.5 text-[var(--background)] transition-transform duration-200 group-hover:translate-y-0 group-focus-visible:translate-y-0">
          <span className="flex min-w-0 items-center gap-2 text-xs font-semibold">
            <Cctv className="size-4 shrink-0" />
            <span className="truncate">Камера насчитала</span>
          </span>
          <span className="flex shrink-0 items-center gap-2">
            <b className="text-lg tabular-nums">{cameraTotal ?? "—"}</b>
            {history.has_recording && <Film className="size-4" />}
          </span>
        </div>
      )}
    </div>
  );
}

/* ── Остановки этапов: иконки с дорогой, по которой едет грузовик ───────── */
function StageNav({
  active,
  counts,
  onSelect,
  canDrop,
  overStage,
  onDragOverStage,
  onDragLeaveStage,
  onDropStage,
}: {
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
  return (
    <div className="rounded-[22px] border bg-[var(--card)] p-2 shadow-card">
      <div className="grid grid-cols-3 gap-1.5 sm:gap-2">
        {BOARD_STAGES.map((stage) => {
          const Icon = stage.icon;
          const isActive = stage.key === active;
          const droppable = canDrop(stage.key);
          const isOver = droppable && overStage === stage.key;
          return (
            <button
              key={stage.key}
              type="button"
              onClick={() => onSelect(stage.key)}
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
              className={cn(
                "group flex min-h-[88px] min-w-0 flex-col items-center justify-center gap-2 rounded-2xl border border-transparent px-2 py-3 text-left outline-none transition-colors sm:flex-row sm:justify-start sm:px-4",
                isActive ? stage.activeItem : "hover:border-[var(--border)] hover:bg-[var(--muted)]/70",
                droppable && !isActive && "border-dashed border-[var(--ring)] bg-[var(--soft-blue)]/50",
                isOver && "border-solid border-[var(--ring)] bg-[var(--soft-blue)]",
              )}
            >
              <span
                className={cn(
                  "relative flex size-10 shrink-0 items-center justify-center rounded-xl border bg-[var(--muted)] text-[var(--muted-foreground)] transition-colors group-focus-visible:ring-2 group-focus-visible:ring-[var(--ring)]",
                  isActive && stage.activeIcon,
                )}
              >
                <Icon className="size-5" />
                <span
                  className={cn(
                    "absolute -right-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full border-2 border-[var(--card)] px-1 text-[11px] font-bold tabular-nums",
                    isActive
                      ? "bg-[var(--foreground)] text-[var(--background)]"
                      : "bg-[var(--card)] text-[var(--muted-foreground)]",
                  )}
                >
                  {counts[stage.key]}
                </span>
              </span>
              <span className="min-w-0">
                <span
                  className={cn(
                    "block text-center text-[12px] font-bold leading-tight sm:truncate sm:text-left sm:text-[13px]",
                    isActive ? stage.activeLabel : "text-[var(--foreground)]",
                  )}
                >
                  {stage.label}
                </span>
                <span className="mt-1 hidden truncate text-[11px] text-[var(--muted-foreground)] md:block">
                  {stage.hint}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** Неактивные панели размонтированы: скрытые кнопки не попадают в tab-порядок. */
function StageCarousel({ active, slides }: { active: number; slides: ReactNode[] }) {
  return (
    <div key={BOARD_STAGES[active]?.key} className="w-full pb-1">
      {slides[active]}
    </div>
  );
}

function LiveBoard({
  orders,
  cameras,
  sessions,
  histories,
  completedDays,
  onOpen,
  onHistory,
  allowedStages,
  onMove,
  movingId,
  error,
}: {
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
  const cameraBySource = useMemo(
    () => new Map(cameras.flatMap((camera) => (camera.src ? [[camera.src, camera] as const] : []))),
    [cameras],
  );
  const sessionByOrder = useMemo(
    () => new Map(sessions.map((session) => [session.order_id, session] as const)),
    [sessions],
  );
  const dragged = orders.find((order) => order.id === draggedId);
  const draggedTargets = dragged ? allowedStages(dragged) : [];
  const stageRows = BOARD_STAGES.map((stage) =>
    orders.filter((order) => (stage.statuses as readonly string[]).includes(order.status)),
  );
  const counts = Object.fromEntries(BOARD_STAGES.map((stage, index) => [stage.key, stageRows[index].length])) as Record<
    BoardStageKey,
    number
  >;
  const activeIndex = BOARD_STAGES.findIndex((stage) => stage.key === active);

  const completedLabel = completedDays === 1 ? "сегодня" : `за ${completedDays} дн.`;
  return (
    <section>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <span className="flex size-10 items-center justify-center rounded-xl border border-[var(--soft-blue-border)] bg-[var(--soft-blue)] text-[var(--soft-blue-foreground)] shadow-sm">
          <Layers3 className="size-5" />
        </span>
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-[20px] font-bold tracking-tight text-[var(--foreground)]">Заказы на посту</h2>
            <span className="relative flex size-2">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-[var(--success)] opacity-40" />
              <span className="relative size-2 rounded-full bg-[var(--success)]" />
            </span>
          </div>
          <span className="text-[12px] text-[var(--muted-foreground)]">
            обновляется автоматически · отгруженные {completedLabel}
          </span>
        </div>
      </div>
      {error && (
        <div className="mb-3">
          <ErrorAlert message={error} />
        </div>
      )}

      <div className="flex flex-col gap-4">
        <StageNav
          active={active}
          counts={counts}
          onSelect={setActive}
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
          }}
        />

        <StageCarousel
          active={activeIndex}
          slides={BOARD_STAGES.map((stage, index) => {
            const rows = stageRows[index];
            const finished = stage.key === "done";
            const EmptyIcon = stage.icon;
            return (
              <div key={stage.key} className={cn("min-h-[300px] rounded-[22px] border p-3 sm:p-4", stage.surface)}>
                {rows.length === 0 ? (
                  <div className="flex min-h-[280px] flex-col items-center justify-center rounded-2xl border border-dashed bg-[var(--card)] px-5 py-8 text-center">
                    <span
                      className={cn(
                        "mb-4 flex size-16 items-center justify-center rounded-2xl border",
                        stage.activeIcon,
                      )}
                    >
                      <EmptyIcon className="size-7" strokeWidth={1.7} />
                    </span>
                    <div className="text-[14px] font-semibold text-[var(--muted-foreground)]">{stage.hint}: пусто</div>
                    <p className="mt-1 max-w-[210px] text-[12px] leading-relaxed text-[var(--muted-foreground)]">
                      {stage.key === "loading" && "Здесь появятся заказы в процессе погрузки."}
                      {stage.key === "done" &&
                        `Нет отгруженных заказов ${completedDays === 1 ? "за сегодня" : `за последние ${completedDays} дней`}.`}
                      {stage.key === "waiting" && "Новые подтверждённые заказы появятся здесь."}
                    </p>
                  </div>
                ) : (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                    {rows.map((o) => {
                      const quickTargets = allowedStages(o);
                      return (
                        <BoardCard
                          key={o.id}
                          order={o}
                          stage={stage}
                          camera={o.loading_camera ? cameraBySource.get(o.loading_camera) : undefined}
                          session={sessionByOrder.get(o.id)}
                          history={historyByOrder.get(o.id)}
                          onOpen={finished ? undefined : onOpen}
                          onHistory={finished ? onHistory : undefined}
                          draggable={quickTargets.length > 0 && movingId == null}
                          moving={movingId === o.id}
                          quickTargets={quickTargets}
                          onQuickMove={movingId == null ? (target) => onMove(o, target) : undefined}
                          onDragStart={(event) => {
                            event.dataTransfer.effectAllowed = "move";
                            event.dataTransfer.setData("text/plain", String(o.id));
                            setDraggedId(o.id);
                          }}
                          onDragEnd={() => {
                            setDraggedId(null);
                            setOverStage(null);
                          }}
                        />
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        />
      </div>
    </section>
  );
}

function CompletedOrdersSettingsModal({
  open,
  settings,
  onClose,
  onSaved,
}: {
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
    setBusy(true);
    setError("");
    try {
      await api.patch("/cameras/shipping-settings/", { completed_orders_days: days });
      await onSaved();
      onClose();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Настройка администратора"
      title="Отгруженные заказы"
      description="Выберите, сколько дней отгруженные заказы остаются на живом посту."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button disabled={busy || days < 1 || days > 90} onClick={save}>
            <Check className="size-4" /> Сохранить
          </Button>
        </>
      }
    >
      <div className="rounded-2xl border bg-[var(--secondary)] p-4">
        <div className="flex items-center gap-3">
          <span className="flex size-11 items-center justify-center rounded-xl bg-[var(--ring)] text-white">
            <CalendarDays className="size-5" />
          </span>
          <div>
            <div className="text-sm font-bold text-[var(--foreground)]">Период на доске</div>
            <div className="text-xs text-[var(--muted-foreground)]">От 1 до 90 дней</div>
          </div>
          <div className="relative ml-auto">
            <input
              type="number"
              min={1}
              max={90}
              value={days}
              onChange={(event) => setDays(Number(event.target.value))}
              className="h-12 w-24 rounded-xl border bg-[var(--card)] pr-9 text-center text-xl font-black tabular-nums outline-none focus:ring-2 focus:ring-[var(--ring)]"
            />
            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-[var(--muted-foreground)]">
              дн.
            </span>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-5 gap-2">
          {[1, 3, 7, 14, 30].map((value) => (
            <button
              type="button"
              key={value}
              onClick={() => setDays(value)}
              className={cn(
                "min-h-11 rounded-lg border px-2 py-2 text-xs font-bold transition-colors",
                days === value
                  ? "border-[var(--ring)] bg-[var(--ring)] text-white"
                  : "bg-[var(--card)] text-[var(--muted-foreground)] hover:border-[var(--ring)]",
              )}
            >
              {value === 1 ? "Сегодня" : value}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-4 flex items-start gap-3 rounded-xl border border-[var(--soft-green-border)] bg-[var(--soft-green)] px-4 py-3 text-sm text-[var(--soft-green-foreground)]">
        <Archive className="mt-0.5 size-4 shrink-0" />
        Видео подсчёта хранится отдельно на компьютере камер {settings?.video_retention_days ?? 14} дней.
      </div>
      {error && <p className="mt-3 text-sm text-[var(--destructive)]">{error}</p>}
    </Modal>
  );
}

function CountingHistoryModal({ history, onClose }: { history: AiCountingHistory | null; onClose: () => void }) {
  const recordingUrl = history ? `/cameras/ai/history/${history.id}/recording/` : null;
  const { data: recording, loading, error } = useApi<AiRecording>(recordingUrl);
  const [segmentIndex, setSegmentIndex] = useState(0);
  useEffect(() => setSegmentIndex(0), [history?.id]);
  const segments = recording?.segments ?? [];
  const segment = segments[segmentIndex] ?? segments[0];
  const videoUrl = useMemo(() => {
    if (!segment?.video_url || typeof window === "undefined") return "";
    return resolveApiMediaUrl(segment.video_url, String(api.defaults.baseURL || ""), window.location.origin);
  }, [segment?.video_url]);
  const total = history?.final_total ?? history?.last_status?.total;

  return (
    <Modal
      open={!!history}
      onClose={onClose}
      className="max-w-3xl"
      eyebrow={history ? `История отгрузки · заказ #${history.order_id}` : undefined}
      title="Как камера считала мешки"
      description="Запись хранится на компьютере камер и не занимает место на сервере."
    >
      {history && (
        <div className="grid gap-4 md:grid-cols-[0.85fr_1.4fr]">
          <div className="flex flex-col gap-3">
            <div className="rounded-2xl bg-[var(--foreground)] p-5 text-[var(--background)]">
              <div className="text-[11px] font-bold uppercase tracking-[0.14em] opacity-65">Итог модели</div>
              <div className="mt-2 text-6xl font-black tabular-nums tracking-[-0.06em]">{total ?? "—"}</div>
              <div className="text-sm opacity-55">мешков насчитано камерой</div>
            </div>
            <div className="rounded-2xl border p-4 text-sm">
              <div className="flex items-center gap-2 font-bold">
                <Cctv className="size-4 text-[var(--soft-blue-foreground)]" /> {history.camera_name}
              </div>
              <div className="mt-3 grid gap-2 text-xs text-[var(--muted-foreground)]">
                <span>
                  <b className="text-[var(--foreground)]">Клиент:</b> {history.order_client_name}
                </span>
                <span>
                  <b className="text-[var(--foreground)]">Оператор:</b> {history.started_by_name || "—"}
                </span>
                <span>
                  <b className="text-[var(--foreground)]">Начало:</b> {formatDateTime(history.started_at)}
                </span>
                {history.ended_at && (
                  <span>
                    <b className="text-[var(--foreground)]">Завершение:</b> {formatDateTime(history.ended_at)}
                  </span>
                )}
              </div>
            </div>
          </div>
          <div className="min-h-[280px] overflow-hidden rounded-2xl border bg-[#111315]">
            {videoUrl ? (
              <div className="flex h-full flex-col">
                <video
                  key={videoUrl}
                  controls
                  preload="metadata"
                  src={videoUrl}
                  className="aspect-video w-full flex-1 bg-black object-contain"
                />
                {segments.length > 1 && (
                  <div className="flex gap-2 overflow-x-auto border-t border-white/10 p-3">
                    {segments.map((item, index) => (
                      <button
                        key={`${item.start}-${index}`}
                        type="button"
                        onClick={() => setSegmentIndex(index)}
                        className={cn(
                          "min-h-11 shrink-0 rounded-lg px-3 py-2 text-xs font-semibold",
                          index === segmentIndex
                            ? "bg-white text-black"
                            : "bg-white/10 text-white/70 hover:bg-white/15",
                        )}
                      >
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
                  {error ||
                    recording?.detail ||
                    (history.has_recording
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
function ActiveLoadingCard({
  session,
  order,
  camera,
  onOpen,
}: {
  session: AiCountingSession;
  order?: Order;
  camera?: CameraFeed;
  onOpen: (id: number) => void;
}) {
  const ai = useAiCounter(session.camera, session.order_id, true);
  const counted = ai.status?.total ?? session.last_status?.total ?? 0;
  const expected = order ? orderedBagCount(order) : 0;
  const accepted = order?.bags_loaded ?? 0;
  const percent = expected > 0 ? Math.min(100, Math.round((counted / expected) * 100)) : 0;
  const isLive = !!ai.status?.running;

  return (
    <button
      type="button"
      onClick={() => onOpen(session.order_id)}
      className="group relative min-h-11 overflow-hidden rounded-[20px] border bg-[var(--card)] p-4 text-left shadow-card transition duration-200 hover:-translate-y-0.5 hover:border-[var(--soft-blue-border)] hover:shadow-float"
    >
      <div className="absolute inset-y-0 left-0 w-1 bg-[var(--ring)]" />
      <div className="flex items-start gap-3 pl-1">
        <span className="relative flex size-11 shrink-0 items-center justify-center rounded-[14px] bg-[var(--foreground)] text-[var(--background)]">
          <Cctv className="size-5" />
          <span
            className={cn(
              "absolute -right-1 -top-1 size-3 rounded-full border-2 border-[var(--card)]",
              isLive ? "animate-pulse bg-[var(--success)]" : "bg-[var(--warning)]",
            )}
          />
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[15px] font-bold text-[var(--foreground)]">Заказ #{session.order_id}</span>
            <span className="rounded-full bg-[var(--soft-green)] px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.12em] text-[var(--soft-green-foreground)]">
              {isLive ? "камера считает" : "подключение"}
            </span>
          </div>
          <p className="mt-0.5 truncate text-[12px] text-[var(--muted-foreground)]">
            {order?.client_name || session.order_client_name || "Без клиента"}
          </p>
        </div>

        <div className="shrink-0 text-right">
          <div className="flex items-baseline justify-end gap-1 text-[var(--foreground)]">
            <span className="text-4xl font-black tabular-nums leading-none tracking-[-0.05em]">{counted}</span>
            {expected > 0 && <span className="text-sm font-semibold text-[var(--muted-foreground)]">/ {expected}</span>}
          </div>
          <span className="text-[10px] font-bold uppercase tracking-[0.13em] text-[var(--muted-foreground)]">
            мешков камерой
          </span>
        </div>
      </div>

      <div className="mt-4 pl-1">
        <div className="h-2 overflow-hidden rounded-full bg-[var(--muted)]">
          <div
            className="h-full rounded-full bg-[var(--success)] transition-all duration-500"
            style={{ width: `${percent}%` }}
          />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-[var(--muted-foreground)]">
          <span className="flex items-center gap-1.5 font-semibold text-[var(--soft-blue-foreground)]">
            <Radio className="size-3.5" /> {camera?.zone || camera?.name || session.camera}
          </span>
          <span className="flex items-center gap-1.5">
            <User className="size-3.5" /> {session.started_by_name}
          </span>
          <span className="flex items-center gap-1.5">
            <Clock3 className="size-3.5" /> {formatDateTime(session.started_at)}
          </span>
          {accepted !== counted && (
            <span className="ml-auto tabular-nums text-[var(--muted-foreground)]">на посту принято: {accepted}</span>
          )}
        </div>
      </div>
    </button>
  );
}

function ActiveLoadings({
  sessions,
  orders,
  cameras,
  onOpen,
}: {
  sessions: AiCountingSession[] | null;
  orders: Order[];
  cameras: CameraFeed[];
  onOpen: (id: number) => void;
}) {
  return (
    <section className="overflow-hidden rounded-[24px] border bg-[var(--card)] p-4 shadow-card sm:p-5">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <span className="flex size-10 items-center justify-center rounded-xl bg-[var(--foreground)] text-[var(--background)]">
          <Activity className="size-5" />
        </span>
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-[18px] font-bold tracking-tight text-[var(--foreground)]">Сейчас на погрузке</h2>
            {!!sessions?.length && <span className="size-2 animate-pulse rounded-full bg-[var(--success)]" />}
          </div>
          <p className="text-[12px] text-[var(--muted-foreground)]">Активные заказы и живой счёт камер</p>
        </div>
        <span className="ml-auto flex min-h-8 items-center rounded-full border bg-[var(--muted)] px-3 text-[12px] font-semibold tabular-nums text-[var(--muted-foreground)]">
          {sessions?.length ?? 0} активн.
        </span>
      </div>

      {!sessions?.length ? (
        <div className="flex min-h-28 flex-col items-center justify-center rounded-2xl border border-dashed bg-[var(--muted)]/45 px-4 text-center">
          <Cctv className="size-6 text-[var(--muted-foreground)]" />
          <p className="mt-2 text-sm font-semibold text-[var(--muted-foreground)]">Камеры пока не считают</p>
          <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
            Активная отгрузка появится после запуска в «Моноблоке».
          </p>
        </div>
      ) : (
        <div className="grid gap-3 xl:grid-cols-2 2xl:grid-cols-3">
          {sessions.map((session) => (
            <ActiveLoadingCard
              key={session.id}
              session={session}
              order={orders.find((order) => order.id === session.order_id)}
              camera={cameras.find((camera) => camera.src === session.camera)}
              onOpen={onOpen}
            />
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
  const board = useMemo(
    () =>
      (orders ?? [])
        .filter((order) => ACTIVE_STATUSES.includes(order.status) || order.status === "shipped")
        .sort((a, b) => a.id - b.id),
    [orders],
  );
  const historyOrderIds = useMemo(
    () =>
      board
        .filter((order) => order.status === "loaded" || order.status === "shipped")
        .map((order) => order.id)
        .join(","),
    [board],
  );
  const historyUrl = canViewHistory && historyOrderIds ? `/cameras/ai/history/?order_ids=${historyOrderIds}` : null;
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
  // Кнопка отгрузки стоит вплотную под «+1/+5»: промах перчаткой отправлял
  // машину с неверным счётом, а откат требует отдельного права.
  const [finishing, setFinishing] = useState<Order | null>(null);
  const bagCounterRef = useRef<BagCounterHandle>(null);

  // Инвентарь камер и живой борд восстанавливаются после сетевых сбоев.
  // Каждый цикл сериализован, чтобы медленный ответ не отменял следующий.
  useVisiblePolling(reloadCameras, 30_000);
  useVisiblePolling(() => Promise.all([reload(), reloadSessions(), reloadHistories()]), POLL_MS);

  const active = board.filter((o) => ACTIVE_STATUSES.includes(o.status));
  const selected = selectedId != null ? (active.find((o) => o.id === selectedId) ?? null) : null;
  const isTrain = selected?.transport_type === "train";
  const rewindSession = rewindDropOrder
    ? (sessions?.find((item) => item.order_id === rewindDropOrder.id) ?? null)
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
    () =>
      selected?.loading_camera ? (cameras ?? []).find((camera) => camera.src === selected.loading_camera) : undefined,
    [cameras, selected?.loading_camera],
  );

  const isLoadStep =
    !!selected &&
    canLoad &&
    (isTrain ? selected.status === "loading" : selected.status === "arrived" || selected.status === "loading");
  const ai = useAiCounter(aiCam?.src ?? null, selected?.id ?? null, isLoadStep);
  const aiLive = ai.running && isAiOnlineStatus(ai.status?.status);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await reload();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  // Куда пишется счёт мешков: машина — /load/, вагон — train count.
  const saveBags = useCallback(
    (order: Order) => (bags: number) =>
      order.transport_type === "train"
        ? api.post(`/orders/${order.id}/train/`, { action: "count", bags })
        : api.post(`/orders/${order.id}/load/`, { bags }),
    [],
  );

  const openOrder = (id: number) => {
    setSelectedId(id);
    setError("");
  };

  const allowedBoardStages = useCallback(
    (order: Order): BoardStageKey[] => {
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
    },
    [canEditStatus, canLoad, canRollback, canTrain],
  );

  const stopOrderAi = useCallback(
    async (order: Order, completeOrder = false) => {
      const session = sessions?.find((item) => item.order_id === order.id);
      if (!session) return false;
      await api.delete(`/cameras/${session.camera}/ai/`, {
        params: { order_id: order.id, complete_order: completeOrder ? 1 : 0 },
        data: { order_id: order.id, complete_order: completeOrder },
      });
      return true;
    },
    [sessions],
  );

  const completeOrder = useCallback(
    async (order: Order, latestBags = order.bags_loaded ?? 0) => {
      // При активной AI-сессии один backend-вызов фиксирует финальный кадр/счёт,
      // закрывает processor и сразу переводит заказ в `shipped`.
      if (await stopOrderAi(order, true)) return;
      if (order.transport_type === "train") {
        await api.post(`/orders/${order.id}/train/`, { action: "finish" });
        return;
      }
      if (order.status === "arrived") {
        await api.post(`/orders/${order.id}/load/`, { bags: latestBags });
      }
      if (order.status === "arrived" || order.status === "loading") {
        await api.post(`/orders/${order.id}/finish-loading/`, {});
        return;
      }
      // Совместимость с единичными заказами, оставшимися в старом `loaded`.
      if (order.status === "loaded") await api.post(`/orders/${order.id}/ship/`, {});
    },
    [stopOrderAi],
  );

  const finishSelectedOrder = useCallback(
    async (order: Order) => {
      // Completion must never overtake the counter's debounce or an active save.
      const latestBags = await bagCounterRef.current?.saveNow();
      await completeOrder(order, latestBags ?? order.bags_loaded ?? 0);
      setSelectedId(null);
    },
    [completeOrder],
  );

  const executeMove = useCallback(
    async (order: Order, target: BoardStageKey) => {
      if (!allowedBoardStages(order).includes(target)) return;
      setMovingId(order.id);
      setMoveError("");
      try {
        if (target === "waiting") {
          await stopOrderAi(order);
          await api.post(`/orders/${order.id}/rewind-loading/`, {});
        } else if (target === "done") {
          await completeOrder(order);
        }
        await Promise.all([reload(), reloadSessions(), reloadHistories()]);
      } catch (e) {
        setMoveError(apiError(e));
      } finally {
        setMovingId(null);
      }
    },
    [allowedBoardStages, completeOrder, reload, reloadHistories, reloadSessions, stopOrderAi],
  );

  const moveOrder = useCallback(
    (order: Order, target: BoardStageKey) => {
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
      // Отгрузка необратима без отдельного права: показываем счёт мешков,
      // чтобы промах перчаткой мимо «+1» был виден до выезда машины.
      if (target === "done") {
        setFinishing(order);
        return;
      }
      void executeMove(order, target);
    },
    [executeMove],
  );

  return (
    <AppShell
      title="Пост погрузки"
      section="Работа"
      actions={
        canManage ? (
          <Button variant="outline" onClick={() => setSettingsOpen(true)}>
            <Settings2 className="size-4" />
            <span className="hidden sm:inline">
              Отгруженные:{" "}
              {boardSettings?.completed_orders_days === 1 || !boardSettings
                ? "сегодня"
                : `${boardSettings.completed_orders_days} дн.`}
            </span>
          </Button>
        ) : undefined
      }
    >
      {loadError && !orders ? (
        <ErrorAlert message={loadError} onRetry={reload} />
      ) : !selected ? (
        <div className="flex flex-col gap-6">
          {/* Лайв-статус заказов по этапам */}
          <LiveBoard
            orders={board}
            cameras={cameras ?? []}
            sessions={sessions ?? []}
            histories={histories ?? []}
            completedDays={boardSettings?.completed_orders_days ?? 1}
            onOpen={openOrder}
            onHistory={setSelectedHistory}
            allowedStages={allowedBoardStages}
            onMove={moveOrder}
            movingId={movingId}
            error={moveError}
          />
          {canLoad && <ActiveLoadings sessions={sessions} orders={board} cameras={cameras ?? []} onOpen={openOrder} />}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {/* назад к посту */}
          <button
            type="button"
            onClick={() => setSelectedId(null)}
            className="flex min-h-11 w-fit items-center gap-1.5 rounded-xl px-2 text-sm font-semibold text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
          >
            <ArrowLeft className="size-4" /> К посту
          </button>

          {/* Рабочая зона выбранного заказа */}
          <div className="overflow-hidden rounded-2xl border bg-[var(--card)] shadow-card">
            {/* шапка: номер как госзнак (или вагон) + этапы */}
            <div className="flex flex-wrap items-center justify-between gap-4 border-b px-5 py-4">
              <div className="flex flex-wrap items-center gap-4">
                <TransportBadge order={selected} size="lg" />
                <div className="leading-tight">
                  <div className="flex items-center gap-1.5 text-sm font-semibold">
                    <User className="size-3.5 text-[var(--muted-foreground)]" />
                    {selected.client_name || "—"}
                  </div>
                  {selected.client_phone && (
                    <a
                      href={`tel:${selected.client_phone}`}
                      className="mt-0.5 flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]"
                    >
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
                <span
                  key={it.id ?? `item-${i}`}
                  className="flex items-center gap-1.5 rounded-lg border bg-[var(--card)] px-2.5 py-1 text-sm"
                >
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
            <div className={cn("grid gap-4 p-5", selected.loading_camera && "xl:grid-cols-[1.4fr_1fr]")}>
              {selected.loading_camera && (
                <PostCamera
                  cameras={orderCameras}
                  zoneKeywords={["загруз"]}
                  preferId={aiCam?.id ?? null}
                  ai={aiLive && aiCam ? { camId: aiCam.id, src: ai.status?.stream ?? `${aiCam.src}ai` } : null}
                />
              )}

              <div className="flex flex-col justify-center gap-4 rounded-2xl bg-[var(--muted)]/40 p-5">
                {/* ── Вагон: старт → счёт → финиш (без въезда и весов) ── */}
                {isTrain && selected.status === "confirmed" && (
                  <div>
                    <div className="text-base font-semibold">Ожидает запуска в Моноблоке</div>
                    <p className="mt-1 text-sm leading-relaxed text-[var(--muted-foreground)]">
                      Выберите заказ и свободную камеру в «Моноблоке». Только там создаётся активная отгрузка.
                    </p>
                  </div>
                )}
                {isTrain &&
                  selected.status === "loading" &&
                  (canTrain ? (
                    <>
                      {selected.loading_camera && <BoundCamera camera={boundCamera} source={selected.loading_camera} />}
                      <BagCounter ref={bagCounterRef} key={selected.id} order={selected} onSave={saveBags(selected)} />
                      {aiCam && (
                        <AiCounterPanel
                          ai={ai}
                          accepted={selected.bags_loaded ?? 0}
                          onAccept={(bags) => act(() => saveBags(selected)(bags))}
                        />
                      )}
                      <Button
                        className="h-14 rounded-xl text-base"
                        disabled={busy}
                        onClick={() => setFinishing(selected)}
                      >
                        <Check className="size-5" /> Завершить отгрузку
                      </Button>
                    </>
                  ) : (
                    <p className="text-sm text-[var(--muted-foreground)]">Идёт загрузка вагона.</p>
                  ))}

                {/* ── Машина: приём → погрузка → выезд ── */}
                {!isTrain && selected.status === "confirmed" && (
                  <div>
                    <div className="text-base font-semibold">Ожидает запуска в Моноблоке</div>
                    <p className="mt-1 text-sm leading-relaxed text-[var(--muted-foreground)]">
                      Приём машины, выбор камеры и запуск AI выполняются одним действием в «Моноблоке».
                    </p>
                  </div>
                )}

                {!isTrain &&
                  (selected.status === "arrived" || selected.status === "loading") &&
                  (canLoad ? (
                    <>
                      {selected.loading_camera && <BoundCamera camera={boundCamera} source={selected.loading_camera} />}
                      <BagCounter ref={bagCounterRef} key={selected.id} order={selected} onSave={saveBags(selected)} />
                      {aiCam && (
                        <AiCounterPanel
                          ai={ai}
                          accepted={selected.bags_loaded ?? 0}
                          onAccept={(bags) => act(() => saveBags(selected)(bags))}
                        />
                      )}
                      <Button
                        className="h-14 rounded-xl text-base"
                        disabled={busy}
                        onClick={() => setFinishing(selected)}
                      >
                        <Check className="size-5" /> Завершить отгрузку
                      </Button>
                    </>
                  ) : (
                    <p className="text-sm text-[var(--muted-foreground)]">Идёт погрузка.</p>
                  ))}

                {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
              </div>
            </div>
          </div>
        </div>
      )}
      <CompletedOrdersSettingsModal
        open={settingsOpen}
        settings={boardSettings}
        onClose={() => setSettingsOpen(false)}
        onSaved={async () => {
          await Promise.all([reloadBoardSettings(), reload()]);
        }}
      />
      <CountingHistoryModal history={selectedHistory} onClose={() => setSelectedHistory(null)} />
      <ManualOrderStatusModal
        order={manualStatusOrder}
        target={manualStatusTarget}
        onClose={() => {
          setManualStatusOrder(null);
          setManualStatusTarget(null);
        }}
        onChanged={async () => {
          await Promise.all([reload(), reloadSessions(), reloadHistories()]);
        }}
      />
      <ShipmentRollbackModal
        order={rollbackOrder}
        onClose={() => setRollbackOrder(null)}
        onChanged={async () => {
          await Promise.all([reload(), reloadSessions(), reloadHistories()]);
        }}
      />

      {/* Счёт мешков виден в подтверждении: если промахнулись мимо «+1»,
          ошибка заметна здесь, а не после выезда машины. */}
      <ConfirmDialog
        open={finishing !== null}
        onClose={() => !busy && setFinishing(null)}
        title="Завершить отгрузку?"
        description={
          finishing
            ? `Машина ${formatPlate(finishing.truck_number) || "без номера"} уедет с ${
                finishing.bags_loaded ?? 0
              } из ${orderedBagCount(finishing)} меш. Откат потребует отдельного права.`
            : ""
        }
        confirmLabel="Завершить"
        confirmVariant="default"
        busy={busy}
        error={error}
        onConfirm={() => {
          const order = finishing;
          if (!order) return;
          void act(() => finishSelectedOrder(order)).then(() => setFinishing(null));
        }}
      />
      <Modal
        open={!!rewindDropOrder}
        onClose={() => setRewindDropOrder(null)}
        eyebrow={rewindDropOrder ? `Заказ #${rewindDropOrder.id}` : undefined}
        title="Вернуть в ожидание въезда?"
        description="Заказ вернётся в первую колонку, а назначенная камера снова станет свободной."
        footer={
          <>
            <Button variant="ghost" onClick={() => setRewindDropOrder(null)}>
              Отмена
            </Button>
            <Button
              disabled={movingId != null || (!!rewindSession && !rewindSession.can_stop)}
              onClick={() => {
                if (!rewindDropOrder) return;
                const order = rewindDropOrder;
                setRewindDropOrder(null);
                void executeMove(order, "waiting");
              }}
            >
              <RotateCcw className="size-4" /> Вернуть в ожидание
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl border border-[var(--soft-amber-border)] bg-[var(--soft-amber)] p-4">
              <div className="text-xs font-medium uppercase tracking-wide text-[var(--soft-amber-foreground)]">
                Сбросится
              </div>
              <div className="mt-1 text-2xl font-black tabular-nums text-[var(--soft-amber-foreground)]">
                {rewindDropOrder?.bags_loaded ?? 0} <span className="text-sm font-semibold">меш.</span>
              </div>
            </div>
            <div className="rounded-xl border bg-[var(--secondary)] p-4">
              <div className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">Камера</div>
              <div className="mt-1 truncate text-sm font-bold text-[var(--foreground)]">
                {rewindDropOrder?.loading_camera
                  ? ((cameras ?? []).find((camera) => camera.src === rewindDropOrder.loading_camera)?.name ??
                    rewindDropOrder.loading_camera)
                  : "Не назначена"}
              </div>
            </div>
          </div>
          {rewindSession && (
            <div
              className={cn(
                "rounded-xl border px-4 py-3 text-sm",
                rewindSession.can_stop
                  ? "border-[var(--soft-blue-border)] bg-[var(--soft-blue)] text-[var(--soft-blue-foreground)]"
                  : "border-[var(--soft-red-border)] bg-[var(--soft-red)] text-[var(--soft-red-foreground)]",
              )}
            >
              {rewindSession.can_stop
                ? "Перед возвратом активный AI-подсчёт будет остановлен автоматически."
                : `AI-подсчёт запустил ${rewindSession.started_by_name || "другой сотрудник"}. Сначала он или администратор должен остановить сессию.`}
            </div>
          )}
          <p className="text-sm leading-relaxed text-[var(--muted-foreground)]">
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
