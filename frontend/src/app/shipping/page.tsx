"use client";
import { useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { formatPlate } from "@/components/ui/license-plate-input";
import { StatusBadge } from "@/components/status-badge";
import { useApi } from "@/lib/use-api";
import { can } from "@/lib/can";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import {
  Truck, ChevronDown, User, Phone, Package, Scale, CheckCircle2, Circle,
  AlertTriangle, Upload,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Order, VideoJob } from "@/lib/types";

// 6 классов мешков (совпадают с cv_class товаров и классами детектора).
const CLASS_ORDER = ["Red_50", "Red_25", "Green_50", "Green_25", "Blue_50", "Blue_25"];
const CLASS_META: Record<string, { label: string; dot: string }> = {
  Red_50: { label: "Красный 50 кг", dot: "#dc2626" },
  Red_25: { label: "Красный 25 кг", dot: "#dc2626" },
  Green_50: { label: "Зелёный 50 кг", dot: "#16a34a" },
  Green_25: { label: "Зелёный 25 кг", dot: "#16a34a" },
  Blue_50: { label: "Синий 50 кг", dot: "#2563eb" },
  Blue_25: { label: "Синий 25 кг", dot: "#2563eb" },
};

// Ожидание по заказу: cv_class → сколько мешков заказано.
function expectedByClass(order: Order): Record<string, number> {
  const exp: Record<string, number> = {};
  for (const it of order.items) {
    if (it.cv_class) exp[it.cv_class] = (exp[it.cv_class] ?? 0) + it.quantity;
  }
  return exp;
}

// Разбивка «посчитано / заказано» по классам мешков.
function BagBreakdown({ order, counts }: { order: Order; counts: Record<string, number> }) {
  const expected = expectedByClass(order);
  const keys = CLASS_ORDER.filter((k) => expected[k] || counts[k]);
  if (keys.length === 0) {
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    return total > 0
      ? <div className="text-center text-sm text-[var(--muted-foreground)]">
          Классы не распознаны · всего {total}
        </div>
      : null;
  }
  return (
    <div className="flex flex-col gap-1.5">
      {keys.map((k) => {
        const got = counts[k] ?? 0;
        const exp = expected[k] ?? 0;
        const done = exp > 0 && got >= exp;
        const over = exp > 0 && got > exp;
        return (
          <div key={k} className="flex items-center gap-2 text-sm">
            <span className="size-2.5 shrink-0 rounded-full" style={{ background: CLASS_META[k]?.dot }} />
            <span className="flex-1">{CLASS_META[k]?.label ?? k}</span>
            <span className={cn("tabular-nums font-medium",
              over ? "text-[var(--warning)]" : done ? "text-[var(--success)]" : "text-[var(--foreground)]")}>
              {got}{exp > 0 && <span className="text-[var(--muted-foreground)]"> / {exp}</span>}
            </span>
            {done && !over && <CheckCircle2 className="size-4 text-[var(--success)]" />}
            {over && <AlertTriangle className="size-4 text-[var(--warning)]" />}
          </div>
        );
      })}
    </div>
  );
}

function VideoCounter({ order }: { order: Order }) {
  const orderId = order.id;
  const fileRef = useRef<HTMLInputElement>(null);
  const [job, setJob] = useState<VideoJob | null>(null);
  const [bags, setBags] = useState<number | null>(null);
  const [byClass, setByClass] = useState<Record<string, number>>({});
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const { data } = await api.get<VideoJob[]>(`/video-jobs/?order=${orderId}`);
        if (alive && data.length) setJob(data[0]);
      } catch { /* ignore */ }
    };
    tick();
    const t = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(t); };
  }, [orderId]);

  useEffect(() => {
    if (job?.status !== "processing") return;
    let alive = true;
    const cnt = async () => {
      try {
        const cams = await api.get("/cameras/");
        const counter = (cams.data as { id: number; kind: string; status: string }[])
          .find((c) => c.kind === "counter" && c.status === "active");
        if (!counter) return;
        const { data } = await api.get(`/count/${counter.id}/`);
        if (alive) { setBags(data.bags); setByClass(data.by_class ?? {}); }
      } catch { /* ignore */ }
    };
    cnt();
    const t = setInterval(cnt, 1500);
    return () => { alive = false; clearInterval(t); };
  }, [job?.status]);

  async function upload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true); setError("");
    try {
      const fd = new FormData();
      fd.append("video", file);
      await api.post(`/orders/${orderId}/upload-video/`, fd,
        { headers: { "Content-Type": "multipart/form-data" } });
    } catch (err) { setError(apiError(err)); } finally { setUploading(false); }
    if (fileRef.current) fileRef.current.value = "";
  }

  const statusLabel: Record<string, string> = {
    queued: "В очереди", processing: "Обработка…", done: "Готово", failed: "Ошибка",
  };
  const doneCounts = job?.counts_by_class ?? {};

  return (
    <div className="flex flex-col gap-2 rounded-lg border bg-[var(--card)] p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">Видео загрузки</span>
        {job && <Badge tone={job.status === "done" ? "success"
          : job.status === "failed" ? "destructive"
          : job.status === "processing" ? "warning" : "muted"}>
          {statusLabel[job.status]}</Badge>}
      </div>

      {job?.status === "processing" && (
        <div className="flex flex-col items-center gap-3">
          {/* Живой поток с разметкой модели (MJPEG). Необязателен: при ошибке
              картинка скрывается, число остаётся. */}
          <img
            src={`${process.env.NEXT_PUBLIC_API_URL}/video-jobs/${job.id}/stream/`}
            alt="Обработка видео"
            className="w-full max-w-md rounded-lg border bg-black/5"
            onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
          />
          <div className="text-center">
            <div className="text-4xl font-bold tabular-nums">{bags ?? 0}</div>
            <div className="text-xs text-[var(--muted-foreground)]">мешков посчитано</div>
          </div>
          <div className="w-full max-w-md">
            <BagBreakdown order={order} counts={byClass} />
          </div>
        </div>
      )}
      {job?.status === "done" && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-[var(--success)]">Готово: {job.bags_counted} мешков записано.</p>
          <BagBreakdown order={order} counts={doneCounts} />
        </div>
      )}
      {job?.status === "failed" && (
        <p className="text-sm text-[var(--destructive)]">Ошибка обработки: {job.error || "—"}</p>
      )}

      <input ref={fileRef} type="file" accept="video/mp4,video/avi,video/quicktime"
        className="hidden" onChange={upload} />
      <Button size="sm" variant="outline" disabled={uploading}
        onClick={() => fileRef.current?.click()}>
        <Upload className="size-4" /> {uploading ? "Загрузка…" : "Загрузить видео"}
      </Button>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
    </div>
  );
}

const QUEUE_STATUSES = ["paid", "arrived", "loading", "loaded"];

// шаги жизненного цикла на посту
const STEPS = [
  { key: "paid", label: "Оплачен" },
  { key: "arrived", label: "Прибытие" },
  { key: "loading", label: "Загрузка" },
  { key: "shipped", label: "Выезд" },
];
function stepIndex(status: string) {
  if (status === "confirmed") return 0;
  if (status === "loaded") return 2; // «Загрузка» завершена, ждём выезд
  const i = STEPS.findIndex((s) => s.key === status);
  return i < 0 ? 0 : i;
}

function Stepper({ status, compact = false }: { status: string; compact?: boolean }) {
  const current = stepIndex(status);
  return (
    <div className="flex items-center">
      {STEPS.map((s, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <div key={s.key} className="flex items-center">
            <div className="flex flex-col items-center gap-1">
              {done ? (
                <CheckCircle2 className={cn(compact ? "size-3.5" : "size-5", "text-[var(--success)]")} />
              ) : (
                <Circle className={cn(compact ? "size-3.5" : "size-5",
                  active ? "text-[var(--primary)]" : "text-[var(--muted-foreground)]/40")}
                  {...(active ? { fill: "currentColor", fillOpacity: 0.15 } : {})} />
              )}
              {!compact && (
                <span className={cn("text-[11px]",
                  active ? "font-medium text-[var(--foreground)]"
                    : done ? "text-[var(--success)]" : "text-[var(--muted-foreground)]")}>
                  {s.label}
                </span>
              )}
            </div>
            {i < STEPS.length - 1 && (
              <div className={cn(compact ? "mx-1 w-4" : "mx-2 w-10 mb-4", "h-0.5 rounded-full",
                i < current ? "bg-[var(--success)]" : "bg-[var(--border)]")} />
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function ShippingPage() {
  const { data: orders, reload } = useApi<Order[]>("/orders/");
  const { me } = useAuth();
  const isBoss = can(me, "shipping.debt_override");
  const [openId, setOpenId] = useState<number | null>(null);

  const queue = (orders ?? []).filter((o) =>
    QUEUE_STATUSES.includes(o.status) || (o.status === "confirmed" && isBoss)
  );

  return (
    <AppShell title="Пост отгрузки" section="Работа" description="Очередь машин на отгрузку: прибытие, загрузка, выезд и расчёт нетто по весам.">
      <div className="mb-4 flex items-center gap-2 text-sm">
        <Truck className="size-4 text-[var(--muted-foreground)]" />
        <span className="text-[var(--muted-foreground)]">Очередь машин:</span>
        <span className="font-semibold">{queue.length}</span>
      </div>

      {queue.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-[var(--muted-foreground)]">
            Нет машин в очереди. Заказы появляются здесь после оплаты.
          </CardContent>
        </Card>
      ) : (
        <div className="flex flex-col gap-3">
          {queue.map((o) => (
            <QueueRow key={o.id} order={o} isBoss={!!isBoss}
              open={openId === o.id}
              onToggle={() => setOpenId(openId === o.id ? null : o.id)}
              onChange={reload} />
          ))}
        </div>
      )}
    </AppShell>
  );
}

// Сравнение фактического веса груза (выезд − въезд) с ожидаемым (мешки × вес).
// Авторитетный расчёт делает бэкенд (eventlog); здесь — предпросмотр для оператора.
function WeightCompare({ order, weighOut }: { order: Order; weighOut: string }) {
  const inKg = order.weigh_in_kg ? Number(order.weigh_in_kg) : null;
  const out = weighOut ? Number(weighOut) : null;
  if (inKg === null || out === null || Number.isNaN(out)) return null;
  const cargo = Math.abs(out - inKg);
  const estimate = Number(order.bag_estimate_kg ?? 0);
  const diff = cargo - estimate;
  const big = estimate > 0 && Math.abs(diff) > estimate * 0.05; // порог 5%
  return (
    <div className={cn("rounded-md px-3 py-2 text-xs",
      big ? "bg-[var(--destructive)]/10 text-[var(--destructive)]"
          : "bg-[var(--muted)]/40 text-[var(--muted-foreground)]")}>
      Вес груза: <b>{formatMoney(cargo)} кг</b> · Ожидалось:{" "}
      <b>{formatMoney(estimate)} кг</b> · Расхождение:{" "}
      <b>{diff > 0 ? "+" : ""}{formatMoney(diff)} кг</b>
      {big && " — большое расхождение"}
    </div>
  );
}

function QueueRow({
  order, isBoss, open, onToggle, onChange,
}: {
  order: Order; isBoss: boolean; open: boolean;
  onToggle: () => void; onChange: () => void;
}) {
  const [weighIn, setWeighIn] = useState("");
  const [weighOut, setWeighOut] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const needsPayWarn = !order.is_fully_paid;

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); onChange(); }
    catch (e) { setError(apiError(e)); }
    finally { setBusy(false); }
  }

  return (
    <Card className="overflow-hidden">
      {/* строка (свёрнуто) */}
      <button onClick={onToggle}
        className="flex w-full items-center gap-4 px-5 py-4 text-left transition-colors hover:bg-[var(--muted)]/40">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-[var(--secondary)]">
          <Truck className="size-5 text-[var(--muted-foreground)]" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-semibold tabular-nums">{order.truck_number ? formatPlate(order.truck_number) : `Заказ #${order.id}`}</span>
            <span className="text-sm text-[var(--muted-foreground)]">· {order.client_name || "—"}</span>
          </div>
          <div className="text-xs text-[var(--muted-foreground)]">
            #{order.id} · {formatMoney(order.total_amount)} ₸
          </div>
        </div>
        <div className="hidden sm:block"><Stepper status={order.status} compact /></div>
        <StatusBadge status={order.status} />
        <ChevronDown className={cn("size-4 text-[var(--muted-foreground)] transition-transform",
          open && "rotate-180")} />
      </button>

      {/* детали (раскрыто) */}
      {open && (
        <div className="border-t bg-[var(--muted)]/20 px-5 py-5">
          {/* большой stepper */}
          <div className="mb-5 flex justify-center">
            <Stepper status={order.status} />
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            {/* инфо */}
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-2 text-sm">
                <User className="size-4 text-[var(--muted-foreground)]" />
                <span className="font-medium">{order.client_name || "—"}</span>
              </div>
              <div className="flex items-center gap-2 text-sm text-[var(--muted-foreground)]">
                <Phone className="size-4" /> {order.client_phone || "—"}
              </div>
              <div className="flex items-start gap-2 text-sm">
                <Package className="mt-0.5 size-4 text-[var(--muted-foreground)]" />
                <div className="flex flex-col gap-0.5">
                  {order.items.map((it, i) => (
                    <span key={i}>{it.product_label || `Товар #${it.product}`}
                      <span className="text-[var(--muted-foreground)]"> × {it.quantity} меш.</span>
                    </span>
                  ))}
                </div>
              </div>
              {(order.weigh_in_kg || order.weigh_out_kg) && (
                <div className="flex flex-wrap gap-4 border-t pt-3 text-sm">
                  {order.weigh_in_kg && (
                    <span className="flex items-center gap-1.5">
                      <Scale className="size-4 text-[var(--muted-foreground)]" />
                      Въезд: <span className="tabular-nums font-medium">{formatMoney(order.weigh_in_kg)} кг</span>
                    </span>
                  )}
                  {order.net_weight_kg && (
                    <span>Нетто: <span className="tabular-nums font-medium text-[var(--success)]">{formatMoney(order.net_weight_kg)} кг</span></span>
                  )}
                </div>
              )}
              {needsPayWarn && order.status !== "shipped" && (
                <div className="flex items-center gap-2 rounded-md bg-[var(--warning)]/12 px-3 py-2 text-xs text-[var(--warning)]">
                  <AlertTriangle className="size-4 shrink-0" />
                  Заказ не оплачен. {isBoss ? "Можно отгрузить в долг." : "Въезд запрещён без оплаты."}
                </div>
              )}
            </div>

            {/* действие текущего шага */}
            <div className="flex flex-col gap-3 rounded-lg border bg-[var(--card)] p-4">
              {/* Прибытие: номер уже в заказе, вес приходит датчиком (вебхук).
                  Ручное поле веса — fallback для теста без датчика. */}
              {(order.status === "paid" || order.status === "confirmed") && (
                <>
                  <Label>Прибытие машины</Label>
                  <div className="text-sm text-[var(--muted-foreground)]">
                    Номер: <b className="text-[var(--foreground)] tabular-nums">
                      {order.truck_number ? formatPlate(order.truck_number) : "—"}</b>
                  </div>
                  <Input type="number" placeholder="Вес въезда, кг (или с датчика)" value={weighIn}
                    onChange={(e) => setWeighIn(e.target.value)} />
                  <Button disabled={busy || !weighIn}
                    onClick={() => act(() => api.post(`/orders/${order.id}/arrive/`, {
                      weigh_in_kg: weighIn,
                      debt_override: needsPayWarn && isBoss,
                    }))}>
                    {needsPayWarn && isBoss ? "Принять (в долг)" : "Принять машину"}
                  </Button>
                </>
              )}

              {/* Прибыл: загрузка начинается с загрузки видео (start_loading). */}
              {order.status === "arrived" && (
                <>
                  <Label>Загрузка</Label>
                  <p className="text-xs text-[var(--muted-foreground)]">
                    Загрузите видео — система начнёт считать мешки.
                  </p>
                  <VideoCounter order={order} />
                </>
              )}

              {/* Идёт загрузка: живой счётчик + кнопка завершения. */}
              {order.status === "loading" && (
                <>
                  <VideoCounter order={order} />
                  <Button disabled={busy}
                    onClick={() => act(() => api.post(`/orders/${order.id}/finish-loading/`, {}))}>
                    Загрузка завершена
                  </Button>
                </>
              )}

              {/* Загрузка завершена: вес выезда + сравнение. */}
              {order.status === "loaded" && (
                <>
                  <Label>Выезд</Label>
                  <div className="text-sm text-[var(--muted-foreground)]">
                    Посчитано мешков: <b className="text-[var(--foreground)] tabular-nums">
                      {order.bags_loaded ?? 0}</b>
                  </div>
                  <Input type="number" placeholder="Вес выезда, кг" value={weighOut}
                    onChange={(e) => setWeighOut(e.target.value)} />
                  <WeightCompare order={order} weighOut={weighOut} />
                  <Button disabled={busy || !weighOut}
                    onClick={() => act(() => api.post(`/orders/${order.id}/ship/`, { weigh_out_kg: weighOut }))}>
                    Отгрузить (выезд)
                  </Button>
                </>
              )}

              {order.status === "shipped" && (
                <>
                  <Label>Отгружено</Label>
                  <div className="text-sm">
                    Нетто: <b className="tabular-nums text-[var(--success)]">
                      {order.net_weight_kg ? `${formatMoney(order.net_weight_kg)} кг` : "—"}</b>
                  </div>
                  <WeightCompare order={order} weighOut={order.weigh_out_kg ?? ""} />
                </>
              )}

              {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}
