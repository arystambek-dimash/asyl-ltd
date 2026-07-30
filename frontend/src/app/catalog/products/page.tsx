"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/field";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Tabs } from "@/components/ui/tabs";
import { ErrorAlert } from "@/components/ui/data-state";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { api, apiError } from "@/lib/api";
import { Plus, Check, Pencil, Archive, ArchiveRestore } from "lucide-react";
import type { Product } from "@/lib/types";

function ProductsPageInner() {
  const { data: products, error: loadError, reload } = useApi<Product[]>("/products/");
  const { data: archived, reload: reloadArchived } = useApi<Product[]>("/products/?archived=1");
  const { me } = useAuth();
  const canCreate = can(me, "catalog.create");
  const canEdit = can(me, "catalog.edit");
  const canViewColor = can(me, "orders.create");

  const [tab, setTab] = useState<"active" | "archive">("active");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Product | null>(null);
  const [name, setName] = useState("");
  const [color, setColor] = useState("Red");
  const [weight, setWeight] = useState("50");
  const [askWeight, setAskWeight] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [arcItem, setArcItem] = useState<Product | null>(null);
  const [arcError, setArcError] = useState("");
  const [arcBusy, setArcBusy] = useState(false);
  const [restoreError, setRestoreError] = useState("");
  const [restoreBusyId, setRestoreBusyId] = useState<number | null>(null);

  function openNew() {
    setEditing(null);
    setName("");
    setColor("Red");
    setWeight("50");
    setAskWeight(false);
    setError("");
    setOpen(true);
  }
  function openEdit(p: Product) {
    setEditing(p);
    setName(p.name);
    setColor(p.color ?? "Red");
    setWeight(String(Number(p.weight_kg)));
    setAskWeight(p.ask_truck_weight ?? false);
    setError("");
    setOpen(true);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = {
        name,
        weight_kg: weight,
        ask_truck_weight: askWeight,
        ...(canViewColor ? { color } : editing ? {} : { color: "Red" }),
      };
      if (editing) await api.patch(`/products/${editing.id}/`, body);
      else await api.post("/products/", body);
      setOpen(false);
      reload();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  async function confirmArchive() {
    if (!arcItem) return;
    setArcBusy(true);
    setArcError("");
    try {
      await api.post(`/products/${arcItem.id}/archive/`);
      setArcItem(null);
      reload();
      reloadArchived();
    } catch (e) {
      setArcError(apiError(e));
    } finally {
      setArcBusy(false);
    }
  }

  async function restore(p: Product) {
    setRestoreBusyId(p.id);
    setRestoreError("");
    try {
      await api.post(`/products/${p.id}/restore/`);
      reload();
      reloadArchived();
    } catch (e) {
      setRestoreError(apiError(e));
    } finally {
      setRestoreBusyId(null);
    }
  }

  const [sortKey, setSortKey] = useState("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const list = products ?? [];
  const archiveList = archived ?? [];
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else {
      setSortKey(k);
      setSortDir("asc");
    }
  };
  const sorted = [...list].sort((a, b) => {
    const cmp = a.name.localeCompare(b.name, "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  return (
    <AppShell
      title="Товары"
      section="Работа"
      description={
        canViewColor
          ? "Номенклатура: сорт, цвет и фасовка. Цены закрепляются отдельно в прайс-листе каждого клиента."
          : "Номенклатура: сорт и фасовка. Цены закрепляются отдельно в прайс-листе каждого клиента."
      }
      tabs={
        <Tabs
          active={tab}
          onChange={(k) => setTab(k as "active" | "archive")}
          tabs={[
            { key: "active", label: "Товары", icon: Check },
            { key: "archive", label: "Архив", icon: Archive },
          ]}
        />
      }
      actions={
        canCreate && (
          <Button size="sm" onClick={openNew} aria-label="Создать товар">
            <Plus className="size-4" /> <span className="hidden sm:inline">Создать товар</span>
          </Button>
        )
      }
    >
      <div className="mb-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <StatCard label="Активных товаров" value={String(list.length)} accent />
          <StatCard label="В архиве" value={String(archiveList.length)} />
        </div>
      </div>

      {loadError && !products && (
        <div className="mb-4">
          <ErrorAlert message={loadError} onRetry={reload} />
        </div>
      )}

      {tab === "archive" ? (
        <Card>
          <CardContent className="pt-6">
            {restoreError && (
              <p className="mb-3 rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
                {restoreError}
              </p>
            )}
            <div className="space-y-3 md:hidden">
              {archiveList.map((product) => (
                <article
                  key={product.id}
                  className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-4 shadow-card"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className="font-semibold">{product.name}</h3>
                      <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                        {canViewColor && product.color_label ? `${product.color_label} · ` : ""}
                        <span className="tabular-nums">{Number(product.weight_kg)} кг</span>
                      </p>
                    </div>
                    <Badge tone="muted">В архиве</Badge>
                  </div>
                  {canEdit && (
                    <Button
                      className="mt-4 w-full"
                      variant="outline"
                      disabled={restoreBusyId === product.id}
                      onClick={() => restore(product)}
                    >
                      <ArchiveRestore className="size-4" /> Восстановить
                    </Button>
                  )}
                </article>
              ))}
              {archiveList.length === 0 && (
                <div className="rounded-2xl border border-dashed border-[var(--border)] px-4 py-10 text-center text-sm text-[var(--muted-foreground)]">
                  Архив пуст.
                </div>
              )}
            </div>

            <div className="hidden md:block">
              <Table>
                <THead>
                  <TR>
                    <TH>Название</TH>
                    {canViewColor && <TH>Цвет</TH>}
                    <TH>Фасовка</TH>
                    <TH></TH>
                  </TR>
                </THead>
                <TBody>
                  {archiveList.map((p) => (
                    <TR key={p.id}>
                      <TD className="font-medium">{p.name}</TD>
                      {canViewColor && <TD>{p.color_label}</TD>}
                      <TD className="tabular-nums">{Number(p.weight_kg)} кг</TD>
                      <TD>
                        <div className="flex items-center justify-end gap-1">
                          <Badge tone="muted">В архиве</Badge>
                          {canEdit && (
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={restoreBusyId === p.id}
                              onClick={() => restore(p)}
                            >
                              <ArchiveRestore className="size-4" /> Восстановить
                            </Button>
                          )}
                        </div>
                      </TD>
                    </TR>
                  ))}
                  {archiveList.length === 0 && (
                    <TR>
                      <TD colSpan={canViewColor ? 4 : 3} className="py-4 text-center text-[var(--muted-foreground)]">
                        Архив пуст.
                      </TD>
                    </TR>
                  )}
                </TBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="pt-6">
            <div className="space-y-3 md:hidden">
              {sorted.map((product) => (
                <article
                  key={product.id}
                  className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-4 shadow-card"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className="font-semibold">{product.name}</h3>
                      <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                        {canViewColor && product.color_label ? `${product.color_label} · ` : ""}
                        <span className="tabular-nums">{Number(product.weight_kg)} кг</span>
                      </p>
                    </div>
                    {canEdit && (
                      <div className="flex shrink-0 items-center gap-1">
                        <Button
                          size="icon"
                          variant="ghost"
                          onClick={() => openEdit(product)}
                          aria-label={`Изменить товар ${product.name}`}
                        >
                          <Pencil className="size-4" />
                        </Button>
                        <Button
                          size="icon"
                          variant="ghost"
                          className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                          onClick={() => {
                            setArcError("");
                            setArcItem(product);
                          }}
                          aria-label={`Отправить товар ${product.name} в архив`}
                        >
                          <Archive className="size-4" />
                        </Button>
                      </div>
                    )}
                  </div>
                  <div className="mt-4 flex items-center justify-between rounded-xl bg-[var(--muted)]/55 px-3 py-2 text-sm">
                    <span className="text-[var(--muted-foreground)]">Контроль веса машины</span>
                    <Badge tone={product.ask_truck_weight ? "primary" : "muted"}>
                      {product.ask_truck_weight ? "Включён" : "Не нужен"}
                    </Badge>
                  </div>
                </article>
              ))}
              {sorted.length === 0 && (
                <div className="rounded-2xl border border-dashed border-[var(--border)] px-4 py-10 text-center text-sm text-[var(--muted-foreground)]">
                  Товаров пока нет.
                </div>
              )}
            </div>

            <div className="hidden md:block">
              <Table>
                <THead>
                  <TR>
                    <SortableHeader
                      label="Название"
                      sortKey="name"
                      activeKey={sortKey}
                      dir={sortDir}
                      onClick={toggleSort}
                    />
                    {canViewColor && <TH>Цвет</TH>}
                    <TH>Фасовка</TH>
                    <TH></TH>
                  </TR>
                </THead>
                <TBody>
                  {sorted.map((p) => (
                    <TR key={p.id}>
                      <TD className="font-medium">{p.name}</TD>
                      {canViewColor && <TD>{p.color_label}</TD>}
                      <TD className="tabular-nums">{Number(p.weight_kg)} кг</TD>
                      <TD>
                        <div className="flex items-center justify-end gap-1">
                          {canEdit && (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => openEdit(p)}
                              aria-label={`Изменить товар ${p.name}`}
                              title="Изменить"
                            >
                              <Pencil className="size-4" />
                            </Button>
                          )}
                          {canEdit && (
                            <Button
                              size="sm"
                              variant="ghost"
                              className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                              onClick={() => {
                                setArcError("");
                                setArcItem(p);
                              }}
                              aria-label={`Отправить товар ${p.name} в архив`}
                              title="В архив"
                            >
                              <Archive className="size-4" />
                            </Button>
                          )}
                        </div>
                      </TD>
                    </TR>
                  ))}
                  {sorted.length === 0 && (
                    <TR>
                      <TD colSpan={canViewColor ? 4 : 3} className="py-4 text-center text-[var(--muted-foreground)]">
                        Товаров пока нет.
                      </TD>
                    </TR>
                  )}
                </TBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        eyebrow={editing ? "Номенклатура · Изменение" : "Номенклатура · Товар"}
        title={editing ? "Изменить товар" : "Новый товар"}
        description={canViewColor ? "Сорт, цвет (тип) и фасовка." : "Сорт и фасовка."}
        footer={
          <>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button type="submit" form="product-form" disabled={busy}>
              {busy ? "Сохранение…" : editing ? "Сохранить" : "Создать"}
            </Button>
          </>
        }
      >
        <form id="product-form" onSubmit={save} className="flex flex-col gap-4">
          <Field label="Название" htmlFor="product-name">
            <Input
              id="product-name"
              value={name}
              autoFocus
              placeholder="напр. Высший сорт"
              onChange={(e) => setName(e.target.value)}
              required
            />
          </Field>
          <div className={`grid grid-cols-1 gap-3 ${canViewColor ? "sm:grid-cols-2" : ""}`}>
            {canViewColor && (
              <Field label="Цвет (тип)" htmlFor="product-color">
                <Select id="product-color" value={color} onChange={(e) => setColor(e.target.value)}>
                  <option value="Red">Красный</option>
                  <option value="Green">Зелёный</option>
                  <option value="Blue">Синий</option>
                </Select>
              </Field>
            )}
            <Field label="Фасовка" htmlFor="product-weight">
              <Select id="product-weight" value={weight} onChange={(e) => setWeight(e.target.value)}>
                <option value="50">50 кг</option>
                <option value="25">25 кг</option>
                <option value="10">10 кг</option>
                <option value="5">5 кг</option>
                <option value="2">2 кг</option>
              </Select>
            </Field>
          </div>
          <label className="flex min-h-12 cursor-pointer items-start gap-2.5 rounded-xl border border-[var(--border)] p-3">
            <input
              type="checkbox"
              className="mt-0.5 size-4 accent-[var(--primary)]"
              checked={askWeight}
              onChange={(e) => setAskWeight(e.target.checked)}
            />
            <span className="text-sm">
              <span className="font-medium">Спрашивать вес машины при въезде</span>
              <span className="block text-xs text-[var(--muted-foreground)]">
                Если выключено — вес не спрашивается, берётся расчётный по мешкам.
              </span>
            </span>
          </label>
          {error && (
            <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
              {error}
            </p>
          )}
        </form>
      </Modal>

      <ConfirmDialog
        open={!!arcItem}
        onClose={() => setArcItem(null)}
        title="Отправить товар в архив?"
        description={
          arcItem
            ? `«${arcItem.label}» уйдёт в архив: пропадёт из выбора новых заказов. Старые заказы и отчёты не изменятся. Можно восстановить.`
            : ""
        }
        busy={arcBusy}
        error={arcError}
        onConfirm={confirmArchive}
      />
    </AppShell>
  );
}

export default function ProductsPage() {
  return (
    <RequirePerm perm="catalog.view" title="Товары">
      <ProductsPageInner />
    </RequirePerm>
  );
}
