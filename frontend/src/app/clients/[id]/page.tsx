"use client";
import { use, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Tabs } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/status-badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { DataGate } from "@/components/ui/data-state";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select-ui";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { currencySymbol, formatCurrency, formatDateTime } from "@/lib/utils";
import { StatementExportModal } from "@/components/statement-export-modal";
import {
  ORDER_STATUS_LABELS, PAYMENT_METHOD_LABELS, PAYMENT_STAGE_LABELS, PAYMENT_STAGE_TONE,
  orderStatusGroup,
} from "@/lib/constants";
import {
  AlertCircle,
  ArrowLeft,
  FileText,
  MapPin,
  Phone,
  SlidersHorizontal,
  TrendingUp,
  Wallet,
  X,
  FileSpreadsheet,
} from "lucide-react";

interface SaleRow {
  id: number; date: string; status: string; payment_status: string;
  settlement_intent: string; items: { label: string; qty: number }[];
  bags: number; amount: string; paid: string;
}
interface PaymentRow {
  id: number; order_id: number; date: string; employee: string | null;
  method: string; status: string; amount: string;
}
interface DebtRow {
  id: number; date: string; bags: number;
  amount: string; paid: string; remaining: string;
}
interface History {
  client: { id: number; name: string; phone: string; country: string; currency: "KZT" | "USD" };
  summary: { revenue: string; paid: string; debt: string; orders_count: number };
  sales: SaleRow[];
  payments: PaymentRow[];
  debts: DebtRow[];
}

const SETTLEMENT_LABELS: Record<string, string> = { debt: "В долг", instant: "Сразу" };

/** Общие для всех вкладок поля строки — по ним работает единый фильтр. */
interface CommonRow { id: number; date: string; amount: string }

function uniq(values: (string | null | undefined)[]): string[] {
  return [...new Set(values.filter((v): v is string => !!v))].sort((a, b) => a.localeCompare(b, "ru"));
}

function itemsText(items: { label: string; qty: number }[]): string {
  return items.map((i) => `${i.label} × ${i.qty}`).join(", ");
}

function DocLink({ orderId, children }: { orderId: number; children: React.ReactNode }) {
  return (
    <Link href={`/orders/${orderId}`} className="font-medium text-[var(--ring)] hover:underline">
      {children}
    </Link>
  );
}

function EmptyRow({ colSpan, filtered, onReset }: {
  colSpan: number;
  filtered: boolean;
  onReset: () => void;
}) {
  return (
    <TR>
      <TD colSpan={colSpan} className="py-12 text-center">
        <div className="mx-auto flex max-w-sm flex-col items-center">
          <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-[var(--muted)] text-[var(--muted-foreground)]">
            <FileText className="size-5" />
          </div>
          <div className="font-medium">{filtered ? "Документы не найдены" : "Документов пока нет"}</div>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            {filtered
              ? "Попробуйте изменить условия поиска или сбросить фильтры."
              : "Здесь появятся документы клиента после первой операции."}
          </p>
          {filtered && (
            <Button size="sm" variant="outline" className="mt-4" onClick={onReset}>
              <X className="size-4" /> Сбросить фильтры
            </Button>
          )}
        </div>
      </TD>
    </TR>
  );
}

function Money({ value, currency, muted }: { value: string; currency: string; muted?: boolean }) {
  if (muted && Number(value) === 0) return <span className="text-[var(--muted-foreground)]">—</span>;
  return <>{formatCurrency(value, currency)}</>;
}

function ClientDetailPageInner({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useAuth();
  const canExport = can(me, "reports.export");
  const { data, loading, error, reload } = useApi<History>(`/clients/${id}/history/`);

  const [tab, setTab] = useState("analytics");
  // Общие фильтры — переживают смену вкладки.
  const [fFrom, setFFrom] = useState("");
  const [fTo, setFTo] = useState("");
  const [fDoc, setFDoc] = useState("");
  const [fMin, setFMin] = useState("");
  const [fMax, setFMax] = useState("");
  // Вкладочные фильтры — сбрасываются при переключении.
  const [fProduct, setFProduct] = useState("all");
  const [fPay, setFPay] = useState("all");
  const [fStatus, setFStatus] = useState("all");
  const [fEmployee, setFEmployee] = useState("all");
  const [sortKey, setSortKey] = useState("date");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [statementOpen, setStatementOpen] = useState(false);

  if (!data) {
    return <AppShell title="Клиент"><DataGate loading={loading} error={error} onRetry={reload} /></AppShell>;
  }

  const { client, summary } = data;
  const symbol = currencySymbol(client.currency);
  const hasDebt = Number(summary.debt) > 0;
  const initials = client.name.trim().split(/\s+/).slice(0, 2)
    .map((part) => part[0]).join("").toUpperCase() || "К";
  const tabs = [
    { key: "analytics", label: "Аналитика клиента" },
    { key: "sales", label: `Продажи · ${data.sales.length}` },
    { key: "payments", label: `Погашения · ${data.payments.length}` },
    { key: "debts", label: `Долги · ${data.debts.length}` },
  ];

  const switchTab = (k: string) => {
    setTab(k); setFProduct("all"); setFPay("all"); setFStatus("all"); setFEmployee("all");
  };
  const hasFilters = !!fFrom || !!fTo || !!fDoc || !!fMin || !!fMax
    || fProduct !== "all" || fPay !== "all" || fStatus !== "all" || fEmployee !== "all";
  const resetFilters = () => {
    setFFrom(""); setFTo(""); setFDoc(""); setFMin(""); setFMax("");
    setFProduct("all"); setFPay("all"); setFStatus("all"); setFEmployee("all");
  };

  const matches = (r: CommonRow) =>
    (!fFrom || r.date.slice(0, 10) >= fFrom)
    && (!fTo || r.date.slice(0, 10) <= fTo)
    && (!fDoc.trim() || String(r.id).includes(fDoc.replace(/\D/g, "")))
    && (!fMin || Number(r.amount) >= Number(fMin))
    && (!fMax || Number(r.amount) <= Number(fMax));

  const sortRows = <T extends CommonRow>(rows: T[]) => [...rows].sort((a, b) => {
    const cmp = sortKey === "amount" ? Number(a.amount) - Number(b.amount) : a.date.localeCompare(b.date);
    return sortDir === "asc" ? cmp : -cmp;
  });
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("desc"); }
  };

  const sales = sortRows(data.sales.filter((r) =>
    matches(r)
    && (fProduct === "all" || r.items.some((i) => i.label === fProduct))
    && (fPay === "all" || r.settlement_intent === fPay)
    && (fStatus === "all" || orderStatusGroup(r.status) === fStatus)));
  // № документа у погашения — заказ, к которому оно привязано.
  const payments = sortRows(data.payments.filter((r) =>
    matches({ ...r, id: r.order_id })
    && (fPay === "all" || r.method === fPay)
    && (fStatus === "all" || r.status === fStatus)
    && (fEmployee === "all" || r.employee === fEmployee)));
  const debts = sortRows(data.debts.filter(matches));

  const products = uniq(data.sales.flatMap((r) => r.items.map((i) => i.label)));
  const saleStatuses = uniq(data.sales.map((r) => orderStatusGroup(r.status)));
  const paymentStatuses = uniq(data.payments.map((r) => r.status));
  const employees = uniq(data.payments.map((r) => r.employee));

  const shownCount = tab === "analytics" ? 0
    : tab === "sales" ? sales.length : tab === "payments" ? payments.length : debts.length;
  const shownTotal = tab === "analytics" ? 0
    : tab === "sales" ? sales.reduce((s, r) => s + Number(r.amount), 0)
    : tab === "payments" ? payments.reduce((s, r) => s + Number(r.amount), 0)
    : debts.reduce((s, r) => s + Number(r.remaining), 0);
  const activeTitle = tab === "sales" ? "История продаж"
    : tab === "payments" ? "История погашений"
    : "Текущие долги";
  const activeCaption = tab === "sales" ? "Заказы и отгрузки клиента"
    : tab === "payments" ? "Все поступившие платежи"
    : "Заказы с непогашенным остатком";

  return (
    <AppShell title="Клиент" section="Работа">
      {/* Компактная шапка профиля без большой пустой карточки. */}
      <div className="mb-5 flex items-center gap-3 border-b pb-4">
        <Link href="/clients" aria-label="К клиентам"
          className="flex size-9 shrink-0 items-center justify-center rounded-lg border bg-[var(--card)] text-[var(--muted-foreground)] transition-colors hover:border-[var(--input)] hover:bg-[var(--muted)]/60 hover:text-[var(--foreground)]">
          <ArrowLeft className="size-4.5" />
        </Link>
        <div className="flex size-10 shrink-0 items-center justify-center rounded-full border bg-[var(--muted)] text-sm font-medium text-[var(--muted-foreground)]">
          {initials}
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-xl font-semibold leading-tight tracking-tight">{client.name}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-[var(--muted-foreground)]">
            {client.phone && (
              <a href={`tel:${client.phone}`}
                className="flex items-center gap-1.5 hover:text-[var(--foreground)]">
                <Phone className="size-3.5" /> {client.phone}
              </a>
            )}
            {client.country && (
              <span className="flex items-center gap-1.5">
                <MapPin className="size-3.5" /> {client.country}
              </span>
            )}
          </div>
        </div>
        {canExport && (
          <Button variant="outline" className="shrink-0" onClick={() => {
            setStatementOpen(true);
          }}>
            <FileSpreadsheet className="size-4 text-emerald-600" /> Excel-выписка
          </Button>
        )}
      </div>

      <Card className="overflow-hidden">
        <CardContent className="p-0">
          <div className="px-4 sm:px-5">
            <Tabs tabs={tabs} active={tab} onChange={switchTab} />
          </div>

          {tab === "analytics" ? (
            <div className="p-4 sm:p-5">
              <div className="mb-4">
                <h3 className="font-semibold">Аналитика клиента</h3>
                <p className="mt-0.5 text-sm text-[var(--muted-foreground)]">
                  Общая картина по продажам, оплатам и текущей задолженности.
                </p>
              </div>
              <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
                <StatCard label="Продаж" value={String(summary.orders_count)}
                  caption="всего заказов" icon={FileText} />
                <StatCard label="Сумма продаж" value={formatCurrency(summary.revenue, client.currency)}
                  caption="за всё время" icon={TrendingUp} accent />
                <StatCard label="Оплачено" value={formatCurrency(summary.paid, client.currency)}
                  caption="получено от клиента" icon={Wallet} />
                <StatCard label="Текущий долг" value={formatCurrency(summary.debt, client.currency)}
                  caption={hasDebt ? "ожидает погашения" : "задолженности нет"} icon={AlertCircle}
                  className={hasDebt ? "border-[var(--destructive)]/25 bg-[var(--destructive)]/6" : undefined} />
              </div>
            </div>
          ) : <>
          {/* Подписанные фильтры не требуют угадывать назначение полей. */}
          <div className="border-b bg-[var(--muted)]/20 p-4 sm:p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <SlidersHorizontal className="size-4 text-[var(--muted-foreground)]" />
                <span className="text-sm font-medium">Фильтры</span>
                <span className="text-xs text-[var(--muted-foreground)]">Показано: {shownCount}</span>
              </div>
              {hasFilters && (
                <Button size="sm" variant="ghost" onClick={resetFilters}>
                  <X className="size-4" /> Сбросить фильтры
                </Button>
              )}
            </div>

            <div className={`grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 ${tab === "debts" ? "2xl:grid-cols-3" : "2xl:grid-cols-6"}`}>
              <div className="grid gap-1.5">
                <span className="text-xs font-medium text-[var(--muted-foreground)]">Период</span>
                <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-1.5">
                  <Input type="date" value={fFrom} onChange={(e) => setFFrom(e.target.value)} aria-label="Период с" />
                  <span className="text-[var(--muted-foreground)]">—</span>
                  <Input type="date" value={fTo} onChange={(e) => setFTo(e.target.value)} aria-label="Период по" />
                </div>
              </div>
              {tab === "sales" && (
                <div className="grid gap-1.5">
                  <span className="text-xs font-medium text-[var(--muted-foreground)]">Товар</span>
                  <Select value={fProduct} onValueChange={setFProduct}>
                    <SelectTrigger className="h-10"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Все товары</SelectItem>
                      {products.map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              )}
              {tab !== "debts" && (
                <div className="grid gap-1.5">
                  <span className="text-xs font-medium text-[var(--muted-foreground)]">Оплата</span>
                  <Select value={fPay} onValueChange={setFPay}>
                    <SelectTrigger className="h-10"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Любой тип</SelectItem>
                      {tab === "sales"
                        ? Object.entries(SETTLEMENT_LABELS).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)
                        : ["cash", "card", "kaspi"].map((m) => (
                            <SelectItem key={m} value={m}>{PAYMENT_METHOD_LABELS[m]}</SelectItem>
                          ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
              {tab !== "debts" && (
                <div className="grid gap-1.5">
                  <span className="text-xs font-medium text-[var(--muted-foreground)]">Статус</span>
                  <Select value={fStatus} onValueChange={setFStatus}>
                    <SelectTrigger className="h-10"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Любой статус</SelectItem>
                      {(tab === "sales" ? saleStatuses : paymentStatuses).map((s) => (
                        <SelectItem key={s} value={s}>
                          {(tab === "sales" ? ORDER_STATUS_LABELS : PAYMENT_STAGE_LABELS)[s] ?? s}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
              {tab === "payments" && employees.length > 0 && (
                <div className="grid gap-1.5">
                  <span className="text-xs font-medium text-[var(--muted-foreground)]">Принял</span>
                  <Select value={fEmployee} onValueChange={setFEmployee}>
                    <SelectTrigger className="h-10"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Все сотрудники</SelectItem>
                      {employees.map((e) => <SelectItem key={e} value={e}>{e}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              )}
              <label className="grid gap-1.5">
                <span className="text-xs font-medium text-[var(--muted-foreground)]">Номер документа</span>
                <Input placeholder="Например, 54" inputMode="numeric"
                  value={fDoc} onChange={(e) => setFDoc(e.target.value)} />
              </label>
              <div className="grid gap-1.5">
                <span className="text-xs font-medium text-[var(--muted-foreground)]">Сумма, {symbol}</span>
                <div className="grid grid-cols-2 gap-1.5">
                  <Input placeholder="От" inputMode="numeric" value={fMin}
                    onChange={(e) => setFMin(e.target.value.replace(/\D/g, ""))} />
                  <Input placeholder="До" inputMode="numeric" value={fMax}
                    onChange={(e) => setFMax(e.target.value.replace(/\D/g, ""))} />
                </div>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-end justify-between gap-3 border-b px-4 py-4 sm:px-5">
            <div>
              <h3 className="font-semibold">{activeTitle}</h3>
              <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">{activeCaption}</p>
            </div>
            <span className="text-sm text-[var(--muted-foreground)]">
              {shownCount > 0 && <><span className="tabular-nums font-semibold text-[var(--foreground)]">{formatCurrency(String(shownTotal), client.currency)}</span> · итог по списку</>}
            </span>
          </div>

          {tab === "sales" && (
            <Table>
              <THead>
                <TR>
                  <TH>№ документа</TH>
                  <SortableHeader label="Дата" sortKey="date" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <TH>Товар</TH>
                  <TH className="text-right">Мешков</TH>
                  <SortableHeader label="Сумма" sortKey="amount" activeKey={sortKey} dir={sortDir} onClick={toggleSort} align="right" />
                  <TH className="text-right">Оплачено</TH>
                  <TH>Способ оплаты</TH>
                  <TH>Статус</TH>
                </TR>
              </THead>
              <TBody>
                {sales.length === 0 ? <EmptyRow colSpan={8} filtered={hasFilters} onReset={resetFilters} /> : sales.map((r) => (
                  <TR key={r.id}>
                    <TD><DocLink orderId={r.id}>№ {r.id}</DocLink></TD>
                    <TD className="tabular-nums text-[var(--muted-foreground)]">{formatDateTime(r.date)}</TD>
                    <TD>
                      <span className="block max-w-70 truncate" title={itemsText(r.items)}>
                        {r.items.length ? itemsText(r.items) : "—"}
                      </span>
                    </TD>
                    <TD className="text-right tabular-nums">{r.bags || "—"}</TD>
                    <TD className="text-right tabular-nums font-medium">{formatCurrency(r.amount, client.currency)}</TD>
                    <TD className="text-right tabular-nums"><Money value={r.paid} currency={client.currency} muted /></TD>
                    <TD>{SETTLEMENT_LABELS[r.settlement_intent] ?? r.settlement_intent}</TD>
                    <TD><StatusBadge status={r.status} dot /></TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}

          {tab === "payments" && (
            <Table>
              <THead>
                <TR>
                  <TH>№ документа</TH>
                  <SortableHeader label="Дата" sortKey="date" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <SortableHeader label="Сумма" sortKey="amount" activeKey={sortKey} dir={sortDir} onClick={toggleSort} align="right" />
                  <TH>Способ оплаты</TH>
                  <TH>Принял</TH>
                  <TH>Статус</TH>
                </TR>
              </THead>
              <TBody>
                {payments.length === 0 ? <EmptyRow colSpan={6} filtered={hasFilters} onReset={resetFilters} /> : payments.map((r) => (
                  <TR key={r.id}>
                    <TD><DocLink orderId={r.order_id}>№ {r.order_id}</DocLink></TD>
                    <TD className="tabular-nums text-[var(--muted-foreground)]">{formatDateTime(r.date)}</TD>
                    <TD className="text-right tabular-nums font-medium">{formatCurrency(r.amount, client.currency)}</TD>
                    <TD>{PAYMENT_METHOD_LABELS[r.method] ?? r.method}</TD>
                    <TD>{r.employee ?? "—"}</TD>
                    <TD>
                      <Badge tone={PAYMENT_STAGE_TONE[r.status] ?? "muted"} dot>
                        {PAYMENT_STAGE_LABELS[r.status] ?? r.status}
                      </Badge>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}

          {tab === "debts" && (
            <Table>
              <THead>
                <TR>
                  <TH>№ документа</TH>
                  <SortableHeader label="Дата" sortKey="date" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <TH className="text-right">Мешков</TH>
                  <SortableHeader label="Сумма" sortKey="amount" activeKey={sortKey} dir={sortDir} onClick={toggleSort} align="right" />
                  <TH className="text-right">Оплачено</TH>
                  <TH className="text-right">Остаток</TH>
                </TR>
              </THead>
              <TBody>
                {debts.length === 0 ? <EmptyRow colSpan={6} filtered={hasFilters} onReset={resetFilters} /> : debts.map((r) => (
                  <TR key={r.id}>
                    <TD><DocLink orderId={r.id}>№ {r.id}</DocLink></TD>
                    <TD className="tabular-nums text-[var(--muted-foreground)]">{formatDateTime(r.date)}</TD>
                    <TD className="text-right tabular-nums">{r.bags || "—"}</TD>
                    <TD className="text-right tabular-nums">{formatCurrency(r.amount, client.currency)}</TD>
                    <TD className="text-right tabular-nums text-[var(--success)]"><Money value={r.paid} currency={client.currency} muted /></TD>
                    <TD className="text-right tabular-nums font-semibold text-[var(--destructive)]">
                      {formatCurrency(r.remaining, client.currency)}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}

          {shownCount > 0 && (
            <div className="flex items-center justify-between border-t bg-[var(--muted)]/20 px-4 py-3 text-[13px] sm:px-5">
              <span className="text-[var(--muted-foreground)]">Документов: {shownCount}</span>
              <span className="tabular-nums font-semibold">
                {tab === "debts" ? "Остаток" : "Итого"}: {formatCurrency(String(shownTotal), client.currency)}
              </span>
            </div>
          )}
          </>}
        </CardContent>
      </Card>

      <StatementExportModal
        open={statementOpen}
        onClose={() => setStatementOpen(false)}
        endpoint={`/clients/${id}/statement/`}
        filename={`client-${id}-statement.xlsx`}
        title="Выписка клиента"
        description="Полная финансовая история выбранного клиента по отдельным листам Excel."
        scopeLabel={`${client.name}: все заказы и движения`}
        sheetsLabel="6 листов: сводка, операции, заказы, позиции, платежи и текущие долги."
      />
    </AppShell>
  );
}

export default function ClientDetailPage(props: { params: Promise<{ id: string }> }) {
  return <RequirePerm perm="reports.view" title="Клиент"><ClientDetailPageInner {...props} /></RequirePerm>;
}
