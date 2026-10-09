"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { ErrorAlert } from "@/components/ui/data-state";
import { DepartmentBadge } from "@/components/ui/department-badge";
import { LoadMore } from "@/components/ui/load-more";
import { SearchInput } from "@/components/ui/search-input";
import { EmptyRow, Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { DateRangeFilter, DepartmentFilter } from "@/components/orders/order-filters";
import { withBack } from "@/lib/navigation";
import { usePagedApi } from "@/lib/use-paged-api";
import { useDebounced } from "@/lib/use-debounced";
import { apiUrl, bagsLabel, formatDateTime } from "@/lib/utils";
import type { Department, GoodsReturn } from "@/lib/types";

const BACK = "/orders?tab=returns";
const COLUMNS = 7;

type ReturnLine = GoodsReturn["lines"][number];

/** Мешки возврата по заказам: строки одного заказа — одна ссылка, порядок сервера сохраняется. */
function bagsByOrder(lines: ReturnLine[]) {
  const orders = new Map<number, { order: number; bags: number }>();
  for (const line of lines) {
    const row = orders.get(line.order) ?? { order: line.order, bags: 0 };
    row.bags += line.bags;
    orders.set(line.order, row);
  }
  return [...orders.values()];
}

/** Мешки возврата по муке: одна мука из нескольких заказов — одной строкой. */
function bagsByProduct(lines: ReturnLine[]) {
  const products = new Map<string, number>();
  for (const line of lines) products.set(line.product_label, (products.get(line.product_label) ?? 0) + line.bags);
  return [...products.entries()].map(([label, bags]) => ({ label, bags }));
}

/** Отделы заказов возврата — без повторов. */
function returnDepartments(lines: ReturnLine[]) {
  const seen = new Map<string, string>();
  for (const line of lines) seen.set(line.order_department, line.order_department_name);
  return [...seen.entries()].map(([code, name]) => ({ code, name }));
}

function ReturnDepartments({ row, departments }: { row: GoodsReturn; departments?: Department[] | null }) {
  return (
    <div className="flex flex-wrap gap-1">
      {returnDepartments(row.lines).map(({ code, name }) => (
        <DepartmentBadge
          key={code || "none"}
          name={code ? name : null}
          color={departments?.find((department) => department.code === code)?.color}
          className="max-w-44 bg-[var(--card)]"
        />
      ))}
    </div>
  );
}

function ReturnProducts({ row }: { row: GoodsReturn }) {
  const products = bagsByProduct(row.lines);
  return (
    <ul className="flex flex-col gap-0.5">
      {products.map((product) => (
        <li key={product.label}>
          {product.label} <span className="whitespace-nowrap tabular-nums">· {bagsLabel(product.bags)}</span>
        </li>
      ))}
      {products.length > 1 && (
        <li className="text-xs tabular-nums text-[var(--muted-foreground)]">Всего {bagsLabel(row.bags)}</li>
      )}
    </ul>
  );
}

function ReturnOrders({ row }: { row: GoodsReturn }) {
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-0.5 md:flex-col">
      {bagsByOrder(row.lines).map(({ order, bags }) => (
        <li key={order} className="whitespace-nowrap">
          <Link
            href={withBack(`/orders/${order}`, BACK)}
            className="font-medium tabular-nums underline-offset-2 hover:underline"
          >
            #{order} · {bags} меш.
          </Link>
        </li>
      ))}
    </ul>
  );
}

/** Что стало с деньгами: «В счёт долга» / «Из кассы» и сумма — ₸ и $ отдельными равными итогами. */
function ReturnMoney({ row, className }: { row: GoodsReturn; className?: string }) {
  return (
    <div className={className}>
      <div className="text-xs text-[var(--muted-foreground)]">{row.settlement_label}</div>
      <CurrencyAmounts byCurrency={row.amounts} equal amountClassName="font-semibold tabular-nums" />
    </div>
  );
}

/**
 * Вкладка «Возвраты» в «Заказах»: проведённые возвраты товара, новые сверху.
 * Строки, суммы и видимость по отделам считает сервер; заказы из корзины в
 * список не попадают.
 *
 * `refreshKey` меняется после нового возврата — список перечитывается с теми же фильтрами.
 */
export function GoodsReturnsSection({
  departments,
  refreshKey = 0,
}: {
  departments?: Department[] | null;
  refreshKey?: number;
}) {
  const [q, setQ] = useState("");
  const search = useDebounced(q.trim());
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [dept, setDept] = useState("all");
  const paged = usePagedApi<GoodsReturn>(
    apiUrl("/orders/returns/", { search, date_from: dateFrom, date_to: dateTo, department: dept }),
    50,
  );
  const { items, loading, error, reload } = paged;
  const filtered = Boolean(search || dateFrom || dateTo || dept !== "all");
  const emptyText = filtered ? "По этим условиям возвратов нет." : "Возвратов пока нет.";
  const showDept =
    (departments?.length ?? 0) > 1 || items.some((row) => row.lines.some((line) => !line.order_department));

  const seenRefreshKey = useRef(refreshKey);
  useEffect(() => {
    if (seenRefreshKey.current === refreshKey) return;
    seenRefreshKey.current = refreshKey;
    void reload();
  }, [refreshKey, reload]);

  return (
    <section className="flex flex-col">
      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <SearchInput
          wrapperClassName="max-w-md flex-1"
          placeholder="Поиск по клиенту, телефону или № заказа"
          value={q}
          onChange={(event) => setQ(event.target.value)}
        />
        <div className="flex flex-wrap items-center gap-2">
          <DateRangeFilter
            title="Дата возврата"
            dateFrom={dateFrom}
            dateTo={dateTo}
            onDateFrom={setDateFrom}
            onDateTo={setDateTo}
          />
          <DepartmentFilter departments={departments} active={dept} onChange={setDept} />
        </div>
      </div>

      {error && (
        <div className="mb-4">
          <ErrorAlert message={error} onRetry={reload} />
        </div>
      )}

      {/* Телефон — карточки: семь колонок на узком экране не читаются. */}
      <ul className="flex flex-col gap-3 md:hidden">
        {loading && items.length === 0 ? (
          <li className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</li>
        ) : items.length === 0 ? (
          !error && <li className="py-6 text-center text-sm text-[var(--muted-foreground)]">{emptyText}</li>
        ) : (
          items.map((row) => (
            <li key={row.id} className="flex flex-col gap-2.5 rounded-xl border bg-[var(--card)] p-4 shadow-card">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-semibold">{row.client_name}</div>
                  <div className="text-xs tabular-nums text-[var(--muted-foreground)]">
                    Возврат №{row.id} · {formatDateTime(row.created_at)}
                  </div>
                </div>
                {showDept && <ReturnDepartments row={row} departments={departments} />}
              </div>
              <div className="text-sm">
                <ReturnProducts row={row} />
              </div>
              <div className="text-sm">
                <ReturnOrders row={row} />
              </div>
              <div className="flex items-end justify-between gap-3 border-t pt-2">
                <div className="min-w-0 text-xs text-[var(--muted-foreground)]">
                  <div className="truncate">{row.warehouse_name}</div>
                  {row.created_by_name && <div className="truncate">{row.created_by_name}</div>}
                </div>
                <ReturnMoney row={row} className="flex shrink-0 flex-col items-end text-right" />
              </div>
            </li>
          ))
        )}
      </ul>

      <Card className="hidden md:block">
        <CardContent className="pt-6">
          <Table>
            <THead>
              <TR>
                <TH>Дата</TH>
                <TH>Клиент</TH>
                <TH>Мука и мешки</TH>
                <TH>Заказы</TH>
                <TH className="text-right">Деньги</TH>
                <TH>Склад</TH>
                <TH>Кто</TH>
              </TR>
            </THead>
            <TBody>
              {loading && items.length === 0 ? (
                <EmptyRow colSpan={COLUMNS}>Загрузка…</EmptyRow>
              ) : items.length === 0 ? (
                !error && <EmptyRow colSpan={COLUMNS}>{emptyText}</EmptyRow>
              ) : (
                items.map((row) => (
                  <TR key={row.id} className="align-top">
                    <TD className="whitespace-nowrap py-3 align-top tabular-nums">
                      <div>{formatDateTime(row.created_at)}</div>
                      <div className="text-xs text-[var(--muted-foreground)]">Возврат №{row.id}</div>
                    </TD>
                    <TD className="py-3 align-top">
                      <div className="font-medium">{row.client_name}</div>
                      {showDept && (
                        <div className="mt-1">
                          <ReturnDepartments row={row} departments={departments} />
                        </div>
                      )}
                    </TD>
                    <TD className="py-3 align-top">
                      <ReturnProducts row={row} />
                    </TD>
                    <TD className="py-3 align-top">
                      <ReturnOrders row={row} />
                    </TD>
                    <TD className="py-3 align-top">
                      <ReturnMoney row={row} className="flex flex-col items-end text-right" />
                    </TD>
                    <TD className="py-3 align-top text-[var(--muted-foreground)]">{row.warehouse_name}</TD>
                    <TD className="py-3 align-top text-[var(--muted-foreground)]">{row.created_by_name || "—"}</TD>
                  </TR>
                ))
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>
      <LoadMore
        shown={items.length}
        total={paged.count}
        hasMore={paged.hasMore}
        loading={paged.loading || paged.loadingMore}
        onClick={paged.loadMore}
      />
    </section>
  );
}
