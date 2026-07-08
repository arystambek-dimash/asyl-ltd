"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { formatPlate } from "@/components/ui/license-plate-input";
import { StatusBadge } from "@/components/status-badge";
import { CameraStream } from "@/components/camera-stream";
import type { CameraFeed } from "@/components/camera-wall";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { cn, formatMoney } from "@/lib/utils";
import {
  CheckCircle2, Circle, LogOut, Minus, Package, Phone, Plus, Scale,
  Truck, User, VideoOff,
} from "lucide-react";
import type { Order } from "@/lib/types";

const QUEUE_STATUSES = ["confirmed", "arrived", "loading", "loaded"];
const POLL_MS = 10_000; // очередь и счётчик обновляются сами — пост «живой»

// Шаги поста: вес фиксируется при въезде, оплата — после отгрузки.
const STEPS = [
  { key: "arrive", label: "Прибытие" },
  { key: "load", label: "Погрузка" },
  { key: "exit", label: "Выезд" },
];
function stepIndex(status: string) {
  if (status === "confirmed") return 0;
  if (status === "arrived" || status === "loading") return 1;
  return 2; // loaded | shipped
}

function Stepper({ status }: { status: string }) {
  const current = stepIndex(status);
  return (
    <div className="flex items-center">
      {STEPS.map((s, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <div key={s.key} className="flex items-center">
            <div className="flex items-center gap-1.5">
              {done
                ? <CheckCircle2 className="size-5 text-[var(--success)]" />
                : <Circle className={cn("size-5",
                    active ? "text-[var(--primary)]" : "text-[var(--muted-foreground)]/40")}
                    {...(active ? { fill: "currentColor", fillOpacity: 0.15 } : {})} />}
              <span className={cn("text-sm",
                active ? "font-semibold" : done ? "text-[var(--success)]" : "text-[var(--muted-foreground)]")}>
                {s.label}
              </span>
            </div>
            {i < STEPS.length - 1 && (
              <div className={cn("mx-3 h-0.5 w-8 rounded-full sm:w-14",
                i < current ? "bg-[var(--success)]" : "bg-[var(--border)]")} />
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Живая камера поста: зона подбирается по шагу, можно переключить вручную. */
function PostCamera({ cameras, tokenReady, zoneKeywords }: {
  cameras: CameraFeed[];
  tokenReady: boolean;
  zoneKeywords: string[];
}) {
  const auto = useMemo(() => {
    for (const kw of zoneKeywords) {
      const hit = cameras.find((c) => c.zone.toLowerCase().includes(kw));
      if (hit) return hit;
    }
    return cameras[0];
  }, [cameras, zoneKeywords]);
  const [manualId, setManualId] = useState<number | null>(null);
  const cam = cameras.find((c) => c.id === manualId) ?? auto;
  const [online, setOnline] = useState(false);

  return (
    <div className="overflow-hidden rounded-xl border bg-[#1c1c1e]">
      <div className="relative aspect-video">
        {cam && tokenReady && (
          <CameraStream key={cam.id} src={cam.src} onStateChange={setOnline}
            className="absolute inset-0 h-full w-full object-cover" />
        )}
        {!online && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 text-white/30">
            <VideoOff className="size-6" />
            <span className="text-xs">{cam ? "Нет сигнала" : "Камеры недоступны"}</span>
          </div>
        )}
        {cam && (
          <div className="absolute inset-x-0 bottom-0 flex items-center justify-between bg-gradient-to-t from-black/70 to-transparent px-3 pb-2 pt-6">
            <span className="text-xs font-medium text-white">{cam.zone}</span>
            <span className={cn("size-1.5 rounded-full", online ? "bg-emerald-400" : "bg-white/30")} />
          </div>
        )}
      </div>
      {cameras.length > 1 && (
        <div className="border-t border-white/10 p-2">
          <Select value={String(cam?.id ?? "")}
            onChange={(e) => setManualId(Number(e.target.value))}
            className="h-8 border-0 bg-transparent text-xs text-white/70">
            {cameras.map((c) => <option key={c.id} value={c.id}>{c.zone}</option>)}
          </Select>
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

  // Если счёт обновил кто-то другой (или камера) — подтягиваем при поллинге,
  // но не перетираем то, что контролёр набирает прямо сейчас.
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
    <div className="flex flex-col gap-3">
      <div className="flex items-end justify-between">
        <div>
          <div className="text-xs text-[var(--muted-foreground)]">Погружено мешков</div>
          <div className="text-5xl font-bold tabular-nums leading-tight">
            {bags}
            <span className="ml-2 text-lg font-normal text-[var(--muted-foreground)]">/ {ordered}</span>
          </div>
        </div>
        <span className={cn("text-sm tabular-nums",
          pct >= 100 ? "font-semibold text-[var(--success)]" : "text-[var(--muted-foreground)]")}>
          {pct}%{saving ? " · сохранение…" : ""}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-[var(--muted)]">
        <div className={cn("h-full rounded-full transition-all",
          pct >= 100 ? "bg-[var(--success)]" : "bg-[var(--primary)]")}
          style={{ width: `${pct}%` }} />
      </div>
      {/* планшет: крупные кнопки */}
      <div className="grid grid-cols-3 gap-2">
        <Button variant="outline" className="h-14 text-lg" disabled={bags <= 0}
          onClick={() => change(-1)} aria-label="Минус один мешок">
          <Minus className="size-5" />
        </Button>
        <Button variant="outline" className="h-14 text-lg" onClick={() => change(1)}>
          <Plus className="size-5" /> 1
        </Button>
        <Button variant="outline" className="h-14 text-lg" onClick={() => change(5)}>
          <Plus className="size-5" /> 5
        </Button>
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
    </div>
  );
}

function ShippingPageInner() {
  const { me } = useAuth();
  const { data: orders, reload } = useApi<Order[]>("/orders/");
  const { data: cameras } = useApi<CameraFeed[]>("/cameras/");
  const [tokenReady, setTokenReady] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [weighIn, setWeighIn] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // cookie-доступ к потокам go2rtc; без неё nginx отдаст 403
  useEffect(() => {
    api.post("/cameras/token/").then(() => setTokenReady(true)).catch(() => setTokenReady(false));
  }, []);

  // Пост — «живой» экран: очередь и счётчики обновляются сами.
  useEffect(() => {
    const t = setInterval(() => {
      if (!document.hidden) reload();
    }, POLL_MS);
    return () => clearInterval(t);
  }, [reload]);

  // Очередь FIFO со стабильным порядком: API не гарантирует сортировку,
  // а прыжки карточек (и «выбранного») после каждого обновления недопустимы.
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

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); await reload(); }
    catch (e) { setError(apiError(e)); }
    finally { setBusy(false); }
  }

  return (
    <AppShell title="Пост погрузки" section="Работа"
      description="Планшет контролёра: выберите машину на весах, примите вес, следите за погрузкой по камере и выпускайте.">
      {queue.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border bg-[var(--card)] py-16 text-center">
          <Truck className="size-8 text-[var(--muted-foreground)]/50" />
          <p className="text-sm text-[var(--muted-foreground)]">
            Нет машин в очереди. Заказы появляются после подтверждения.
          </p>
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[340px_1fr]">
          {/* Очередь машин: крупные карточки под палец */}
          <div className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">
            {queue.map((o) => {
              const active = selected?.id === o.id;
              return (
                <button key={o.id} onClick={() => { setSelectedId(o.id); setWeighIn(""); setError(""); }}
                  className={cn(
                    "min-w-[240px] shrink-0 rounded-xl border bg-[var(--card)] p-4 text-left transition-colors lg:min-w-0",
                    active ? "border-[var(--primary)] ring-2 ring-[var(--primary)]/30"
                      : "hover:border-[var(--ring)]/50"
                  )}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-lg font-bold tabular-nums">
                      {o.truck_number ? formatPlate(o.truck_number) : `Заказ #${o.id}`}
                    </span>
                    <StatusBadge status={o.status} dot />
                  </div>
                  <div className="mt-1 text-sm text-[var(--muted-foreground)]">
                    {o.client_name || "—"} · #{o.id}
                  </div>
                  <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                    {o.items.reduce((s, it) => s + Number(it.quantity), 0)} меш. · {formatMoney(o.total_amount)} ₸
                  </div>
                </button>
              );
            })}
          </div>

          {/* Выбранная машина */}
          {selected && (
            <div className="flex flex-col gap-4 rounded-xl border bg-[var(--card)] p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-2xl font-bold tabular-nums">
                    {selected.truck_number ? formatPlate(selected.truck_number) : `Заказ #${selected.id}`}
                  </div>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-[var(--muted-foreground)]">
                    <span className="flex items-center gap-1.5"><User className="size-3.5" /> {selected.client_name || "—"}</span>
                    {selected.client_phone && (
                      <span className="flex items-center gap-1.5"><Phone className="size-3.5" /> {selected.client_phone}</span>
                    )}
                  </div>
                </div>
                <Stepper status={selected.status} />
              </div>

              <div className="flex flex-wrap gap-x-5 gap-y-1 rounded-lg border bg-[var(--muted)]/30 px-4 py-3 text-sm">
                <span className="flex items-center gap-1.5">
                  <Package className="size-4 text-[var(--muted-foreground)]" />
                  {selected.items.map((it) => `${it.product_label ?? "Товар"} × ${it.quantity}`).join(" · ")}
                </span>
                {selected.weigh_in_kg && (
                  <span className="flex items-center gap-1.5">
                    <Scale className="size-4 text-[var(--muted-foreground)]" />
                    Вес на въезде: <b className="tabular-nums">{formatMoney(selected.weigh_in_kg)} кг</b>
                  </span>
                )}
              </div>

              <div className="grid gap-4 xl:grid-cols-2">
                {/* Камера шага: весы на прибытии, зона погрузки дальше */}
                <PostCamera cameras={cameras ?? []} tokenReady={tokenReady}
                  zoneKeywords={selected.status === "confirmed" ? ["вес", "въезд"] : ["загруз"]} />

                {/* Действие текущего шага */}
                <div className="flex flex-col justify-center gap-3">
                  {selected.status === "confirmed" && (
                    canArrive ? (
                      <>
                        <div className="text-sm text-[var(--muted-foreground)]">
                          Машина встала на весы — введите вес с индикатора.
                        </div>
                        <Input type="number" inputMode="numeric" placeholder="Вес КАМАЗа, кг"
                          className="h-14 text-2xl tabular-nums" value={weighIn}
                          onChange={(e) => setWeighIn(e.target.value)} />
                        <Button className="h-14 text-base" disabled={busy || !weighIn}
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
                        <Button className="h-14 text-base" disabled={busy}
                          onClick={() => act(async () => {
                            if (selected.status === "arrived") {
                              await api.post(`/orders/${selected.id}/load/`, { bags: selected.bags_loaded ?? 0 });
                            }
                            await api.post(`/orders/${selected.id}/finish-loading/`, {});
                          })}>
                          <CheckCircle2 className="size-5" /> Погрузка завершена
                        </Button>
                      </>
                    ) : <p className="text-sm text-[var(--muted-foreground)]">Идёт погрузка.</p>
                  )}

                  {selected.status === "loaded" && (
                    canShip ? (
                      <>
                        <div className="text-sm text-[var(--muted-foreground)]">
                          Погружено <b className="tabular-nums text-[var(--foreground)]">{selected.bags_loaded ?? 0}</b> меш.
                          — выпускайте машину.
                        </div>
                        <Button className="h-14 text-base" disabled={busy}
                          onClick={() => act(() => api.post(`/orders/${selected.id}/ship/`, {}))}>
                          <LogOut className="size-5" /> Отгрузить (выезд)
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
