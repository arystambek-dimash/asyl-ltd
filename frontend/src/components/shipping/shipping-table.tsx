"use client";

import { Fragment, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, ChevronRight, Film } from "lucide-react";
import type { CameraFeed } from "@/components/camera-wall";
import { ShipmentRollbackModal } from "@/components/shipment-rollback-modal";
import type { BagCounterHandle } from "@/components/shipping/bag-counter";
import { CountingHistoryModal } from "@/components/shipping/counting-history-modal";
import { RewindLoadingModal } from "@/components/shipping/rewind-loading-modal";
import { ShippingRowDetail } from "@/components/shipping/shipping-row-detail";
import { StartShipmentModal } from "@/components/shipping/start-shipment-modal";
import {
  finishLoadingConfirmText,
  resetAiConfirmText,
  shipOutConfirmText,
  stopAiConfirmText,
  useShippingActions,
  type ShippingActionResult,
  type ShippingConfirmText,
} from "@/components/shipping/use-shipping-actions";
import { ActionMenu, type ActionMenuItem } from "@/components/ui/action-menu";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ErrorAlert } from "@/components/ui/data-state";
import { PlateBadge } from "@/components/ui/license-plate-input";
import { ProgressBar } from "@/components/ui/progress-bar";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { apiError } from "@/lib/api";
import { orderedBagCount } from "@/lib/orders";
import { indexFirstBy, type CameraAvailabilityContext, type PlayableCamera } from "@/lib/shipping-cameras";
import type { ShippingCapabilities } from "@/lib/shipping-flow";
import type {
  AiCountingHistory,
  AiCountingSession,
  AlwaysOnProcessorStatus,
  CameraContinuousReadiness,
  Order,
} from "@/lib/types";
import { cn, formatDateTime } from "@/lib/utils";

export interface ShippingTableCapabilities extends ShippingCapabilities {
  /** shipping.view — история подсчёта и подпись «камера: N». */
  canViewShipping: boolean;
  /** orders.view && !isKiosk — пункт «Открыть заказ». */
  canOpenOrder: boolean;
  /** Техническая учётка моноблока: крупные кнопки, автораскрытие своей погрузки. */
  isKiosk: boolean;
  /** `me.monoblock_camera` — камера, которую киоск считает своей. */
  kioskCamera: string | null;
}

export interface ShippingTableProps {
  /** null — первая загрузка (скелет); при ошибке с данными таблица держит последние строки. */
  orders: Order[] | null;
  sessions: AiCountingSession[];
  /** История подсчёта (только при shipping.view, иначе []). */
  histories: AiCountingHistory[];
  /** Играбельные камеры по src — зона, имя, сохранённая линия подсчёта. */
  camerasBySrc: Map<string, CameraFeed>;
  capabilities: ShippingTableCapabilities;
  /** Камеры, разрешённые настройкой «Камеры моноблока», — выбор при запуске. */
  monoblockCameras: PlayableCamera[];
  shippingProcessors?: AlwaysOnProcessorStatus[];
  cameraOwners: Record<string, number>;
  cameraReadiness?: Record<string, CameraContinuousReadiness>;
  continuousReady: boolean;
  continuousDetail: string;
  /** `cameraSettings.locked` — камера закреплена настройкой, а не выбирается. */
  cameraLocked: boolean;
  /** Окно группы «Выехали» (бэкенд применяет его сам). */
  completedOrdersDays: number;
  reloadOrders: () => Promise<unknown>;
  reloadSessions: () => Promise<unknown>;
  /** Только при shipping.view. */
  reloadHistories?: () => Promise<unknown>;
}

type OrderRow = {
  kind: "order";
  key: string;
  id: number;
  order: Order;
  session: AiCountingSession | null;
  history: AiCountingHistory | null;
};
/** Сессия, чей заказ не пришёл в /orders/?post_board=1 (скоуп отдела). */
type SessionRow = { kind: "session"; key: string; id: number; session: AiCountingSession };
type Row = OrderRow | SessionRow;

type Dialog =
  | { kind: "finish"; row: Row; text: ShippingConfirmText }
  | { kind: "ship"; order: Order; text: ShippingConfirmText }
  | { kind: "reset"; session: AiCountingSession; text: ShippingConfirmText }
  | { kind: "stopAi"; session: AiCountingSession; text: ShippingConfirmText };

const LOADING_STATUSES = ["arrived", "loading"];
const COLUMN_COUNT = 6;

function isLoadingStatus(status: string) {
  return LOADING_STATUSES.includes(status);
}

function formatTime(value: string) {
  return new Date(value).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function rowOf(order: Order, session: AiCountingSession | null, history: AiCountingHistory | null): OrderRow {
  return { kind: "order", key: `order-${order.id}`, id: order.id, order, session, history };
}

function sessionRowOf(session: AiCountingSession): SessionRow {
  return { kind: "session", key: `session-${session.id}`, id: session.order_id, session };
}

/** Живой итог свёрнутой строки — из опроса сессий (лаг ≤3 с). */
function liveBags(row: Row): number {
  if (row.kind === "session") return row.session.last_status?.total ?? 0;
  return row.session?.last_status?.total ?? row.order.bags_loaded ?? 0;
}

function sessionBadge(session: AiCountingSession | null, hasCamera: boolean): ReactNode {
  if (session?.status === "active") {
    return (
      <Badge tone="success" dot>
        считает {session.last_status?.total ?? 0}
      </Badge>
    );
  }
  if (session?.status === "starting") {
    return (
      <Badge tone="warning" dot>
        запуск
      </Badge>
    );
  }
  if (hasCamera) {
    return (
      <Badge tone="muted" dot>
        без AI
      </Badge>
    );
  }
  return null;
}

/** Очередь отгрузки: группы по вниманию оператора, одна раскрытая строка. */
export function ShippingTable({
  orders,
  sessions,
  histories,
  camerasBySrc,
  capabilities,
  monoblockCameras,
  shippingProcessors,
  cameraOwners,
  cameraReadiness,
  continuousReady,
  continuousDetail,
  cameraLocked,
  completedOrdersDays,
  reloadOrders,
  reloadSessions,
  reloadHistories,
}: ShippingTableProps) {
  const router = useRouter();
  const { canLoad, canTrain, canShip, canRollback, canViewShipping, canOpenOrder, isKiosk, kioskCamera } = capabilities;
  const flowCapabilities = useMemo<ShippingCapabilities>(
    () => ({ canLoad, canTrain, canShip, canRollback }),
    [canLoad, canRollback, canShip, canTrain],
  );

  const { ordersById, sessionsByOrderId, sessionsByCamera, historiesByOrderId } = useMemo(
    () => ({
      ordersById: indexFirstBy(orders ?? [], (order) => order.id),
      sessionsByOrderId: indexFirstBy(sessions, (session) => session.order_id),
      sessionsByCamera: indexFirstBy(sessions, (session) => session.camera),
      historiesByOrderId: indexFirstBy(histories, (history) => history.order_id),
    }),
    [histories, orders, sessions],
  );

  const actions = useShippingActions({
    sessionsByOrderId,
    capabilities: flowCapabilities,
    reloadOrders,
    reloadSessions,
    reloadHistories,
  });

  const groups = useMemo(() => {
    const loading: Row[] = [];
    const ready: Row[] = [];
    const waiting: Row[] = [];
    const shipped: Row[] = [];
    for (const order of orders ?? []) {
      const row = rowOf(order, sessionsByOrderId.get(order.id) ?? null, historiesByOrderId.get(order.id) ?? null);
      if (isLoadingStatus(order.status)) loading.push(row);
      else if (order.status === "loaded") ready.push(row);
      else if (order.status === "confirmed") waiting.push(row);
      else if (order.status === "shipped") shipped.push(row);
    }
    for (const session of sessions) {
      if (!ordersById.has(session.order_id)) loading.push(sessionRowOf(session));
    }
    const startedAt = (row: Row) => row.session?.started_at ?? null;
    loading.sort((a, b) => {
      const sa = startedAt(a);
      const sb = startedAt(b);
      if (sa && sb) return sa.localeCompare(sb) || a.id - b.id;
      if (sa) return -1;
      if (sb) return 1;
      return a.id - b.id;
    });
    const byId = (a: Row, b: Row) => a.id - b.id;
    ready.sort(byId);
    waiting.sort(byId);
    shipped.sort((a, b) => {
      const sa = a.kind === "order" ? (a.order.shipped_at ?? "") : "";
      const sb = b.kind === "order" ? (b.order.shipped_at ?? "") : "";
      return sb.localeCompare(sa) || b.id - a.id;
    });
    const completedLabel = completedOrdersDays <= 1 ? "сегодня" : `за ${completedOrdersDays} дн.`;
    return [
      { key: "loading", title: "На погрузке", rows: loading, always: true },
      { key: "ready", title: "Готовы к выезду", rows: ready, always: true },
      { key: "waiting", title: "Ожидают погрузки", rows: waiting, always: true },
      { key: "shipped", title: `Выехали · ${completedLabel}`, rows: shipped, always: false },
    ];
  }, [completedOrdersDays, historiesByOrderId, orders, ordersById, sessions, sessionsByOrderId]);

  const rowsByKey = useMemo(() => {
    const map = new Map<string, Row>();
    for (const group of groups) for (const row of group.rows) map.set(row.key, row);
    return map;
  }, [groups]);
  const totalRows = rowsByKey.size;

  /* ── Раскрытие: одна строка, киоск автораскрывает свою погрузку ─────── */
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);
  const kioskRow = isKiosk
    ? groups[0].rows.find((row) => row.kind === "order" && row.order.loading_camera === kioskCamera)
    : undefined;
  const isExpandable = (row: Row) => row.kind === "session" || isLoadingStatus(row.order.status);
  // Строка, ушедшая из погрузки (возврат, завершение с другого места), сама
  // закрывается: панель живёт только у раскрываемых строк.
  const expandedRow = expandedKey ? rowsByKey.get(expandedKey) : undefined;
  const expanded =
    expandedRow && isExpandable(expandedRow)
      ? expandedRow.key
      : kioskRow && dismissedKey !== kioskRow.key
        ? kioskRow.key
        : null;
  function toggleRow(row: Row) {
    if (!isExpandable(row)) return;
    if (expanded === row.key) {
      setExpandedKey(null);
      setDismissedKey(row.key);
    } else {
      setExpandedKey(row.key);
    }
  }
  function onRowClick(event: MouseEvent<HTMLTableRowElement>, row: Row) {
    // Кнопки и меню внутри строки — не повод её раскрывать.
    if ((event.target as HTMLElement).closest("button,a,[role=menuitem]")) return;
    toggleRow(row);
  }

  /* ── Модалки и подтверждения ──────────────────────────────────────── */
  const bagCounterRef = useRef<BagCounterHandle>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [dialogBusy, setDialogBusy] = useState(false);
  const [dialogError, setDialogError] = useState("");
  const [startOrder, setStartOrder] = useState<Order | null>(null);
  const [rewindOrder, setRewindOrder] = useState<Order | null>(null);
  const [rollbackOrder, setRollbackOrder] = useState<Order | null>(null);
  const [historyOpen, setHistoryOpen] = useState<AiCountingHistory | null>(null);
  const [actionError, setActionError] = useState("");

  function openDialog(next: Dialog) {
    setDialogError("");
    setDialog(next);
  }

  async function finishRow(row: Row): Promise<ShippingActionResult> {
    if (row.kind === "session") return actions.stopSessionAi(row.session, true);
    const order = row.order;
    // Completion must never overtake the counter's debounce or an active save.
    // The ref points at the expanded row's counter only.
    let latestBags = order.bags_loaded ?? 0;
    if (expanded === row.key) {
      try {
        latestBags = (await bagCounterRef.current?.saveNow()) ?? latestBags;
      } catch (cause) {
        return { ok: false, error: apiError(cause) };
      }
    }
    return actions.executeMove(order, "exit", latestBags);
  }

  async function confirmDialog() {
    if (!dialog) return;
    setDialogBusy(true);
    setDialogError("");
    let result: ShippingActionResult;
    if (dialog.kind === "finish") result = await finishRow(dialog.row);
    else if (dialog.kind === "ship") result = await actions.executeMove(dialog.order, "done");
    else if (dialog.kind === "reset") result = await actions.resetSessionAi(dialog.session);
    else result = await actions.stopSessionAi(dialog.session, false);
    setDialogBusy(false);
    if (!result.ok) {
      setDialogError(result.error);
      return;
    }
    // Завершённая строка сворачивается; ключ чистим даже если строка уже уехала в другую группу.
    if (dialog.kind === "finish" && expandedKey === dialog.row.key) setExpandedKey(null);
    setDialog(null);
  }

  /** Действия без модалки: ошибка уходит в алерт над таблицей. */
  async function runInline(run: () => Promise<ShippingActionResult>) {
    setActionError("");
    const result = await run();
    if (!result.ok && result.error) setActionError(result.error);
  }

  const availability = useMemo<CameraAvailabilityContext>(
    () => ({
      busyCameras: sessions.map((session) => session.camera),
      shippingProcessors,
      cameraOwners,
      cameraReadiness,
      continuousReady,
    }),
    [cameraOwners, cameraReadiness, continuousReady, sessions, shippingProcessors],
  );

  /* ── Действия строки: ровно одна главная кнопка + кебаб ───────────── */
  type Primary = { label: string; onClick: () => void; disabled?: boolean; hint?: string };
  function rowActions(row: Row): { primary: Primary | null; note: string | null; menu: ActionMenuItem[] } {
    const menu: ActionMenuItem[] = [];
    if (row.kind === "session") {
      const { session } = row;
      const own = session.can_stop && canLoad;
      const primary: Primary | null = own ? { label: "Завершить погрузку", onClick: () => openFinish(row) } : null;
      if (own && session.status === "active") menu.push(...sessionMenu(session));
      return { primary, note: primary ? null : "Идёт погрузка", menu };
    }

    const { order, session, history } = row;
    const train = order.transport_type === "train";
    const stages = actions.allowedStages(order);
    const canCount = train ? canTrain : canLoad;
    let primary: Primary | null = null;
    let note: string | null = null;

    if (order.status === "confirmed") {
      if (session) {
        // Сессии опрашиваются чаще заказов: другой оператор уже запустил AI,
        // а заказ ещё не успел стать «loading» — второй старт недопустим.
        note = session.status === "starting" ? "Запуск AI" : "Идёт погрузка";
      } else if (train && canTrain) {
        primary = { label: "Начать загрузку вагона", onClick: () => void runInline(() => actions.startTrain(order)) };
      } else if (!train && canLoad) {
        primary = { label: "Начать погрузку", onClick: () => setStartOrder(order) };
      } else {
        note = "Ожидает запуска";
      }
    } else if (isLoadingStatus(order.status)) {
      if (stages.includes("exit")) {
        const foreign = !!session && !session.can_stop;
        primary = {
          label: "Завершить погрузку",
          onClick: () => openFinish(row),
          disabled: foreign,
          hint: foreign ? `сессию запустил ${session.started_by_name || "другой сотрудник"}` : undefined,
        };
      } else {
        note = "Идёт погрузка";
      }
      if (stages.includes("waiting")) {
        menu.push({ key: "rewind", label: "Вернуть в ожидание", onSelect: () => setRewindOrder(order) });
      }
      if (canCount) {
        menu.push({ key: "manual", label: "Мешки вручную", onSelect: () => setExpandedKey(row.key) });
      }
      if (!train && !session && canLoad) {
        menu.push({ key: "start-ai", label: "Запустить AI-подсчёт", onSelect: () => setStartOrder(order) });
      }
      if (session?.status === "active" && session.can_stop && canLoad) menu.push(...sessionMenu(session));
    } else if (order.status === "loaded") {
      if (canShip && stages.includes("done")) {
        primary = {
          label: "Оформить выезд",
          onClick: () => openDialog({ kind: "ship", order, text: shipOutConfirmText(order) }),
        };
      } else {
        note = "Ожидает оформления выезда";
      }
    }
    if (history && canViewShipping && (order.status === "loaded" || order.status === "shipped")) {
      menu.push({ key: "history", label: "История подсчёта", icon: Film, onSelect: () => setHistoryOpen(history) });
    }
    if (order.status === "shipped" && canRollback && stages.includes("waiting")) {
      menu.push({
        key: "rollback",
        label: "Отменить отгрузку",
        tone: "destructive",
        onSelect: () => setRollbackOrder(order),
      });
    }
    if (canOpenOrder) {
      menu.push({ key: "open", label: "Открыть заказ", onSelect: () => router.push(`/orders/${order.id}`) });
    }
    return { primary, note, menu };
  }

  function sessionMenu(session: AiCountingSession): ActionMenuItem[] {
    return [
      {
        key: "reset-ai",
        label: "Обнулить AI-счёт",
        onSelect: () => openDialog({ kind: "reset", session, text: resetAiConfirmText(session.order_id) }),
      },
      {
        key: "stop-ai",
        label: "Выключить AI-подсчёт",
        onSelect: () => openDialog({ kind: "stopAi", session, text: stopAiConfirmText(session.order_id) }),
      },
    ];
  }

  function openFinish(row: Row) {
    const bags = liveBags(row);
    const text =
      row.kind === "order"
        ? finishLoadingConfirmText(row.order, bags)
        : {
            title: "Завершить погрузку?",
            description: `Камера насчитала ${bags} меш. для заказа #${row.session.order_id}. После завершения заказ перейдёт в «Готов к выезду», но выезд ещё не будет оформлен.`,
            confirmLabel: "Завершить погрузку",
            confirmVariant: "default" as const,
          };
    openDialog({ kind: "finish", row, text });
  }

  /* ── Ячейки ──────────────────────────────────────────────────────── */
  const cellClass = cn(isKiosk && "h-14");
  const primaryButtonClass = isKiosk ? "h-11 min-w-[168px] text-[15px]" : "h-10";
  const menuClass = isKiosk ? "size-11" : "size-10";

  function transportCell(row: Row) {
    const expandable = isExpandable(row);
    const open = expanded === row.key;
    const label = row.kind === "order" ? `заказ #${row.order.id}` : `сессию заказа #${row.session.order_id}`;
    return (
      <div className="flex items-center gap-2">
        {expandable ? (
          <button
            type="button"
            aria-expanded={open}
            aria-label={`${open ? "Свернуть" : "Раскрыть"} ${label}`}
            onClick={() => toggleRow(row)}
            className="flex size-6 shrink-0 items-center justify-center rounded-md text-[var(--muted-foreground)]"
          >
            {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          </button>
        ) : (
          <span className="size-6 shrink-0" />
        )}
        <div className="min-w-0">
          {row.kind === "session" ? (
            <PlateBadge value={row.session.order_truck_number} size="md" />
          ) : row.order.transport_type === "train" ? (
            <Badge tone="outline">Вагон</Badge>
          ) : row.order.truck_number ? (
            <PlateBadge value={row.order.truck_number} size="md" />
          ) : (
            <Badge tone="muted">Без номера</Badge>
          )}
          <div className="mt-1 text-[12px] tabular-nums text-[var(--muted-foreground)]">#{row.id}</div>
        </div>
      </div>
    );
  }

  function clientCell(row: Row) {
    if (row.kind === "session") {
      return (
        <div className="min-w-0">
          <div className="truncate text-[14px] font-medium" title={row.session.order_client_name}>
            {row.session.order_client_name || "Без клиента"}
          </div>
          <div className="text-[12px] text-[var(--muted-foreground)]">нет доступа к заказу</div>
        </div>
      );
    }
    const name = row.order.client_name || "Без клиента";
    const cargo = row.order.items.map((item) => `${item.product_label ?? "Товар"} × ${item.quantity}`).join(" · ");
    return (
      <div className="min-w-0">
        <div className="truncate text-[14px] font-medium" title={name}>
          {name}
        </div>
        {cargo && (
          <div className="hidden truncate text-[12px] text-[var(--muted-foreground)] xl:block" title={cargo}>
            {cargo}
          </div>
        )}
      </div>
    );
  }

  function bagsCell(row: Row) {
    if (row.kind === "session") {
      return <span className="tabular-nums">{row.session.last_status?.total ?? 0} / —</span>;
    }
    const { order, history } = row;
    const ordered = orderedBagCount(order);
    const loaded = order.bags_loaded ?? 0;
    const cameraTotal = history ? (history.final_total ?? history.last_status?.total ?? null) : null;
    const showHistory = canViewShipping && history && (order.status === "loaded" || order.status === "shipped");
    let main: ReactNode;
    if (order.status === "confirmed") main = <span className="tabular-nums">— / {ordered}</span>;
    else if (isLoadingStatus(order.status)) {
      const live = liveBags(row);
      const pct = ordered > 0 ? (live / ordered) * 100 : 0;
      main = (
        <div className="flex flex-col items-end gap-1">
          <span className="tabular-nums">
            {live} / {ordered}
          </span>
          <ProgressBar pct={pct} tone={live > ordered ? "destructive" : undefined} className="h-1 w-20" />
        </div>
      );
    } else if (order.status === "loaded") {
      main = (
        <span className="font-semibold tabular-nums">
          {loaded} / {ordered}
        </span>
      );
    } else main = <span className="tabular-nums">{loaded}</span>;
    return (
      <div className="flex flex-col items-end gap-0.5">
        {main}
        {showHistory && (
          <button
            type="button"
            onClick={() => setHistoryOpen(history)}
            className="flex items-center gap-1 text-[12px] tabular-nums text-[var(--muted-foreground)]"
          >
            камера: {cameraTotal ?? "—"}
            {history.has_recording && <Film className="size-3" />}
          </button>
        )}
      </div>
    );
  }

  function cameraCell(row: Row): { badge: ReactNode; name: string } {
    if (row.kind === "session") {
      const camera = camerasBySrc.get(row.session.camera);
      return { badge: sessionBadge(row.session, true), name: camera?.zone || camera?.name || row.session.camera };
    }
    const { order, session, history } = row;
    if (order.status === "shipped") {
      return {
        badge: null,
        name: history ? `${history.camera_name} · насчитано ${history.final_total ?? "—"}` : "—",
      };
    }
    if (!order.loading_camera) return { badge: null, name: "—" };
    const camera = camerasBySrc.get(order.loading_camera);
    return { badge: sessionBadge(session, true), name: camera?.zone || camera?.name || order.loading_camera };
  }

  function statusCell(row: Row, cameraLine: ReactNode) {
    let badge: ReactNode;
    let line2: ReactNode = null;
    const session = row.session;
    if (row.kind === "session" || isLoadingStatus(row.order.status)) {
      badge =
        session?.status === "starting" ? (
          <Badge tone="warning" dot>
            Запуск AI
          </Badge>
        ) : (
          <Badge tone="primary" dot>
            Погрузка
          </Badge>
        );
      if (session) line2 = `с ${formatTime(session.started_at)} · ${session.started_by_name || "—"}`;
    } else if (row.order.status === "confirmed") {
      badge = <Badge tone="outline">{row.order.transport_type === "train" ? "Вагон ожидает" : "Ожидает"}</Badge>;
    } else if (row.order.status === "loaded") {
      badge = (
        <Badge tone="warning" dot>
          Готов к выезду
        </Badge>
      );
    } else {
      badge = <Badge tone="success">Выехал</Badge>;
      if (row.order.shipped_at) line2 = formatDateTime(row.order.shipped_at);
    }
    return (
      <div className="flex flex-col items-start gap-1">
        {badge}
        {line2 && <span className="text-[12px] text-[var(--muted-foreground)]">{line2}</span>}
        <div className="xl:hidden">{cameraLine}</div>
      </div>
    );
  }

  function actionsCell(row: Row) {
    const { primary, note, menu } = rowActions(row);
    const busy = actions.busyOrderId === row.id;
    return (
      <div className="flex items-center justify-end gap-2">
        {primary ? (
          <div className="flex flex-col items-end gap-1">
            <Button className={primaryButtonClass} disabled={primary.disabled || busy} onClick={primary.onClick}>
              {primary.label}
            </Button>
            {primary.disabled && primary.hint && (
              // На планшете нет hover: причина блокировки видна текстом.
              <span className="max-w-[220px] text-right text-[11px] text-[var(--muted-foreground)]">
                {primary.hint}
              </span>
            )}
          </div>
        ) : (
          note && <span className="text-[12px] text-[var(--muted-foreground)]">{note}</span>
        )}
        <ActionMenu
          label={`Действия: ${row.kind === "order" ? `заказ #${row.order.id}` : `сессия #${row.session.id}`}`}
          items={menu}
          className={menuClass}
        />
      </div>
    );
  }

  function detailRow(row: Row) {
    const order = row.kind === "order" ? row.order : null;
    const session = row.session;
    const cameraSrc = order?.loading_camera ?? session?.camera ?? null;
    const camera = cameraSrc ? camerasBySrc.get(cameraSrc) : undefined;
    const train = order?.transport_type === "train";
    const canCount = !!order && (train ? canTrain : canLoad);
    const occupiedBy =
      order && !session && order.loading_camera ? (sessionsByCamera.get(order.loading_camera)?.order_id ?? null) : null;
    const { primary } = rowActions(row);
    const finish =
      primary?.label === "Завершить погрузку"
        ? { disabled: !!primary.disabled, hint: primary.hint, onClick: primary.onClick }
        : null;
    return (
      <ShippingRowDetail
        order={order}
        session={session}
        camera={camera}
        cameraSrc={cameraSrc}
        occupiedByOrderId={occupiedBy}
        canCount={canCount}
        canLoad={canLoad}
        isKiosk={isKiosk}
        busy={actions.busyOrderId === row.id}
        bagCounterRef={bagCounterRef}
        onSaveBags={order ? actions.saveBags(order) : () => Promise.resolve()}
        onAccept={(bags) =>
          order ? actions.act(order.id, () => actions.saveBags(order)(bags)) : Promise.resolve({ ok: true, error: "" })
        }
        onResetAi={() => session && openDialog({ kind: "reset", session, text: resetAiConfirmText(session.order_id) })}
        onStopAi={() => session && openDialog({ kind: "stopAi", session, text: stopAiConfirmText(session.order_id) })}
        onSessionChanged={() => void Promise.all([reloadOrders(), reloadSessions()])}
        finish={finish}
      />
    );
  }

  const emptyTitle = isKiosk ? "Нет заказов, готовых к погрузке" : "Нет заказов на посту";

  return (
    <Card className="rounded-lg p-0">
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <h3 className="text-[15px] font-semibold tracking-tight">Очередь отгрузки</h3>
        <span className="ml-auto text-[12px] text-[var(--muted-foreground)]">обновляется автоматически</span>
      </div>
      {actionError && (
        <div className="px-4 pt-3">
          <ErrorAlert message={actionError} />
        </div>
      )}
      <Table>
        <THead>
          <TR className="[&>th]:border-b [&>th]:border-[var(--border)]">
            <TH className="w-[140px] xl:w-[180px]">Транспорт</TH>
            <TH className="min-w-[160px] xl:min-w-[200px]">Клиент</TH>
            <TH className="w-[110px] text-right xl:w-[120px]">Мешки</TH>
            <TH className="hidden w-[180px] xl:table-cell">Камера / AI</TH>
            {/* Уже xl (киоск с сайдбаром) статус уходит под имя клиента, чтобы
                действия никогда не уезжали за край карточки. */}
            <TH className="hidden w-[140px] xl:table-cell">Статус</TH>
            <TH className="w-[160px] xl:w-[220px]">
              <span className="sr-only">Действия</span>
            </TH>
          </TR>
        </THead>
        <TBody>
          {orders === null ? (
            [0, 1, 2].map((index) => (
              <TR key={`skeleton-${index}`} aria-hidden>
                {Array.from({ length: COLUMN_COUNT }, (_, column) => (
                  <TD key={column} className={cn((column === 3 || column === 4) && "hidden xl:table-cell")}>
                    <span className="block h-3 w-3/4 rounded bg-[var(--muted)]" />
                  </TD>
                ))}
              </TR>
            ))
          ) : totalRows === 0 ? (
            <TR>
              <TD colSpan={COLUMN_COUNT} className="h-auto py-12 text-center">
                <div className="text-[14px]">{emptyTitle}</div>
                <div className="mt-1 text-[12px] text-[var(--muted-foreground)]">
                  Подтверждённые заказы появятся здесь автоматически
                </div>
              </TD>
            </TR>
          ) : (
            groups.map((group) => {
              if (!group.always && group.rows.length === 0) return null;
              return (
                <Fragment key={group.key}>
                  <tr>
                    <td
                      colSpan={COLUMN_COUNT}
                      className="h-9 border-b border-[var(--border)] bg-[var(--muted)]/40 px-3 text-[12px] font-medium text-[var(--muted-foreground)] sm:px-4"
                    >
                      {group.title} · {group.rows.length}
                    </td>
                  </tr>
                  {group.rows.length === 0 && (
                    <TR>
                      <TD colSpan={COLUMN_COUNT} className="text-[12px] text-[var(--muted-foreground)]">
                        Пусто
                      </TD>
                    </TR>
                  )}
                  {group.rows.map((row) => {
                    const open = expanded === row.key;
                    const camera = cameraCell(row);
                    const cameraLine = (
                      <div className="flex flex-wrap items-center gap-1.5">
                        {camera.badge}
                        <span className="truncate text-[12px] text-[var(--muted-foreground)]">{camera.name}</span>
                      </div>
                    );
                    return (
                      <Fragment key={row.key}>
                        <TR
                          onClick={(event) => onRowClick(event, row)}
                          className={cn(isExpandable(row) && "cursor-pointer", open && "bg-[var(--muted)]/30")}
                        >
                          <TD className={cellClass}>{transportCell(row)}</TD>
                          <TD className={cellClass}>
                            {clientCell(row)}
                            <div className="mt-1 xl:hidden">{statusCell(row, cameraLine)}</div>
                          </TD>
                          <TD className={cn(cellClass, "text-right")}>{bagsCell(row)}</TD>
                          <TD className={cn(cellClass, "hidden xl:table-cell")}>{cameraLine}</TD>
                          <TD className={cn(cellClass, "hidden xl:table-cell")}>{statusCell(row, cameraLine)}</TD>
                          <TD className={cellClass}>{actionsCell(row)}</TD>
                        </TR>
                        {open && (
                          <TR className="hover:bg-transparent">
                            <TD colSpan={COLUMN_COUNT} className="h-auto bg-[var(--muted)]/20 p-4">
                              {detailRow(row)}
                            </TD>
                          </TR>
                        )}
                      </Fragment>
                    );
                  })}
                </Fragment>
              );
            })
          )}
        </TBody>
      </Table>

      <StartShipmentModal
        order={startOrder}
        cameras={monoblockCameras}
        camerasBySrc={camerasBySrc}
        availability={availability}
        continuousDetail={continuousDetail}
        cameraLocked={cameraLocked || isKiosk}
        kioskCamera={kioskCamera}
        onClose={() => setStartOrder(null)}
        onStart={(order, cameraSrc) => actions.startAi(order, cameraSrc)}
      />
      <ConfirmDialog
        open={dialog !== null}
        onClose={() => !dialogBusy && setDialog(null)}
        title={dialog?.text.title ?? ""}
        description={dialog?.text.description}
        confirmLabel={dialog?.text.confirmLabel}
        confirmVariant={dialog?.text.confirmVariant}
        busy={dialogBusy}
        error={dialogError}
        onConfirm={() => void confirmDialog()}
      />
      <RewindLoadingModal
        order={rewindOrder}
        session={rewindOrder ? (sessionsByOrderId.get(rewindOrder.id) ?? null) : null}
        cameraName={rewindOrder?.loading_camera ? camerasBySrc.get(rewindOrder.loading_camera)?.name : undefined}
        onClose={() => setRewindOrder(null)}
        onConfirm={(order) => actions.executeMove(order, "waiting")}
      />
      <ShipmentRollbackModal
        order={rollbackOrder}
        onClose={() => setRollbackOrder(null)}
        onChanged={async () => {
          await Promise.all([reloadOrders(), reloadSessions(), reloadHistories?.()]);
        }}
      />
      <CountingHistoryModal history={historyOpen} onClose={() => setHistoryOpen(null)} />
    </Card>
  );
}
