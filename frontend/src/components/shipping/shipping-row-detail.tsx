"use client";

import { useEffect, useState, type Ref } from "react";
import { Check, Package, Phone, RefreshCw, RotateCcw, Square, VideoOff } from "lucide-react";
import { CameraCountingLineOverlay } from "@/components/camera-counting-line-overlay";
import { CameraStream } from "@/components/camera-stream";
import type { CameraFeed } from "@/components/camera-wall";
import { DetectionOverlay } from "@/components/detection-overlay";
import { BagCounter, type BagCounterHandle } from "@/components/shipping/bag-counter";
import type { ShippingActionResult } from "@/components/shipping/use-shipping-actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { resolveCountingLine } from "@/lib/camera-counting-line";
import { orderedBagCount } from "@/lib/orders";
import { bagColor, isAiOnlineStatus } from "@/lib/shipping-cameras";
import type { AiCountingSession, Order } from "@/lib/types";
import { useAiCounter } from "@/lib/use-ai-counter";
import { cn, formatDateTime, formatMoney } from "@/lib/utils";

// Рамка старше этого времени описывает уже уехавший мешок — гасим её, чтобы
// она не висела на пустом месте при обрыве связи или остановке модели.
const DETECTIONS_STALE_MS = 2_500;

export interface ShippingRowDetailProps {
  /** Заказ строки; null — сессия, к заказу которой нет доступа. */
  order: Order | null;
  session: AiCountingSession | null;
  /** Камера из инвентаря по loading_camera / session.camera (зона, линия). */
  camera?: CameraFeed;
  /** Поток базовой камеры camN; null — камера не назначена. */
  cameraSrc: string | null;
  /** Заказ, чья сессия занимает камеру, когда у этой строки сессии нет. */
  occupiedByOrderId?: number | null;
  /** Ручной счёт и «Принять N»: (грузовик && shipping.load) || (вагон && train.load). */
  canCount: boolean;
  /** Команды сессии (обнулить/выключить/перезапуск) — shipping.load. */
  canLoad: boolean;
  isKiosk: boolean;
  /** Действие строки уже выполняется — кнопки панели заблокированы. */
  busy: boolean;
  /** Владелец таблицы читает `saveNow()` перед завершением погрузки. */
  bagCounterRef: Ref<BagCounterHandle>;
  onSaveBags: (bags: number) => Promise<unknown>;
  onAccept: (bags: number) => Promise<ShippingActionResult>;
  onResetAi: () => void;
  onStopAi: () => void;
  /** Перезапуск/отмена/восстановление сессии выполнены — перечитать заказы и сессии. */
  onSessionChanged: () => void;
  /** Главная кнопка «Завершить погрузку»; null — по правам недоступна. */
  finish: { disabled: boolean; hint?: string; onClick: () => void } | null;
}

/** Раскрытая строка очереди: живое видео, AI-блок, ручной счёт и завершение. */
export function ShippingRowDetail({
  order,
  session,
  camera,
  cameraSrc,
  occupiedByOrderId = null,
  canCount,
  canLoad,
  isKiosk,
  busy,
  bagCounterRef,
  onSaveBags,
  onAccept,
  onResetAi,
  onStopAi,
  onSessionChanged,
  finish,
}: ShippingRowDetailProps) {
  // Вторая точность живого счёта: свёрнутые строки показывают
  // session.last_status.total из опроса сессий (лаг ≤3 с), раскрытая — этот
  // счётчик (500 мс). Он монтируется только здесь и только при сессии.
  const ai = useAiCounter(session?.camera ?? null, session?.order_id ?? null, !!session);
  const [streamOnline, setStreamOnline] = useState(false);
  const [acceptError, setAcceptError] = useState("");

  const live = !!ai.status?.running;
  const total = ai.status?.total ?? session?.last_status?.total ?? 0;
  const orderTarget = order ? orderedBagCount(order) : 0;
  const target = orderTarget > 0 ? orderTarget : null;
  const goalReached = target !== null && total >= target;
  const canStop = ai.status?.can_stop ?? session?.can_stop ?? false;
  // camNai зависит от отдельного RTSP publisher на ПК цеха. Счётчик может
  // продолжать работать, когда этот publisher переподключается, и тогда
  // карточка становилась полностью чёрной. Базовый camN уже контролируется
  // сервером камер; рамки и линию собираем браузером поверх него.
  const stream = camera?.src ?? cameraSrc;
  const countingLine = resolveCountingLine(ai.status, camera?.line_config);
  const detectionRevision = ai.status?.last_frame_at ?? null;
  const [detectionFreshness, setDetectionFreshness] = useState<{ revision: string | null; at?: number }>({
    revision: null,
  });
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

  const isStarting = session?.status === "starting";
  const needsRecovery =
    isStarting || ai.status?.code === "ai_reconciliation_required" || ai.status?.code === "ai_processor_stopped";
  const warming = !isAiOnlineStatus(ai.status?.status);
  const accepted = order?.bags_loaded ?? 0;
  const aiLabel = !session
    ? "AI не запущен"
    : ai.stale
      ? "Связь потеряна"
      : goalReached
        ? "Цель достигнута"
        : live
          ? "AI считает"
          : needsRecovery
            ? "Требует запуска"
            : "Запуск";
  const chipLabel = streamOnline ? aiLabel : "Подключение видео";
  const chipDot = !streamOnline
    ? "bg-amber-400"
    : !session
      ? "bg-white/50"
      : ai.stale
        ? "bg-red-400"
        : goalReached || live
          ? "bg-emerald-400"
          : "bg-amber-400";

  async function runCommand(command: () => Promise<void>) {
    try {
      await command();
    } catch {
      // useAiCounter показывает нормализованную ошибку внутри AI-блока.
    } finally {
      onSessionChanged();
    }
  }

  async function accept() {
    setAcceptError("");
    const result = await onAccept(total);
    if (!result.ok) setAcceptError(result.error);
  }

  const items = order?.items ?? [];
  const sessionButton = cn("h-8", isKiosk && "h-10");
  const perColor = ai.status?.per_color ?? session?.last_status?.per_color;

  return (
    <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
      {/* Видео базовой камеры с рамками и линией подсчёта поверх */}
      {stream ? (
        <div className="relative aspect-video max-h-[360px] w-full max-w-[640px] overflow-hidden rounded-lg bg-[#172033] xl:max-h-none">
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
              {countingLine && (
                <CameraCountingLineOverlay line={countingLine.line} direction={countingLine.direction} />
              )}
            </>
          ) : (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-white/35">
              <VideoOff className="size-6" />
              <span className="text-[12px]">Подключаем прямой поток…</span>
            </div>
          )}
          <span className="absolute left-3 top-3 flex items-center gap-1.5 rounded-md bg-black/50 px-2 py-1 text-[11px] text-white">
            <span className={cn("size-1.5 rounded-full", chipDot)} />
            {chipLabel}
          </span>
          <div className="absolute inset-x-0 bottom-0 flex items-end justify-between gap-3 bg-gradient-to-t from-black/70 to-transparent px-3 pb-3 pt-8 text-white">
            <span className="truncate text-[14px] font-medium">{camera?.zone || camera?.name || stream}</span>
            {session && (
              <span className="shrink-0 text-3xl font-semibold tabular-nums leading-none">
                {total}
                {target !== null && <span className="text-base text-white/60"> / {target}</span>}
              </span>
            )}
          </div>
        </div>
      ) : (
        <div className="flex aspect-video max-h-[360px] w-full max-w-[640px] items-center justify-center rounded-lg border border-dashed text-[12px] text-[var(--muted-foreground)]">
          Камера не назначена
        </div>
      )}

      <div className="flex min-w-0 flex-col gap-3">
        {order ? (
          <div className="flex flex-col gap-1.5">
            <div className="text-[14px] font-medium">{order.client_name || "Без клиента"}</div>
            {order.client_phone && (
              <a
                href={`tel:${order.client_phone}`}
                className="flex w-fit items-center gap-1.5 text-[12px] text-[var(--muted-foreground)]"
              >
                <Phone className="size-3" /> {order.client_phone}
              </a>
            )}
            {items.length > 0 && (
              <ul className="flex flex-col gap-1 text-[14px]">
                {items.map((item, index) => (
                  <li key={item.id ?? `item-${index}`} className="flex items-center gap-1.5">
                    <Package className="size-3.5 shrink-0 text-[var(--muted-foreground)]" />
                    <span className="min-w-0 truncate">{item.product_label ?? "Товар"}</span>
                    <span className="tabular-nums text-[var(--muted-foreground)]">× {item.quantity}</span>
                  </li>
                ))}
              </ul>
            )}
            {order.transport_type !== "train" && order.weigh_in_kg && (
              <div className="text-[12px] tabular-nums text-[var(--muted-foreground)]">
                Учётный вес {formatMoney(order.weigh_in_kg)} кг
              </div>
            )}
          </div>
        ) : (
          session && (
            <div className="flex flex-col gap-1">
              <div className="text-[14px] font-medium">{session.order_client_name || "Без клиента"}</div>
              <div className="text-[12px] text-[var(--muted-foreground)]">
                Заказ #{session.order_id} · нет доступа к заказу
              </div>
            </div>
          )
        )}

        {session && (
          <div className="flex flex-col gap-2 rounded-lg border bg-[var(--card)] p-3">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="text-4xl font-semibold tabular-nums leading-none">{total}</span>
              <span className="text-[12px] text-[var(--muted-foreground)]">меш. камерой</span>
              {(ai.status?.weight ?? 0) > 0 && (
                <span className="ml-auto text-[12px] tabular-nums text-[var(--muted-foreground)]">
                  ≈ {formatMoney(ai.status!.weight!)} кг
                </span>
              )}
            </div>
            {perColor && Object.keys(perColor).length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(perColor).map(([color, n]) => (
                  <span
                    key={color}
                    className="flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[12px] tabular-nums"
                  >
                    <span className="size-2 rounded-full" style={{ background: bagColor(color) }} />
                    {color.replace(/_/g, " ")} · {n}
                  </span>
                ))}
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2 text-[12px] text-[var(--muted-foreground)]">
              <span>
                запустил {session.started_by_name || "другой сотрудник"} · {formatDateTime(session.started_at)}
              </span>
              {ai.stale && (
                <Badge tone="destructive" dot>
                  связь потеряна
                </Badge>
              )}
            </div>

            {canStop && canLoad ? (
              <div className="flex flex-wrap gap-2 pt-1">
                {isStarting ? (
                  <>
                    <Button
                      variant="outline"
                      size="sm"
                      className={sessionButton}
                      disabled={ai.busy || busy}
                      onClick={() => void runCommand(() => ai.start(session.id))}
                    >
                      <RefreshCw className="size-3.5" /> Повторить запуск
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className={sessionButton}
                      disabled={ai.busy || busy}
                      onClick={() => void runCommand(() => ai.stop(false, session.id))}
                    >
                      <Square className="size-3.5" /> Отменить запуск
                    </Button>
                  </>
                ) : (
                  <>
                    {needsRecovery && (
                      <Button
                        variant="outline"
                        size="sm"
                        className={sessionButton}
                        disabled={ai.busy || busy}
                        onClick={() => void runCommand(() => ai.start(session.id))}
                      >
                        <RefreshCw className="size-3.5" /> Восстановить AI-счётчик
                      </Button>
                    )}
                    {/* Принимается только явно: на прогреве счёт ещё нулевой. */}
                    {order && canCount && (
                      <Button
                        variant="outline"
                        size="sm"
                        className={sessionButton}
                        disabled={busy || ai.busy || !live || warming || total === accepted}
                        onClick={() => void accept()}
                      >
                        <Check className="size-3.5" /> Принять {total}
                      </Button>
                    )}
                    {canLoad && session.status === "active" && (
                      <>
                        <Button
                          variant="outline"
                          size="sm"
                          className={sessionButton}
                          disabled={busy || ai.busy}
                          onClick={onResetAi}
                        >
                          <RotateCcw className="size-3.5" /> Обнулить AI-счёт
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          className={sessionButton}
                          disabled={busy || ai.busy}
                          onClick={onStopAi}
                        >
                          <Square className="size-3.5" /> Выключить AI-подсчёт
                        </Button>
                      </>
                    )}
                  </>
                )}
              </div>
            ) : !canStop ? (
              <p className="text-[12px] text-[var(--muted-foreground)]">
                Управлять сессией может {session.started_by_name || "другой сотрудник"} или администратор
              </p>
            ) : null}
            {(ai.error || acceptError) && (
              <p role="alert" className="text-[12px] text-[var(--destructive)]">
                {ai.error || acceptError}
              </p>
            )}
          </div>
        )}

        {!session && occupiedByOrderId != null && (
          <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-900">
            AI-подсчёт занят — считается заказ #{occupiedByOrderId}
          </p>
        )}

        {order && canCount && <BagCounter ref={bagCounterRef} key={order.id} order={order} onSave={onSaveBags} />}

        {finish && (
          <div className="flex flex-col gap-1">
            <Button
              className="h-12 w-full"
              disabled={finish.disabled || busy}
              title={finish.hint}
              onClick={finish.onClick}
            >
              <Check className="size-4" /> Завершить погрузку
            </Button>
            {finish.disabled && finish.hint && (
              <p className="text-center text-[12px] text-[var(--muted-foreground)]">{finish.hint}</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
