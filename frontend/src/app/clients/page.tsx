"use client";
import { useMemo, useState } from "react";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import {
  Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { Badge } from "@/components/ui/badge";
import {
  Form, FormField, FormItem, FormLabel, FormControl, FormMessage,
} from "@/components/ui/form";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select-ui";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { cn, formatPhone, formatMoney } from "@/lib/utils";
import { COUNTRIES } from "@/lib/countries";
import {
  AlertTriangle, ArrowUpRight, BarChart3, CircleDollarSign, ClipboardList,
  Pencil, Phone, PieChart as PieChartIcon, Plus, Search, Trash2, Wallet,
} from "lucide-react";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  isFinancialOrderStatus, ORDER_STATUS_LABELS, ORDER_STATUS_TONE,
} from "@/lib/constants";
import type { Client, Order } from "@/lib/types";

const schema = z.object({
  first_name: z.string().min(2, "Введите имя (мин. 2 символа)"),
  last_name: z.string().min(2, "Введите фамилию (мин. 2 символа)"),
  phone: z
    .string()
    .refine((v) => v.replace(/\D/g, "").length === 11, "Введите номер полностью"),
  country: z.string().optional(),
  iin: z.string().optional().refine(
    (v) => !v || /^\d{12}$/.test(v), "ИИН/БИН — 12 цифр"
  ),
  bank: z.string().optional(),
  bank_account: z.string().optional(),
});
type FormValues = z.infer<typeof schema>;

const ORDER_STATUS_KEYS = Object.keys(ORDER_STATUS_LABELS);
const EMPTY_ORDERS: Order[] = [];
const STATUS_COLORS: Record<string, string> = {
  draft: "#8a8f98",
  pending: "#d49a32",
  confirmed: "#477fca",
  arrived: "#2f9ab7",
  loading: "#d28a28",
  loaded: "#5b67c9",
  shipped: "#5aa060",
  rejected: "#d85d57",
  cancelled: "#6f737a",
};

interface ClientSummary {
  ordersCount: number;
  revenue: number;
  paid: number;
  debt: number;
  rejected: number;
  average: number;
}

interface StatusSlice {
  status: string;
  label: string;
  count: number;
  amount: number;
  color: string;
}

interface MonthPoint {
  key: string;
  label: string;
  revenue: number;
  paid: number;
}

interface ClientAnalytics extends ClientSummary {
  statusData: StatusSlice[];
  monthData: MonthPoint[];
  lastOrders: Order[];
}

function isDebtOrder(order: Order): boolean {
  const remaining = Number(order.remaining_amount ?? (Number(order.total_amount) - Number(order.paid_total)));
  return order.is_debt ?? (order.status === "shipped" && order.settlement_intent === "debt" && remaining > 0);
}

function remainingAmount(order: Order): number {
  return Number(order.remaining_amount ?? (Number(order.total_amount) - Number(order.paid_total)));
}

function summarizeClientOrders(orders: Order[]): ClientSummary {
  const financialOrders = orders.filter((order) => isFinancialOrderStatus(order.status));
  const revenue = financialOrders.reduce((sum, order) => sum + Number(order.total_amount), 0);
  const paid = financialOrders.reduce((sum, order) => sum + Number(order.paid_total), 0);
  const debt = orders.filter((order) => isDebtOrder(order))
    .reduce((sum, order) => sum + remainingAmount(order), 0);
  const rejected = orders.filter((order) => order.status === "rejected").length;

  return {
    ordersCount: orders.length,
    revenue,
    paid,
    debt,
    rejected,
    average: financialOrders.length ? revenue / financialOrders.length : 0,
  };
}

function monthLabel(date: Date): string {
  return date.toLocaleDateString("ru-RU", { month: "short", year: "2-digit" });
}

function buildClientAnalytics(orders: Order[]): ClientAnalytics {
  const summary = summarizeClientOrders(orders);
  const statusMap = new Map<string, { count: number; amount: number }>();
  const monthMap = new Map<string, MonthPoint>();

  orders.forEach((order) => {
    const current = statusMap.get(order.status) ?? { count: 0, amount: 0 };
    statusMap.set(order.status, {
      count: current.count + 1,
      amount: current.amount + Number(order.total_amount),
    });

    if (isFinancialOrderStatus(order.status)) {
      const created = new Date(order.created_at);
      const key = `${created.getFullYear()}-${String(created.getMonth() + 1).padStart(2, "0")}`;
      const point = monthMap.get(key) ?? {
        key,
        label: monthLabel(created),
        revenue: 0,
        paid: 0,
      };
      point.revenue += Number(order.total_amount);
      point.paid += Number(order.paid_total);
      monthMap.set(key, point);
    }
  });

  const statusData = ORDER_STATUS_KEYS.map((status) => {
    const value = statusMap.get(status) ?? { count: 0, amount: 0 };
    return {
      status,
      label: ORDER_STATUS_LABELS[status] ?? status,
      count: value.count,
      amount: value.amount,
      color: STATUS_COLORS[status] ?? "#7a7f87",
    };
  }).filter((row) => row.count > 0);

  statusMap.forEach((value, status) => {
    if (ORDER_STATUS_KEYS.includes(status)) return;
    statusData.push({
      status,
      label: status,
      count: value.count,
      amount: value.amount,
      color: "#7a7f87",
    });
  });

  return {
    ...summary,
    statusData,
    monthData: [...monthMap.values()].sort((a, b) => a.key.localeCompare(b.key)).slice(-8),
    lastOrders: [...orders].sort((a, b) => (
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    )).slice(0, 5),
  };
}

function ClientForm({ onDone, onCancel, editing }: { onDone: () => void; onCancel: () => void; editing?: Client | null }) {
  const [serverError, setServerError] = useState("");
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: editing ? {
      first_name: editing.first_name, last_name: editing.last_name, phone: editing.phone,
      country: editing.country ?? "", iin: editing.iin ?? "",
      bank: editing.bank ?? "", bank_account: editing.bank_account ?? "",
    } : {
      first_name: "", last_name: "", phone: "", country: "",
      iin: "", bank: "", bank_account: "",
    },
  });

  async function onSubmit(values: FormValues) {
    setServerError("");
    try {
      if (editing) await api.patch(`/clients/${editing.id}/`, values);
      else await api.post("/clients/", values);
      onDone();
    } catch (e) {
      setServerError(apiError(e));
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)}
        className="grid grid-cols-1 gap-x-5 gap-y-5 sm:grid-cols-2">
        <FormField control={form.control} name="first_name" render={({ field }) => (
          <FormItem>
            <FormLabel>Имя</FormLabel>
            <FormControl><Input autoFocus placeholder="Иван" {...field} /></FormControl>
            <FormMessage />
          </FormItem>
        )} />

        <FormField control={form.control} name="last_name" render={({ field }) => (
          <FormItem>
            <FormLabel>Фамилия</FormLabel>
            <FormControl><Input placeholder="Петров" {...field} /></FormControl>
            <FormMessage />
          </FormItem>
        )} />

        <FormField control={form.control} name="phone" render={({ field }) => (
          <FormItem>
            <FormLabel>Номер телефона</FormLabel>
            <FormControl>
              <Input
                type="tel"
                inputMode="tel"
                placeholder="+7 (___) ___-__-__"
                value={field.value}
                onChange={(e) => field.onChange(formatPhone(e.target.value))}
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )} />

        <FormField control={form.control} name="country" render={({ field }) => (
          <FormItem>
            <FormLabel>Страна</FormLabel>
            <Select value={field.value} onValueChange={field.onChange}>
              <FormControl>
                <SelectTrigger>
                  <SelectValue placeholder="Выберите страну" />
                </SelectTrigger>
              </FormControl>
              <SelectContent>
                {COUNTRIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
              </SelectContent>
            </Select>
            <FormMessage />
          </FormItem>
        )} />

        <div className="sm:col-span-2 mt-1 border-t border-[var(--border)] pt-4 text-[12px] font-medium text-[var(--muted-foreground)]">
          Реквизиты
        </div>

        <FormField control={form.control} name="iin" render={({ field }) => (
          <FormItem>
            <FormLabel>ИИН / БИН</FormLabel>
            <FormControl>
              <Input inputMode="numeric" placeholder="12 цифр" maxLength={12}
                value={field.value}
                onChange={(e) => field.onChange(e.target.value.replace(/\D/g, "").slice(0, 12))} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )} />

        <FormField control={form.control} name="bank" render={({ field }) => (
          <FormItem>
            <FormLabel>Банк</FormLabel>
            <FormControl>
              <Input placeholder="напр. Halyk Bank" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )} />

        <FormField control={form.control} name="bank_account" render={({ field }) => (
          <FormItem className="sm:col-span-2">
            <FormLabel>Расчётный счёт (IBAN)</FormLabel>
            <FormControl>
              <Input placeholder="KZ…" {...field}
                onChange={(e) => field.onChange(e.target.value.toUpperCase())} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )} />

        {serverError && (
          <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)] sm:col-span-2">
            {serverError}
          </p>
        )}

        <div className="flex flex-col-reverse gap-2 border-t pt-5 sm:col-span-2 sm:flex-row sm:justify-end">
          <Button type="button" variant="outline" className="w-full sm:w-auto sm:min-w-28"
            onClick={onCancel}>Отмена</Button>
          <Button type="submit" className="w-full sm:w-auto sm:min-w-28"
            disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? "Сохранение…" : "Сохранить"}
          </Button>
        </div>
      </form>
    </Form>
  );
}

function ClientAnalyticsPanel({ client, analytics }: { client: Client | null; analytics: ClientAnalytics }) {
  const maxStatusCount = Math.max(1, ...analytics.statusData.map((row) => row.count));

  if (!client) {
    return (
      <Card className="min-h-[420px]">
        <CardContent className="flex h-full min-h-[420px] items-center justify-center text-sm text-[var(--muted-foreground)]">
          Клиентов пока нет.
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader className="pb-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
                Аналитика клиента
              </div>
              <CardTitle className="mt-1 text-xl">{client.name}</CardTitle>
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-[var(--muted-foreground)]">
                <span className="inline-flex items-center gap-1.5">
                  <Phone className="size-3.5" /> {client.phone || "—"}
                </span>
                <span>{client.country || "Страна не указана"}</span>
              </div>
            </div>
            {analytics.debt > 0 ? (
              <Link href={`/debts/clients/${client.id}`}>
                <Button size="sm" variant="outline">
                  Долг <ArrowUpRight className="size-4" />
                </Button>
              </Link>
            ) : (
              <Badge tone="success" dot>Без долга</Badge>
            )}
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <StatCard label="Принёс" value={`${formatMoney(analytics.revenue)} ₸`} accent icon={CircleDollarSign} />
          <StatCard label="Оплачено" value={`${formatMoney(analytics.paid)} ₸`} icon={Wallet} />
          <StatCard label="Текущий долг" value={`${formatMoney(analytics.debt)} ₸`} icon={AlertTriangle}
            caption={analytics.debt > 0 ? "по отгруженным заказам" : "нет непогашенных заказов"} />
          <StatCard label="Заказов" value={String(analytics.ordersCount)} icon={ClipboardList}
            caption={`Отклонено: ${analytics.rejected}`} />
          <StatCard label="Средний чек" value={`${formatMoney(analytics.average)} ₸`} icon={BarChart3} />
          <StatCard label="Отклонённые" value={String(analytics.rejected)} icon={AlertTriangle} />
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 2xl:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-center justify-between gap-3 pb-3">
            <CardTitle className="text-base">Динамика</CardTitle>
            <BarChart3 className="size-4 text-[var(--muted-foreground)]" />
          </CardHeader>
          <CardContent>
            {analytics.monthData.length === 0 ? (
              <p className="py-14 text-center text-sm text-[var(--muted-foreground)]">Нет финансовых заказов.</p>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={analytics.monthData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                    axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                    axisLine={false} tickLine={false} width={48}
                    tickFormatter={(value) => (Number(value) >= 1000 ? `${Math.round(Number(value) / 1000)}k` : String(value))} />
                  <Tooltip
                    contentStyle={{ background: "var(--card)", borderRadius: 12, border: "1px solid var(--border)", fontSize: 12 }}
                    formatter={(value: number, name) => [
                      `${formatMoney(value)} ₸`,
                      name === "revenue" ? "Принёс" : "Оплачено",
                    ]}
                  />
                  <Bar dataKey="revenue" fill="var(--ring)" radius={[5, 5, 0, 0]} />
                  <Bar dataKey="paid" fill="var(--success)" radius={[5, 5, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
            <div className="mt-3 flex gap-4 text-xs text-[var(--muted-foreground)]">
              <span className="flex items-center gap-1.5">
                <span className="size-2.5 rounded-full bg-[var(--ring)]" /> Принёс
              </span>
              <span className="flex items-center gap-1.5">
                <span className="size-2.5 rounded-full bg-[var(--success)]" /> Оплачено
              </span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between gap-3 pb-3">
            <CardTitle className="text-base">Статусы</CardTitle>
            <PieChartIcon className="size-4 text-[var(--muted-foreground)]" />
          </CardHeader>
          <CardContent>
            {analytics.statusData.length === 0 ? (
              <p className="py-14 text-center text-sm text-[var(--muted-foreground)]">Заказов нет.</p>
            ) : (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-[170px_minmax(0,1fr)]">
                <ResponsiveContainer width="100%" height={180}>
                  <PieChart>
                    <Pie data={analytics.statusData} dataKey="count" nameKey="label"
                      innerRadius={48} outerRadius={74} paddingAngle={3}>
                      {analytics.statusData.map((row) => (
                        <Cell key={row.status} fill={row.color} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{ background: "var(--card)", borderRadius: 12, border: "1px solid var(--border)", fontSize: 12 }}
                      formatter={(value: number, name) => [`${value} заказов`, name]}
                    />
                  </PieChart>
                </ResponsiveContainer>
                <div className="flex flex-col gap-2">
                  {analytics.statusData.map((row) => (
                    <div key={row.status} className="space-y-1">
                      <div className="flex items-center justify-between gap-2 text-xs">
                        <span className="flex items-center gap-1.5">
                          <span className="size-2 rounded-full" style={{ background: row.color }} />
                          {row.label}
                        </span>
                        <span className="tabular-nums text-[var(--muted-foreground)]">{row.count}</span>
                      </div>
                      <div className="h-1.5 rounded-full bg-[var(--muted)]">
                        <div className="h-full rounded-full" style={{
                          width: `${Math.max(8, (row.count / maxStatusCount) * 100)}%`,
                          background: row.color,
                        }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex-row items-center justify-between gap-3 pb-3">
          <CardTitle className="text-base">Последние заказы</CardTitle>
          <span className="text-xs text-[var(--muted-foreground)]">{analytics.lastOrders.length} из {analytics.ordersCount}</span>
        </CardHeader>
        <CardContent>
          {analytics.lastOrders.length === 0 ? (
            <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Заказов нет.</p>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Заказ</TH>
                  <TH>Статус</TH>
                  <TH>Сумма</TH>
                  <TH></TH>
                </TR>
              </THead>
              <TBody>
                {analytics.lastOrders.map((order) => (
                  <TR key={order.id}>
                    <TD>
                      <div className="font-medium">#{order.id}</div>
                      <div className="text-xs text-[var(--muted-foreground)]">
                        {new Date(order.created_at).toLocaleDateString("ru-RU", {
                          day: "2-digit", month: "2-digit", year: "2-digit",
                        })}
                      </div>
                    </TD>
                    <TD>
                      <Badge tone={ORDER_STATUS_TONE[order.status] ?? "muted"} dot>
                        {ORDER_STATUS_LABELS[order.status] ?? order.status}
                      </Badge>
                    </TD>
                    <TD className="tabular-nums">{formatMoney(order.total_amount)} ₸</TD>
                    <TD>
                      <Link href={`/orders/${order.id}`}>
                        <Button size="sm" variant="ghost">
                          <ArrowUpRight className="size-4" />
                        </Button>
                      </Link>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default function ClientsPage() {
  const { data: clients, reload } = useApi<Client[]>("/clients/");
  const { data: orders } = useApi<Order[]>("/orders/");
  const { me } = useAuth();
  const canEdit = can(me, "clients.edit");
  const canDelete = can(me, "clients.delete");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Client | null>(null);
  const [q, setQ] = useState("");
  const [sortKey, setSortKey] = useState("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [delItem, setDelItem] = useState<Client | null>(null);
  const [delError, setDelError] = useState("");
  const [delBusy, setDelBusy] = useState(false);

  async function confirmDelete() {
    if (!delItem) return;
    setDelBusy(true); setDelError("");
    try {
      await api.delete(`/clients/${delItem.id}/`);
      if (selectedId === delItem.id) setSelectedId(null);
      setDelItem(null); reload();
    } catch (e) { setDelError(apiError(e)); } finally { setDelBusy(false); }
  }

  const list = clients ?? [];
  const allOrders = orders ?? EMPTY_ORDERS;

  const ordersByClient = useMemo(() => {
    const map = new Map<number, Order[]>();
    allOrders.forEach((order) => {
      const rows = map.get(order.client) ?? [];
      rows.push(order);
      map.set(order.client, rows);
    });
    return map;
  }, [allOrders]);

  const clientSummary = useMemo(() => {
    const map = new Map<number, ClientSummary>();
    list.forEach((client) => {
      map.set(client.id, summarizeClientOrders(ordersByClient.get(client.id) ?? EMPTY_ORDERS));
    });
    return map;
  }, [list, ordersByClient]);

  const globalSummary = useMemo(() => {
    const revenue = allOrders
      .filter((order) => isFinancialOrderStatus(order.status))
      .reduce((sum, order) => sum + Number(order.total_amount), 0);
    const debtors = list.filter((client) => (clientSummary.get(client.id)?.debt ?? Number(client.debt_total ?? 0)) > 0).length;
    const activeClients = list.filter((client) => (clientSummary.get(client.id)?.ordersCount ?? 0) > 0).length;
    const rejected = allOrders.filter((order) => order.status === "rejected").length;
    return { revenue, debtors, activeClients, rejected };
  }, [allOrders, clientSummary, list]);

  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };
  const filtered = list.filter((c) => {
    if (!q) return true;
    return `${c.name} ${c.phone} ${c.country ?? ""}`.toLowerCase().includes(q.toLowerCase());
  });
  const sorted = [...filtered].sort((a, b) => {
    const aSummary = clientSummary.get(a.id) ?? summarizeClientOrders(EMPTY_ORDERS);
    const bSummary = clientSummary.get(b.id) ?? summarizeClientOrders(EMPTY_ORDERS);
    let av: string | number = a.name;
    let bv: string | number = b.name;
    if (sortKey === "phone") { av = a.phone; bv = b.phone; }
    if (sortKey === "orders") { av = aSummary.ordersCount; bv = bSummary.ordersCount; }
    if (sortKey === "revenue") { av = aSummary.revenue; bv = bSummary.revenue; }
    if (sortKey === "debt") { av = aSummary.debt; bv = bSummary.debt; }

    const cmp = typeof av === "number" && typeof bv === "number"
      ? av - bv
      : String(av).localeCompare(String(bv), "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  const selectedClient = list.find((client) => client.id === selectedId) ?? sorted[0] ?? null;
  const selectedOrders = selectedClient ? ordersByClient.get(selectedClient.id) ?? EMPTY_ORDERS : EMPTY_ORDERS;
  const selectedAnalytics = useMemo(() => buildClientAnalytics(selectedOrders), [selectedOrders]);

  return (
    <AppShell title="Клиенты" section="Работа" description="Клиентская база, оборот, долги и статусы заказов по каждому клиенту."
      actions={
        <Button size="sm" onClick={() => { setEditing(null); setOpen(true); }}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Добавить клиента</span>
        </Button>
      }>
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Всего клиентов" value={String(list.length)} />
        <StatCard label="С заказами" value={String(globalSummary.activeClients)} icon={ClipboardList} />
        <StatCard label="Выручка клиентов" value={`${formatMoney(globalSummary.revenue)} ₸`} accent icon={CircleDollarSign} />
        <StatCard label="Клиентов с долгом" value={String(globalSummary.debtors)} icon={AlertTriangle}
          caption={`Отклонено заказов: ${globalSummary.rejected}`} />
      </section>

      <div className="mb-4">
        <div className="relative max-w-xl flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск по имени, телефону, стране"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(460px,560px)]">
        <Card>
          <CardContent className="pt-6">
            <Table>
              <THead>
                <TR>
                  <SortableHeader label="Имя" sortKey="name" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <SortableHeader label="Телефон" sortKey="phone" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <TH>Страна</TH>
                  <SortableHeader label="Заказов" sortKey="orders" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <SortableHeader label="Принёс" sortKey="revenue" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <SortableHeader label="Долг" sortKey="debt" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <TH></TH>
                </TR>
              </THead>
              <TBody>
                {sorted.map((c) => {
                  const summary = clientSummary.get(c.id) ?? summarizeClientOrders(EMPTY_ORDERS);
                  const active = selectedClient?.id === c.id;
                  return (
                    <TR key={c.id} onClick={() => setSelectedId(c.id)}
                      className={cn("cursor-pointer", active && "bg-[var(--ring)]/8 hover:bg-[var(--ring)]/10")}>
                      <TD className="font-medium">
                        <div>{c.name}</div>
                        {summary.rejected > 0 && (
                          <div className="mt-1 text-xs text-[var(--destructive)]">
                            Отклонено: {summary.rejected}
                          </div>
                        )}
                      </TD>
                      <TD className="tabular-nums">{c.phone}</TD>
                      <TD>{c.country || "—"}</TD>
                      <TD className="tabular-nums">{summary.ordersCount}</TD>
                      <TD className="tabular-nums">{formatMoney(summary.revenue)} ₸</TD>
                      <TD className="tabular-nums">
                        {summary.debt > 0
                          ? <span className="font-medium text-[var(--destructive)]">{formatMoney(summary.debt)} ₸</span>
                          : <span className="text-[var(--muted-foreground)]">—</span>}
                      </TD>
                      <TD onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-end gap-1">
                          {canEdit && (
                            <Button size="sm" variant="ghost" onClick={() => { setEditing(c); setOpen(true); }} title="Изменить">
                              <Pencil className="size-4" />
                            </Button>
                          )}
                          {canDelete && (
                            <Button size="sm" variant="ghost"
                              className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                              onClick={() => { setDelError(""); setDelItem(c); }} title="Удалить">
                              <Trash2 className="size-4" />
                            </Button>
                          )}
                        </div>
                      </TD>
                    </TR>
                  );
                })}
                {sorted.length === 0 && (
                  <TR><TD colSpan={7} className="py-4 text-center text-[var(--muted-foreground)]">
                    Клиентов пока нет.</TD></TR>
                )}
              </TBody>
            </Table>
          </CardContent>
        </Card>

        <ClientAnalyticsPanel client={selectedClient} analytics={selectedAnalytics} />
      </div>

      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow={editing ? "Работа · Изменение" : "Работа · Клиент"}
        title={editing ? "Изменить клиента" : "Новый клиент"}
        description="Контакты и платёжные реквизиты клиента."
        className="max-w-xl">
        {open && (
          <ClientForm
            editing={editing}
            onCancel={() => setOpen(false)}
            onDone={() => { setOpen(false); reload(); }}
          />
        )}
      </Modal>

      <ConfirmDialog
        open={!!delItem}
        onClose={() => setDelItem(null)}
        title="Удалить клиента?"
        description={delItem ? `«${delItem.name}» будет удалён. Действие необратимо.` : ""}
        busy={delBusy}
        error={delError}
        onConfirm={confirmDelete}
      />
    </AppShell>
  );
}
