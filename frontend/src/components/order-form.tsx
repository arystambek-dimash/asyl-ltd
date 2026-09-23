"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, CalendarClock, Check, Info, Plus, RefreshCw, Search, Trash2, Truck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Segmented } from "@/components/ui/segmented";
import { PlateInput } from "@/components/ui/plate-input";
import { DataGate } from "@/components/ui/data-state";
import {
  FixationFields,
  emptyFixationDraft,
  fixationBody,
  fixationDraftError,
  type FixationDraft,
} from "@/components/orders/fixation-fields";
import { OptionToggle } from "@/components/orders/option-toggle";
import { PayNowFields, payAfterCreate, usePayNow } from "@/components/orders/pay-now";
import { moneyCents } from "@/lib/debt-orders";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { formatEstimate, requestEstimate } from "@/lib/orders";
import { formatPlatePair, isValidWagonNumber } from "@/lib/plates";
import { cn, formatCurrency, todayLocalIsoDate, currencySymbol } from "@/lib/utils";
import { useAuth } from "@/store/auth";
import {
  clearOrderDraft,
  loadOrderDraft,
  orderDraftHasContent,
  saveOrderDraft,
  type OrderDraft,
} from "@/lib/order-draft";
import type { Client, Department, Order, Product, Store, Warehouse } from "@/lib/types";

type Row = { id: number; product: string; quantity: string; price: string };
type OrderClientOption = Pick<Client, "id" | "name" | "company_name" | "phone" | "currency"> & {
  /** Страна клиента — страна номера машины по умолчанию. */
  country?: string;
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
  step,
  title,
  caption,
  aside,
}: {
  step: number;
  title: string;
  caption?: string;
  aside?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex items-center gap-3">
        <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-slate-900 text-xs font-bold text-white">
          {step}
        </span>
        <div>
          <h3 className="text-base font-semibold text-slate-900">{title}</h3>
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
  onDraftChange,
}: {
  editing?: Order | null;
  template?: Order | null;
  onCancel: () => void;
  onDone: () => void;
  /** Новый заказ: сообщает, есть ли в сохранённом черновике введённые данные. */
  onDraftChange?: (hasContent: boolean) => void;
}) {
  const router = useRouter();
  const { me } = useAuth();
  // Черновик восстанавливаем, только если он начат от того же шаблона (или с нуля):
  // явный выбор другого шаблона в окне начинает заказ заново.
  const [draft] = useState<OrderDraft | null>(() => {
    if (editing) return null;
    const saved = loadOrderDraft(me?.id);
    return saved && (saved.template?.id ?? null) === (template?.id ?? null) ? saved : null;
  });
  const {
    data: formOptions,
    loading: formOptionsLoading,
    error: formOptionsError,
    reload: reloadFormOptions,
  } = useApi<OrderFormOptions>("/orders/form-options/");
  const { clients, products, stores, departments, warehouses = [] } = formOptions ?? EMPTY_FORM_OPTIONS;
  const source = editing ?? template;
  const nextRowId = useRef(draft ? Math.max(0, ...draft.rows.map((row) => row.id)) + 1 : (source?.items.length ?? 1));
  const [clientSearch, setClientSearch] = useState("");
  const [clientPickerOpen, setClientPickerOpen] = useState(draft ? !draft.client : !source);
  const [dept, setDept] = useState(draft?.dept ?? source?.department ?? "");
  const [client, setClient] = useState(draft?.client ?? (source ? String(source.client) : ""));
  const [currency, setCurrency] = useState<"KZT" | "USD">(draft?.currency ?? source?.currency ?? "KZT");
  const [store, setStore] = useState(draft?.store ?? (source?.store ? String(source.store) : ""));
  const [warehouse, setWarehouse] = useState(draft?.warehouse ?? (source?.warehouse ? String(source.warehouse) : ""));
  const [transport, setTransport] = useState<"truck" | "train">(draft?.transport ?? source?.transport_type ?? "truck");
  const [truck, setTruck] = useState(
    draft?.truck ?? (source?.transport_type === "train" ? "" : (source?.truck_number ?? "")),
  );
  const [trailer, setTrailer] = useState(
    draft?.trailer ?? (source?.transport_type === "train" ? "" : (source?.trailer_number ?? "")),
  );
  const [wagonNumber, setWagonNumber] = useState(
    draft?.wagonNumber ?? (source?.transport_type === "train" ? source.truck_number : ""),
  );
  const [arrival, setArrival] = useState(
    draft?.arrival ?? editing?.arrival_date ?? (template ? todayLocalIsoDate() : ""),
  );
  const [rows, setRows] = useState<Row[]>(
    draft?.rows.length
      ? draft.rows
      : source
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
  const [backdateOn, setBackdateOn] = useState(draft?.backdateOn ?? false);
  const [fixation, setFixation] = useState<FixationDraft>(() => draft?.fixation ?? emptyFixationDraft());

  const canBackdate = !editing && can(me, "orders.edit");
  const canPay = can(me, "payments.create");
  const backdating = canBackdate && backdateOn;
  // «Оплата сразу»: новый заказ подтверждается при создании (orders.confirm), и
  // касса тут же принимает предоплату. С задним числом оплату фиксирует свой блок.
  const canPayNow = !editing && canPay && can(me, "orders.confirm");

  const compositionLocked = editing?.status === "loading";
  const shippedCorrection = editing?.status === "shipped";
  const physicalFieldsLocked = Boolean(editing && ["arrived", "loading", "loaded", "shipped"].includes(editing.status));
  const unchangedWagonNumber = editing && transport === editing.transport_type && wagonNumber === editing.truck_number;
  const wagonNumberInvalid =
    !physicalFieldsLocked &&
    !unchangedWagonNumber &&
    transport === "train" &&
    wagonNumber !== "" &&
    !isValidWagonNumber(wagonNumber);

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

  // Цены из черновика уже проверены человеком — первый загруженный прайс их не перетирает.
  const keepDraftPrices = useRef(Boolean(draft?.client));
  useEffect(() => {
    if (!loadedClientPrices || editing) return;
    if (keepDraftPrices.current) {
      keepDraftPrices.current = false;
      return;
    }
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
  const selectedWarehouse = warehouseOptions.find((item) => String(item.id) === warehouse);
  const productsHaveWarehouseScope = products.some(
    (item) => item.stock_by_warehouse !== undefined || item.warehouse !== undefined,
  );
  const warehouseProducts =
    productsHaveWarehouseScope && warehouse
      ? products.filter((item) => productIsAssignedToWarehouse(item, warehouse))
      : products;
  const validRows = rows.filter((row) => row.product && Number(row.quantity) > 0);
  // Та же оценка, что у заявки: без цены у позиции сумма «Не рассчитана», а не «0 ₸».
  const estimate = requestEstimate(validRows.map((row) => ({ quantity: row.quantity, unit_price: row.price })));
  const allPriced = estimate.amount !== null;
  const totalLabel = formatEstimate(estimate.amount, currency);
  const selectedBags = estimate.bags;
  // Исторический заказ склад не списывает — товар без остатка тоже можно выбрать.
  const allowOutOfStock = shippedCorrection || backdating;
  const fixationError = backdating ? fixationDraftError(fixation) : "";
  // Без цены у позиции итога нет — создать заказ всё равно нельзя (allPriced).
  const payNow = usePayNow(currency, moneyCents(estimate.amount ?? 0), draft?.payNow);
  const payingNow = canPayNow && !backdating && payNow.on;
  const payNowError = payingNow ? payNow.problem : "";

  // Автосохранение черновика нового заказа: случайное закрытие окна ничего не теряет.
  const onDraftChangeRef = useRef(onDraftChange);
  useEffect(() => {
    onDraftChangeRef.current = onDraftChange;
  }, [onDraftChange]);
  const draftSaveBlocked = useRef(false);
  useEffect(() => {
    if (editing || draftSaveBlocked.current) return;
    const next: OrderDraft = {
      template: template ?? null,
      dept,
      client,
      currency,
      store,
      warehouse,
      transport,
      truck,
      trailer,
      wagonNumber,
      arrival,
      rows,
      backdateOn,
      fixation,
      payNow: payNow.draft,
    };
    const hasContent = orderDraftHasContent(next);
    if (hasContent) saveOrderDraft(me?.id, next);
    else clearOrderDraft(me?.id);
    onDraftChangeRef.current?.(hasContent);
  }, [
    editing,
    template,
    me?.id,
    dept,
    client,
    currency,
    store,
    warehouse,
    transport,
    truck,
    trailer,
    wagonNumber,
    arrival,
    rows,
    backdateOn,
    fixation,
    payNow.draft,
  ]);

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
    if (payNowError) return payNowError;
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
              // Номера уходят как записаны: неизменённый старый номер бэкенд не перепроверяет.
              ...(transport === "train"
                ? { truck_number: wagonNumber }
                : { truck_number: truck, trailer_number: trailer }),
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
        const { data } = await api.post<Order>("/orders/", { ...body, client: Number(client) });
        // Заказ создан — черновик больше не нужен; блокируем повторное сохранение до размонтирования.
        // Чистим до «Оплаты сразу»: её повтор идёт с карточки заказа, а не из черновика.
        draftSaveBlocked.current = true;
        clearOrderDraft(me?.id);
        onDraftChangeRef.current?.(false);
        const target = payingNow ? await payAfterCreate(data, payNow.payment) : `/orders/${data.id}`;
        onDone();
        router.push(target);
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
    !!fixationError ||
    !!payNowError;

  const orderSummaryRows = [
    { label: "Клиент", value: selectedClient?.name ?? "—" },
    { label: "Отдел", value: assignedDepartment?.name ?? selectedDepartment?.name ?? "—" },
    ...(selectedStore ? [{ label: "Магазин", value: selectedStore.name }] : []),
    { label: "Склад", value: selectedWarehouse?.name ?? source?.warehouse_name ?? "Основной" },
    {
      label: "Транспорт",
      value:
        transport === "train"
          ? `Вагон${wagonNumber ? ` ${wagonNumber}` : ""}`
          : `Машина${truck || trailer ? ` ${formatPlatePair(truck, trailer)}` : ""}`,
    },
    ...(arrival ? [{ label: "Прибытие", value: arrival }] : []),
  ];

  return (
    <form onSubmit={submit} className="flex flex-col gap-5">
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
        <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_300px] lg:items-start">
          {/* ── Основная колонка: клиент → состав → дополнительно ─────────── */}
          <div className="min-w-0 space-y-8">
            <section className="space-y-4">
              <SectionTitle
                step={1}
                title="Клиент"
                caption={editing ? "Клиент и валюта закреплены за заказом." : "Кому оформляем заказ."}
              />

              {selectedClient && !clientPickerVisible && (
                <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-3.5 shadow-sm">
                  <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-slate-900 text-sm font-bold text-white">
                    {selectedClient.name.slice(0, 1).toUpperCase()}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold text-slate-900">{selectedClient.name}</div>
                    <div className="truncate text-xs text-slate-500">
                      {[selectedClient.company_name, selectedClient.phone].filter(Boolean).join(" · ") ||
                        "Без дополнительных данных"}
                    </div>
                  </div>
                  {assignedDepartment && (
                    <span className="hidden items-center gap-1.5 rounded-full border border-slate-200 px-2.5 py-1 text-[11px] font-semibold text-slate-600 sm:inline-flex">
                      <span className="size-2 rounded-full" style={{ backgroundColor: assignedDepartment.color }} />
                      {assignedDepartment.name}
                      <span className="font-normal text-slate-400">
                        · {clientDepartment ? "отдел клиента" : "ваш отдел"}
                      </span>
                    </span>
                  )}
                  {!editing && (
                    <Button type="button" variant="outline" size="sm" onClick={() => setClientPickerOpen(true)}>
                      Изменить
                    </Button>
                  )}
                </div>
              )}

              {clientPickerVisible && (
                <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
                  <div className="relative border-b border-slate-100">
                    <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
                    <input
                      value={clientSearch}
                      onChange={(event) => setClientSearch(event.target.value)}
                      placeholder="Имя, компания или телефон…"
                      className="h-12 w-full bg-transparent pl-10 pr-4 text-sm outline-none placeholder:text-slate-400"
                      autoFocus={!client}
                      aria-label="Поиск клиента"
                    />
                  </div>
                  <div className="max-h-64 overflow-y-auto p-1.5">
                    {filteredClients.map((item) => {
                      const selected = String(item.id) === client;
                      return (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => chooseClient(item)}
                          className={cn(
                            "flex w-full min-w-0 items-center gap-3 rounded-lg px-3 py-2 text-left transition",
                            selected ? "bg-slate-100" : "hover:bg-slate-50",
                          )}
                        >
                          <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xs font-bold text-slate-600">
                            {item.name.slice(0, 1).toUpperCase()}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-medium text-slate-900">{item.name}</span>
                            <span className="block truncate text-xs text-slate-500">
                              {[item.company_name, item.phone].filter(Boolean).join(" · ") || "—"}
                            </span>
                          </span>
                          {item.department_name && (
                            <span className="hidden shrink-0 text-[11px] text-slate-400 sm:block">
                              {item.department_name}
                            </span>
                          )}
                          {selected && <Check className="size-4 shrink-0 text-slate-900" />}
                        </button>
                      );
                    })}
                    {!filteredClients.length && (
                      <div className="flex min-h-24 flex-col items-center justify-center text-center text-slate-400">
                        <span className="text-sm font-medium">Ничего не найдено</span>
                        <span className="mt-0.5 text-xs">Проверьте имя или номер телефона.</span>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {(!assignedDepartment || (client && clientStores.length > 0)) && (
                <div className="grid gap-4 sm:grid-cols-2">
                  {!assignedDepartment && (
                    <div className="grid gap-1.5">
                      <Label htmlFor="order-department">Отдел продаж</Label>
                      <Select
                        id="order-department"
                        value={dept}
                        disabled={departmentLocked}
                        onChange={(event) => setDept(event.target.value)}
                        className="h-10 rounded-lg bg-white"
                      >
                        <option value="">Выберите отдел</option>
                        {departments.map((department) => (
                          <option
                            key={department.code}
                            value={department.code}
                            disabled={departmentLocked && department.code !== editing?.department}
                          >
                            {department.name}
                          </option>
                        ))}
                      </Select>
                    </div>
                  )}
                  {client && clientStores.length > 0 && (
                    <div className="grid gap-1.5">
                      <Label htmlFor="order-store">Магазин</Label>
                      <Select
                        id="order-store"
                        value={store}
                        onChange={(event) => setStore(event.target.value)}
                        className="h-10 rounded-lg bg-white"
                      >
                        <option value="">Без магазина — заказ на клиента</option>
                        {clientStores.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.name}
                            {item.address ? ` · ${item.address}` : ""}
                          </option>
                        ))}
                      </Select>
                    </div>
                  )}
                </div>
              )}
            </section>

            <section className="space-y-4">
              <SectionTitle
                step={2}
                title="Состав заказа"
                caption={
                  compositionLocked
                    ? "Во время активной погрузки состав зафиксирован."
                    : client
                      ? `Цены подставляются из личного прайса клиента в ${currency}.`
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
                  className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900"
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
              <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
                <div className="hidden grid-cols-[minmax(0,1fr)_96px_128px_112px_36px] gap-3 border-b border-slate-100 bg-slate-50/80 px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500 sm:grid">
                  <span>Товар</span>
                  <span>Мешков</span>
                  <span>Цена, {currencySymbol(currency)}</span>
                  <span className="text-right">Сумма</span>
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
                        className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_36px] gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_96px_128px_112px_36px] sm:gap-3 sm:items-center"
                      >
                        <Select
                          value={row.product}
                          className="col-span-3 h-10 rounded-lg sm:col-span-1"
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
                          className="h-10 rounded-lg"
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
                          className="h-10 rounded-lg"
                          aria-label={`Цена, позиция ${index + 1}`}
                          placeholder={`Цена, ${currencySymbol(currency)}`}
                          value={row.price}
                          disabled={compositionLocked}
                          onChange={(event) => updateRow(index, { price: event.target.value })}
                        />
                        <div className="col-span-2 self-center text-sm font-semibold tabular-nums text-slate-900 sm:col-span-1 sm:text-right">
                          {lineTotal > 0 ? (
                            formatCurrency(String(lineTotal), currency)
                          ) : (
                            <span className="text-slate-300">—</span>
                          )}
                        </div>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="justify-self-end text-slate-400 hover:text-red-600"
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
                      </div>
                    );
                  })}
                </div>
                <div className="flex items-center justify-between border-t border-slate-100 bg-slate-50/60 px-4 py-2.5">
                  <button
                    type="button"
                    disabled={compositionLocked}
                    onClick={addRow}
                    className="inline-flex items-center gap-1.5 text-sm font-semibold text-slate-700 transition hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Plus className="size-4" /> Добавить позицию
                  </button>
                  <span className="text-xs text-slate-500">
                    {selectedBags} меш. · <b className="font-semibold tabular-nums text-slate-900">{totalLabel}</b>
                  </span>
                </div>
              </div>
            </section>

            {(canBackdate || canPayNow || shippedCorrection || editing || template) && (
              <section className="space-y-4">
                <SectionTitle step={3} title="Дополнительно" caption="Необязательно." />

                {canBackdate && (
                  <OptionToggle
                    checked={backdateOn}
                    onChange={(checked) => {
                      setBackdateOn(checked);
                      setError("");
                    }}
                    icon={CalendarClock}
                    tone="amber"
                    title="Оформить задним числом"
                    caption="Указать дату заказа и сразу зафиксировать статус и оплату."
                    ariaLabel="Задним числом"
                  >
                    <FixationFields draft={fixation} onChange={setFixation} canPay={canPay} idPrefix="order-backdate" />
                  </OptionToggle>
                )}

                {canPayNow && !backdating && <PayNowFields payNow={payNow} />}

                {shippedCorrection && (
                  <div className="space-y-2 rounded-xl border border-amber-200 bg-amber-50/70 p-4">
                    <Label htmlFor="order-edit-reason">Причина корректировки отгруженного заказа</Label>
                    <textarea
                      id="order-edit-reason"
                      value={editReason}
                      onChange={(event) => setEditReason(event.target.value)}
                      placeholder="Например: исправление фактически отгруженного количества"
                      rows={3}
                      required
                      minLength={5}
                      className="w-full resize-y rounded-lg border border-amber-200 bg-white px-3 py-2 text-sm outline-none focus:ring-4 focus:ring-amber-500/10"
                    />
                    <p className="text-xs text-amber-800">
                      Изменение состава автоматически скорректирует склад и сохранится в журнале.
                    </p>
                  </div>
                )}

                {(editing || template) && (
                  <div className="flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-xs text-slate-600">
                    <Info className="mt-0.5 size-3.5 shrink-0 text-slate-400" />
                    {editing
                      ? compositionLocked
                        ? "Заказ открыт для исправления, но состав защищён до завершения текущей погрузки."
                        : "Заказ можно исправить на любом этапе. Физические и финансовые изменения попадут в журнал."
                      : `Данные взяты из заказа #${template!.id}, цены обновлены из текущего прайса клиента. Проверьте всё перед созданием.`}
                  </div>
                )}
              </section>
            )}
          </div>

          {/* ── Боковая колонка: доставка и итог ────────────────────────── */}
          <aside className="space-y-4 lg:sticky lg:top-0">
            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="mb-4 flex items-center gap-2">
                <Truck className="size-4 text-slate-500" />
                <h3 className="text-sm font-semibold text-slate-900">Доставка</h3>
              </div>
              <div className="space-y-4">
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
                      className="h-10 rounded-lg bg-white"
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
                      { value: "truck", label: "Трак" },
                      { value: "train", label: "Вагон" },
                    ]}
                  />
                </div>
                {transport === "truck" ? (
                  <>
                    <div className="grid gap-1.5">
                      <Label htmlFor="order-truck">Тягач</Label>
                      <PlateInput
                        id="order-truck"
                        warning
                        defaultCountry={selectedClient?.country}
                        value={truck}
                        onChange={setTruck}
                        disabled={physicalFieldsLocked}
                      />
                    </div>
                    <div className="grid gap-1.5">
                      <Label htmlFor="order-trailer">Прицеп (необязательно)</Label>
                      <PlateInput
                        id="order-trailer"
                        kind="trailer"
                        defaultCountry={selectedClient?.country}
                        value={trailer}
                        onChange={setTrailer}
                        disabled={physicalFieldsLocked}
                      />
                      <p className="text-[11px] text-slate-500">Номера можно указать позже, при въезде.</p>
                    </div>
                  </>
                ) : (
                  <div className="grid gap-1.5">
                    <Label htmlFor="order-wagon-number">Номер вагона</Label>
                    <Input
                      id="order-wagon-number"
                      inputMode="numeric"
                      maxLength={8}
                      placeholder="8 цифр"
                      value={wagonNumber}
                      onChange={(event) => setWagonNumber(event.target.value)}
                      disabled={physicalFieldsLocked}
                      aria-invalid={wagonNumberInvalid || undefined}
                      className="h-10 rounded-lg tabular-nums"
                    />
                    <p className="text-[11px] text-slate-500">Можно указать позже, до начала погрузки.</p>
                  </div>
                )}
                <div className="grid gap-1.5">
                  <Label htmlFor="order-arrival">Плановая дата прибытия</Label>
                  <Input
                    id="order-arrival"
                    type="date"
                    value={arrival}
                    onChange={(event) => setArrival(event.target.value)}
                    className="h-10 rounded-lg"
                  />
                </div>
              </div>
            </div>

            <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Итог</div>
              <div className="mt-1 text-2xl font-bold tabular-nums text-slate-900">{totalLabel}</div>
              <div className="text-xs text-slate-500">
                {validRows.length} {validRows.length === 1 ? "позиция" : "позиций"} · {selectedBags} меш.
              </div>
              <dl className="mt-3 space-y-1.5 border-t border-slate-200 pt-3 text-xs">
                {orderSummaryRows.map((row) => (
                  <div key={row.label} className="flex items-baseline justify-between gap-3">
                    <dt className="shrink-0 text-slate-500">{row.label}</dt>
                    <dd className="truncate text-right font-medium text-slate-800">{row.value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </aside>
        </div>
      )}

      {error && (
        <p
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-sm font-medium text-[var(--destructive)]"
        >
          {error}
        </p>
      )}

      <div className="sticky -bottom-5 z-10 flex items-center justify-end gap-2 border-t border-slate-200 bg-white/95 pb-1 pt-3 backdrop-blur-md">
        <span className="mr-auto text-sm text-slate-500 lg:hidden">
          <b className="font-semibold tabular-nums text-slate-900">{totalLabel}</b> · {selectedBags} меш.
        </span>
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
                : payingNow
                  ? "Создать и принять оплату"
                  : "Создать заказ"}
          {!busy && <Check className="size-4" />}
        </Button>
      </div>
    </form>
  );
}
