"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { ErrorAlert } from "@/components/ui/data-state";
import { DepartmentBadge } from "@/components/ui/department-badge";
import { LoadMore } from "@/components/ui/load-more";
import { SearchInput } from "@/components/ui/search-input";
import { EmptyRow, Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { GoodsReturnAcceptedBy, GoodsReturnStatusBadge } from "@/components/orders/goods-return-status";
import { DateRangeFilter, DepartmentFilter } from "@/components/orders/order-filters";
import { api } from "@/lib/api";
import { can } from "@/lib/can";
import { withBack } from "@/lib/navigation";
import { useConfirmAction } from "@/lib/use-confirm-action";
import { usePagedApi } from "@/lib/use-paged-api";
import { useDebounced } from "@/lib/use-debounced";
import { acceptedBagsLabel, apiUrl, bagsLabel, cn, formatDateTime } from "@/lib/utils";
import type { Department, GoodsReturn } from "@/lib/types";
import { useAuth } from "@/store/auth";

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

/** Мешки старого возврата по муке: одна мука из нескольких заказов — одной строкой. */
function bagsByProduct(lines: ReturnLine[]) {
  const products = new Map<string, number>();
  for (const line of lines) products.set(line.product_label, (products.get(line.product_label) ?? 0) + line.bags);
  return [...products.entries()].map(([label, bags]) => ({ label, bags }));
}

/** Что принял кладовщик: старый возврат — что легло на заказы, новый — принятое по каждому товару. */
function acceptedProducts(row: GoodsReturn) {
  if (row.lines.length > 0) return bagsByProduct(row.lines);
  return row.items
    .filter((item) => item.accepted_bags)
    .map((item) => ({ label: item.product_label, bags: item.accepted_bags ?? 0 }));
}

/** Отделы заказов старого возврата — без повторов; у нового заказов нет. */
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

/** Принят ли возврат кладовщиком: полностью или частично. */
const isAccepted = (row: GoodsReturn) => row.status === "full" || row.status === "partial";

/**
 * Товар и мешки: у принятого — что принято, у частичного ещё и «принято N из
 * M»; пока не принят (и у отменённого) — что менеджер записал в возврат.
 */
function ReturnProducts({ row }: { row: GoodsReturn }) {
  if (!isAccepted(row)) {
    return (
      <ul className={cn("flex flex-col gap-0.5", row.status === "cancelled" && "text-[var(--muted-foreground)]")}>
        {row.items.map((item) => (
          <li key={item.id}>
            {item.product_label} <span className="whitespace-nowrap tabular-nums">· {bagsLabel(item.bags)}</span>
          </li>
        ))}
      </ul>
    );
  }
  const products = acceptedProducts(row);
  const total = products.reduce((sum, product) => sum + product.bags, 0);
  const requested = row.items.reduce((sum, item) => sum + item.bags, 0);
  const accepted = row.items.reduce((sum, item) => sum + (item.accepted_bags ?? 0), 0);
  return (
    <ul className="flex flex-col gap-0.5">
      {products.map((product) => (
        <li key={product.label}>
          {product.label} <span className="whitespace-nowrap tabular-nums">· {bagsLabel(product.bags)}</span>
        </li>
      ))}
      {row.status === "partial" ? (
        <li className="text-xs font-medium tabular-nums text-[var(--warning)]">
          {acceptedBagsLabel(accepted, requested)}
        </li>
      ) : (
        products.length > 1 && (
          <li className="text-xs tabular-nums text-[var(--muted-foreground)]">Всего {bagsLabel(total)}</li>
        )
      )}
    </ul>
  );
}

/** Заказы старого возврата; новый с заказами не связан — «—». */
function ReturnOrders({ row }: { row: GoodsReturn }) {
  if (row.lines.length === 0) return <span className="text-[var(--muted-foreground)]">—</span>;
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

/**
 * Деньги старого возврата (с деньгами): «В счёт долга» / «Из кассы» и сумма —
 * ₸ и $ отдельными равными итогами. Новый возврат денег не трогает.
 */
function ReturnMoney({ row, className }: { row: GoodsReturn; className?: string }) {
  return (
    <div className={className}>
      <div className="text-xs text-[var(--muted-foreground)]">{row.settlement_label}</div>
      <CurrencyAmounts byCurrency={row.amounts} equal amountClassName="font-semibold tabular-nums" />
    </div>
  );
}

/** «Отменить» ждущего приёмки возврата: склад ещё не менялся. */
function CancelReturnButton({
  row,
  onCancel,
  className,
}: {
  row: GoodsReturn;
  onCancel: (row: GoodsReturn) => void;
  className?: string;
}) {
  return (
    <Button
      variant="ghost"
      size="sm"
      className={className}
      onClick={() => onCancel(row)}
      aria-label={`Отменить возврат №${row.id}`}
    >
      <Undo2 className="size-4" /> Отменить
    </Button>
  );
}

/**
 * Вкладка «Заказы → Возвраты»: возвраты товара со статусом приёмки, новые
 * сверху. Возврат — товар и мешки клиента, принятое ложится на склад; старые
 * возвраты (с деньгами по заказам) показаны как были. Видимость по отделам
 * считает сервер; заказы из корзины в список не попадают. Ждущий приёмки
 * возврат менеджер может отменить.
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
  const { me } = useAuth();
  const canCancel = can(me, "orders.edit");
  // Ответ отмены — свежая строка списка: применяем её, а не перечитываем.
  const cancel = useConfirmAction<GoodsReturn>(async (row) => {
    const { data } = await api.post<GoodsReturn>(`/orders/returns/${row.id}/cancel/`, {});
    paged.applyItems((rows) => rows.map((current) => (current.id === data.id ? data : current)));
  });
  const cancellable = (row: GoodsReturn) => canCancel && row.status === "pending";
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
                  <div className="mt-1">
                    <GoodsReturnStatusBadge row={row} />
                  </div>
                </div>
                {showDept && <ReturnDepartments row={row} departments={departments} />}
              </div>
              <div className="text-sm">
                <ReturnProducts row={row} />
              </div>
              {row.lines.length > 0 && (
                <div className="text-sm">
                  <ReturnOrders row={row} />
                </div>
              )}
              <div className="flex items-end justify-between gap-3 border-t pt-2">
                <div className="min-w-0 text-xs text-[var(--muted-foreground)]">
                  <div className="truncate">{row.warehouse_name}</div>
                  {row.created_by_name && <div className="truncate">{row.created_by_name}</div>}
                  <GoodsReturnAcceptedBy row={row} />
                </div>
                {row.settlement_label && (
                  <ReturnMoney row={row} className="flex shrink-0 flex-col items-end text-right" />
                )}
              </div>
              {cancellable(row) && (
                <div className="flex justify-end">
                  <CancelReturnButton row={row} onCancel={cancel.open} />
                </div>
              )}
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
                <TH>Товар и мешки</TH>
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
                      <div className="mt-1">
                        <GoodsReturnStatusBadge row={row} />
                      </div>
                      {cancellable(row) && (
                        <CancelReturnButton row={row} onCancel={cancel.open} className="-ml-3 mt-1" />
                      )}
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
                      {row.settlement_label ? (
                        <ReturnMoney row={row} className="flex flex-col items-end text-right" />
                      ) : (
                        <div className="text-right text-[var(--muted-foreground)]">—</div>
                      )}
                    </TD>
                    <TD className="py-3 align-top text-[var(--muted-foreground)]">{row.warehouse_name}</TD>
                    <TD className="py-3 align-top text-[var(--muted-foreground)]">
                      <div>{row.created_by_name || "—"}</div>
                      <GoodsReturnAcceptedBy row={row} className="text-xs" />
                    </TD>
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
      <ConfirmDialog
        {...cancel.dialog}
        title={`Отменить возврат №${cancel.item?.id ?? ""}?`}
        description="Кладовщик не будет принимать эти мешки. Склад не менялся — возврат ещё не принят."
        confirmLabel="Отменить возврат"
      />
    </section>
  );
}
