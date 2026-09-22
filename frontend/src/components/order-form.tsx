"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  Building2,
  CalendarClock,
  Check,
  Info,
  PackageOpen,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Store as StoreIcon,
  Trash2,
  Truck,
  UserRound,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Segmented } from "@/components/ui/segmented";
import { LicensePlateInput } from "@/components/ui/license-plate-input";
import { DataGate } from "@/components/ui/data-state";
import {
  FixationFields,
  emptyFixationDraft,
  fixationBody,
  fixationDraftError,
  type FixationDraft,
} from "@/components/orders/fixation-fields";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { cn, formatCurrency, todayLocalIsoDate, currencySymbol } from "@/lib/utils";
import { useAuth } from "@/store/auth";
import type { Client, Department, Order, Product, Store, Warehouse } from "@/lib/types";

type Row = { id: number; product: string; quantity: string; price: string };
type OrderClientOption = Pick<Client, "id" | "name" | "company_name" | "phone" | "currency"> & {
  department_code?: string;
  department_name?: string;
};
type OrderProductOption = Pick<
  Product,
  "id" | "label" | "available_bags" | "stock_by_warehouse" | "warehouse" | "warehouse_name"
>;
type OrderStoreOption = Pick<Store, "id" | "client" | "name" | "address">;
type OrderDepartmentOption = Pick<Department, "id" | "code" | "name" | "color" | "is_default">;

interface OrderFormOptions {
  clients: OrderClientOption[];
  products: OrderProductOption[];
  stores: OrderStoreOption[];
  departments: OrderDepartmentOption[];
  warehouses?: Warehouse[];
}

const EMPTY_FORM_OPTIONS: OrderFormOptions = {
  clients: [],
  products: [],
  stores: [],
  departments: [],
  warehouses: [],
};

function productIsAssignedToWarehouse(product: OrderProductOption, warehouse: string) {
  if (!warehouse) return true;
  if (product.stock_by_warehouse !== undefined) {
    return Object.prototype.hasOwnProperty.call(product.stock_by_warehouse, warehouse);
  }
  return String(product.warehouse) === warehouse;
}

function productBagsAtWarehouse(product: OrderProductOption, warehouse: string) {
  if (warehouse && product.stock_by_warehouse !== undefined) {
    return product.stock_by_warehouse[warehouse] ?? 0;
  }
  return product.available_bags ?? 0;
}

function SectionTitle({
  icon: Icon,
  title,
  caption,
  aside,
}: {
  icon: React.ElementType;
  title: string;
  caption?: string;
  aside?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex items-center gap-2.5">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-600">
          <Icon className="size-4" />
        </span>
        <div>
          <h3 className="text-sm font-bold text-slate-900">{title}</h3>
          {caption && <p className="text-xs text-slate-500">{caption}</p>}
        </div>
      </div>
      {aside}
    </div>
  );
}

export function OrderForm({
  editing,
  template,
  onCancel,
  onDone,
}: {
  editing?: Order | null;
  template?: Order | null;
  onCancel: () => void;
  onDone: () => void;
}) {
  const router = useRouter();
  const { me } = useAuth();
  const {
    data: formOptions,
    loading: formOptionsLoading,
    error: formOptionsError,
    reload: reloadFormOptions,
  } = useApi<OrderFormOptions>("/orders/form-options/");
  const { clients, products, stores, departments, warehouses = [] } = formOptions ?? EMPTY_FORM_OPTIONS;
  const source = editing ?? template;
  const nextRowId = useRef(source?.items.length ?? 1);
  const [clientSearch, setClientSearch] = useState("");
  const [clientPickerOpen, setClientPickerOpen] = useState(!source);
  const [dept, setDept] = useState(source?.department ?? "");
  const [client, setClient] = useState(source ? String(source.client) : "");
  const [currency, setCurrency] = useState<"KZT" | "USD">(source?.currency ?? "KZT");
  const [store, setStore] = useState(source?.store ? String(source.store) : "");
  const [warehouse, setWarehouse] = useState(source?.warehouse ? String(source.warehouse) : "");
  const [transport, setTransport] = useState<"truck" | "train">(source?.transport_type ?? "truck");
  const [truck, setTruck] = useState(source?.transport_type === "train" ? "" : (source?.truck_number ?? ""));
  const [wagonNumber, setWagonNumber] = useState(source?.transport_type === "train" ? source.truck_number : "");
  const [arrival, setArrival] = useState(editing?.arrival_date ?? (template ? todayLocalIsoDate() : ""));
  const [rows, setRows] = useState<Row[]>(
    source
      ? source.items.map((item, index) => ({
          id: index,
          product: String(item.product ?? ""),
          quantity: String(item.quantity),
          price: item.unit_price ?? item.price ?? "",
        }))
      : [{ id: 0, product: "", quantity: "", price: "" }],
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editReason, setEditReason] = useState("");
  // Заказ задним числом: дата, статус и оплата фиксируются вместе с созданием.
  const [backdateOn, setBackdateOn] = useState(false);
  const [fixation, setFixation] = useState<FixationDraft>(emptyFixationDraft);

  const canBackdate = !editing && can(me, "orders.edit");
  const canPay = can(me, "payments.create");
  const backdating = canBackdate && backdateOn;

  const compositionLocked = editing?.status === "loading";
  const shippedCorrection = editing?.status === "shipped";
  const physicalFieldsLocked = Boolean(editing && ["arrived", "loading", "loaded", "shipped"].includes(editing.status));
  const unchangedWagonNumber = editing && transport === editing.transport_type && wagonNumber === editing.truck_number;
  const wagonNumberInvalid =
    !physicalFieldsLocked &&
    !unchangedWagonNumber &&
    transport === "train" &&
    wagonNumber !== "" &&
    !/^[0-9]{8}$/.test(wagonNumber);

  const selectedClient = clients.find((item) => String(item.id) === client);
  const clientDepartment = departments.find((item) => item.code === selectedClient?.department_code);
  const assignedDepartment = !editing ? (clientDepartment ?? me?.sales_department) : null;
  const clientPricesUrl = client ? `/client-prices/?client=${client}&currency=${currency}` : null;
  const {
    data: loadedClientPrices,
    loading: clientPricesLoading,
    error: clientPricesError,
    reload: reloadClientPrices,
  } = useApi<Record<string, string>>(clientPricesUrl);
  const clientPrices = loadedClientPrices ?? {};

  const referenceDataReady = formOptions !== null;

  function reloadReferenceData() {
    void reloadFormOptions();
  }

  useEffect(() => {
    if (editing) return;
    if (assignedDepartment) {
      setDept(assignedDepartment.code);
      return;
    }
  }, [assignedDepartment, departments, dept, editing]);

  useEffect(() => {
    if (!warehouses.length || editing) return;
    if (warehouse && warehouses.some((item) => String(item.id) === warehouse)) return;
    const initial = warehouses.find((item) => item.is_default) ?? warehouses[0];
    setWarehouse(String(initial.id));
    if (template && products.some((item) => item.stock_by_warehouse !== undefined || item.warehouse !== undefined)) {
      setRows((current) =>
        current.map((row) => {
          const selected = products.find((item) => String(item.id) === row.product);
          if (!selected || productIsAssignedToWarehouse(selected, String(initial.id))) return row;
          return { ...row, product: "", price: "" };
        }),
      );
    }
  }, [editing, products, template, warehouse, warehouses]);

  useEffect(() => {
    if (!loadedClientPrices || editing) return;
    setRows((current) =>
      current.map((row) => (row.product ? { ...row, price: loadedClientPrices[row.product] ?? "" } : row)),
    );
  }, [editing, loadedClientPrices]);

  const normalizedSearch = clientSearch.trim().toLocaleLowerCase("ru");
  const filteredClients = clients.filter((item) => {
    if (!normalizedSearch) return true;
    return `${item.name} ${item.company_name || ""} ${item.phone || ""}`
      .toLocaleLowerCase("ru")
      .includes(normalizedSearch);
  });
  const clientStores = stores.filter((item) => String(item.client) === client);
  const selectedStore = clientStores.find((item) => String(item.id) === store);
  const selectedDepartment = departments.find((item) => item.code === dept);
  const editingWarehouseMissing = Boolean(
    editing?.warehouse && !warehouses.some((item) => item.id === editing.warehouse),
  );
  const warehouseOptions: Warehouse[] = editingWarehouseMissing
    ? [
        ...warehouses,
        {
          id: editing!.warehouse!,
          code: `inactive-${editing!.warehouse}`,
          name: editing!.warehouse_name || "Отключённый склад",
          address: "",
          is_active: false,
          is_default: false,
        },
      ]
    : warehouses;
  const productsHaveWarehouseScope = products.some(
    (item) => item.stock_by_warehouse !== undefined || item.warehouse !== undefined,
  );
  const warehouseProducts =
    productsHaveWarehouseScope && warehouse
      ? products.filter((item) => productIsAssignedToWarehouse(item, warehouse))
      : products;
  const validRows = rows.filter((row) => row.product && Number(row.quantity) > 0);
  const allPriced = validRows.every((row) => Number(row.price) > 0);
  const total = validRows.reduce((sum, row) => sum + Number(row.price || 0) * Number(row.quantity || 0), 0);
  const selectedBags = validRows.reduce((sum, row) => sum + Number(row.quantity || 0), 0);
  // Исторический заказ склад не списывает — товар без остатка тоже можно выбрать.
  const allowOutOfStock = shippedCorrection || backdating;
  const fixationError = backdating ? fixationDraftError(fixation, { shippedAlready: false }) : "";

  const clientPickerVisible = !editing && (!selectedClient || clientPickerOpen || !!clientSearch);
  const departmentLocked = physicalFieldsLocked || (!!selectedClient?.department_code && !editing);

  function chooseClient(item: OrderClientOption) {
    if (item.department_code) setDept(item.department_code);
    else if (selectedClient?.department_code) setDept(me?.sales_department?.code ?? "");
    setClient(String(item.id));
    setStore("");
    setCurrency(item.currency);
    setClientSearch("");
    setClientPickerOpen(false);
    setError("");
  }

  function updateRow(index: number, patch: Partial<Row>) {
    setRows((current) => current.map((item, itemIndex) => (itemIndex === index ? { ...item, ...patch } : item)));
  }

  function addRow() {
    setRows((current) => [...current, { id: nextRowId.current++, product: "", quantity: "", price: "" }]);
  }

  function validate(): string {
    if (!referenceDataReady) return "Сначала загрузите справочники заказа.";
    if (!editing && selectedClient?.department_code && !clientDepartment) {
      return "Отдел клиента недоступен. Проверьте его в карточке клиента.";
    }
    if (!client) return "Выберите клиента.";
    if (!dept) return "Выберите отдел продаж.";
    if (warehouseOptions.length > 0 && !warehouse) return "Выберите склад отгрузки.";
    if (wagonNumberInvalid) {
      return "Номер вагона должен содержать 8 цифр. Если номер пока неизвестен, оставьте поле пустым.";
    }
    if (!compositionLocked && !validRows.length) return "Добавьте хотя бы одну позицию.";
    if (!compositionLocked && !allPriced) return "Укажите цену для каждой позиции.";
    if (shippedCorrection && editReason.trim().length < 5) {
      return "Укажите причину изменения отгруженного заказа — минимум 5 символов.";
    }
    if (fixationError) return fixationError;
    return "";
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const problem = validate();
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const items = validRows.map((row) => ({
        product: Number(row.product),
        quantity: Number(row.quantity),
      }));
      const prices = Object.fromEntries(validRows.map((row) => [row.product, row.price]));
      const body = {
        store: store ? Number(store) : null,
        ...(warehouse ? { warehouse: Number(warehouse) } : {}),
        arrival_date: arrival || null,
        currency,
        ...(!physicalFieldsLocked
          ? {
              department: assignedDepartment?.code ?? dept,
              transport_type: transport,
              truck_number: transport === "train" ? wagonNumber : truck,
            }
          : {}),
        ...(!compositionLocked
          ? {
              items,
              prices,
              ...(shippedCorrection ? { edit_reason: editReason.trim() } : {}),
            }
          : {}),
        ...(!editing && template ? { template_order: template.id } : {}),
        ...(backdating ? { backdate: fixationBody(fixation) } : {}),
      };
      if (editing) {
        await api.patch(`/orders/${editing.id}/`, body);
        onDone();
      } else {
        const { data } = await api.post("/orders/", { ...body, client: Number(client) });
        onDone();
        router.push(`/orders/${data.id}`);
      }
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  const submitDisabled =
    busy ||
    !referenceDataReady ||
    !client ||
    !dept ||
    (warehouseOptions.length > 0 && !warehouse) ||
    (!compositionLocked && (!validRows.length || !allPriced)) ||
    (shippedCorrection && editReason.trim().length < 5) ||
    !!fixationError;

  return (
    <form onSubmit={submit} className="flex flex-col gap-6">
      {(!referenceDataReady || formOptionsError) && (
        <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
          <DataGate
            loading={!formOptionsError && formOptionsLoading}
            error={formOptionsError || undefined}
            onRetry={reloadReferenceData}
          />
        </div>
      )}

      {referenceDataReady && (
        <>
          {/* ── Клиент, отдел, магазин ───────────────────────────────────── */}
          <section className="space-y-3">
            <SectionTitle
              icon={UserRound}
              title="Клиент"
              caption={editing ? "Клиент и валюта закреплены за заказом." : "Найдите по имени, компании или телефону."}
              aside={
                assignedDepartment && (
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold text-emerald-700">
                    <ShieldCheck className="size-3.5" />
                    <span className="size-2 rounded-full" style={{ backgroundColor: assignedDepartment.color }} />
                    {assignedDepartment.name} · {clientDepartment ? "отдел клиента" : "ваш отдел"}
                  </span>
                )
              }
            />

            {selectedClient && !clientPickerVisible && (
              <div className="flex items-center gap-3 rounded-2xl border border-blue-200 bg-blue-50/70 p-3">
                <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-blue-600 text-sm font-black text-white">
                  {selectedClient.name.slice(0, 1).toUpperCase()}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-bold text-slate-900">{selectedClient.name}</div>
                  <div className="truncate text-xs text-slate-500">
                    {[selectedClient.company_name, selectedClient.phone].filter(Boolean).join(" · ") ||
                      "Без дополнительных данных"}
                  </div>
                </div>
                <span className="rounded-lg bg-white px-2 py-1 text-xs font-bold text-slate-600 shadow-sm">
                  {selectedClient.currency}
                </span>
                {!editing && (
                  <button
                    type="button"
                    onClick={() => setClientPickerOpen(true)}
                    className="text-xs font-semibold text-blue-700 hover:underline"
                  >
                    Изменить
                  </button>
                )}
              </div>
            )}

            {clientPickerVisible && (
              <div className="space-y-2">
                <div className="relative">
                  <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
                  <Input
                    value={clientSearch}
                    onChange={(event) => setClientSearch(event.target.value)}
                    placeholder="Поиск клиента…"
                    className="h-11 rounded-xl bg-white pl-10"
                    autoFocus={!client}
                    aria-label="Поиск клиента"
                  />
                </div>
                <div className="max-h-56 overflow-y-auto rounded-2xl border border-slate-200 bg-white p-1.5">
                  <div className="grid gap-0.5 sm:grid-cols-2">
                    {filteredClients.map((item) => {
                      const selected = String(item.id) === client;
                      return (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => chooseClient(item)}
                          className={cn(
                            "flex min-w-0 items-center gap-3 rounded-xl px-3 py-2 text-left transition",
                            selected ? "bg-blue-50 ring-1 ring-blue-200" : "hover:bg-slate-50",
                          )}
                        >
                          <span
                            className={cn(
                              "flex size-8 shrink-0 items-center justify-center rounded-lg text-xs font-black",
                              selected ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-500",
                            )}
                          >
                            {item.name.slice(0, 1).toUpperCase()}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-semibold text-slate-800">{item.name}</span>
                            <span className="block truncate text-[11px] text-slate-400">
                              {item.company_name || item.phone || "—"}
                            </span>
                          </span>
                          {selected && <Check className="size-4 shrink-0 text-blue-600" />}
                        </button>
                      );
                    })}
                  </div>
                  {!filteredClients.length && (
                    <div className="flex min-h-24 flex-col items-center justify-center text-center text-slate-400">
                      <Search className="mb-2 size-5" />
                      <span className="text-sm font-semibold">Ничего не найдено</span>
                      <span className="mt-0.5 text-xs">Проверьте имя или номер телефона.</span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {!assignedDepartment && (
              <div className="grid gap-1.5">
                <Label>Отдел продаж</Label>
                <div className="flex flex-wrap gap-1.5">
                  {departments.map((department) => (
                    <button
                      key={department.code}
                      type="button"
                      disabled={departmentLocked && department.code !== editing?.department}
                      onClick={() => setDept(department.code)}
                      className={cn(
                        "flex min-h-9 items-center gap-2 rounded-xl border px-3 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-60",
                        dept === department.code
                          ? "border-slate-900 bg-slate-900 text-white shadow-sm"
                          : "border-slate-200 bg-white text-slate-700 hover:border-slate-300",
                      )}
                    >
                      <span className="size-2 shrink-0 rounded-full" style={{ backgroundColor: department.color }} />
                      {department.name}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {client && clientStores.length > 0 && (
              <div className="grid gap-1.5">
                <Label>Магазин</Label>
                <div className="flex flex-wrap gap-1.5">
                  <button
                    type="button"
                    onClick={() => setStore("")}
                    className={cn(
                      "flex min-h-9 items-center gap-2 rounded-xl border px-3 text-sm font-semibold transition",
                      !store
                        ? "border-slate-900 bg-slate-900 text-white shadow-sm"
                        : "border-slate-200 bg-white text-slate-700 hover:border-slate-300",
                    )}
                  >
                    <Building2 className="size-4" /> Без магазина
                  </button>
                  {clientStores.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      title={item.address || undefined}
                      onClick={() => setStore(String(item.id))}
                      className={cn(
                        "flex min-h-9 max-w-full items-center gap-2 rounded-xl border px-3 text-sm font-semibold transition",
                        store === String(item.id)
                          ? "border-slate-900 bg-slate-900 text-white shadow-sm"
                          : "border-slate-200 bg-white text-slate-700 hover:border-slate-300",
                      )}
                    >
                      <StoreIcon className="size-4 shrink-0" />
                      <span className="truncate">{item.name}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </section>

          {/* ── Доставка ────────────────────────────────────────────────── */}
          <section className="space-y-3 border-t border-slate-200 pt-5">
            <SectionTitle icon={Truck} title="Доставка" caption="Склад, валюта и транспорт." />
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {warehouseOptions.length > 0 && (
                <div className="grid gap-1.5">
                  <Label htmlFor="order-warehouse">Склад отгрузки</Label>
                  <Select
                    id="order-warehouse"
                    value={warehouse}
                    disabled={Boolean(
                      editing && ["confirmed", "arrived", "loading", "loaded", "shipped"].includes(editing.status),
                    )}
                    onChange={(event) => {
                      const nextWarehouse = event.target.value;
                      setWarehouse(nextWarehouse);
                      if (!productsHaveWarehouseScope) return;
                      setRows((current) =>
                        current.map((row) => {
                          const selected = products.find((item) => String(item.id) === row.product);
                          if (!selected || productIsAssignedToWarehouse(selected, nextWarehouse)) return row;
                          return { ...row, product: "", price: "" };
                        }),
                      );
                    }}
                    className="h-10 rounded-xl bg-white"
                  >
                    {warehouseOptions.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                        {item.is_default ? " · основной" : ""}
                        {!item.is_active ? " · отключён" : ""}
                      </option>
                    ))}
                  </Select>
                </div>
              )}
              <div className="grid gap-1.5">
                <Label>Валюта</Label>
                <Segmented
                  ariaLabel="Валюта заказа"
                  value={currency}
                  disabled={!!editing}
                  onChange={setCurrency}
                  options={[
                    { value: "KZT", label: "₸ Тенге" },
                    { value: "USD", label: "$ Доллары" },
                  ]}
                />
              </div>
              <div className="grid gap-1.5">
                <Label>Транспорт</Label>
                <Segmented
                  ariaLabel="Транспорт"
                  value={transport}
                  disabled={physicalFieldsLocked}
                  onChange={setTransport}
                  options={[
                    { value: "truck", label: "🚚 Трак" },
                    { value: "train", label: "🚃 Вагон" },
                  ]}
                />
              </div>
              {transport === "truck" ? (
                <div className="grid gap-1.5">
                  <Label id="order-truck-label">Номер машины</Label>
                  <LicensePlateInput
                    labelledBy="order-truck-label"
                    value={truck}
                    onChange={setTruck}
                    disabled={physicalFieldsLocked}
                  />
                </div>
              ) : (
                <div className="grid gap-1.5">
                  <Label htmlFor="order-wagon-number">Номер вагона</Label>
                  <Input
                    id="order-wagon-number"
                    inputMode="numeric"
                    maxLength={8}
                    placeholder="8 цифр, можно позже"
                    value={wagonNumber}
                    onChange={(event) => setWagonNumber(event.target.value)}
                    disabled={physicalFieldsLocked}
                    aria-invalid={wagonNumberInvalid || undefined}
                    className="h-10 rounded-xl tabular-nums"
                  />
                </div>
              )}
              <div className="grid gap-1.5">
                <Label htmlFor="order-arrival">Плановая дата прибытия</Label>
                <Input
                  id="order-arrival"
                  type="date"
                  value={arrival}
                  onChange={(event) => setArrival(event.target.value)}
                  className="h-10 rounded-xl"
                />
              </div>
            </div>
          </section>

          {/* ── Позиции ─────────────────────────────────────────────────── */}
          <section className="space-y-3 border-t border-slate-200 pt-5">
            <SectionTitle
              icon={PackageOpen}
              title="Позиции"
              caption={
                compositionLocked
                  ? "Во время активной погрузки состав зафиксирован."
                  : client
                    ? `Цена подставляется из личного прайса клиента в ${currency}.`
                    : "Сначала выберите клиента — цены подставятся из его прайса."
              }
              aside={
                client && clientPricesLoading && !loadedClientPrices ? (
                  <span role="status" className="inline-flex items-center gap-1.5 text-xs text-slate-500">
                    <RefreshCw className="size-3.5 animate-spin" /> Прайс…
                  </span>
                ) : undefined
              }
            />
            {client && clientPricesError && (
              <div
                role="alert"
                className="flex flex-wrap items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900"
              >
                <AlertTriangle className="size-3.5 shrink-0 text-amber-600" />
                <span>
                  {loadedClientPrices
                    ? "Не удалось обновить личный прайс. Загруженные и введённые цены сохранены."
                    : "Личный прайс не загрузился. Цены можно ввести вручную."}
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="ml-auto h-7 border-amber-300 bg-white px-2 text-xs text-amber-900 hover:bg-amber-100"
                  onClick={() => void reloadClientPrices()}
                >
                  <RefreshCw className="size-3" /> Повторить
                </Button>
              </div>
            )}
            <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
              <div className="hidden grid-cols-[minmax(0,1fr)_100px_140px_36px] gap-2 border-b border-slate-100 bg-slate-50 px-3 py-2 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-400 sm:grid">
                <span>Товар</span>
                <span>Мешков</span>
                <span>Цена, {currencySymbol(currency)}</span>
                <span />
              </div>
              <div className="divide-y divide-slate-100">
                {rows.map((row, index) => {
                  const currentProduct = products.find((item) => String(item.id) === row.product);
                  const selectableProducts =
                    editing && currentProduct && !warehouseProducts.some((item) => item.id === currentProduct.id)
                      ? [currentProduct, ...warehouseProducts]
                      : warehouseProducts;
                  const lineTotal = Number(row.price || 0) * Number(row.quantity || 0);
                  return (
                    <div
                      key={row.id}
                      className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_36px] gap-2 px-3 py-2.5 sm:grid-cols-[minmax(0,1fr)_100px_140px_36px] sm:items-center"
                    >
                      <Select
                        value={row.product}
                        className="col-span-3 h-10 rounded-xl sm:col-span-1"
                        aria-label={`Товар, позиция ${index + 1}`}
                        disabled={compositionLocked}
                        onChange={(event) => {
                          const product = event.target.value;
                          updateRow(index, { product, price: clientPrices[product] ?? "" });
                        }}
                      >
                        <option value="">Выберите товар</option>
                        {selectableProducts.map((product) => {
                          const bags = productBagsAtWarehouse(product, warehouse);
                          const unavailable = bags <= 0 && !allowOutOfStock;
                          return (
                            <option key={product.id} value={product.id} disabled={unavailable}>
                              {product.label}
                              {bags > 0
                                ? ` · ${bags} меш.`
                                : allowOutOfStock
                                  ? " — нет остатка, но доступен"
                                  : " — нет в наличии"}
                            </option>
                          );
                        })}
                      </Select>
                      <Input
                        type="number"
                        min="1"
                        inputMode="numeric"
                        placeholder="Мешков"
                        className="h-10 rounded-xl"
                        value={row.quantity}
                        aria-label={`Количество мешков, позиция ${index + 1}`}
                        disabled={compositionLocked}
                        onChange={(event) => updateRow(index, { quantity: event.target.value })}
                      />
                      <Input
                        type="number"
                        min="0"
                        step="0.01"
                        inputMode="decimal"
                        className="h-10 rounded-xl"
                        aria-label={`Цена, позиция ${index + 1}`}
                        placeholder={`Цена, ${currencySymbol(currency)}`}
                        value={row.price}
                        disabled={compositionLocked}
                        onChange={(event) => updateRow(index, { price: event.target.value })}
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        title="Удалить позицию"
                        aria-label={`Удалить позицию ${index + 1}`}
                        disabled={compositionLocked}
                        onClick={() =>
                          setRows((current) =>
                            current.length > 1
                              ? current.filter((_, itemIndex) => itemIndex !== index)
                              : [{ id: nextRowId.current++, product: "", quantity: "", price: "" }],
                          )
                        }
                      >
                        <Trash2 className="size-4" />
                      </Button>
                      {lineTotal > 0 && (
                        <div className="col-span-3 -mt-1 text-right text-[11px] tabular-nums text-slate-400 sm:col-span-4">
                          = {formatCurrency(String(lineTotal), currency)}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              <button
                type="button"
                disabled={compositionLocked}
                onClick={addRow}
                className="flex w-full items-center justify-center gap-2 border-t border-dashed border-slate-200 bg-slate-50/60 py-2.5 text-sm font-semibold text-slate-600 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Plus className="size-4" /> Добавить позицию
              </button>
            </div>
          </section>

          {/* ── Задним числом ───────────────────────────────────────────── */}
          {canBackdate && (
            <section className="border-t border-slate-200 pt-5">
              <div
                className={cn(
                  "rounded-2xl border transition",
                  backdateOn ? "border-amber-200 bg-amber-50/40" : "border-slate-200 bg-white",
                )}
              >
                <label className="flex cursor-pointer items-center gap-3 px-4 py-3">
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-amber-100 text-amber-700">
                    <CalendarClock className="size-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-bold text-slate-900">Задним числом</span>
                    <span className="block text-xs text-slate-500">
                      Указать дату заказа и сразу зафиксировать статус и оплату.
                    </span>
                  </span>
                  <span
                    aria-hidden="true"
                    className={cn(
                      "relative h-6 w-11 shrink-0 rounded-full transition",
                      backdateOn ? "bg-amber-500" : "bg-slate-300",
                    )}
                  >
                    <span
                      className={cn(
                        "absolute top-0.5 size-5 rounded-full bg-white shadow transition",
                        backdateOn ? "left-[22px]" : "left-0.5",
                      )}
                    />
                  </span>
                  <input
                    type="checkbox"
                    className="sr-only"
                    aria-label="Задним числом"
                    checked={backdateOn}
                    onChange={(event) => {
                      setBackdateOn(event.target.checked);
                      setError("");
                    }}
                  />
                </label>
                {backdateOn && (
                  <div className="border-t border-amber-200/70 px-4 py-4">
                    <FixationFields draft={fixation} onChange={setFixation} canPay={canPay} idPrefix="order-backdate" />
                  </div>
                )}
              </div>
            </section>
          )}

          {shippedCorrection && (
            <section className="space-y-2 rounded-2xl border border-amber-200 bg-amber-50/70 p-4">
              <Label htmlFor="order-edit-reason">Причина корректировки отгруженного заказа</Label>
              <textarea
                id="order-edit-reason"
                value={editReason}
                onChange={(event) => setEditReason(event.target.value)}
                placeholder="Например: исправление фактически отгруженного количества"
                rows={3}
                required
                minLength={5}
                className="w-full resize-y rounded-xl border border-amber-200 bg-white px-3 py-2 text-sm outline-none focus:ring-4 focus:ring-amber-500/10"
              />
              <p className="text-xs text-amber-800">
                Изменение состава автоматически скорректирует склад и сохранится в журнале.
              </p>
            </section>
          )}

          {(editing || template) && (
            <div className="flex items-start gap-2 rounded-xl border border-blue-100 bg-blue-50/60 px-3 py-2.5 text-xs text-blue-700">
              <Info className="mt-0.5 size-3.5 shrink-0" />
              {editing
                ? compositionLocked
                  ? "Заказ открыт для исправления, но состав защищён до завершения текущей погрузки."
                  : "Заказ можно исправить на любом этапе. Физические и финансовые изменения попадут в журнал."
                : `Данные взяты из заказа #${template!.id}, цены обновлены из текущего прайса клиента. Проверьте всё перед созданием.`}
            </div>
          )}
        </>
      )}

      {error && (
        <p
          role="alert"
          className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm font-medium text-[var(--destructive)]"
        >
          {error}
        </p>
      )}

      <div className="sticky -bottom-5 z-10 flex items-center justify-between gap-3 border-t border-slate-200 bg-white/95 pb-1 pt-3 backdrop-blur-md">
        <div className="min-w-0">
          <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-400">Итог</div>
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-black tabular-nums text-slate-900">
              {formatCurrency(String(total), currency)}
            </span>
            <span className="text-xs text-slate-500">
              {selectedBags} меш.
              {selectedClient ? ` · ${selectedClient.name}` : ""}
              {selectedStore ? ` · ${selectedStore.name}` : ""}
              {selectedDepartment && !assignedDepartment ? ` · ${selectedDepartment.name}` : ""}
            </span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>
            Отмена
          </Button>
          <Button type="submit" disabled={submitDisabled}>
            {busy
              ? "Сохранение…"
              : editing
                ? "Сохранить изменения"
                : backdating
                  ? "Создать задним числом"
                  : "Создать заказ"}
            {!busy && <Check className="size-4" />}
          </Button>
        </div>
      </div>
    </form>
  );
}
