"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { EmptyRow, Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, useSortState } from "@/components/ui/sortable-header";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  EMPTY_PRODUCT_DRAFT,
  ProductFields,
  canViewProductColor,
  productPayload,
  type ProductDraft,
} from "@/components/catalog/product-fields";
import { ProductAliasCodes, ProductAliasesEditor } from "@/components/catalog/product-aliases";
import { ProductPhoto, ProductPhotoPicker } from "@/components/catalog/product-photo";
import { saveProductPhoto } from "@/lib/product-photo";
import { Tabs } from "@/components/ui/tabs";
import { DataGate, ErrorAlert, FormError } from "@/components/ui/data-state";
import { useApi } from "@/lib/use-api";
import { useConfirmAction } from "@/lib/use-confirm-action";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { api, apiError } from "@/lib/api";
import { Plus, Check, Pencil, Archive, ArchiveRestore } from "lucide-react";
import type { Product, Warehouse } from "@/lib/types";

function ProductsPageInner() {
  const { data: products, loading, error: loadError, reload, setData: setProducts } = useApi<Product[]>("/products/");
  const {
    data: archived,
    loading: archivedLoading,
    error: archivedLoadError,
    reload: reloadArchived,
  } = useApi<Product[]>("/products/?archived=1");
  const { me } = useAuth();
  const canCreate = can(me, "catalog.create");
  const canEdit = can(me, "catalog.edit");
  const canViewColor = canViewProductColor(me);
  // Новый товар сразу приходуется на склад — если у сотрудника есть право корректировки.
  const canStock = can(me, "warehouse.adjust");
  const { data: warehouseData } = useApi<Warehouse[]>(canStock ? "/warehouses/" : null);
  const warehouses = (warehouseData ?? []).filter((item) => item.is_active);

  const [tab, setTab] = useState<"active" | "archive">("active");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Product | null>(null);
  const [draft, setDraft] = useState<ProductDraft>(EMPTY_PRODUCT_DRAFT);
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [photoRemoved, setPhotoRemoved] = useState(false);
  const [stockBags, setStockBags] = useState("");
  const [stockWarehouse, setStockWarehouse] = useState("");
  // Товар, созданный в этом окне: если приход не прошёл, повтор досоздаст только приход.
  const [createdId, setCreatedId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [restoreError, setRestoreError] = useState("");
  const [restoreBusyId, setRestoreBusyId] = useState<number | null>(null);

  function openNew() {
    setEditing(null);
    setDraft(EMPTY_PRODUCT_DRAFT);
    setPhotoFile(null);
    setPhotoRemoved(false);
    setStockBags("");
    setStockWarehouse("");
    setCreatedId(null);
    setError("");
    setOpen(true);
  }
  function openEdit(p: Product) {
    setEditing(p);
    setDraft({ name: p.name, color: p.color ?? "Red", weight: String(Number(p.weight_kg)) });
    setPhotoFile(null);
    setPhotoRemoved(false);
    setStockBags("");
    setCreatedId(null);
    setError("");
    setOpen(true);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = productPayload(draft, { canViewColor, editing: Boolean(editing) });
      const saved = editing
        ? await api.patch<Product>(`/products/${editing.id}/`, body)
        : await api.post<Product>("/products/", body);
      // Фото — отдельный запрос: товар уже сохранён, и повтор после ошибки фото
      // пойдёт правкой этого товара, а не созданием дубля.
      setEditing(saved.data);
      if (!editing) setCreatedId(saved.data.id);
      await saveProductPhoto(saved.data.id, { file: photoFile, removed: photoRemoved && Boolean(editing?.photo_url) });
      const bags = Number(stockBags);
      if (stockStepVisible && bags > 0) {
        const warehouseId = stockWarehouse || defaultWarehouseId;
        await api.post("/stock/adjust/", {
          ...(warehouseId ? { warehouse: Number(warehouseId) } : {}),
          product: saved.data.id,
          delta: bags,
        });
        setStockBags("");
      }
      setOpen(false);
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
      reload();
    }
  }

  /** Коды в отчётах сохраняются сразу: ответ — свежий товар для окна и списка. */
  function applyProduct(product: Product) {
    setEditing(product);
    setProducts((products ?? []).map((item) => (item.id === product.id ? product : item)));
  }

  const stockStepVisible = canStock && (!editing || editing.id === createdId);
  const defaultWarehouseId = String((warehouses.find((item) => item.is_default) ?? warehouses[0])?.id ?? "");
  const stockBagsInvalid = stockBags !== "" && !(Number.isInteger(Number(stockBags)) && Number(stockBags) >= 0);

  const archive = useConfirmAction<Product>(async (product) => {
    await api.post(`/products/${product.id}/archive/`);
    reload();
    reloadArchived();
  });

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

  const { sortKey, sortDir, toggleSort } = useSortState("name", "asc");
  const list = products ?? [];
  const archiveList = archived ?? [];
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
          <StatCard label="Активных товаров" value={products ? String(list.length) : "—"} accent />
          <StatCard label="В архиве" value={archived ? String(archiveList.length) : "—"} />
        </div>
      </div>

      {tab === "archive" ? (
        <Card>
          <CardContent className="pt-6">
            <FormError message={restoreError} className="mb-3" />
            {!archived ? (
              <DataGate loading={archivedLoading} error={archivedLoadError} onRetry={reloadArchived} />
            ) : (
              <>
                {archivedLoadError && (
                  <div className="mb-4">
                    <ErrorAlert message={archivedLoadError} onRetry={reloadArchived} />
                  </div>
                )}
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
                    {archiveList.length === 0 && <EmptyRow colSpan={canViewColor ? 4 : 3}>Архив пуст.</EmptyRow>}
                  </TBody>
                </Table>
              </>
            )}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="pt-6">
            {!products ? (
              <DataGate loading={loading} error={loadError} onRetry={reload} />
            ) : (
              <>
                {loadError && (
                  <div className="mb-4">
                    <ErrorAlert message={loadError} onRetry={reload} />
                  </div>
                )}
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
                      <TH>Коды в отчётах</TH>
                      <TH></TH>
                    </TR>
                  </THead>
                  <TBody>
                    {sorted.map((p) => (
                      <TR key={p.id}>
                        <TD className="font-medium">
                          <span className="flex items-center gap-3">
                            <ProductPhoto
                              url={p.photo_url}
                              alt={p.name}
                              className="size-10 shrink-0 rounded-md"
                              iconClassName="size-4"
                            />
                            {p.name}
                          </span>
                        </TD>
                        {canViewColor && <TD>{p.color_label}</TD>}
                        <TD className="tabular-nums">{Number(p.weight_kg)} кг</TD>
                        <TD>
                          <ProductAliasCodes aliases={p.aliases} />
                        </TD>
                        <TD>
                          <div className="flex items-center justify-end gap-1">
                            {canEdit && (
                              <Button size="sm" variant="ghost" onClick={() => openEdit(p)} title="Изменить">
                                <Pencil className="size-4" />
                              </Button>
                            )}
                            {canEdit && (
                              <Button
                                size="sm"
                                variant="ghost"
                                className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                                onClick={() => archive.open(p)}
                                title="В архив"
                              >
                                <Archive className="size-4" />
                              </Button>
                            )}
                          </div>
                        </TD>
                      </TR>
                    ))}
                    {sorted.length === 0 && <EmptyRow colSpan={canViewColor ? 5 : 4}>Товаров пока нет.</EmptyRow>}
                  </TBody>
                </Table>
              </>
            )}
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
            <Button type="submit" form="product-form" disabled={busy || (stockStepVisible && stockBagsInvalid)}>
              {busy ? "Сохранение…" : editing ? "Сохранить" : "Создать"}
            </Button>
          </>
        }
      >
        <form id="product-form" onSubmit={save} className="flex flex-col gap-4">
          <ProductPhotoPicker
            currentUrl={editing?.photo_url}
            file={photoFile}
            removed={photoRemoved}
            disabled={busy}
            onPick={(file) => {
              setPhotoFile(file);
              setPhotoRemoved(false);
            }}
            onRemove={() => {
              setPhotoFile(null);
              setPhotoRemoved(true);
            }}
          />
          <ProductFields idPrefix="product" draft={draft} onChange={setDraft} canViewColor={canViewColor} autoFocus />
          {editing && canEdit && <ProductAliasesEditor product={editing} onChange={applyProduct} />}
          {stockStepVisible && (
            <div className="flex flex-col gap-3 rounded-lg border bg-[var(--muted)]/30 p-3">
              <div>
                <div className="text-sm font-medium">Сколько добавить на склад?</div>
                <div className="text-xs text-[var(--muted-foreground)]">
                  Мешков этого товара уже в наличии. Оставьте пустым — добавите позже на странице «Склады».
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {warehouses.length > 1 && (
                  <Field label="Склад" htmlFor="product-stock-warehouse">
                    <Select
                      id="product-stock-warehouse"
                      value={stockWarehouse || defaultWarehouseId}
                      onChange={(e) => setStockWarehouse(e.target.value)}
                    >
                      {warehouses.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                          {item.is_default ? " · основной" : ""}
                        </option>
                      ))}
                    </Select>
                  </Field>
                )}
                <Field
                  label="Количество мешков"
                  htmlFor="product-stock-bags"
                  error={stockBagsInvalid ? "Целое число мешков, 0 или больше." : undefined}
                  className={warehouses.length > 1 ? undefined : "sm:col-span-2"}
                >
                  <Input
                    id="product-stock-bags"
                    type="number"
                    min="0"
                    step="1"
                    inputMode="numeric"
                    placeholder="Например, 100"
                    value={stockBags}
                    onChange={(e) => setStockBags(e.target.value)}
                    aria-invalid={stockBagsInvalid || undefined}
                  />
                </Field>
              </div>
            </div>
          )}
          <FormError message={error} />
        </form>
      </Modal>

      <ConfirmDialog
        {...archive.dialog}
        title="Отправить товар в архив?"
        description={
          archive.item
            ? `«${archive.item.label}» уйдёт в архив: пропадёт из выбора новых заказов. Старые заказы и отчёты не изменятся. Можно восстановить.`
            : ""
        }
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
