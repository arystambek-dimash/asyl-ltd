"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Button } from "@/components/ui/button";
import { PlateBadge } from "@/components/ui/license-plate-input";
import { CameraStream } from "@/components/camera-stream";
import { ErrorAlert } from "@/components/ui/data-state";
import { playableCameras, type CameraFeed } from "@/components/camera-wall";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { cn, formatMoney } from "@/lib/utils";
import {
  Cctv, Check, LogOut, Minus, Package, Phone, Plus, RotateCcw, Scale, Truck,
  User, VideoOff,
} from "lucide-react";
import type { Order } from "@/lib/types";
import { useAiCounter, type AiCounter } from "@/lib/use-ai-counter";

const QUEUE_STATUSES = ["confirmed", "arrived", "loading", "loaded"];
const POLL_MS = 10_000; // очередь и счётчик обновляются сами — пост «живой»

// Этапы поста и их цвета: ожидает въезда → погрузка → готов к выезду.
const STAGES = {
  confirmed: { label: "Ожидает въезда", color: "var(--ring)" },
  arrived: { label: "Погрузка", color: "var(--warning)" },
  loading: { label: "Погрузка", color: "var(--warning)" },
  loaded: { label: "Готов к выезду", color: "var(--success)" },
} as const;

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
function PostCamera({ cameras, zoneKeywords, ai }: {
  /** Только играбельные камеры (locked пост не показывает). */
  cameras: (CameraFeed & { src: string })[];
  zoneKeywords: string[];
  /** Работающий AI-подсчёт: на этой камере показываем аннотированный поток. */
  ai?: { camId: string; src: string } | null;
}) {
  const auto = useMemo(() => {
    for (const kw of zoneKeywords) {
      const hit = cameras.find((c) => c.zone.toLowerCase().includes(kw));
      if (hit) return hit;
    }
    return cameras[0];
  }, [cameras, zoneKeywords]);
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

/** Крупный счётчик мешков под палец: −/+1/+5, автосохранение с дебаунсом. */
function BagCounter({ order, onSaved }: { order: Order; onSaved: () => void }) {
  const [bags, setBags] = useState(order.bags_loaded ?? 0);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSaved = useRef(order.bags_loaded ?? 0);

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
      await api.post(`/orders/${order.id}/load/`, { bags: value });
      lastSaved.current = value;
      onSaved();
    } catch (e) { setError(apiError(e)); }
    finally { setSaving(false); }
  }, [order.id, onSaved]);

  function change(delta: number) {
    setBags((prev) => {
      const next = Math.max(0, prev + delta);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => { timer.current = null; save(next); }, 700);
      return next;
    });
  }

  const ordered = order.items.reduce((s, it) => s + Number(it.quantity), 0);
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

  const warming = st.status !== "онлайн";
  const total = st.total ?? 0;
  return (
    <div className="rounded-xl border border-emerald-600/25 bg-emerald-500/[0.06] p-3.5">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-emerald-600">
          <span className={cn("size-1.5 rounded-full bg-emerald-500", !warming && "animate-pulse")} />
          AI-подсчёт{warming && " · запуск модели…"}
        </span>
        <button onClick={() => ai.stop().catch(() => {})} disabled={ai.busy}
          className="text-xs text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]">
          Выключить
        </button>
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
        <Button variant="outline" className="h-11 rounded-lg px-3" disabled={ai.busy}
          onClick={() => ai.reset().catch(() => {})} aria-label="Обнулить AI-счётчик"
          title="Начать счёт заново">
          <RotateCcw className="size-4" />
        </Button>
      </div>
      {ai.error && <p className="mt-2 text-sm text-[var(--destructive)]">{ai.error}</p>}
    </div>
  );
}

function ShippingPageInner() {
  const { me } = useAuth();
  const { data: orders, error: loadError, reload } = useApi<Order[]>("/orders/");
  const { data: cameras, reload: reloadCameras } = useApi<CameraFeed[]>("/cameras/");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [weighIn, setWeighIn] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Инвентарь камер сам восстанавливается после сбоя MediaMTX/Tailscale.
  // Последний успешный список остаётся в useApi, пока очередной запрос падает;
  // CameraStream самостоятельно получает и обновляет cookie видеопотока.
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

  // Пост — «живой» экран: очередь и счётчики обновляются сами.
  useEffect(() => {
    const t = setInterval(() => {
      if (!document.hidden) reload();
    }, POLL_MS);
    return () => clearInterval(t);
  }, [reload]);

  // Очередь FIFO со стабильным порядком: прыжки карточек недопустимы.
  const queue = (orders ?? [])
    .filter((o) => o.transport_type !== "train" && QUEUE_STATUSES.includes(o.status))
    .sort((a, b) => a.id - b.id);
  const selected = queue.find((o) => o.id === selectedId) ?? queue[0] ?? null;

  // Фиксируем выбор явно, чтобы обновление очереди не переключало машину.
  useEffect(() => {
    if (selected && selected.id !== selectedId) setSelectedId(selected.id);
  }, [selected, selectedId]);

  const canArrive = can(me, "shipping.arrive");
  const canLoad = can(me, "shipping.load");
  const canShip = can(me, "shipping.ship");

  // Пост работает только с играбельными камерами; locked-устройства — тема
  // дашборда, контролёру они не нужны.
  const playable = useMemo(() => playableCameras(cameras), [cameras]);
  // AI-подсчёт привязан к камере зоны загрузки, а не к той, что контролёр
  // листает в моменте: переключение вида не должно прятать счётчик.
  const aiCam = useMemo(
    () => playable.find((c) => c.zone.toLowerCase().includes("загруз")) ?? playable[0] ?? null,
    [playable],
  );
  const isLoadStep = !!selected
    && (selected.status === "arrived" || selected.status === "loading") && canLoad;
  const ai = useAiCounter(aiCam?.src ?? null, selected?.id ?? null, isLoadStep);
  // Плеер переключаем на аннотированный поток, только когда модель в эфире
  // (на прогреве поток ещё 404 — держим обычную картинку).
  const aiLive = ai.running && ai.status?.status === "онлайн";

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); await reload(); }
    catch (e) { setError(apiError(e)); }
    finally { setBusy(false); }
  }

  const stage = selected ? STAGES[selected.status as keyof typeof STAGES] : null;

  return (
    <AppShell title="Пост погрузки" section="Работа">
      {loadError && !orders ? (
        <ErrorAlert message={loadError} onRetry={reload} />
      ) : queue.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed py-20 text-center">
          <span className="flex size-14 items-center justify-center rounded-2xl bg-[var(--muted)]">
            <Truck className="size-7 text-[var(--muted-foreground)]" />
          </span>
          <div className="text-base font-semibold">Нет машин в очереди</div>
          <p className="max-w-sm text-sm text-[var(--muted-foreground)]">
            Подтверждённые заказы с машиной появятся здесь автоматически.
          </p>
        </div>
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[300px_1fr]">
          {/* Очередь машин */}
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between px-1 pb-1">
              <span className="text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                Очередь
              </span>
              <span className="rounded-full bg-[var(--muted)] px-2 py-0.5 text-xs font-semibold tabular-nums">
                {queue.length}
              </span>
            </div>
            <div className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">
              {queue.map((o) => {
                const active = selected?.id === o.id;
                const st = STAGES[o.status as keyof typeof STAGES];
                return (
                  <button key={o.id}
                    onClick={() => { setSelectedId(o.id); setWeighIn(""); setError(""); }}
                    style={{ borderLeftColor: st?.color }}
                    className={cn(
                      "min-w-[230px] shrink-0 rounded-xl border border-l-4 bg-[var(--card)] p-3.5 text-left transition-all lg:min-w-0",
                      active
                        ? "shadow-md ring-2 ring-[var(--foreground)]/80"
                        : "hover:shadow-sm"
                    )}>
                    <div className="flex items-center justify-between gap-2">
                      <PlateBadge value={o.truck_number} />
                      <span className="text-xs font-medium" style={{ color: st?.color }}>
                        {st?.label}
                      </span>
                    </div>
                    <div className="mt-2 truncate text-sm font-medium">{o.client_name || "—"}</div>
                    <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                      Заказ #{o.id} · {o.items.reduce((s, it) => s + Number(it.quantity), 0)} меш.
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Рабочая зона выбранной машины */}
          {selected && (
            <div className="overflow-hidden rounded-2xl border bg-[var(--card)] shadow-card">
              {/* шапка: номер как госзнак + этапы */}
              <div className="flex flex-wrap items-center justify-between gap-4 border-b px-5 py-4">
                <div className="flex flex-wrap items-center gap-4">
                  <PlateBadge value={selected.truck_number} size="lg" />
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
                <StageTrack status={selected.status} />
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
              <div className="grid gap-4 p-5 xl:grid-cols-[1.4fr_1fr]">
                <PostCamera cameras={playable}
                  zoneKeywords={selected.status === "confirmed" ? ["вес", "въезд"] : ["загруз"]}
                  ai={aiLive && aiCam
                    ? { camId: aiCam.id, src: ai.status?.stream ?? `${aiCam.src}ai` }
                    : null} />

                <div className="flex flex-col justify-center gap-4 rounded-2xl bg-[var(--muted)]/40 p-5">
                  {selected.status === "confirmed" && (
                    canArrive ? (
                      <>
                        <div>
                          <div className="text-base font-semibold">Машина на весах</div>
                          <p className="mt-0.5 text-sm text-[var(--muted-foreground)]">
                            Введите вес с индикатора и примите машину.
                          </p>
                        </div>
                        <div className="relative">
                          <input type="number" inputMode="numeric" placeholder="0"
                            value={weighIn} onChange={(e) => setWeighIn(e.target.value)}
                            className="h-20 w-full rounded-xl border bg-[var(--card)] pl-5 pr-16 text-right text-5xl font-bold tabular-nums outline-none focus:ring-[3px] focus:ring-[var(--ring)]/40" />
                          <span className="absolute right-5 top-1/2 -translate-y-1/2 text-lg text-[var(--muted-foreground)]">кг</span>
                        </div>
                        <Button className="h-14 rounded-xl text-base" disabled={busy || !weighIn}
                          onClick={() => act(() => api.post(`/orders/${selected.id}/arrive/`, { weigh_in_kg: weighIn }))}>
                          <Scale className="size-5" /> Принять машину
                        </Button>
                      </>
                    ) : <p className="text-sm text-[var(--muted-foreground)]">Ожидает приёма машины.</p>
                  )}

                  {(selected.status === "arrived" || selected.status === "loading") && (
                    canLoad ? (
                      <>
                        <BagCounter key={selected.id} order={selected} onSaved={reload} />
                        {aiCam && (
                          <AiCounterPanel ai={ai} accepted={selected.bags_loaded ?? 0}
                            onAccept={(bags) =>
                              act(() => api.post(`/orders/${selected.id}/load/`, { bags }))} />
                        )}
                        <Button className="h-14 rounded-xl text-base" disabled={busy}
                          onClick={() => act(async () => {
                            if (selected.status === "arrived") {
                              await api.post(`/orders/${selected.id}/load/`, { bags: selected.bags_loaded ?? 0 });
                            }
                            await api.post(`/orders/${selected.id}/finish-loading/`, {});
                            // Погрузка закрыта — освобождаем GPU; сбой стопа не мешает выезду.
                            if (ai.running) await ai.stop().catch(() => {});
                          })}>
                          <Check className="size-5" /> Погрузка завершена
                        </Button>
                      </>
                    ) : <p className="text-sm text-[var(--muted-foreground)]">Идёт погрузка.</p>
                  )}

                  {selected.status === "loaded" && (
                    canShip ? (
                      <>
                        <div>
                          <div className="text-base font-semibold">Готов к выезду</div>
                          <p className="mt-0.5 text-sm text-[var(--muted-foreground)]">
                            Проверьте машину и выпускайте.
                          </p>
                        </div>
                        <div className="grid grid-cols-2 gap-2">
                          <div className="rounded-xl border bg-[var(--card)] p-3">
                            <div className="text-xs text-[var(--muted-foreground)]">Погружено</div>
                            <div className="text-2xl font-bold tabular-nums">{selected.bags_loaded ?? 0} <span className="text-sm font-normal">меш.</span></div>
                          </div>
                          <div className="rounded-xl border bg-[var(--card)] p-3">
                            <div className="text-xs text-[var(--muted-foreground)]">Вес на въезде</div>
                            <div className="text-2xl font-bold tabular-nums">
                              {selected.weigh_in_kg ? formatMoney(selected.weigh_in_kg) : "—"} <span className="text-sm font-normal">кг</span>
                            </div>
                          </div>
                        </div>
                        <Button className="h-14 rounded-xl text-base" disabled={busy}
                          onClick={() => act(() => api.post(`/orders/${selected.id}/ship/`, {}))}>
                          <LogOut className="size-5" /> Отгрузить — выезд
                        </Button>
                      </>
                    ) : <p className="text-sm text-[var(--muted-foreground)]">Готов к выезду.</p>
                  )}

                  {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </AppShell>
  );
}

export default function ShippingPage() {
  return <RequirePerm perm="shipping.view" title="Пост погрузки"><ShippingPageInner /></RequirePerm>;
}
