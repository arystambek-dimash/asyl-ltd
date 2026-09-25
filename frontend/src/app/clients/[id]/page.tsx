"use client";
import { use, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { OtherCurrencyRows } from "@/components/ui/currency-amounts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/status-badge";
import { PaymentStageBadge } from "@/components/payment-chain";
import { OrderRef } from "@/components/orders/order-ref";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { SortableHeader, useSortState } from "@/components/ui/sortable-header";
import { DataGate } from "@/components/ui/data-state";
import { Select } from "@/components/ui/select";
import { useApi } from "@/lib/use-api";
import { onlyDigits } from "@/lib/phone";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { fieldByCurrency, finiteMoney } from "@/lib/currency-map";
import type { ClientHistory } from "@/lib/types";
import { formatCurrency, formatDateTime, sumMoneyByCurrency, toLocalIsoDate } from "@/lib/utils";
import { StatementExportModal } from "@/components/statement-export-modal";
import { ORDER_STATUS_LABELS, orderStatusGroup } from "@/lib/constants";
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

const SETTLEMENT_LABELS: Record<string, string> = { debt: "В долг", instant: "Сразу" };
// Только для ячейки таблицы: «ещё не выбрал» — не вариант фильтра «Оплата».
const SETTLEMENT_CELL_LABELS: Record<string, string> = { ...SETTLEMENT_LABELS, pending: "Не выбран" };

type ClientTab = "analytics" | "sales" | "payments" | "debts";

const TAB_META: Record<ClientTab, { label: string; title: string; caption: string; totalLabel?: string }> = {
  analytics: {
    label: "Аналитика клиента",
    title: "Аналитика клиента",
    caption: "Общая картина по продажам, оплатам и текущей задолженности.",
  },
  sales: {
    label: "Продажи",
    title: "История продаж",
    caption: "Заказы и отгрузки клиента; заявки, отказы и отмены в итог не входят",
  },
  payments: {
    label: "Погашения",
    title: "История погашений",
    caption: "Все платежи; в итог входят подтверждённые за вычетом возвратов",
  },
  debts: {
    label: "Долги",
    title: "Текущие долги",
    caption: "Заказы с непогашенным остатком",
    totalLabel: "Остаток",
  },
};

// Вкладочные фильтры — сбрасываются при переключении вкладки.
const TAB_FILTERS = { product: "all", pay: "all", status: "all", employee: "all" };
// Общие фильтры (период, документ, сумма, валюта) переживают смену вкладки.
const DEFAULT_FILTERS = { from: "", to: "", doc: "", min: "", max: "", currency: "all", ...TAB_FILTERS };
type Filters = typeof DEFAULT_FILTERS;

/** Общие для всех вкладок поля строки — по ним работает единый фильтр. */
interface CommonRow {
  id: number;
  date: string;
  amount: string;
  currency: string;
}

function uniq(values: (string | null | undefined)[]): string[] {
  return [...new Set(values.filter((v): v is string => !!v))].sort((a, b) => a.localeCompare(b, "ru"));
}

type FilterOption = { value: string; label: string };

/** Варианты фильтра: подпись из словаря, без неё — само значение. */
function filterOptions(values: string[], labels?: Record<string, string>): FilterOption[] {
  return values.map((value) => ({ value, label: labels?.[value] ?? value }));
}

/** Список в панели фильтров: подпись, «Все…» (значение "all") и варианты. */
function FilterSelect({
  label,
  value,
  onChange,
  allLabel,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  allLabel: string;
  options: FilterOption[];
}) {
  return (
    <label className="grid gap-1.5">
      <span className="text-xs font-medium text-[var(--muted-foreground)]">{label}</span>
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="all">{allLabel}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </Select>
    </label>
  );
}

function itemsText(items: { label: string; qty: number }[]): string {
  return items.map((i) => `${i.label} × ${i.qty}`).join(", ");
}

function EmptyRow({ colSpan, filtered, onReset }: { colSpan: number; filtered: boolean; onReset: () => void }) {
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

function formatMoneyTotals(totals: Record<string, number>): string {
  return Object.entries(totals)
    .map(([currency, amount]) => formatCurrency(amount, currency))
    .join(" · ");
}

function ClientDetailPageInner({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useAuth();
  const canExport = can(me, "reports.export");
  const canViewOrders = can(me, "orders.view");
  const { data, loading, error, reload } = useApi<ClientHistory>(`/clients/${id}/history/`);

  const [tab, setTab] = useState<ClientTab>("analytics");
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const { sortKey, sortDir, toggleSort } = useSortState("date", "desc", "desc");
  const [statementOpen, setStatementOpen] = useState(false);

  if (!data) {
    return (
      <AppShell title="Клиент">
        <DataGate loading={loading} error={error} onRetry={reload} />
      </AppShell>
    );
  }

  const { client, summary } = data;
  const summaryCurrency = summary.currency;
  const summaryByCurrency = summary.by_currency;
  const hasDebt = Object.values(summaryByCurrency).some((row) => finiteMoney(row.debt) > 0);
  const initials =
    client.name
      .trim()
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part[0])
      .join("")
      .toUpperCase() || "К";
  const tabs = (Object.keys(TAB_META) as ClientTab[]).map((key) => ({
    key,
    label: TAB_META[key].label,
    count: key === "analytics" ? undefined : data[key].length,
  }));
  const meta = TAB_META[tab];

  const setFilter = (key: keyof Filters, value: string) => setFilters((prev) => ({ ...prev, [key]: value }));
  const switchTab = (key: string) => {
    setTab(key as ClientTab);
    setFilters((prev) => ({ ...prev, ...TAB_FILTERS }));
  };
  const hasFilters = (Object.keys(DEFAULT_FILTERS) as (keyof Filters)[]).some(
    (key) => filters[key] !== DEFAULT_FILTERS[key],
  );
  const resetFilters = () => setFilters(DEFAULT_FILTERS);

  const currencies = uniq([
    ...data.sales.map((row) => row.currency),
    ...data.payments.map((row) => row.currency),
    ...data.debts.map((row) => row.currency),
  ]);
  const amountFilterEnabled = currencies.length <= 1 || filters.currency !== "all";

  // Бэк отдаёт время в UTC: календарный день берём местный — тот, что в таблице.
  const matches = (r: CommonRow) => {
    const day = toLocalIsoDate(new Date(r.date));
    return (
      (!filters.from || day >= filters.from) &&
      (!filters.to || day <= filters.to) &&
      (!filters.doc.trim() || String(r.id).includes(onlyDigits(filters.doc))) &&
      (filters.currency === "all" || r.currency === filters.currency) &&
      (!amountFilterEnabled || !filters.min || finiteMoney(r.amount) >= Number(filters.min)) &&
      (!amountFilterEnabled || !filters.max || finiteMoney(r.amount) <= Number(filters.max))
    );
  };

  const sortRows = <T extends CommonRow>(rows: T[]) =>
    [...rows].sort((a, b) => {
      if (sortKey === "amount" && a.currency !== b.currency) return a.currency.localeCompare(b.currency);
      const cmp = sortKey === "amount" ? finiteMoney(a.amount) - finiteMoney(b.amount) : a.date.localeCompare(b.date);
      return sortDir === "asc" ? cmp : -cmp;
    });
  const sales = sortRows(
    data.sales.filter(
      (r) =>
        matches(r) &&
        (filters.product === "all" || r.items.some((i) => i.label === filters.product)) &&
        (filters.pay === "all" || r.settlement_intent === filters.pay) &&
        (filters.status === "all" || orderStatusGroup(r.status) === filters.status),
    ),
  );
  // № документа у погашения — заказ, к которому оно привязано.
  const payments = sortRows(
    data.payments.filter(
      (r) =>
        matches({ ...r, id: r.order_id }) &&
        (filters.pay === "all" || r.method === filters.pay) &&
        (filters.status === "all" || r.status === filters.status) &&
        (filters.employee === "all" || r.employee === filters.employee),
    ),
  );
  const debts = sortRows(data.debts.filter(matches));

  const products = uniq(data.sales.flatMap((r) => r.items.map((i) => i.label)));
  const saleStatuses = uniq(data.sales.map((r) => orderStatusGroup(r.status)));
  const paymentStatuses = uniq(data.payments.map((r) => r.status));
  const paymentMethods = uniq(data.payments.map((r) => r.method));
  // Подписи вариантов фильтра — из самих строк (labels.py на бэке).
  const paymentStatusLabels = Object.fromEntries(data.payments.map((r) => [r.status, r.status_label]));
  const paymentMethodLabels = Object.fromEntries(data.payments.map((r) => [r.method, r.method_label]));
  const employees = uniq(data.payments.map((r) => r.employee));

  const shownCount = { analytics: 0, sales: sales.length, payments: payments.length, debts: debts.length }[tab];
  const shownTotalLabel = formatMoneyTotals(
    {
      analytics: {},
      sales: sumMoneyByCurrency(
        sales.filter((row) => row.is_financial),
        (row) => row.amount,
        (row) => row.currency,
      ),
      payments: sumMoneyByCurrency(
        payments,
        (row) => row.counted_amount,
        (row) => row.currency,
      ),
      debts: sumMoneyByCurrency(
        debts,
        (row) => row.remaining,
        (row) => row.currency,
      ),
    }[tab],
  );

  return (
    <AppShell title="Клиент" section="Работа">
      {/* Компактная шапка профиля без большой пустой карточки. */}
      <div className="mb-5 flex items-center gap-3 border-b pb-4">
        <Link
          href="/clients"
          aria-label="К клиентам"
          className="flex size-9 shrink-0 items-center justify-center rounded-lg border bg-[var(--card)] text-[var(--muted-foreground)] transition-colors hover:border-[var(--input)] hover:bg-[var(--muted)]/60 hover:text-[var(--foreground)]"
        >
          <ArrowLeft className="size-4.5" />
        </Link>
        <div className="flex size-10 shrink-0 items-center justify-center rounded-full border bg-[var(--muted)] text-sm font-medium text-[var(--muted-foreground)]">
          {initials}
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-xl font-semibold leading-tight tracking-tight">{client.name}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-[var(--muted-foreground)]">
            {client.phone && (
              <a href={`tel:${client.phone}`} className="flex items-center gap-1.5 hover:text-[var(--foreground)]">
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
          <Button
            variant="outline"
            className="shrink-0"
            onClick={() => {
              setStatementOpen(true);
            }}
          >
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
                <h3 className="font-semibold">{meta.title}</h3>
                <p className="mt-0.5 text-sm text-[var(--muted-foreground)]">{meta.caption}</p>
              </div>
              <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
                <StatCard label="Продаж" value={String(summary.orders_count)} caption="всего заказов" icon={FileText} />
                <StatCard
                  label="Сумма продаж"
                  value={formatCurrency(summary.revenue, summaryCurrency)}
                  caption="за всё время"
                  icon={TrendingUp}
                  accent
                >
                  <OtherCurrencyRows
                    byCurrency={fieldByCurrency(summaryByCurrency, "revenue")}
                    primary={summaryCurrency}
                  />
                </StatCard>
                <StatCard
                  label="Оплачено"
                  value={formatCurrency(summary.paid, summaryCurrency)}
                  caption="получено от клиента"
                  icon={Wallet}
                >
                  <OtherCurrencyRows
                    byCurrency={fieldByCurrency(summaryByCurrency, "paid")}
                    primary={summaryCurrency}
                  />
                </StatCard>
                <StatCard
                  label="Текущий долг"
                  value={formatCurrency(summary.debt, summaryCurrency)}
                  caption={hasDebt ? "ожидает погашения" : "задолженности нет"}
                  icon={AlertCircle}
                  className={hasDebt ? "border-[var(--destructive)]/25 bg-[var(--destructive)]/6" : undefined}
                >
                  <OtherCurrencyRows
                    byCurrency={fieldByCurrency(summaryByCurrency, "debt")}
                    primary={summaryCurrency}
                  />
                </StatCard>
              </div>
            </div>
          ) : (
            <>
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

                <div
                  className={`grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 ${tab === "debts" ? "2xl:grid-cols-3" : "2xl:grid-cols-6"}`}
                >
                  <div className="grid gap-1.5">
                    <span className="text-xs font-medium text-[var(--muted-foreground)]">Период</span>
                    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-1.5">
                      <Input
                        type="date"
                        value={filters.from}
                        onChange={(e) => setFilter("from", e.target.value)}
                        aria-label="Период с"
                      />
                      <span className="text-[var(--muted-foreground)]">—</span>
                      <Input
                        type="date"
                        value={filters.to}
                        onChange={(e) => setFilter("to", e.target.value)}
                        aria-label="Период по"
                      />
                    </div>
                  </div>
                  {currencies.length > 1 && (
                    <FilterSelect
                      label="Валюта"
                      value={filters.currency}
                      onChange={(currency) =>
                        setFilters((prev) =>
                          currency === "all" ? { ...prev, currency, min: "", max: "" } : { ...prev, currency },
                        )
                      }
                      allLabel="Все валюты"
                      options={filterOptions(currencies)}
                    />
                  )}
                  {tab === "sales" && (
                    <FilterSelect
                      label="Товар"
                      value={filters.product}
                      onChange={(value) => setFilter("product", value)}
                      allLabel="Все товары"
                      options={filterOptions(products)}
                    />
                  )}
                  {tab !== "debts" && (
                    <FilterSelect
                      label="Оплата"
                      value={filters.pay}
                      onChange={(value) => setFilter("pay", value)}
                      allLabel="Любой тип"
                      options={
                        tab === "sales"
                          ? filterOptions(Object.keys(SETTLEMENT_LABELS), SETTLEMENT_LABELS)
                          : filterOptions(paymentMethods, paymentMethodLabels)
                      }
                    />
                  )}
                  {tab !== "debts" && (
                    <FilterSelect
                      label="Статус"
                      value={filters.status}
                      onChange={(value) => setFilter("status", value)}
                      allLabel="Любой статус"
                      options={
                        tab === "sales"
                          ? filterOptions(saleStatuses, ORDER_STATUS_LABELS)
                          : filterOptions(paymentStatuses, paymentStatusLabels)
                      }
                    />
                  )}
                  {tab === "payments" && employees.length > 0 && (
                    <FilterSelect
                      label="Принял"
                      value={filters.employee}
                      onChange={(value) => setFilter("employee", value)}
                      allLabel="Все сотрудники"
                      options={filterOptions(employees)}
                    />
                  )}
                  <label className="grid gap-1.5">
                    <span className="text-xs font-medium text-[var(--muted-foreground)]">Номер документа</span>
                    <Input
                      placeholder="Например, 54"
                      inputMode="numeric"
                      value={filters.doc}
                      onChange={(e) => setFilter("doc", e.target.value)}
                    />
                  </label>
                  <div className="grid gap-1.5">
                    <span className="text-xs font-medium text-[var(--muted-foreground)]">
                      Сумма{filters.currency !== "all" ? `, ${filters.currency}` : ""}
                    </span>
                    <div className="grid grid-cols-2 gap-1.5">
                      <Input
                        placeholder={amountFilterEnabled ? "От" : "Выберите валюту"}
                        inputMode="numeric"
                        disabled={!amountFilterEnabled}
                        value={filters.min}
                        onChange={(e) => setFilter("min", onlyDigits(e.target.value))}
                      />
                      <Input
                        placeholder={amountFilterEnabled ? "До" : "Выберите валюту"}
                        inputMode="numeric"
                        disabled={!amountFilterEnabled}
                        value={filters.max}
                        onChange={(e) => setFilter("max", onlyDigits(e.target.value))}
                      />
                    </div>
                  </div>
                </div>
              </div>

              <div className="flex flex-wrap items-end justify-between gap-3 border-b px-4 py-4 sm:px-5">
                <div>
                  <h3 className="font-semibold">{meta.title}</h3>
                  <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">{meta.caption}</p>
                </div>
                <span className="text-sm text-[var(--muted-foreground)]">
                  {shownCount > 0 && (
                    <>
                      <span className="tabular-nums font-semibold text-[var(--foreground)]">{shownTotalLabel}</span> ·
                      итог по списку
                    </>
                  )}
                </span>
              </div>

              {tab === "sales" && (
                <Table>
                  <THead>
                    <TR>
                      <TH>№ документа</TH>
                      <SortableHeader
                        label="Дата"
                        sortKey="date"
                        activeKey={sortKey}
                        dir={sortDir}
                        onClick={toggleSort}
                      />
                      <TH>Товар</TH>
                      <TH className="text-right">Мешков</TH>
                      <SortableHeader
                        label="Сумма"
                        sortKey="amount"
                        activeKey={sortKey}
                        dir={sortDir}
                        onClick={toggleSort}
                        align="right"
                      />
                      <TH className="text-right">Оплачено</TH>
                      <TH>Способ оплаты</TH>
                      <TH>Статус</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {sales.length === 0 ? (
                      <EmptyRow colSpan={8} filtered={hasFilters} onReset={resetFilters} />
                    ) : (
                      sales.map((r) => (
                        <TR key={r.id}>
                          <TD>
                            <OrderRef
                              id={r.id}
                              canOpen={canViewOrders}
                              className="font-medium"
                              linkClassName="text-[var(--ring)]"
                            >
                              № {r.id}
                            </OrderRef>
                          </TD>
                          <TD className="tabular-nums text-[var(--muted-foreground)]">{formatDateTime(r.date)}</TD>
                          <TD>
                            <span className="block max-w-70 truncate" title={itemsText(r.items)}>
                              {r.items.length ? itemsText(r.items) : "—"}
                            </span>
                          </TD>
                          <TD className="text-right tabular-nums">{r.bags || "—"}</TD>
                          <TD className="text-right tabular-nums font-medium">
                            {formatCurrency(r.amount, r.currency)}
                          </TD>
                          <TD className="text-right tabular-nums">
                            <Money value={r.paid} currency={r.currency} muted />
                          </TD>
                          <TD>{SETTLEMENT_CELL_LABELS[r.settlement_intent] ?? r.settlement_intent}</TD>
                          <TD>
                            <StatusBadge status={r.status} dot />
                          </TD>
                        </TR>
                      ))
                    )}
                  </TBody>
                </Table>
              )}

              {tab === "payments" && (
                <Table>
                  <THead>
                    <TR>
                      <TH>№ документа</TH>
                      <SortableHeader
                        label="Дата"
                        sortKey="date"
                        activeKey={sortKey}
                        dir={sortDir}
                        onClick={toggleSort}
                      />
                      <SortableHeader
                        label="Сумма"
                        sortKey="amount"
                        activeKey={sortKey}
                        dir={sortDir}
                        onClick={toggleSort}
                        align="right"
                      />
                      <TH>Способ оплаты</TH>
                      <TH>Принял</TH>
                      <TH>Статус</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {payments.length === 0 ? (
                      <EmptyRow colSpan={6} filtered={hasFilters} onReset={resetFilters} />
                    ) : (
                      payments.map((r) => (
                        <TR key={r.id}>
                          <TD>
                            <OrderRef
                              id={r.order_id}
                              canOpen={canViewOrders}
                              className="font-medium"
                              linkClassName="text-[var(--ring)]"
                            >
                              № {r.order_id}
                            </OrderRef>
                          </TD>
                          <TD className="tabular-nums text-[var(--muted-foreground)]">{formatDateTime(r.date)}</TD>
                          <TD className="text-right tabular-nums font-medium">
                            {formatCurrency(r.amount, r.currency)}
                          </TD>
                          <TD>{r.method_label}</TD>
                          <TD>{r.employee ?? "—"}</TD>
                          <TD>
                            <PaymentStageBadge payment={r} />
                          </TD>
                        </TR>
                      ))
                    )}
                  </TBody>
                </Table>
              )}

              {tab === "debts" && (
                <Table>
                  <THead>
                    <TR>
                      <TH>№ документа</TH>
                      <SortableHeader
                        label="Дата"
                        sortKey="date"
                        activeKey={sortKey}
                        dir={sortDir}
                        onClick={toggleSort}
                      />
                      <TH className="text-right">Мешков</TH>
                      <SortableHeader
                        label="Сумма"
                        sortKey="amount"
                        activeKey={sortKey}
                        dir={sortDir}
                        onClick={toggleSort}
                        align="right"
                      />
                      <TH className="text-right">Оплачено</TH>
                      <TH className="text-right">Остаток</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {debts.length === 0 ? (
                      <EmptyRow colSpan={6} filtered={hasFilters} onReset={resetFilters} />
                    ) : (
                      debts.map((r) => (
                        <TR key={r.id}>
                          <TD>
                            <OrderRef
                              id={r.id}
                              canOpen={canViewOrders}
                              className="font-medium"
                              linkClassName="text-[var(--ring)]"
                            >
                              № {r.id}
                            </OrderRef>
                          </TD>
                          <TD className="tabular-nums text-[var(--muted-foreground)]">{formatDateTime(r.date)}</TD>
                          <TD className="text-right tabular-nums">{r.bags || "—"}</TD>
                          <TD className="text-right tabular-nums">{formatCurrency(r.amount, r.currency)}</TD>
                          <TD className="text-right tabular-nums text-[var(--success)]">
                            <Money value={r.paid} currency={r.currency} muted />
                          </TD>
                          <TD className="text-right tabular-nums font-semibold text-[var(--destructive)]">
                            {formatCurrency(r.remaining, r.currency)}
                          </TD>
                        </TR>
                      ))
                    )}
                  </TBody>
                </Table>
              )}

              {shownCount > 0 && (
                <div className="flex items-center justify-between border-t bg-[var(--muted)]/20 px-4 py-3 text-[13px] sm:px-5">
                  <span className="text-[var(--muted-foreground)]">Документов: {shownCount}</span>
                  <span className="tabular-nums font-semibold">
                    {meta.totalLabel ?? "Итого"}: {shownTotalLabel}
                  </span>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <StatementExportModal
        open={statementOpen}
        onClose={() => setStatementOpen(false)}
        endpoint={`/clients/${id}/statement/`}
        filenameStem={`client-${id}-statement`}
        title="Выписка клиента"
        description="Полная финансовая история выбранного клиента в Excel или PDF."
        scopeLabel={`${client.name}: все заказы и движения`}
      />
    </AppShell>
  );
}

export default function ClientDetailPage(props: { params: Promise<{ id: string }> }) {
  return (
    <RequirePerm perm="reports.view" title="Клиент">
      <ClientDetailPageInner {...props} />
    </RequirePerm>
  );
}
