"use client";
import { useMemo, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/field";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { ErrorAlert } from "@/components/ui/data-state";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { cn } from "@/lib/utils";
import { ArrowDown, ArrowUp, Pencil, Search, SlidersHorizontal } from "lucide-react";
import type { StockItem, Product } from "@/lib/types";

// Статус остатка: нет / мало (<20 мешков) / в наличии.
function stockTone(bags: number): { tone: "destructive" | "warning" | "success"; label: string } {
  if (bags <= 0) return { tone: "destructive", label: "Нет" };
  if (bags < 20) return { tone: "warning", label: "Мало" };
  return { tone: "success", label: "В наличии" };
}

const QUICK_AMOUNTS = [10, 50, 100, 500];

function WarehousePageInner() {
  const { data: stock, error: loadError, reload } = useApi<StockItem[]>("/stock/");
  const { data: products } = useApi<Product[]>("/products/");
  const { me } = useAuth();
  const canAdjust = can(me, "warehouse.adjust");

  // фильтры
  const [search, setSearch] = useState("");
  const [grade, setGrade] = useState("");
  const [packaging, setPackaging] = useState("");

  // модалка корректировки: открывается пустой или с товаром из строки
  const [open, setOpen] = useState(false);
  const [product, setProduct] = useState("");
  const [mode, setMode] = useState<"add" | "remove">("add");
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const items = stock ?? [];
  const grades = useMemo(() => Array.from(new Set(items.map((s) => s.grade))).filter(Boolean), [items]);
  const packagings = useMemo(() => Array.from(new Set(items.map((s) => s.packaging))).filter(Boolean), [items]);
  const bagsByProduct = useMemo(
    () => new Map(items.map((s) => [String(s.product), s.bags])), [items]);

  const filtered = items.filter((s) =>
    (!search || s.product_label.toLowerCase().includes(search.toLowerCase())) &&
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

  function openAdjust(productId?: number) {
    setProduct(productId ? String(productId) : "");
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

  const hasFilters = search || grade || packaging;

  const adjustButton = canAdjust ? (
    <Button size="sm" aria-label="Изменить остаток" onClick={() => openAdjust()}>
      <SlidersHorizontal className="size-4" /> <span className="hidden sm:inline">Изменить остаток</span>
    </Button>
  ) : undefined;

  return (
    <AppShell title="Остатки склада" section="Работа" description="Остатки готовой муки по сортам и фасовкам в мешках, с расчётным весом и статусом наличия."
      actions={adjustButton}>
      {/* шапка: stat-карточки */}
      <div className="mb-4">
        <div className="grid grid-cols-3 gap-3">
          <StatCard label="Позиций" value={String(filtered.length)} />
          <StatCard label="Мешков" value={formatMoney(totalBags)} />
          <StatCard label="Вес, т" value={totalTons.toFixed(2)} accent />
        </div>
      </div>

      {/* фильтры */}
      <Card className="mb-4">
        <CardContent className="pt-6">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
              <Input className="pl-8" placeholder="Быстрый поиск по товару"
                value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
            <Select value={grade} onChange={(e) => setGrade(e.target.value)}>
              <option value="">Все сорта</option>
              {grades.map((g) => <option key={g} value={g}>{g}</option>)}
            </Select>
            <Select value={packaging} onChange={(e) => setPackaging(e.target.value)}>
              <option value="">Все фасовки</option>
              {packagings.map((p) => <option key={p} value={p}>{p}</option>)}
            </Select>
          </div>
        </CardContent>
      </Card>

      {loadError && !stock && <div className="mb-4"><ErrorAlert message={loadError} onRetry={reload} /></div>}

      {/* мобильные карточки */}
      <div className="flex flex-col gap-3 md:hidden">
        {sorted.map((s) => {
          const st = stockTone(s.bags);
          const tons = (s.bags * Number(s.weight_kg)) / 1000;
          return (
            <div key={s.id} className="flex flex-col gap-2.5 rounded-xl border bg-[var(--card)] p-4 shadow-card">
              <div className="flex items-start justify-between gap-2">
                <div className="text-sm font-semibold">{s.product_label}</div>
                <Badge tone={st.tone}>{st.label}</Badge>
              </div>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div>
                  <div className="text-[11px] text-[var(--muted-foreground)]">Остаток</div>
                  <div className="font-semibold tabular-nums">{formatMoney(s.bags)} меш.</div>
                </div>
                <div>
                  <div className="text-[11px] text-[var(--muted-foreground)]">Вес</div>
                  <div className="tabular-nums">{tons.toFixed(2)} т</div>
                </div>
              </div>
              {canAdjust && (
                <Button size="sm" variant="outline" className="self-start"
                  onClick={() => openAdjust(s.product)}>
                  <Pencil className="size-3.5" /> Изменить остаток
                </Button>
              )}
            </div>
          );
        })}
        {filtered.length === 0 && (
          <div className="flex flex-col items-center gap-3 rounded-xl border bg-[var(--card)] py-10 text-center">
            <p className="text-sm text-[var(--muted-foreground)]">
              {hasFilters ? "Ничего не найдено по фильтрам." : "Склад пуст."}
            </p>
            {!hasFilters && canAdjust && (
              <Button size="sm" onClick={() => openAdjust()}>
                <SlidersHorizontal className="size-4" /> Внести первую приёмку
              </Button>
            )}
          </div>
        )}
      </div>

      {/* таблица остатков (десктоп) */}
      <Card className="hidden md:block">
        <CardContent className="pt-6">
          <Table>
            <THead>
              <TR>
                <TH>#</TH>
                <SortableHeader label="Товар" sortKey="product_label" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                <TH>Сорт</TH><TH>Фасовка</TH>
                <SortableHeader label="Остаток" sortKey="bags" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                <TH>Вес</TH><TH>Статус</TH>
                {canAdjust && <TH></TH>}
              </TR>
            </THead>
            <TBody>
              {sorted.map((s) => {
                const st = stockTone(s.bags);
                const tons = (s.bags * Number(s.weight_kg)) / 1000;
                return (
                  <TR key={s.id}>
                    <TD className="text-[var(--muted-foreground)]">{s.product}</TD>
                    <TD className="font-medium">{s.product_label}</TD>
                    <TD>{s.grade}</TD>
                    <TD>{s.packaging}</TD>
                    <TD className="tabular-nums font-medium">{formatMoney(s.bags)} меш.</TD>
                    <TD className="tabular-nums text-[var(--muted-foreground)]">{tons.toFixed(2)} т</TD>
                    <TD><Badge tone={st.tone}>{st.label}</Badge></TD>
                    {canAdjust && (
                      <TD>
                        <Button size="sm" variant="ghost" title="Изменить остаток"
                          onClick={() => openAdjust(s.product)}>
                          <Pencil className="size-4" />
                        </Button>
                      </TD>
                    )}
                  </TR>
                );
              })}
              {filtered.length === 0 && (
                <TR><TD colSpan={canAdjust ? 8 : 7} className="py-6 text-center text-[var(--muted-foreground)]">
                  {hasFilters ? "Ничего не найдено по фильтрам." : (
                    <span className="inline-flex flex-col items-center gap-3">
                      Склад пуст.
                      {canAdjust && (
                        <Button size="sm" onClick={() => openAdjust()}>
                          <SlidersHorizontal className="size-4" /> Внести первую приёмку
                        </Button>
                      )}
                    </span>
                  )}
                </TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      {/* модалка корректировки */}
      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Склад · Корректировка"
        title="Изменить остаток"
        description="Приёмка добавляет мешки на склад, списание — убирает.">
        <form onSubmit={submitAdjust} className="flex flex-col gap-4">
          <Field label="Товар">
            <Select value={product} autoFocus
              onChange={(e) => setProduct(e.target.value)} required>
              <option value="">Выберите товар</option>
              {(products ?? []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </Select>
          </Field>

          {/* операция — как переключатель транспорта в заказе */}
          <div className="grid grid-cols-2 gap-2">
            {([["add", "Приёмка", ArrowUp], ["remove", "Списание", ArrowDown]] as const).map(([m, label, Icon]) => (
              <button key={m} type="button" onClick={() => setMode(m)}
                className={cn(
                  "flex items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-medium transition-colors",
                  mode === m ? "border-[var(--primary)] bg-[var(--primary)]/5" : "hover:bg-[var(--muted)]/40"
                )}>
                <Icon className="size-4" /> {label}
              </button>
            ))}
          </div>

          <Field label="Мешков">
            <Input type="number" min="1" value={amount}
              onChange={(e) => setAmount(e.target.value)} placeholder="0" required />
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
                {n}
              </button>
            ))}
          </div>

          {/* сейчас → станет */}
          {currentBags !== null && (
            <div className="flex items-center justify-between rounded-lg border bg-[var(--muted)]/30 px-3 py-2.5 text-sm">
              <span className="text-[var(--muted-foreground)]">
                Сейчас: <b className="tabular-nums text-[var(--foreground)]">{formatMoney(currentBags)} меш.</b>
              </span>
              {delta > 0 && (
                <span className={insufficient ? "text-[var(--destructive)]" : "text-[var(--muted-foreground)]"}>
                  Станет: <b className={cn("tabular-nums", insufficient ? "" : "text-[var(--foreground)]")}>
                    {formatMoney(nextBags!)} меш.
                  </b>
                </span>
              )}
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
            <Button type="submit" disabled={busy || !product || delta <= 0 || insufficient}>
              {busy ? "Сохранение…"
                : mode === "add"
                  ? `Добавить${delta > 0 ? ` ${formatMoney(delta)} меш.` : ""}`
                  : `Списать${delta > 0 ? ` ${formatMoney(delta)} меш.` : ""}`}
            </Button>
          </div>
        </form>
      </Modal>
    </AppShell>
  );
}

export default function WarehousePage() {
  return <RequirePerm perm="warehouse.view" title="Склад"><WarehousePageInner /></RequirePerm>;
}
