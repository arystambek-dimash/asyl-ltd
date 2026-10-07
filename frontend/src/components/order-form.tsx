"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, CalendarClock, Check, Info, Plus, RefreshCw, Trash2, Truck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Segmented } from "@/components/ui/segmented";
import { TransportNumberFields, type TruckKind } from "@/components/ui/transport-number-fields";
import { DataGate, FormError } from "@/components/ui/data-state";
import { DepartmentDot } from "@/components/ui/department-badge";
import {
  FixationFields,
  emptyFixationDraft,
  fixationBody,
  fixationDraftError,
  type FixationDraft,
} from "@/components/orders/fixation-fields";
import {
  ClientPicker,
  EMPTY_FORM_OPTIONS,
  SectionTitle,
  type OrderClientOption,
  type OrderFormOptions,
  type OrderProductOption,
} from "@/components/orders/order-form-parts";
import { OptionToggle } from "@/components/orders/option-toggle";
import { ProductPicker } from "@/components/orders/product-picker";
import { PayNowFields, payAfterCreate, usePayNow } from "@/components/orders/pay-now";
import { moneyCents } from "@/lib/debt-orders";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { ORDER_AWAITING_SHIPMENT_STATUSES, ORDER_LOADING_STATUSES } from "@/lib/constants";
import { formatEstimate, requestEstimate } from "@/lib/orders";
import { formatPlatePair, isValidWagonNumber } from "@/lib/plates";
import { formatCurrency, todayLocalIsoDate, currencySymbol } from "@/lib/utils";
import { useAuth } from "@/store/auth";
import {
  clearOrderDraft,
  loadOrderDraft,
  orderDraftHasContent,
  saveOrderDraft,
  type OrderDraft,
} from "@/lib/order-draft";
import type { Order, Warehouse } from "@/lib/types";

type Row = { id: number; product: string; quantity: string; price: string };

// Газель и фура — только в интерфейсе: в заказ обе уходят как «truck».
const TRANSPORT_OPTIONS: { value: TruckKind | "train"; label: string }[] = [
  { value: "gazelle", label: "Газель" },
  { value: "fura", label: "Фура" },
  { value: "train", label: "Вагон" },
];

function productIsAssignedToWarehouse(product: OrderProductOption, warehouse: string) {
  if (!warehouse) return true;
  return Object.prototype.hasOwnProperty.call(product.stock_by_warehouse, warehouse);
}

function productBagsAtWarehouse(product: OrderProductOption, warehouse: string) {
  if (warehouse) return product.stock_by_warehouse[warehouse] ?? 0;
  return product.available_bags ?? 0;
}

/** Позиции с товаром, которого нет на складе, очищаются — склад их не отгрузит. */
function dropRowsOutsideWarehouse(rows: Row[], products: OrderProductOption[], warehouse: string): Row[] {
  return rows.map((row) => {
    const selected = products.find((item) => String(item.id) === row.product);
    if (!selected || productIsAssignedToWarehouse(selected, warehouse)) return row;
    return { ...row, product: "", price: "" };
  });
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
  const [transport, setTransport] = useState<Order["transport_type"]>(
    draft?.transport ?? source?.transport_type ?? "truck",
  );
  const [truck, setTruck] = useState(
    draft?.truck ?? (source?.transport_type === "train" ? "" : (source?.truck_number ?? "")),
  );
  const [trailer, setTrailer] = useState(
    draft?.trailer ?? (source?.transport_type === "train" ? "" : (source?.trailer_number ?? "")),
  );
  // Заказ не помнит, газель это или фура: машина с номером без прицепа открывается газелью.
  const [truckKind, setTruckKind] = useState<TruckKind>(draft?.truckKind ?? (truck && !trailer ? "gazelle" : "fura"));
  const vehicle = transport === "train" ? "train" : truckKind;
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
            price: item.unit_price ?? "",
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
  const physicalFieldsLocked = Boolean(
    editing && (editing.status === "shipped" || ORDER_LOADING_STATUSES.includes(editing.status)),
  );
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
  // Новый заказ уходит в отдел клиента (или менеджера); выбор вручную — только когда его нет.
  const effectiveDept = assignedDepartment?.code ?? dept;

  useEffect(() => {
    if (!warehouses.length || editing) return;
    if (warehouse && warehouses.some((item) => String(item.id) === warehouse)) return;
    const initial = warehouses.find((item) => item.is_default) ?? warehouses[0];
    setWarehouse(String(initial.id));
    if (template) setRows((current) => dropRowsOutsideWarehouse(current, products, String(initial.id)));
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
          id: editing!.warehouse,
          code: `inactive-${editing!.warehouse}`,
          name: editing!.warehouse_name,
          address: "",
          is_active: false,
          is_default: false,
        },
      ]
    : warehouses;
  const selectedWarehouse = warehouseOptions.find((item) => String(item.id) === warehouse);
  const warehouseProducts = products.filter((item) => productIsAssignedToWarehouse(item, warehouse));
  const validRows = rows.filter((row) => row.product && Number(row.quantity) > 0);
  // Та же оценка, что у заявки: без цены у позиции сумма «Не рассчитана», а не «0 ₸».
  const estimate = requestEstimate(validRows.map((row) => ({ quantity: row.quantity, unit_price: row.price })));
  const allPriced = estimate.amount !== null;
  const totalLabel = formatEstimate(estimate.amount, currency);
  const selectedBags = estimate.bags;
  // Исторический заказ склад не списывает — товар без остатка тоже можно выбрать.
  const allowOutOfStock = shippedCorrection || backdating;
  const fixationError = backdating ? fixationDraftError(fixation, { currency }) : "";
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
      dept: effectiveDept,
      client,
      currency,
      store,
      warehouse,
      transport,
      truckKind,
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
    effectiveDept,
    client,
    currency,
    store,
    warehouse,
    transport,
    truckKind,
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

  /** Незаполненная форма: кнопка создания неактивна. */
  function blockingProblem(): string {
    if (!referenceDataReady) return "Сначала загрузите справочники заказа.";
    if (!client) return "Выберите клиента.";
    if (!effectiveDept) return "Выберите отдел продаж.";
    if (warehouseOptions.length > 0 && !warehouse) return "Выберите склад отгрузки.";
    if (!compositionLocked && !validRows.length) return "Добавьте хотя бы одну позицию.";
    if (!compositionLocked && !allPriced) return "Укажите цену для каждой позиции.";
    if (shippedCorrection && editReason.trim().length < 5) {
      return "Укажите причину изменения отгруженного заказа — минимум 5 символов.";
    }
    return fixationError || payNowError;
  }

  // Недоступный отдел клиента и неверный номер вагона объясняются по нажатию — кнопка остаётся активной.
  function validate(): string {
    if (!editing && selectedClient?.department_code && !clientDepartment) {
      return "Отдел клиента недоступен. Проверьте его в карточке клиента.";
    }
    if (wagonNumberInvalid) {
      return "Номер вагона должен содержать 8 цифр. Если номер пока неизвестен, оставьте поле пустым.";
    }
    return blockingProblem();
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
              department: effectiveDept,
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

  const submitDisabled = busy || blockingProblem() !== "";

  const orderSummaryRows = [
    { label: "Клиент", value: selectedClient?.name ?? "—" },
    { label: "Отдел", value: assignedDepartment?.name ?? selectedDepartment?.name ?? "—" },
    ...(selectedStore ? [{ label: "Магазин", value: selectedStore.name }] : []),
    { label: "Склад", value: selectedWarehouse?.name ?? source?.warehouse_name ?? "Основной" },
    {
      label: "Транспорт",
      value: [
        TRANSPORT_OPTIONS.find((option) => option.value === vehicle)?.label,
        transport === "train" ? wagonNumber : formatPlatePair(truck, trailer),
      ]
        .filter(Boolean)
        .join(" "),
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
            onRetry={reloadFormOptions}
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

              <ClientPicker
                clients={clients}
                value={client}
                search={clientSearch}
                onSearch={setClientSearch}
                listOpen={clientPickerVisible}
                onOpenList={() => setClientPickerOpen(true)}
                onChoose={chooseClient}
                canChange={!editing}
                badge={
                  assignedDepartment && (
                    <span className="hidden items-center gap-1.5 rounded-full border border-slate-200 px-2.5 py-1 text-[11px] font-semibold text-slate-600 sm:inline-flex">
                      <DepartmentDot color={assignedDepartment.color} className="size-2" />
                      {assignedDepartment.name}
                      <span className="font-normal text-slate-400">
                        · {clientDepartment ? "отдел клиента" : "ваш отдел"}
                      </span>
                    </span>
                  )
                }
              />

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
                        <ProductPicker
                          products={selectableProducts}
                          value={row.product}
                          className="col-span-3 sm:col-span-1"
                          ariaLabel={`Товар, позиция ${index + 1}`}
                          disabled={compositionLocked}
                          bagsOf={(product) => productBagsAtWarehouse(product, warehouse)}
                          allowOutOfStock={allowOutOfStock}
                          onChange={(product) => updateRow(index, { product, price: clientPrices[product] ?? "" })}
                        />
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
                    <FixationFields
                      draft={fixation}
                      onChange={setFixation}
                      canPay={canPay}
                      currency={currency}
                      idPrefix="order-backdate"
                    />
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
                        editing &&
                        (editing.status === "shipped" || ORDER_AWAITING_SHIPMENT_STATUSES.includes(editing.status)),
                      )}
                      onChange={(event) => {
                        const nextWarehouse = event.target.value;
                        setWarehouse(nextWarehouse);
                        setRows((current) => dropRowsOutsideWarehouse(current, products, nextWarehouse));
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
                    value={vehicle}
                    disabled={physicalFieldsLocked}
                    onChange={(value) => {
                      setTransport(value === "train" ? "train" : "truck");
                      if (value !== "train") setTruckKind(value);
                    }}
                    options={TRANSPORT_OPTIONS}
                  />
                </div>
                <TransportNumberFields
                  id="order"
                  transportType={transport}
                  truckKind={truckKind}
                  defaultCountry={selectedClient?.country}
                  disabled={physicalFieldsLocked}
                  errors={{ truck: wagonNumberInvalid }}
                  hint={
                    transport === "truck"
                      ? "Номера можно указать позже, при въезде."
                      : "Можно указать позже, до начала погрузки."
                  }
                  value={{ truck_number: transport === "train" ? wagonNumber : truck, trailer_number: trailer }}
                  onChange={(pair) => {
                    if (transport === "train") {
                      setWagonNumber(pair.truck_number);
                    } else {
                      setTruck(pair.truck_number);
                      setTrailer(pair.trailer_number);
                    }
                  }}
                  className="gap-4"
                />
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

      <FormError message={error} className="rounded-lg py-2.5 font-medium" />

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
