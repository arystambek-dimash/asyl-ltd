"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Building2,
  Check,
  CircleDollarSign,
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
import { LicensePlateInput } from "@/components/ui/license-plate-input";
import { DataGate } from "@/components/ui/data-state";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { cn, formatCurrency, todayLocalIsoDate, currencySymbol } from "@/lib/utils";
import { useAuth } from "@/store/auth";
import type { Client, Department, Order, Product, Store } from "@/lib/types";

type Row = { id: number; product: string; quantity: string; price: string };
type Step = 1 | 2 | 3;
type OrderClientOption = Pick<Client, "id" | "name" | "company_name" | "phone" | "currency">;
type OrderProductOption = Pick<Product, "id" | "label" | "available_bags">;
type OrderStoreOption = Pick<Store, "id" | "client" | "name" | "address">;
type OrderDepartmentOption = Pick<Department, "id" | "code" | "name" | "color" | "is_default">;

interface OrderFormOptions {
  clients: OrderClientOption[];
  products: OrderProductOption[];
  stores: OrderStoreOption[];
  departments: OrderDepartmentOption[];
}

const EMPTY_FORM_OPTIONS: OrderFormOptions = {
  clients: [],
  products: [],
  stores: [],
  departments: [],
};

const STEPS = [
  { number: 1 as const, label: "Клиент", caption: "Кому и от какого отдела", icon: UserRound },
  { number: 2 as const, label: "Доставка", caption: "Валюта и транспорт", icon: Truck },
  { number: 3 as const, label: "Состав", caption: "Товары и итог", icon: PackageOpen },
];

function Stepper({ step, onSelect }: { step: Step; onSelect: (step: Step) => void }) {
  return (
    <div className="relative grid grid-cols-3 gap-2 rounded-2xl border border-slate-200 bg-slate-50/80 p-2">
      <div className="pointer-events-none absolute left-[17%] right-[17%] top-[25px] h-px bg-slate-200" />
      {STEPS.map((item) => {
        const Icon = item.icon;
        const active = item.number === step;
        const done = item.number < step;
        return (
          <button
            key={item.number}
            type="button"
            onClick={() => item.number < step && onSelect(item.number)}
            className={cn(
              "relative z-10 flex min-w-0 items-center gap-2 rounded-xl px-2 py-2 text-left transition sm:px-3",
              active && "bg-white shadow-sm ring-1 ring-slate-200",
              item.number < step ? "cursor-pointer" : item.number > step ? "cursor-default" : "",
            )}
          >
            <span
              className={cn(
                "flex size-8 shrink-0 items-center justify-center rounded-full border text-xs font-black transition",
                active
                  ? "border-slate-900 bg-slate-900 text-white"
                  : done
                    ? "border-emerald-500 bg-emerald-500 text-white"
                    : "border-slate-200 bg-white text-slate-400",
              )}
            >
              {done ? <Check className="size-4" /> : <Icon className="size-4" />}
            </span>
            <span className="hidden min-w-0 sm:block">
              <span className={cn("block truncate text-xs font-bold", active ? "text-slate-900" : "text-slate-500")}>
                {item.label}
              </span>
              <span className="block truncate text-[10px] text-slate-400">{item.caption}</span>
            </span>
          </button>
        );
      })}
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
  const { clients, products, stores, departments } = formOptions ?? EMPTY_FORM_OPTIONS;
  const source = editing ?? template;
  const nextRowId = useRef(source?.items.length ?? 1);
  const [step, setStep] = useState<Step>(1);
  const [clientSearch, setClientSearch] = useState("");
  const [clientPickerOpen, setClientPickerOpen] = useState(!source);
  const [dept, setDept] = useState(source?.department ?? "");
  const [client, setClient] = useState(source ? String(source.client) : "");
  const [currency, setCurrency] = useState<"KZT" | "USD">(source?.currency ?? "KZT");
  const [store, setStore] = useState(source?.store ? String(source.store) : "");
  const [transport, setTransport] = useState<"truck" | "train">(source?.transport_type ?? "truck");
  const [truck, setTruck] = useState(source?.truck_number ?? "");
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

  const compositionLocked = editing?.status === "loading";
  const shippedCorrection = editing?.status === "shipped";
  const physicalFieldsLocked = Boolean(editing && ["arrived", "loading", "loaded", "shipped"].includes(editing.status));

  const assignedDepartment = !editing ? me?.sales_department : null;
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
    if (dept || !departments?.length) return;
    setDept((departments.find((department) => department.is_default) ?? departments[0]).code);
  }, [assignedDepartment, departments, dept, editing]);

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
  const selectedClient = clients.find((item) => String(item.id) === client);
  const clientStores = stores.filter((item) => String(item.client) === client);
  const selectedStore = clientStores.find((item) => String(item.id) === store);
  const selectedDepartment = departments.find((item) => item.code === dept);
  const validRows = rows.filter((row) => row.product && Number(row.quantity) > 0);
  const allPriced = validRows.every((row) => Number(row.price) > 0);
  const total = validRows.reduce((sum, row) => sum + Number(row.price || 0) * Number(row.quantity || 0), 0);
  const selectedBags = validRows.reduce((sum, row) => sum + Number(row.quantity || 0), 0);

  function chooseClient(item: OrderClientOption) {
    setClient(String(item.id));
    setStore("");
    setCurrency(item.currency);
    setClientSearch("");
    setClientPickerOpen(false);
    setError("");
  }

  function nextStep() {
    setError("");
    if (!referenceDataReady) {
      setError("Сначала загрузите справочники заказа.");
      return;
    }
    if (step === 1 && (!client || !dept)) {
      setError(!client ? "Выберите клиента, чтобы продолжить." : "Выберите отдел продаж.");
      return;
    }
    if (step < 3) setStep((step + 1) as Step);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!referenceDataReady) {
      setError("Сначала загрузите справочники заказа.");
      return;
    }
    if (step < 3) {
      nextStep();
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (!compositionLocked && !validRows.length) throw new Error("empty");
      if (!compositionLocked && !allPriced) throw new Error("price_required");
      if (shippedCorrection && editReason.trim().length < 5) throw new Error("edit_reason_required");
      const items = validRows.map((row) => ({
        product: Number(row.product),
        quantity: Number(row.quantity),
      }));
      const prices = Object.fromEntries(validRows.map((row) => [row.product, row.price]));
      const body = {
        store: store ? Number(store) : null,
        arrival_date: arrival || null,
        currency,
        ...(!physicalFieldsLocked
          ? {
              department: assignedDepartment?.code ?? dept,
              transport_type: transport,
              truck_number: transport === "train" ? "" : truck,
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
      if (cause instanceof Error && cause.message === "empty") {
        setError("Добавьте хотя бы одну позицию.");
      } else if (cause instanceof Error && cause.message === "price_required") {
        setError("Укажите цену для каждой позиции.");
      } else if (cause instanceof Error && cause.message === "edit_reason_required") {
        setError("Укажите причину изменения отгруженного заказа — минимум 5 символов.");
      } else {
        setError(apiError(cause));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-5">
      <Stepper
        step={step}
        onSelect={(next) => {
          setError("");
          setStep(next);
        }}
      />

      {(!referenceDataReady || formOptionsError) && (
        <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
          <DataGate
            loading={!formOptionsError && formOptionsLoading}
            error={formOptionsError || undefined}
            onRetry={reloadReferenceData}
          />
        </div>
      )}

      {referenceDataReady && step === 1 && (
        <div className="space-y-5">
          <section className="space-y-3">
            <div className="flex items-end justify-between gap-3">
              <div>
                <h3 className="text-base font-bold text-slate-900">Отдел продаж</h3>
                <p className="mt-0.5 text-xs text-slate-500">
                  Отдел закрепляется за заказом и учитывается в аналитике.
                </p>
              </div>
              {assignedDepartment && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold text-emerald-700">
                  <ShieldCheck className="size-3.5" /> назначен вам
                </span>
              )}
            </div>
            {assignedDepartment ? (
              <div className="flex min-h-14 items-center gap-3 rounded-2xl border border-blue-200 bg-gradient-to-r from-blue-50 to-white px-4 py-3 shadow-sm">
                <span
                  className="size-3 rounded-full ring-4 ring-blue-100"
                  style={{ backgroundColor: assignedDepartment.color }}
                />
                <div>
                  <div className="font-bold text-slate-900">{assignedDepartment.name}</div>
                  <div className="text-xs text-slate-500">Подставляется автоматически и не меняется в этом заказе</div>
                </div>
              </div>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {departments.map((department) => (
                  <button
                    key={department.code}
                    type="button"
                    disabled={physicalFieldsLocked}
                    onClick={() => setDept(department.code)}
                    className={cn(
                      "flex min-h-12 items-center gap-2.5 rounded-xl border px-3 py-2 text-left text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-60",
                      dept === department.code
                        ? "border-slate-900 bg-slate-900 text-white shadow-md"
                        : "border-slate-200 bg-white text-slate-700 hover:-translate-y-0.5 hover:border-slate-300",
                    )}
                  >
                    <span
                      className="size-2.5 shrink-0 rounded-full ring-4 ring-current/10"
                      style={{ backgroundColor: department.color, color: department.color }}
                    />
                    <span className="truncate">{department.name}</span>
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className="space-y-3 border-t border-slate-200 pt-5">
            <div>
              <h3 className="text-base font-bold text-slate-900">Клиент</h3>
              <p className="mt-0.5 text-xs text-slate-500">Найдите по имени, компании или телефону.</p>
            </div>
            {!editing && (
              <div className="relative">
                <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
                <Input
                  value={clientSearch}
                  onChange={(event) => setClientSearch(event.target.value)}
                  onFocus={() => setClientPickerOpen(true)}
                  placeholder="Поиск клиента…"
                  className="h-11 rounded-xl bg-white pl-10"
                  autoFocus={!client}
                />
              </div>
            )}

            {selectedClient && (
              <div className="flex items-center gap-3 rounded-2xl border border-blue-200 bg-blue-50/70 p-3.5">
                <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-blue-600 text-white">
                  <UserRound className="size-5" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-bold text-slate-900">{selectedClient.name}</div>
                  <div className="truncate text-xs text-slate-500">
                    {selectedClient.company_name || selectedClient.phone || "Без дополнительных данных"}
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

            {!editing && (!selectedClient || clientPickerOpen || !!clientSearch) && (
              <div className="max-h-64 overflow-y-auto rounded-2xl border border-slate-200 bg-white p-2 shadow-inner">
                <div className="grid gap-1 sm:grid-cols-2">
                  {filteredClients.map((item) => {
                    const selected = String(item.id) === client;
                    return (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() => chooseClient(item)}
                        className={cn(
                          "flex min-w-0 items-center gap-3 rounded-xl px-3 py-2.5 text-left transition",
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
                  <div className="flex min-h-28 flex-col items-center justify-center text-center text-slate-400">
                    <Search className="mb-2 size-6" />
                    <span className="text-sm font-semibold">Ничего не найдено</span>
                    <span className="mt-0.5 text-xs">Проверьте имя или номер телефона.</span>
                  </div>
                )}
              </div>
            )}
          </section>

          {client && (
            <section className="space-y-3 border-t border-slate-200 pt-5">
              <div>
                <h3 className="text-base font-bold text-slate-900">Магазин</h3>
                <p className="mt-0.5 text-xs text-slate-500">
                  Необязательно — заказ можно оформить напрямую на клиента.
                </p>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => setStore("")}
                  className={cn(
                    "flex min-h-14 items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition",
                    !store
                      ? "border-slate-900 bg-slate-900 text-white shadow-md"
                      : "border-slate-200 bg-white hover:border-slate-300",
                  )}
                >
                  <Building2 className="size-5 shrink-0" />
                  <span>
                    <span className="block text-sm font-bold">Без магазина</span>
                    <span className="block text-[11px] opacity-60">Заказ на клиента</span>
                  </span>
                </button>
                {clientStores.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setStore(String(item.id))}
                    className={cn(
                      "flex min-h-14 items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition",
                      store === String(item.id)
                        ? "border-slate-900 bg-slate-900 text-white shadow-md"
                        : "border-slate-200 bg-white hover:border-slate-300",
                    )}
                  >
                    <StoreIcon className="size-5 shrink-0" />
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-bold">{item.name}</span>
                      <span className="block truncate text-[11px] opacity-60">{item.address || "Адрес не указан"}</span>
                    </span>
                  </button>
                ))}
              </div>
            </section>
          )}
        </div>
      )}

      {referenceDataReady && step === 2 && (
        <div className="space-y-6">
          <section className="space-y-3">
            <div className="flex items-end justify-between gap-3">
              <div>
                <h3 className="text-base font-bold text-slate-900">Валюта заказа</h3>
                <p className="mt-0.5 text-xs text-slate-500">Оплата и личный прайс клиента будут в этой валюте.</p>
              </div>
              <CircleDollarSign className="size-5 text-emerald-500" />
            </div>
            <div className="grid grid-cols-2 gap-2 rounded-2xl bg-slate-100 p-1.5">
              {(
                [
                  ["KZT", "₸", "Тенге"],
                  ["USD", "$", "Доллары"],
                ] as const
              ).map(([code, symbol, label]) => (
                <button
                  key={code}
                  type="button"
                  disabled={!!editing}
                  onClick={() => setCurrency(code)}
                  className={cn(
                    "flex min-h-14 items-center justify-between rounded-xl border px-4 py-2.5 text-left transition disabled:cursor-not-allowed",
                    currency === code
                      ? "border-emerald-200 bg-white text-slate-900 shadow-sm"
                      : "border-transparent text-slate-500 hover:bg-white/60",
                  )}
                >
                  <span>
                    <b className="mr-2 font-black">{code}</b>
                    <span className="text-xs">{label}</span>
                  </span>
                  <span className="text-xl font-black text-emerald-600">{symbol}</span>
                </button>
              ))}
            </div>
          </section>

          <section className="space-y-3 border-t border-slate-200 pt-5">
            <div>
              <h3 className="text-base font-bold text-slate-900">Транспорт</h3>
              <p className="mt-0.5 text-xs text-slate-500">Как заказ прибудет на погрузку.</p>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {(
                [
                  ["truck", "🚚", "Трак", "Автомобиль"],
                  ["train", "🚃", "Вагон", "Железная дорога"],
                ] as const
              ).map(([value, emoji, label, caption]) => (
                <button
                  key={value}
                  type="button"
                  disabled={physicalFieldsLocked}
                  onClick={() => setTransport(value)}
                  className={cn(
                    "flex min-h-16 items-center gap-3 rounded-2xl border px-4 py-3 text-left transition disabled:cursor-not-allowed disabled:opacity-60",
                    transport === value
                      ? "border-slate-900 bg-slate-900 text-white shadow-md"
                      : "border-slate-200 bg-white text-slate-700 hover:-translate-y-0.5 hover:border-slate-300",
                  )}
                >
                  <span className="text-2xl">{emoji}</span>
                  <span>
                    <span className="block text-sm font-bold">{label}</span>
                    <span className="block text-[11px] opacity-60">{caption}</span>
                  </span>
                </button>
              ))}
            </div>
          </section>

          <section className="grid gap-4 border-t border-slate-200 pt-5 sm:grid-cols-2">
            {transport === "truck" && (
              <div className="grid gap-2">
                <Label id="order-truck-label">Номер машины</Label>
                <LicensePlateInput
                  labelledBy="order-truck-label"
                  value={truck}
                  onChange={setTruck}
                  disabled={physicalFieldsLocked}
                />
              </div>
            )}
            <div className="grid gap-2">
              <Label htmlFor="order-arrival">Плановая дата прибытия</Label>
              <Input
                id="order-arrival"
                type="date"
                value={arrival}
                onChange={(event) => setArrival(event.target.value)}
                className="h-11 rounded-xl"
              />
            </div>
          </section>
        </div>
      )}

      {referenceDataReady && step === 3 && (
        <div className="space-y-5">
          <section className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3 sm:p-3.5">
              <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-400">Клиент</div>
              <div className="mt-1 truncate text-sm font-bold text-slate-900">{selectedClient?.name || "—"}</div>
              <div className="mt-0.5 truncate text-[11px] text-slate-500">{selectedStore?.name || "Без магазина"}</div>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3 sm:p-3.5">
              <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-400">Отдел</div>
              <div className="mt-1 flex items-center gap-2 text-sm font-bold text-slate-900">
                <span
                  className="size-2 rounded-full"
                  style={{ backgroundColor: assignedDepartment?.color || selectedDepartment?.color || "#64748B" }}
                />
                {assignedDepartment?.name || selectedDepartment?.name || dept}
              </div>
              <div className="mt-0.5 text-[11px] text-slate-500">
                {transport === "truck" ? "Трак" : "Вагон"}
                {arrival ? ` · ${arrival}` : ""}
              </div>
            </div>
            <div className="col-span-2 rounded-2xl border border-emerald-200 bg-emerald-50/70 p-3 sm:col-span-1 sm:p-3.5">
              <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-emerald-600">Текущий итог</div>
              <div className="mt-1 text-lg font-black tabular-nums text-slate-900">
                {formatCurrency(String(total), currency)}
              </div>
              <div className="mt-0.5 text-[11px] text-emerald-700">
                {selectedBags} мешков · {currency}
              </div>
            </div>
          </section>

          <section className="space-y-3">
            <div>
              <h3 className="text-base font-bold text-slate-900">Позиции заказа</h3>
              <p className="mt-0.5 text-xs text-slate-500">
                {compositionLocked
                  ? "Во время активной погрузки состав зафиксирован. Остальные данные заказа можно исправить."
                  : `Цена подставляется из личного прайса клиента в ${currency}.`}
              </p>
            </div>
            {client && clientPricesLoading && !loadedClientPrices && (
              <div
                role="status"
                className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600"
              >
                <RefreshCw className="size-3.5 shrink-0 animate-spin" />
                Загружаем личный прайс клиента…
              </div>
            )}
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
            <div className="space-y-2">
              {rows.map((row, index) => (
                <div
                  key={row.id}
                  className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_40px] gap-2 rounded-2xl border border-slate-200 bg-white p-3 sm:grid-cols-[minmax(0,1fr)_100px_140px_36px]"
                >
                  <Select
                    value={row.product}
                    className="col-span-3 h-10 rounded-xl sm:col-span-1"
                    aria-label={`Товар, позиция ${index + 1}`}
                    disabled={compositionLocked}
                    onChange={(event) => {
                      const product = event.target.value;
                      setRows(
                        rows.map((item, itemIndex) =>
                          itemIndex === index ? { ...item, product, price: clientPrices[product] ?? "" } : item,
                        ),
                      );
                    }}
                  >
                    <option value="">Выберите товар</option>
                    {products.map((product) => {
                      const bags = product.available_bags ?? 0;
                      const unavailableForNewShipment = bags <= 0 && !shippedCorrection;
                      return (
                        <option key={product.id} value={product.id} disabled={unavailableForNewShipment}>
                          {product.label}
                          {bags > 0
                            ? ` · ${bags} меш.`
                            : shippedCorrection
                              ? " — нет текущего остатка, доступно для исправления"
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
                    className="rounded-xl"
                    value={row.quantity}
                    aria-label={`Количество мешков, позиция ${index + 1}`}
                    disabled={compositionLocked}
                    onChange={(event) =>
                      setRows(
                        rows.map((item, itemIndex) =>
                          itemIndex === index ? { ...item, quantity: event.target.value } : item,
                        ),
                      )
                    }
                  />
                  <Input
                    type="number"
                    min="0"
                    step="0.01"
                    inputMode="decimal"
                    className="rounded-xl"
                    aria-label={`Цена, позиция ${index + 1}`}
                    placeholder={`Цена, ${currencySymbol(currency)}`}
                    value={row.price}
                    disabled={compositionLocked}
                    onChange={(event) =>
                      setRows(
                        rows.map((item, itemIndex) =>
                          itemIndex === index ? { ...item, price: event.target.value } : item,
                        ),
                      )
                    }
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    title="Удалить позицию"
                    aria-label={`Удалить позицию ${index + 1}`}
                    disabled={compositionLocked}
                    onClick={() =>
                      setRows(
                        rows.length > 1
                          ? rows.filter((_, itemIndex) => itemIndex !== index)
                          : [{ id: nextRowId.current++, product: "", quantity: "", price: "" }],
                      )
                    }
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ))}
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="w-full rounded-xl border-dashed"
              disabled={compositionLocked}
              onClick={() =>
                setRows([
                  ...rows,
                  {
                    id: nextRowId.current++,
                    product: "",
                    quantity: "",
                    price: "",
                  },
                ])
              }
            >
              <Plus className="size-4" /> Добавить позицию
            </Button>
          </section>

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

          <div className="flex items-start gap-2 rounded-xl border border-blue-100 bg-blue-50/60 px-3 py-2.5 text-xs text-blue-700">
            <Info className="mt-0.5 size-3.5 shrink-0" />
            {editing
              ? compositionLocked
                ? "Заказ открыт для исправления, но состав защищён до завершения текущей погрузки."
                : "Заказ можно исправить на любом этапе. Физические и финансовые изменения попадут в журнал."
              : template
                ? `Данные взяты из заказа #${template.id}, а цены обновлены из текущего прайса клиента. Проверьте всё перед созданием.`
                : "После создания клиент, валюта и отдел закрепятся за заказом. Перед сохранением проверьте итог."}
          </div>
        </div>
      )}

      {error && (
        <p className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm font-medium text-[var(--destructive)]">
          {error}
        </p>
      )}

      <div className="sticky -bottom-5 z-10 flex items-center justify-between gap-3 border-t border-slate-200 bg-white/95 pb-1 pt-4 backdrop-blur-md">
        <div>
          {step > 1 ? (
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setError("");
                setStep((step - 1) as Step);
              }}
            >
              <ArrowLeft className="size-4" /> Назад
            </Button>
          ) : (
            <Button type="button" variant="ghost" onClick={onCancel}>
              Отмена
            </Button>
          )}
        </div>
        {step < 3 ? (
          <Button
            key="continue"
            type="button"
            onClick={(event) => {
              // React must not reuse this clicked node as the submit button
              // before the browser runs the click's default action.
              event.preventDefault();
              nextStep();
            }}
            disabled={!referenceDataReady || (step === 1 && (!client || !dept))}
          >
            Продолжить <ArrowRight className="size-4" />
          </Button>
        ) : (
          <Button
            key="submit"
            type="submit"
            disabled={
              busy ||
              !referenceDataReady ||
              !client ||
              !dept ||
              (!compositionLocked && (!validRows.length || !allPriced)) ||
              (shippedCorrection && editReason.trim().length < 5)
            }
          >
            {busy ? "Сохранение…" : editing ? "Сохранить изменения" : "Создать заказ"}
            {!busy && <Check className="size-4" />}
          </Button>
        )}
      </div>
    </form>
  );
}
