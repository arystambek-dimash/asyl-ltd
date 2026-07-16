"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/field";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
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
  Boxes,
  MoreHorizontal,
  Package,
  Pencil,
  Plus,
  Scale,
  Search,
  Trash2,
  X,
} from "lucide-react";
import type { StockItem, Product } from "@/lib/types";

// Статус остатка: нет / мало (<20 мешков) / в наличии.
function stockTone(bags: number): { tone: "destructive" | "warning" | "success"; label: string } {
  if (bags <= 0) return { tone: "destructive", label: "Нет" };
  if (bags < 20) return { tone: "warning", label: "Мало" };
  return { tone: "success", label: "В наличии" };
}

const QUICK_AMOUNTS = [10, 50, 100, 500];

function WarehousePageInner() {
  const { data: stock, loading: stockLoading, error: loadError, reload } = useApi<StockItem[]>("/stock/");
  const { data: products } = useApi<Product[]>("/products/");
  const { me } = useAuth();
  const canAdjust = can(me, "warehouse.adjust");

  // фильтры
  const [search, setSearch] = useState("");
  const [grade, setGrade] = useState("");
  const [packaging, setPackaging] = useState("");

  // Верхняя кнопка добавляет товар, карандаш изменяет конкретную строку.
  const [open, setOpen] = useState(false);
  const [dialogIntent, setDialogIntent] = useState<"add" | "adjust">("add");
  const [product, setProduct] = useState("");
  const [mode, setMode] = useState<"add" | "remove">("add");
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [deleteItem, setDeleteItem] = useState<StockItem | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const [deleteBusy, setDeleteBusy] = useState(false);

  const items = useMemo(() => stock ?? [], [stock]);
  const stockedProductIds = new Set(items.map((item) => item.product));
  const availableProducts = (products ?? []).filter((item) => !stockedProductIds.has(item.id));
  const grades = useMemo(() => Array.from(new Set(items.map((s) => s.grade))).filter(Boolean), [items]);
  const packagings = useMemo(() => Array.from(new Set(items.map((s) => s.packaging))).filter(Boolean), [items]);
  const bagsByProduct = useMemo(
    () => new Map(items.map((s) => [String(s.product), s.bags])), [items]);

  const normalizedSearch = search.trim().toLowerCase();
  const filtered = items.filter((s) =>
    (!normalizedSearch || [s.product_label, s.grade, s.color_label, s.packaging]
      .some((value) => value.toLowerCase().includes(normalizedSearch))) &&
    (!grade || s.grade === grade) &&
    (!packaging || s.packaging === packaging)
  );

  const [sortKey, setSortKey] = useState("product_label");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
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
    setMode("add"); setAmount(""); setError("");
    setOpen(true);
  }

  function openAdjust(productId: number) {
    setDialogIntent("adjust");
    setProduct(String(productId));
    setMode("add"); setAmount(""); setError("");
    setOpen(true);
  }

  // Текущий остаток выбранного товара и каким он станет после операции.
  const currentBags = product ? (bagsByProduct.get(product) ?? 0) : null;
  const delta = Number(amount) || 0;
  const nextBags = currentBags === null ? null
    : mode === "add" ? currentBags + delta : currentBags - delta;
  const insufficient = mode === "remove" && nextBags !== null && nextBags < 0;

  async function submitAdjust(e: React.FormEvent) {
    e.preventDefault();
    if (!product || delta <= 0 || insufficient) return;
    setBusy(true); setError("");
    try {
      await api.post("/stock/adjust/", {
        product: Number(product),
        delta: mode === "add" ? delta : -delta,
      });
      setOpen(false);
      reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  async function confirmDelete() {
    if (!deleteItem) return;
    setDeleteBusy(true); setDeleteError("");
    try {
      await api.delete(`/stock/${deleteItem.id}/`);
      setDeleteItem(null);
      reload();
    } catch (e) { setDeleteError(apiError(e)); } finally { setDeleteBusy(false); }
  }

  const hasFilters = Boolean(search || grade || packaging);

  function resetFilters() {
    setSearch("");
    setGrade("");
    setPackaging("");
  }

  const addButton = canAdjust ? (
    <Button size="sm" aria-label="Добавить товар на склад" onClick={openAdd}
      disabled={products !== null && availableProducts.length === 0}
      title={products !== null && availableProducts.length === 0 ? "Все товары уже добавлены на склад" : undefined}>
      <Plus className="size-4" /> <span className="hidden sm:inline">Добавить товар</span>
    </Button>
  ) : undefined;

  if (!stock) {
    return (
      <AppShell
        title="Остатки склада"
        section="Работа"
        description="Актуальное количество готовой продукции и быстрые операции приёмки и списания."
        actions={addButton}
      >
        <DataGate loading={stockLoading} error={loadError} onRetry={reload} />
      </AppShell>
    );
  }

  return (
    <AppShell title="Остатки склада" section="Работа" description="Актуальное количество готовой продукции и быстрые операции приёмки и списания."
      actions={addButton}>
      {/* Сводка всегда следует текущему набору фильтров. */}
      <div className="mb-5 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <StatCard
          label="Товаров"
          value={String(filtered.length)}
          caption={hasFilters ? `из ${items.length} по фильтру` : "на складе"}
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
                <h2 className="text-base font-semibold">Товары на складе</h2>
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
                  {grades.map((g) => <option key={g} value={g}>{g}</option>)}
                </Select>
              </label>
              <label className="grid gap-1.5">
                <span className="text-xs font-medium text-[var(--muted-foreground)]">Фасовка</span>
                <Select value={packaging} onChange={(e) => setPackaging(e.target.value)}>
                  <option value="">Все фасовки</option>
                  {packagings.map((p) => <option key={p} value={p}>{p}</option>)}
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
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      <Badge tone={st.tone} dot>{st.label}</Badge>
                      {canAdjust && (
                        <StockActionMenu
                          onEdit={() => openAdjust(s.product)}
                          onDelete={() => { setDeleteError(""); setDeleteItem(s); }}
                        />
                      )}
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
              <EmptyStockState hasFilters={hasFilters} canAdjust={canAdjust}
                onReset={resetFilters} onAdd={openAdd} />
            )}
          </div>

          {/* Таблица остатков (десктоп) */}
          <Table className="hidden md:table">
            <THead>
              <TR>
                <SortableHeader label="Товар" sortKey="product_label" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                <TH>Фасовка</TH>
                <SortableHeader label="Остаток" sortKey="bags" activeKey={sortKey} dir={sortDir} onClick={toggleSort} align="right" />
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
                    </TD>
                    <TD>{s.packaging}</TD>
                    <TD className="text-right tabular-nums font-semibold">{formatMoney(s.bags)} <span className="font-normal text-[var(--muted-foreground)]">меш.</span></TD>
                    <TD className="text-right tabular-nums text-[var(--muted-foreground)]">{tons.toFixed(2)} т</TD>
                    <TD><Badge tone={st.tone} dot>{st.label}</Badge></TD>
                    {canAdjust && (
                      <TD className="text-right">
                        <StockActionMenu
                          onEdit={() => openAdjust(s.product)}
                          onDelete={() => { setDeleteError(""); setDeleteItem(s); }}
                        />
                      </TD>
                    )}
                  </TR>
                );
              })}
              {filtered.length === 0 && (
                <TR><TD colSpan={canAdjust ? 6 : 5} className="p-0">
                  <EmptyStockState hasFilters={hasFilters} canAdjust={canAdjust}
                    onReset={resetFilters} onAdd={openAdd} />
                </TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      {/* модалка корректировки */}
      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Операция со складом"
        title={dialogIntent === "add" ? "Добавить товар" : "Изменить остаток"}
        description={dialogIntent === "add"
          ? "Выберите товар и укажите количество мешков для добавления на склад."
          : "Добавьте поступление или спишите выбранный товар."}>
        <form onSubmit={submitAdjust} className="flex flex-col gap-4">
          <Field label="Товар">
            {dialogIntent === "adjust" ? (
              <div className="flex min-h-10 items-center rounded-md border bg-[var(--muted)]/45 px-3.5 py-2 text-sm font-medium">
                {items.find((item) => String(item.product) === product)?.product_label}
              </div>
            ) : (
              <Select value={product} autoFocus
                onChange={(e) => setProduct(e.target.value)} required>
                <option value="">
                  {availableProducts.length === 0 && products !== null
                    ? "Все товары уже добавлены"
                    : "Выберите товар"}
                </option>
                {availableProducts.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </Select>
            )}
          </Field>

          {dialogIntent === "adjust" && <div className="grid gap-1.5">
            <span className="text-sm font-medium">Тип операции</span>
            <div className="grid grid-cols-2 gap-2">
              {([["add", "Приёмка", "Добавить на склад", ArrowUp], ["remove", "Списание", "Убрать со склада", ArrowDown]] as const).map(([m, label, hint, Icon]) => (
                <button key={m} type="button" onClick={() => setMode(m)}
                  aria-pressed={mode === m}
                  className={cn(
                    "flex items-center gap-3 rounded-lg border p-3 text-left outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/40",
                    mode === m && m === "add" && "border-[var(--success)]/50 bg-[var(--success)]/8",
                    mode === m && m === "remove" && "border-[var(--destructive)]/40 bg-[var(--destructive)]/7",
                    mode !== m && "hover:bg-[var(--muted)]/40"
                  )}>
                  <span className={cn(
                    "flex size-8 shrink-0 items-center justify-center rounded-md bg-[var(--muted)] text-[var(--muted-foreground)]",
                    mode === m && m === "add" && "bg-[var(--success)]/12 text-[var(--success)]",
                    mode === m && m === "remove" && "bg-[var(--destructive)]/12 text-[var(--destructive)]"
                  )}>
                    <Icon className="size-4" />
                  </span>
                  <span>
                    <span className="block text-sm font-medium">{label}</span>
                    <span className="block text-xs text-[var(--muted-foreground)]">{hint}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>}

          <Field label="Количество мешков">
            <Input type="number" min="1" value={amount}
              onChange={(e) => setAmount(e.target.value)} placeholder="Например, 50" required />
          </Field>
          <div className="flex flex-wrap gap-2">
            {QUICK_AMOUNTS.map((n) => (
              <button key={n} type="button"
                onClick={() => setAmount(String(n))}
                className={cn(
                  "rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
                  amount === String(n)
                    ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
                    : "text-[var(--muted-foreground)] hover:bg-[var(--accent)]"
                )}>
                {mode === "add" ? "+" : "−"}{n}
              </button>
            ))}
          </div>

          {/* сейчас → станет */}
          {currentBags !== null && (
            <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 rounded-lg border bg-[var(--muted)]/30 p-3 text-sm">
              <div>
                <div className="text-xs text-[var(--muted-foreground)]">Сейчас</div>
                <div className="mt-0.5 font-semibold tabular-nums">{formatMoney(currentBags)} меш.</div>
              </div>
              <ArrowRight className="size-4 text-[var(--muted-foreground)]" />
              <div className="text-right">
                <div className="text-xs text-[var(--muted-foreground)]">После операции</div>
                <div className={cn(
                  "mt-0.5 font-semibold tabular-nums",
                  insufficient && "text-[var(--destructive)]"
                )}>
                  {delta > 0 ? `${formatMoney(nextBags!)} меш.` : "—"}
                </div>
              </div>
            </div>
          )}
          {insufficient && (
            <p className="text-sm text-[var(--destructive)]">
              Нельзя списать больше, чем есть на складе.
            </p>
          )}
          {error && (
            <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2 border-t pt-4">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>Отмена</Button>
            <Button type="submit" variant={mode === "remove" ? "destructive" : "default"}
              disabled={busy || !product || delta <= 0 || insufficient}>
              {busy ? "Сохранение…"
                : mode === "add"
                  ? `${dialogIntent === "add" ? "Добавить" : "Принять"}${delta > 0 ? ` ${formatMoney(delta)} меш.` : ""}`
                  : `Списать${delta > 0 ? ` ${formatMoney(delta)} меш.` : ""}`}
            </Button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        open={!!deleteItem}
        onClose={() => { if (!deleteBusy) setDeleteItem(null); }}
        title="Удалить товар со склада?"
        description={deleteItem
          ? `${deleteItem.product_label}: позиция и текущий остаток ${formatMoney(deleteItem.bags)} меш. будут удалены со склада. Товар останется в каталоге, и его можно будет добавить снова.`
          : ""}
        confirmLabel="Удалить"
        busy={deleteBusy}
        error={deleteError}
        onConfirm={confirmDelete}
      />
    </AppShell>
  );
}

function StockActionMenu({ onEdit, onDelete }: { onEdit: () => void; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent | TouchEvent) => {
      const target = event.target as Node;
      if (!buttonRef.current?.contains(target) && !menuRef.current?.contains(target)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const closeOnViewportChange = () => setOpen(false);
    document.addEventListener("mousedown", closeOutside);
    document.addEventListener("touchstart", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeOnViewportChange);
    window.addEventListener("scroll", closeOnViewportChange, true);
    return () => {
      document.removeEventListener("mousedown", closeOutside);
      document.removeEventListener("touchstart", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeOnViewportChange);
      window.removeEventListener("scroll", closeOnViewportChange, true);
    };
  }, [open]);

  function toggleMenu() {
    if (!open && buttonRef.current) {
      const rect = buttonRef.current.getBoundingClientRect();
      const menuWidth = 176;
      const menuHeight = 82;
      const left = Math.max(8, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 8));
      const top = window.innerHeight - rect.bottom < menuHeight + 8
        ? rect.top - menuHeight - 4
        : rect.bottom + 4;
      setPosition({ top, left });
    }
    setOpen((value) => !value);
  }

  return (
    <>
      <button ref={buttonRef} type="button" onClick={toggleMenu}
        aria-label="Действия с товаром" aria-haspopup="menu" aria-expanded={open}
        className="inline-flex size-8 items-center justify-center rounded-md text-[var(--muted-foreground)] outline-none transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)] focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/50">
        <MoreHorizontal className="size-4" />
      </button>
      {open && createPortal(
        <div ref={menuRef} role="menu" style={{ top: position.top, left: position.left }}
          className="fixed z-[120] w-44 rounded-lg border bg-[var(--card)] p-1 shadow-lg">
          <button type="button" role="menuitem"
            onClick={() => { setOpen(false); onEdit(); }}
            className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm hover:bg-[var(--muted)]">
            <Pencil className="size-4" /> Изменить
          </button>
          <button type="button" role="menuitem"
            onClick={() => { setOpen(false); onDelete(); }}
            className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-[var(--destructive)] hover:bg-[var(--destructive)]/10">
            <Trash2 className="size-4" /> Удалить
          </button>
        </div>,
        document.body
      )}
    </>
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
  return <RequirePerm perm="warehouse.view" title="Склад"><WarehousePageInner /></RequirePerm>;
}
