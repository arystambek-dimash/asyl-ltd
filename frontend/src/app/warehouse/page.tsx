"use client";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/field";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { ActionMenu } from "@/components/ui/action-menu";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { DataGate } from "@/components/ui/data-state";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { cn } from "@/lib/utils";
import {
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  ArrowRightLeft,
  Building2,
  Boxes,
  Package,
  Pencil,
  Plus,
  Scale,
  Search,
  Settings2,
  X,
} from "lucide-react";
import type { StockItem, Product, Warehouse } from "@/lib/types";

// Статус остатка: нет / мало (<20 мешков) / в наличии.
function stockTone(bags: number): { tone: "destructive" | "warning" | "success"; label: string } {
  if (bags <= 0) return { tone: "destructive", label: "Нет" };
  if (bags < 20) return { tone: "warning", label: "Мало" };
  return { tone: "success", label: "В наличии" };
}

const QUICK_AMOUNTS = [10, 50, 100, 500];
type StockOperation = "add" | "remove" | "transfer";

const LEGACY_WAREHOUSE: Warehouse = {
  id: 0,
  code: "main",
  name: "Основной склад",
  address: "Режим совместимости",
  is_active: true,
  is_default: true,
};

function WarehousePageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchParamsKey = searchParams.toString();
  const warehouseParam = searchParams.get("warehouse");
  const { me } = useAuth();
  const canAdjust = can(me, "warehouse.adjust");
  const canBrowseCatalog = can(me, "catalog.view");
  const {
    data: warehouseData,
    loading: warehousesLoading,
    error: warehousesError,
    errorStatus: warehousesErrorStatus,
    reload: reloadWarehouses,
  } = useApi<Warehouse[]>("/warehouses/");
  const legacyWarehouseMode = warehouseData === null && warehousesErrorStatus === 404;
  const warehouses = useMemo(
    () => (legacyWarehouseMode ? [LEGACY_WAREHOUSE] : warehouseData),
    [legacyWarehouseMode, warehouseData],
  );
  const activeWarehouses = useMemo(() => (warehouses ?? []).filter((item) => item.is_active), [warehouses]);
  const selectedWarehouse = useMemo(() => {
    const requested = activeWarehouses.find((item) => String(item.id) === warehouseParam);
    return requested ?? activeWarehouses.find((item) => item.is_default) ?? activeWarehouses[0] ?? null;
  }, [activeWarehouses, warehouseParam]);
  const selectedWarehouseId = selectedWarehouse?.id ?? null;
  const stockUrl = selectedWarehouse
    ? legacyWarehouseMode
      ? "/stock/"
      : `/stock/?warehouse=${selectedWarehouse.id}`
    : null;
  const { data: stock, loading: stockLoading, error: loadError, reload } = useApi<StockItem[]>(stockUrl);
  const { data: products } = useApi<Product[]>(canAdjust && canBrowseCatalog ? "/products/" : null);
  // Aggregate stock powers ownership counts and the destination preview. The
  // selected warehouse still has its own scoped list and filters.
  const { data: allStock, reload: reloadAllStock } = useApi<StockItem[]>(legacyWarehouseMode ? null : "/stock/");

  // фильтры
  const [search, setSearch] = useState("");
  const [grade, setGrade] = useState("");
  const [packaging, setPackaging] = useState("");

  // Верхняя кнопка добавляет товар, карандаш изменяет конкретную строку.
  const [open, setOpen] = useState(false);
  const [dialogIntent, setDialogIntent] = useState<"add" | "adjust">("add");
  const [product, setProduct] = useState("");
  const [mode, setMode] = useState<StockOperation>("add");
  const [destinationWarehouse, setDestinationWarehouse] = useState("");
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [warehouseManagerOpen, setWarehouseManagerOpen] = useState(false);

  useEffect(() => {
    if (!selectedWarehouse || legacyWarehouseMode || warehouseParam === String(selectedWarehouse.id)) return;
    const nextParams = new URLSearchParams(searchParamsKey);
    nextParams.set("warehouse", String(selectedWarehouse.id));
    router.replace(`/warehouse?${nextParams.toString()}`, { scroll: false });
  }, [legacyWarehouseMode, router, searchParamsKey, selectedWarehouse, warehouseParam]);

  useEffect(() => {
    setSearch("");
    setGrade("");
    setPackaging("");
    setOpen(false);
    setProduct("");
    setDestinationWarehouse("");
    setAmount("");
    setError("");
  }, [selectedWarehouseId]);

  function selectWarehouse(warehouseId: number) {
    const nextParams = new URLSearchParams(searchParamsKey);
    nextParams.set("warehouse", String(warehouseId));
    router.replace(`/warehouse?${nextParams.toString()}`, { scroll: false });
  }

  const items = useMemo(() => stock ?? [], [stock]);
  const currentProductIds = new Set(items.map((item) => item.product));
  const availableProducts = products ? products.filter((item) => !currentProductIds.has(item.id)) : [];
  const destinationWarehouses = useMemo(
    () => activeWarehouses.filter((item) => item.id !== selectedWarehouseId),
    [activeWarehouses, selectedWarehouseId],
  );
  const grades = useMemo(() => Array.from(new Set(items.map((s) => s.grade))).filter(Boolean), [items]);
  const packagings = useMemo(() => Array.from(new Set(items.map((s) => s.packaging))).filter(Boolean), [items]);
  const bagsByProduct = useMemo(() => new Map(items.map((s) => [String(s.product), s.bags])), [items]);

  const normalizedSearch = search.trim().toLowerCase();
  const filtered = items.filter(
    (s) =>
      (!normalizedSearch ||
        [s.product_label, s.grade, s.color_label, s.packaging].some((value) =>
          value.toLowerCase().includes(normalizedSearch),
        )) &&
      (!grade || s.grade === grade) &&
      (!packaging || s.packaging === packaging),
  );

  const [sortKey, setSortKey] = useState("product_label");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else {
      setSortKey(k);
      setSortDir("asc");
    }
  };
  const sorted = [...filtered].sort((a, b) => {
    let cmp: number;
    if (sortKey === "bags") cmp = a.bags - b.bags;
    else cmp = String(a.product_label).localeCompare(String(b.product_label), "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  const totalBags = filtered.reduce((sum, s) => sum + s.bags, 0);
  const totalTons = filtered.reduce((sum, s) => sum + (s.bags * Number(s.weight_kg)) / 1000, 0);
  const attentionCount = filtered.filter((s) => s.bags < 20).length;

  function openAdd() {
    setDialogIntent("add");
    setProduct("");
    setMode("add");
    setDestinationWarehouse("");
    setAmount("");
    setError("");
    setOpen(true);
  }

  function openAdjust(productId: number) {
    setDialogIntent("adjust");
    setProduct(String(productId));
    setMode("add");
    setDestinationWarehouse("");
    setAmount("");
    setError("");
    setOpen(true);
  }

  // Текущий остаток выбранного товара и каким он станет после операции.
  const currentBags = product ? (bagsByProduct.get(product) ?? 0) : null;
  const delta = Number(amount) || 0;
  const nextBags = currentBags === null ? null : mode === "add" ? currentBags + delta : currentBags - delta;
  const insufficient = mode !== "add" && nextBags !== null && nextBags < 0;
  const destination = destinationWarehouses.find((item) => String(item.id) === destinationWarehouse) ?? null;
  const destinationCurrentBags =
    mode === "transfer" && product && destination && allStock
      ? (allStock.find((item) => item.product === Number(product) && item.warehouse === destination.id)?.bags ?? 0)
      : null;

  async function submitAdjust(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedWarehouse || !product || delta <= 0 || insufficient || (mode === "transfer" && !destination)) return;
    setBusy(true);
    setError("");
    try {
      if (mode === "transfer") {
        if (!destination) return;
        await api.post("/stock/transfer/", {
          from_warehouse: selectedWarehouse.id,
          to_warehouse: destination.id,
          product: Number(product),
          bags: delta,
        });
      } else {
        await api.post("/stock/adjust/", {
          ...(legacyWarehouseMode ? {} : { warehouse: selectedWarehouseId }),
          product: Number(product),
          delta: mode === "add" ? delta : -delta,
        });
      }
      setOpen(false);
      await Promise.all([reload(), reloadAllStock()]);
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  const hasFilters = Boolean(search || grade || packaging);

  function resetFilters() {
    setSearch("");
    setGrade("");
    setPackaging("");
  }

  const productReferencesLoading = products === null;
  const addButton =
    canAdjust && canBrowseCatalog && selectedWarehouse ? (
      <Button
        size="sm"
        aria-label={`Добавить товар на склад ${selectedWarehouse.name}`}
        onClick={openAdd}
        disabled={productReferencesLoading || availableProducts.length === 0}
        title={
          productReferencesLoading
            ? "Загружаем каталог"
            : availableProducts.length === 0
              ? "Все товары уже распределены по складам"
              : undefined
        }
      >
        <Plus className="size-4" /> <span className="hidden sm:inline">Добавить товар</span>
      </Button>
    ) : undefined;

  const manageButton =
    canAdjust && !legacyWarehouseMode ? (
      <Button size="sm" variant="outline" onClick={() => setWarehouseManagerOpen(true)} disabled={!warehouses}>
        <Settings2 className="size-4" /> <span className="hidden sm:inline">Управление</span>
      </Button>
    ) : undefined;
  const pageActions =
    manageButton || addButton ? (
      <div className="flex items-center gap-2">
        {manageButton}
        {addButton}
      </div>
    ) : undefined;

  const warehouseManager = legacyWarehouseMode ? null : (
    <WarehouseManagerModal
      open={warehouseManagerOpen}
      warehouses={warehouses ?? []}
      onClose={() => setWarehouseManagerOpen(false)}
      onSaved={async (saved, created) => {
        await reloadWarehouses();
        if (created && saved.is_active) selectWarehouse(saved.id);
      }}
    />
  );

  if (!warehouses) {
    return (
      <AppShell title="Склады" section="Работа" description="Остатки готовой продукции по местам хранения.">
        <DataGate loading={warehousesLoading} error={warehousesError} onRetry={reloadWarehouses} />
      </AppShell>
    );
  }

  if (!selectedWarehouse) {
    return (
      <AppShell
        title="Склады"
        section="Работа"
        description="Остатки готовой продукции по местам хранения."
        actions={pageActions}
      >
        <Card>
          <CardContent className="flex flex-col items-center justify-center px-5 py-14 text-center">
            <Building2 className="size-10 text-[var(--muted-foreground)]/45" />
            <h2 className="mt-3 font-semibold">Нет активных складов</h2>
            <p className="mt-1 max-w-md text-sm text-[var(--muted-foreground)]">
              {canAdjust
                ? "Создайте первый активный склад, чтобы распределить товары."
                : "Попросите администратора настроить доступный склад."}
            </p>
            {canAdjust && (
              <Button className="mt-4" size="sm" onClick={() => setWarehouseManagerOpen(true)}>
                <Plus className="size-4" /> Настроить склады
              </Button>
            )}
          </CardContent>
        </Card>
        {warehouseManager}
      </AppShell>
    );
  }

  const warehouseSelector = (
    <Card className="mb-5 overflow-hidden">
      <CardContent className="p-0">
        <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5">
          <div className="min-w-0">
            <div className="text-xs font-medium uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
              Выбранный склад
            </div>
            <div className="mt-1 flex items-center gap-2 text-base font-semibold">
              <Building2 className="size-4 text-[var(--primary)]" />
              <span className="truncate">{selectedWarehouse.name}</span>
            </div>
          </div>
          <Select
            aria-label="Склад"
            className="w-full sm:w-72"
            value={String(selectedWarehouse.id)}
            onChange={(event) => selectWarehouse(Number(event.target.value))}
          >
            {activeWarehouses.map((warehouse) => (
              <option key={warehouse.id} value={warehouse.id}>
                {warehouse.name}
                {warehouse.is_default ? " · основной" : ""}
              </option>
            ))}
          </Select>
        </div>
      </CardContent>
    </Card>
  );

  if (!stock) {
    return (
      <AppShell
        title="Склады"
        section="Работа"
        description="Остатки готовой продукции по местам хранения."
        actions={pageActions}
      >
        {warehouseSelector}
        <DataGate loading={stockLoading} error={loadError} onRetry={reload} />
        {warehouseManager}
      </AppShell>
    );
  }

  return (
    <AppShell
      title="Склады"
      section="Работа"
      description="Остатки готовой продукции по местам хранения."
      actions={pageActions}
    >
      {warehouseSelector}
      {/* Сводка всегда следует текущему набору фильтров. */}
      <div className="mb-5 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <StatCard
          label="Товаров"
          value={String(filtered.length)}
          caption={hasFilters ? `из ${items.length} по фильтру` : selectedWarehouse.name}
          icon={Boxes}
        />
        <StatCard
          label="Мешков"
          value={formatMoney(totalBags)}
          caption={hasFilters ? "по текущему фильтру" : "всего в наличии"}
          icon={Package}
        />
        <StatCard
          label="Расчётный вес"
          value={`${totalTons.toFixed(2)} т`}
          caption="по количеству мешков"
          icon={Scale}
          accent
        />
        <StatCard
          label="Требует внимания"
          value={String(attentionCount)}
          caption="нет или меньше 20 мешков"
          icon={AlertTriangle}
          className={attentionCount > 0 ? "border-[var(--warning)]/35 bg-[var(--warning)]/8" : undefined}
        />
      </div>

      {/* Поиск и список объединены в один рабочий блок. */}
      <Card className="overflow-hidden">
        <CardContent className="p-0">
          <div className="border-b bg-[var(--muted)]/20 p-4 sm:p-5">
            <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold">Товары · {selectedWarehouse.name}</h2>
                <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                  {hasFilters
                    ? `Показано ${sorted.length} из ${items.length} товаров`
                    : `Всего позиций в учёте: ${items.length}`}
                </p>
              </div>
              {hasFilters && (
                <Button size="sm" variant="ghost" onClick={resetFilters}>
                  <X className="size-4" /> Сбросить фильтры
                </Button>
              )}
            </div>

            <div className="grid gap-3 lg:grid-cols-[minmax(260px,1.5fr)_minmax(170px,0.75fr)_minmax(170px,0.75fr)]">
              <label className="grid gap-1.5">
                <span className="text-xs font-medium text-[var(--muted-foreground)]">Поиск</span>
                <div className="relative">
                  <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
                  <Input
                    className="pl-9 pr-9"
                    placeholder="Название, цвет или фасовка"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                  {search && (
                    <button
                      type="button"
                      onClick={() => setSearch("")}
                      className="absolute right-2 top-1/2 inline-flex size-7 -translate-y-1/2 items-center justify-center rounded-md text-[var(--muted-foreground)] outline-none hover:bg-[var(--accent)] hover:text-[var(--foreground)] focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/50"
                      aria-label="Очистить поиск"
                    >
                      <X className="size-3.5" />
                    </button>
                  )}
                </div>
              </label>
              <label className="grid gap-1.5">
                <span className="text-xs font-medium text-[var(--muted-foreground)]">Сорт</span>
                <Select value={grade} onChange={(e) => setGrade(e.target.value)}>
                  <option value="">Все сорта</option>
                  {grades.map((g) => (
                    <option key={g} value={g}>
                      {g}
                    </option>
                  ))}
                </Select>
              </label>
              <label className="grid gap-1.5">
                <span className="text-xs font-medium text-[var(--muted-foreground)]">Фасовка</span>
                <Select value={packaging} onChange={(e) => setPackaging(e.target.value)}>
                  <option value="">Все фасовки</option>
                  {packagings.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </Select>
              </label>
            </div>
          </div>

          {/* Мобильные карточки */}
          <div className="flex flex-col divide-y md:hidden">
            {sorted.map((s) => {
              const st = stockTone(s.bags);
              const tons = (s.bags * Number(s.weight_kg)) / 1000;
              return (
                <div key={s.id} className="flex flex-col gap-4 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="font-semibold">{s.grade}</div>
                      <div className="mt-1 text-xs text-[var(--muted-foreground)]">
                        {s.color_label} · {s.packaging}
                      </div>
                      <div className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-[var(--primary)]">
                        <Building2 className="size-3" /> {s.warehouse_name || selectedWarehouse.name}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      <Badge tone={st.tone} dot>
                        {st.label}
                      </Badge>
                      {canAdjust && <StockActionMenu onEdit={() => openAdjust(s.product)} />}
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3 rounded-lg bg-[var(--muted)]/45 p-3 text-sm">
                    <div>
                      <div className="text-xs text-[var(--muted-foreground)]">Остаток</div>
                      <div className="mt-0.5 font-semibold tabular-nums">{formatMoney(s.bags)} меш.</div>
                    </div>
                    <div>
                      <div className="text-xs text-[var(--muted-foreground)]">Расчётный вес</div>
                      <div className="mt-0.5 font-medium tabular-nums">{tons.toFixed(2)} т</div>
                    </div>
                  </div>
                </div>
              );
            })}
            {filtered.length === 0 && (
              <EmptyStockState
                hasFilters={hasFilters}
                canAdjust={canAdjust && canBrowseCatalog && availableProducts.length > 0}
                onReset={resetFilters}
                onAdd={openAdd}
              />
            )}
          </div>

          {/* Таблица остатков (десктоп) */}
          <Table className="hidden md:table">
            <THead>
              <TR>
                <SortableHeader
                  label="Товар"
                  sortKey="product_label"
                  activeKey={sortKey}
                  dir={sortDir}
                  onClick={toggleSort}
                />
                <TH>Фасовка</TH>
                <SortableHeader
                  label="Остаток"
                  sortKey="bags"
                  activeKey={sortKey}
                  dir={sortDir}
                  onClick={toggleSort}
                  align="right"
                />
                <TH className="text-right">Расчётный вес</TH>
                <TH>Статус</TH>
                {canAdjust && <TH className="text-right">Действие</TH>}
              </TR>
            </THead>
            <TBody>
              {sorted.map((s) => {
                const st = stockTone(s.bags);
                const tons = (s.bags * Number(s.weight_kg)) / 1000;
                return (
                  <TR key={s.id}>
                    <TD>
                      <div className="font-medium">{s.grade}</div>
                      <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">{s.color_label}</div>
                      <div className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-[var(--primary)]">
                        <Building2 className="size-3" /> {s.warehouse_name || selectedWarehouse.name}
                      </div>
                    </TD>
                    <TD>{s.packaging}</TD>
                    <TD className="text-right tabular-nums font-semibold">
                      {formatMoney(s.bags)} <span className="font-normal text-[var(--muted-foreground)]">меш.</span>
                    </TD>
                    <TD className="text-right tabular-nums text-[var(--muted-foreground)]">{tons.toFixed(2)} т</TD>
                    <TD>
                      <Badge tone={st.tone} dot>
                        {st.label}
                      </Badge>
                    </TD>
                    {canAdjust && (
                      <TD className="text-right">
                        <StockActionMenu onEdit={() => openAdjust(s.product)} />
                      </TD>
                    )}
                  </TR>
                );
              })}
              {filtered.length === 0 && (
                <TR>
                  <TD colSpan={canAdjust ? 6 : 5} className="p-0">
                    <EmptyStockState
                      hasFilters={hasFilters}
                      canAdjust={canAdjust && canBrowseCatalog && availableProducts.length > 0}
                      onReset={resetFilters}
                      onAdd={openAdd}
                    />
                  </TD>
                </TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      {/* модалка корректировки */}
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        eyebrow="Операция со складом"
        title={dialogIntent === "add" ? "Добавить товар" : "Изменить остаток"}
        description={
          dialogIntent === "add"
            ? `Выберите товар и укажите количество для склада «${selectedWarehouse.name}».`
            : `Примите, спишите или переместите товар со склада «${selectedWarehouse.name}».`
        }
      >
        <form onSubmit={submitAdjust} className="flex flex-col gap-4">
          <Field label="Товар" htmlFor={dialogIntent === "add" ? "stock-product" : undefined}>
            {dialogIntent === "adjust" ? (
              <div className="flex min-h-10 items-center rounded-md border bg-[var(--muted)]/45 px-3.5 py-2 text-sm font-medium">
                {items.find((item) => String(item.product) === product)?.product_label}
              </div>
            ) : (
              <Select
                id="stock-product"
                value={product}
                autoFocus
                onChange={(e) => setProduct(e.target.value)}
                required
              >
                <option value="">
                  {availableProducts.length === 0 && products !== null ? "Все товары уже добавлены" : "Выберите товар"}
                </option>
                {availableProducts.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>

          {dialogIntent === "adjust" && (
            <div className="grid gap-1.5">
              <span className="text-sm font-medium">Тип операции</span>
              <div className="grid gap-2 sm:grid-cols-3">
                {(
                  [
                    ["add", "Приёмка", "Добавить на склад", ArrowUp],
                    ["remove", "Списание", "Убрать со склада", ArrowDown],
                    ["transfer", "Перемещение", "Передать на другой склад", ArrowRightLeft],
                  ] as const
                )
                  .filter(([m]) => m !== "transfer" || (!legacyWarehouseMode && destinationWarehouses.length > 0))
                  .map(([m, label, hint, Icon]) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => {
                        setMode(m);
                        setDestinationWarehouse("");
                      }}
                      aria-pressed={mode === m}
                      className={cn(
                        "flex items-center gap-2.5 rounded-lg border p-3 text-left outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/40 sm:flex-col sm:items-start",
                        mode === m && m === "add" && "border-[var(--success)]/50 bg-[var(--success)]/8",
                        mode === m && m === "remove" && "border-[var(--destructive)]/40 bg-[var(--destructive)]/7",
                        mode === m && m === "transfer" && "border-[var(--primary)]/45 bg-[var(--primary)]/7",
                        mode !== m && "hover:bg-[var(--muted)]/40",
                      )}
                    >
                      <span
                        className={cn(
                          "flex size-8 shrink-0 items-center justify-center rounded-md bg-[var(--muted)] text-[var(--muted-foreground)]",
                          mode === m && m === "add" && "bg-[var(--success)]/12 text-[var(--success)]",
                          mode === m && m === "remove" && "bg-[var(--destructive)]/12 text-[var(--destructive)]",
                          mode === m && m === "transfer" && "bg-[var(--primary)]/12 text-[var(--primary)]",
                        )}
                      >
                        <Icon className="size-4" />
                      </span>
                      <span>
                        <span className="block text-sm font-medium">{label}</span>
                        <span className="block text-xs text-[var(--muted-foreground)]">{hint}</span>
                      </span>
                    </button>
                  ))}
              </div>
            </div>
          )}

          {mode === "transfer" && (
            <Field label="Склад назначения" htmlFor="stock-destination">
              <Select
                id="stock-destination"
                value={destinationWarehouse}
                onChange={(event) => setDestinationWarehouse(event.target.value)}
                required
              >
                <option value="">Выберите склад</option>
                {destinationWarehouses.map((warehouse) => (
                  <option key={warehouse.id} value={warehouse.id}>
                    {warehouse.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}

          <Field label="Количество мешков" htmlFor="stock-amount">
            <Input
              id="stock-amount"
              type="number"
              min="1"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="Например, 50"
              required
            />
          </Field>
          <div className="flex flex-wrap gap-2">
            {QUICK_AMOUNTS.map((n) => (
              <button
                key={n}
                type="button"
                onClick={() => setAmount(String(n))}
                className={cn(
                  "rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
                  amount === String(n)
                    ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
                    : "text-[var(--muted-foreground)] hover:bg-[var(--accent)]",
                )}
              >
                {mode === "add" ? "+" : mode === "remove" ? "−" : "→ "}
                {n}
              </button>
            ))}
          </div>

          {/* сейчас → станет */}
          {currentBags !== null && mode !== "transfer" && (
            <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 rounded-lg border bg-[var(--muted)]/30 p-3 text-sm">
              <div>
                <div className="text-xs text-[var(--muted-foreground)]">Сейчас</div>
                <div className="mt-0.5 font-semibold tabular-nums">{formatMoney(currentBags)} меш.</div>
              </div>
              <ArrowRight className="size-4 text-[var(--muted-foreground)]" />
              <div className="text-right">
                <div className="text-xs text-[var(--muted-foreground)]">После операции</div>
                <div className={cn("mt-0.5 font-semibold tabular-nums", insufficient && "text-[var(--destructive)]")}>
                  {delta > 0 ? `${formatMoney(nextBags!)} меш.` : "—"}
                </div>
              </div>
            </div>
          )}
          {currentBags !== null && mode === "transfer" && (
            <div className="rounded-lg border bg-[var(--muted)]/30 p-3 text-sm">
              <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-3">
                <div className="min-w-0">
                  <div className="truncate text-xs font-medium text-[var(--muted-foreground)]">
                    Откуда · {selectedWarehouse.name}
                  </div>
                  <div className="mt-1 font-semibold tabular-nums">
                    {formatMoney(currentBags)}
                    <span className="font-normal text-[var(--muted-foreground)]"> → </span>
                    <span className={cn(insufficient && "text-[var(--destructive)]")}>
                      {delta > 0 ? formatMoney(nextBags!) : "—"}
                    </span>{" "}
                    меш.
                  </div>
                </div>
                <span className="flex size-8 items-center justify-center rounded-full bg-[var(--primary)]/10 text-[var(--primary)]">
                  <ArrowRight className="size-4" />
                </span>
                <div className="min-w-0 text-right">
                  <div className="truncate text-xs font-medium text-[var(--muted-foreground)]">
                    Куда · {destination?.name ?? "выберите склад"}
                  </div>
                  <div className="mt-1 font-semibold tabular-nums">
                    {destinationCurrentBags === null ? (
                      "—"
                    ) : (
                      <>
                        {formatMoney(destinationCurrentBags)}
                        <span className="font-normal text-[var(--muted-foreground)]"> → </span>
                        {formatMoney(destinationCurrentBags + delta)} меш.
                      </>
                    )}
                  </div>
                </div>
              </div>
              {destination && delta > 0 && (
                <p className="mt-2 border-t pt-2 text-xs text-[var(--muted-foreground)]">
                  {formatMoney(delta)} меш. будут перенесены без изменения общего остатка.
                </p>
              )}
            </div>
          )}
          {insufficient && (
            <p className="text-sm text-[var(--destructive)]">
              Нельзя {mode === "transfer" ? "переместить" : "списать"} больше, чем есть на складе.
            </p>
          )}
          {error && (
            <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2 border-t pt-4">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button
              type="submit"
              variant={mode === "remove" ? "destructive" : "default"}
              disabled={
                busy ||
                !selectedWarehouse ||
                !product ||
                delta <= 0 ||
                insufficient ||
                (mode === "transfer" && !destination)
              }
            >
              {busy
                ? "Сохранение…"
                : mode === "add"
                  ? `${dialogIntent === "add" ? "Добавить" : "Принять"}${delta > 0 ? ` ${formatMoney(delta)} меш.` : ""}`
                  : mode === "remove"
                    ? `Списать${delta > 0 ? ` ${formatMoney(delta)} меш.` : ""}`
                    : `Переместить${delta > 0 ? ` ${formatMoney(delta)} меш.` : ""}`}
            </Button>
          </div>
        </form>
      </Modal>

      {warehouseManager}
    </AppShell>
  );
}

function WarehouseManagerModal({
  open,
  warehouses,
  onClose,
  onSaved,
}: {
  open: boolean;
  warehouses: Warehouse[];
  onClose: () => void;
  onSaved: (warehouse: Warehouse, created: boolean) => void | Promise<void>;
}) {
  const [editing, setEditing] = useState<Warehouse | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setEditing(null);
    setName("");
    setError("");
  }, [open, warehouses.length]);

  function startCreate() {
    setEditing(null);
    setName("");
    setError("");
  }

  function startEdit(warehouse: Warehouse) {
    setEditing(warehouse);
    setName(warehouse.name);
    setError("");
  }

  async function saveWarehouse(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError("");
    try {
      const created = editing === null;
      const response = editing
        ? await api.patch<Warehouse>(`/warehouses/${editing.id}/`, { name: name.trim() })
        : await api.post<Warehouse>("/warehouses/", { name: name.trim() });
      await onSaved(response.data, created);
      onClose();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Справочник"
      title="Управление складами"
      description="Создавайте понятные места хранения — сотрудникам достаточно названия."
      className="max-w-3xl"
    >
      <div className="grid gap-5 md:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <section className="min-w-0">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">Склады</h3>
            <Button size="sm" variant="outline" onClick={startCreate}>
              <Plus className="size-3.5" /> Новый
            </Button>
          </div>
          <div className="flex max-h-72 flex-col gap-2 overflow-y-auto pr-1">
            {warehouses.map((warehouse) => (
              <button
                key={warehouse.id}
                type="button"
                onClick={() => startEdit(warehouse)}
                aria-pressed={editing?.id === warehouse.id}
                className={cn(
                  "flex items-start gap-3 rounded-lg border p-3 text-left outline-none transition hover:bg-[var(--muted)]/45 focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/40",
                  editing?.id === warehouse.id && "border-[var(--primary)] bg-[var(--primary)]/5",
                )}
              >
                <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-[var(--muted)] text-[var(--muted-foreground)]">
                  <Building2 className="size-4" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-1.5">
                    <span className="truncate text-sm font-medium">{warehouse.name}</span>
                    {warehouse.is_default && <Badge tone="success">Основной</Badge>}
                    {!warehouse.is_active && <Badge>Неактивен</Badge>}
                  </span>
                </span>
                <Pencil className="mt-1 size-3.5 shrink-0 text-[var(--muted-foreground)]" />
              </button>
            ))}
            {warehouses.length === 0 && (
              <div className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-[var(--muted-foreground)]">
                Склады ещё не созданы.
              </div>
            )}
          </div>
        </section>

        <form
          onSubmit={saveWarehouse}
          className="flex min-w-0 flex-col gap-4 border-t pt-5 md:border-l md:border-t-0 md:pl-5 md:pt-0"
        >
          <div>
            <h3 className="text-sm font-semibold">{editing ? "Изменить склад" : "Новый склад"}</h3>
            <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
              Название будет видно во всех складских операциях.
            </p>
          </div>
          <Field label="Название" htmlFor="warehouse-name">
            <Input
              id="warehouse-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Например, Мельница 2"
              autoFocus
              required
            />
          </Field>
          {error && (
            <p role="alert" className="text-sm text-[var(--destructive)]">
              {error}
            </p>
          )}
          <div className="mt-auto flex justify-end gap-2 border-t pt-4">
            <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
              Отмена
            </Button>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy ? "Сохранение…" : editing ? "Сохранить" : "Создать склад"}
            </Button>
          </div>
        </form>
      </div>
    </Modal>
  );
}

function StockActionMenu({ onEdit }: { onEdit: () => void }) {
  return (
    <ActionMenu
      label="Действия с товаром"
      items={[{ key: "edit", label: "Изменить", icon: Pencil, onSelect: onEdit }]}
    />
  );
}

function EmptyStockState({
  hasFilters,
  canAdjust,
  onReset,
  onAdd,
}: {
  hasFilters: boolean;
  canAdjust: boolean;
  onReset: () => void;
  onAdd: () => void;
}) {
  return (
    <div className="flex flex-col items-center px-4 py-12 text-center">
      <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-[var(--muted)] text-[var(--muted-foreground)]">
        {hasFilters ? <Search className="size-5" /> : <Boxes className="size-5" />}
      </div>
      <div className="font-medium">{hasFilters ? "Товары не найдены" : "Склад пока пуст"}</div>
      <p className="mt-1 max-w-sm text-sm text-[var(--muted-foreground)]">
        {hasFilters
          ? "Измените запрос или сбросьте фильтры, чтобы увидеть другие товары."
          : "Проведите первую приёмку, чтобы добавить остатки готовой продукции."}
      </p>
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        {hasFilters ? (
          <Button size="sm" variant="outline" onClick={onReset}>
            <X className="size-4" /> Сбросить фильтры
          </Button>
        ) : canAdjust ? (
          <Button size="sm" onClick={onAdd}>
            <Plus className="size-4" /> Добавить товар
          </Button>
        ) : null}
      </div>
    </div>
  );
}

export default function WarehousePage() {
  return (
    <Suspense fallback={<p className="text-sm text-[var(--muted-foreground)]">Загрузка складов…</p>}>
      <RequirePerm perm="warehouse.view" title="Склады">
        <WarehousePageInner />
      </RequirePerm>
    </Suspense>
  );
}
