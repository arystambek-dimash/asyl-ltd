"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  Check,
  CircleDollarSign,
  Info,
  PackageOpen,
  Plus,
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
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { cn, formatCurrency, todayLocalIsoDate, currencySymbol } from "@/lib/utils";
import { useAuth } from "@/store/auth";
import type { Client, Department, Order, Product, Store } from "@/lib/types";

type Row = { id: number; product: string; quantity: string; price: string };
type Step = 1 | 2 | 3;

const STEPS = [
  { number: 1 as const, label: "Клиент", caption: "Кому и от какого отдела", icon: UserRound },
  { number: 2 as const, label: "Доставка", caption: "Валюта и транспорт", icon: Truck },
  { number: 3 as const, label: "Состав", caption: "Товары и итог", icon: PackageOpen },
];

function Stepper({ step, onSelect }: { step: Step; onSelect: (step: Step) => void }) {
  return (
    <div className="relative grid grid-cols-3 gap-1 rounded-2xl border border-[var(--border)] bg-[var(--secondary)] p-1.5 sm:gap-2 sm:p-2">
      <div className="pointer-events-none absolute left-[17%] right-[17%] top-[25px] h-px bg-[var(--border)]" />
      {STEPS.map((item) => {
        const Icon = item.icon;
        const active = item.number === step;
        const done = item.number < step;
        return (
          <button
            key={item.number}
            type="button"
            onClick={() => item.number < step && onSelect(item.number)}
            aria-label={`Шаг ${item.number}: ${item.label}. ${item.caption}`}
            aria-current={active ? "step" : undefined}
            aria-disabled={item.number > step}
            className={cn(
              "relative z-10 flex min-w-0 flex-col items-center gap-1 rounded-xl px-1 py-2 text-center transition sm:flex-row sm:gap-2 sm:px-3 sm:text-left",
              active && "bg-[var(--card)] shadow-sm ring-1 ring-[var(--border)]",
              item.number < step ? "cursor-pointer" : item.number > step ? "cursor-default" : "",
            )}
          >
            <span
              className={cn(
                "flex size-8 shrink-0 items-center justify-center rounded-full border text-xs font-black transition",
                active
                  ? "border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)]"
                  : done
                    ? "border-[var(--success)] bg-[var(--success)] text-white"
                    : "border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)]",
              )}
            >
              {done ? <Check className="size-4" /> : <Icon className="size-4" />}
            </span>
            <span className="block min-w-0">
              <span
                className={cn(
                  "block truncate text-[10px] font-bold sm:text-xs",
                  active ? "text-[var(--foreground)]" : "text-[var(--muted-foreground)]",
                )}
              >
                {item.label}
              </span>
              <span className="hidden truncate text-[10px] text-[var(--muted-foreground)] lg:block">
                {item.caption}
              </span>
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
  const { data: clients } = useApi<Client[]>("/clients/");
  const { data: products } = useApi<Product[]>("/products/");
  const { data: stores } = useApi<Store[]>("/stores/");
  const { data: departments } = useApi<Department[]>("/departments/");
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
  const [clientPrices, setClientPrices] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const assignedDepartment = !editing ? me?.sales_department : null;

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
    if (!client) {
      setClientPrices({});
      return;
    }
    const controller = new AbortController();
    api
      .get<Record<string, string>>(`/client-prices/?client=${client}&currency=${currency}`, {
        signal: controller.signal,
      })
      .then((response) => {
        if (controller.signal.aborted) return;
        setClientPrices(response.data);
        if (!editing) {
          setRows((current) =>
            current.map((row) => (row.product ? { ...row, price: response.data[row.product] ?? "" } : row)),
          );
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setClientPrices({});
      });
    return () => controller.abort();
  }, [client, currency, editing]);

  const normalizedSearch = clientSearch.trim().toLocaleLowerCase("ru");
  const filteredClients = (clients ?? []).filter((item) => {
    if (!normalizedSearch) return true;
    return `${item.name} ${item.company_name || ""} ${item.phone || ""}`
      .toLocaleLowerCase("ru")
      .includes(normalizedSearch);
  });
  const selectedClient = (clients ?? []).find((item) => String(item.id) === client);
  const clientStores = (stores ?? []).filter((item) => String(item.client) === client);
  const selectedStore = clientStores.find((item) => String(item.id) === store);
  const selectedDepartment = (departments ?? []).find((item) => item.code === dept);
  const validRows = rows.filter((row) => row.product && Number(row.quantity) > 0);
  const allPriced = validRows.every((row) => Number(row.price) > 0);
  const total = validRows.reduce((sum, row) => sum + Number(row.price || 0) * Number(row.quantity || 0), 0);
  const selectedBags = validRows.reduce((sum, row) => sum + Number(row.quantity || 0), 0);

  function chooseClient(item: Client) {
    setClient(String(item.id));
    setStore("");
    setCurrency(item.currency);
    setClientSearch("");
    setClientPickerOpen(false);
    setError("");
  }

  function nextStep() {
    setError("");
    if (step === 1 && (!client || !dept)) {
      setError(!client ? "Выберите клиента, чтобы продолжить." : "Выберите отдел продаж.");
      return;
    }
    if (step < 3) setStep((step + 1) as Step);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (step < 3) {
      nextStep();
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (!validRows.length) throw new Error("empty");
      if (!allPriced) throw new Error("price_required");
      const items = validRows.map((row) => ({
        product: Number(row.product),
        quantity: Number(row.quantity),
      }));
      const prices = Object.fromEntries(validRows.map((row) => [row.product, row.price]));
      const body = {
        department: assignedDepartment?.code ?? dept,
        store: store ? Number(store) : null,
        transport_type: transport,
        truck_number: transport === "train" ? "" : truck,
        arrival_date: arrival || null,
        currency,
        items,
        prices,
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

      {step === 1 && (
        <div className="space-y-5">
          <section className="space-y-3">
            <div className="flex items-end justify-between gap-3">
              <div>
                <h3 className="text-base font-bold text-[var(--foreground)]">Отдел продаж</h3>
                <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                  Отдел закрепляется за заказом и учитывается в аналитике.
                </p>
              </div>
              {assignedDepartment && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-[var(--soft-green)] px-2.5 py-1 text-[11px] font-semibold text-[var(--soft-green-foreground)]">
                  <ShieldCheck className="size-3.5" /> назначен вам
                </span>
              )}
            </div>
            {assignedDepartment ? (
              <div className="flex min-h-14 items-center gap-3 rounded-2xl border border-[var(--soft-blue-border)] bg-[var(--soft-blue)] px-4 py-3">
                <span
                  className="size-3 rounded-full ring-4 ring-[var(--soft-blue-border)]"
                  style={{ backgroundColor: assignedDepartment.color }}
                />
                <div>
                  <div className="font-bold text-[var(--foreground)]">{assignedDepartment.name}</div>
                  <div className="text-xs text-[var(--muted-foreground)]">
                    Подставляется автоматически и не меняется в этом заказе
                  </div>
                </div>
              </div>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {(departments ?? []).map((department) => (
                  <button
                    key={department.code}
                    type="button"
                    onClick={() => setDept(department.code)}
                    className={cn(
                      "flex min-h-12 items-center gap-2.5 rounded-xl border px-3 py-2 text-left text-sm font-semibold transition",
                      dept === department.code
                        ? "border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)] shadow-md"
                        : "border-[var(--border)] bg-[var(--card)] text-[var(--foreground)] hover:-translate-y-0.5 hover:border-[var(--input)]",
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

          <section className="space-y-3 border-t border-[var(--border)] pt-5">
            <div>
              <h3 className="text-base font-bold text-[var(--foreground)]">Клиент</h3>
              <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">Найдите по имени, компании или телефону.</p>
            </div>
            {!editing && (
              <div className="relative">
                <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
                <Input
                  value={clientSearch}
                  onChange={(event) => setClientSearch(event.target.value)}
                  onFocus={() => setClientPickerOpen(true)}
                  placeholder="Поиск клиента…"
                  className="h-11 rounded-xl bg-[var(--card)] pl-10"
                  autoFocus={!client}
                />
              </div>
            )}

            {selectedClient && (
              <div className="flex items-center gap-3 rounded-2xl border border-[var(--soft-blue-border)] bg-[var(--soft-blue)] p-3.5">
                <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-[var(--ring)] text-white">
                  <UserRound className="size-5" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-bold text-[var(--foreground)]">{selectedClient.name}</div>
                  <div className="truncate text-xs text-[var(--muted-foreground)]">
                    {selectedClient.company_name || selectedClient.phone || "Без дополнительных данных"}
                  </div>
                </div>
                <span className="rounded-lg bg-[var(--card)] px-2 py-1 text-xs font-bold text-[var(--muted-foreground)] shadow-sm">
                  {selectedClient.currency}
                </span>
                {!editing && (
                  <button
                    type="button"
                    onClick={() => setClientPickerOpen(true)}
                    className="text-xs font-semibold text-[var(--soft-blue-foreground)] hover:underline"
                  >
                    Изменить
                  </button>
                )}
              </div>
            )}

            {!editing && (!selectedClient || clientPickerOpen || !!clientSearch) && (
              <div className="max-h-64 overflow-y-auto rounded-2xl border border-[var(--border)] bg-[var(--card)] p-2 shadow-inner">
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
                          selected
                            ? "bg-[var(--soft-blue)] ring-1 ring-[var(--soft-blue-border)]"
                            : "hover:bg-[var(--secondary)]",
                        )}
                      >
                        <span
                          className={cn(
                            "flex size-8 shrink-0 items-center justify-center rounded-lg text-xs font-black",
                            selected
                              ? "bg-[var(--ring)] text-white"
                              : "bg-[var(--muted)] text-[var(--muted-foreground)]",
                          )}
                        >
                          {item.name.slice(0, 1).toUpperCase()}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-semibold text-[var(--foreground)]">
                            {item.name}
                          </span>
                          <span className="block truncate text-[11px] text-[var(--muted-foreground)]">
                            {item.company_name || item.phone || "—"}
                          </span>
                        </span>
                        {selected && <Check className="size-4 shrink-0 text-[var(--soft-blue-foreground)]" />}
                      </button>
                    );
                  })}
                </div>
                {!filteredClients.length && (
                  <div className="flex min-h-28 flex-col items-center justify-center text-center text-[var(--muted-foreground)]">
                    <Search className="mb-2 size-6" />
                    <span className="text-sm font-semibold">Ничего не найдено</span>
                    <span className="mt-0.5 text-xs">Проверьте имя или номер телефона.</span>
                  </div>
                )}
              </div>
            )}
          </section>

          {client && (
            <section className="space-y-3 border-t border-[var(--border)] pt-5">
              <div>
                <h3 className="text-base font-bold text-[var(--foreground)]">Магазин</h3>
                <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
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
                      ? "border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)] shadow-md"
                      : "border-[var(--border)] bg-[var(--card)] hover:border-[var(--input)]",
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
                        ? "border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)] shadow-md"
                        : "border-[var(--border)] bg-[var(--card)] hover:border-[var(--input)]",
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

      {step === 2 && (
        <div className="space-y-6">
          <section className="space-y-3">
            <div className="flex items-end justify-between gap-3">
              <div>
                <h3 className="text-base font-bold text-[var(--foreground)]">Валюта заказа</h3>
                <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                  Оплата и личный прайс клиента будут в этой валюте.
                </p>
              </div>
              <CircleDollarSign className="size-5 text-[var(--soft-green-foreground)]" />
            </div>
            <div className="grid grid-cols-2 gap-2 rounded-2xl bg-[var(--muted)] p-1.5">
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
                      ? "border-[var(--soft-green-border)] bg-[var(--card)] text-[var(--foreground)] shadow-sm"
                      : "border-transparent text-[var(--muted-foreground)] hover:bg-[var(--card)]/60",
                  )}
                >
                  <span>
                    <b className="mr-2 font-black">{code}</b>
                    <span className="text-xs">{label}</span>
                  </span>
                  <span className="text-xl font-black text-[var(--soft-green-foreground)]">{symbol}</span>
                </button>
              ))}
            </div>
          </section>

          <section className="space-y-3 border-t border-[var(--border)] pt-5">
            <div>
              <h3 className="text-base font-bold text-[var(--foreground)]">Транспорт</h3>
              <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">Как заказ прибудет на погрузку.</p>
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
                  onClick={() => setTransport(value)}
                  className={cn(
                    "flex min-h-16 items-center gap-3 rounded-2xl border px-4 py-3 text-left transition",
                    transport === value
                      ? "border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)] shadow-md"
                      : "border-[var(--border)] bg-[var(--card)] text-[var(--foreground)] hover:-translate-y-0.5 hover:border-[var(--input)]",
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

          <section className="grid gap-4 border-t border-[var(--border)] pt-5 sm:grid-cols-2">
            {transport === "truck" && (
              <div className="grid gap-2">
                <Label id="order-truck-label">Номер машины</Label>
                <LicensePlateInput labelledBy="order-truck-label" value={truck} onChange={setTruck} />
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

      {step === 3 && (
        <div className="space-y-5">
          <section className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <div className="rounded-2xl border border-[var(--border)] bg-[var(--secondary)] p-3 sm:p-3.5">
              <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
                Клиент
              </div>
              <div className="mt-1 truncate text-sm font-bold text-[var(--foreground)]">
                {selectedClient?.name || "—"}
              </div>
              <div className="mt-0.5 truncate text-[11px] text-[var(--muted-foreground)]">
                {selectedStore?.name || "Без магазина"}
              </div>
            </div>
            <div className="rounded-2xl border border-[var(--border)] bg-[var(--secondary)] p-3 sm:p-3.5">
              <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
                Отдел
              </div>
              <div className="mt-1 flex items-center gap-2 text-sm font-bold text-[var(--foreground)]">
                <span
                  className="size-2 rounded-full"
                  style={{ backgroundColor: assignedDepartment?.color || selectedDepartment?.color || "#64748B" }}
                />
                {assignedDepartment?.name || selectedDepartment?.name || dept}
              </div>
              <div className="mt-0.5 text-[11px] text-[var(--muted-foreground)]">
                {transport === "truck" ? "Трак" : "Вагон"}
                {arrival ? ` · ${arrival}` : ""}
              </div>
            </div>
            <div className="col-span-2 rounded-2xl border border-[var(--soft-green-border)] bg-[var(--soft-green)] p-3 sm:col-span-1 sm:p-3.5">
              <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-[var(--soft-green-foreground)]">
                Текущий итог
              </div>
              <div className="mt-1 text-lg font-black tabular-nums text-[var(--foreground)]">
                {formatCurrency(String(total), currency)}
              </div>
              <div className="mt-0.5 text-[11px] text-[var(--soft-green-foreground)]">
                {selectedBags} мешков · {currency}
              </div>
            </div>
          </section>

          <section className="space-y-3">
            <div>
              <h3 className="text-base font-bold text-[var(--foreground)]">Позиции заказа</h3>
              <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                Цена подставляется из личного прайса клиента в {currency}.
              </p>
            </div>
            <div className="space-y-2">
              {rows.map((row, index) => (
                <div
                  key={row.id}
                  className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_44px] gap-2 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-3 sm:grid-cols-[minmax(0,1fr)_100px_140px_40px]"
                >
                  <Select
                    value={row.product}
                    className="col-span-3 h-10 rounded-xl sm:col-span-1"
                    aria-label={`Товар, позиция ${index + 1}`}
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
                    {(products ?? []).map((product) => {
                      const bags = product.available_bags ?? 0;
                      return (
                        <option key={product.id} value={product.id} disabled={bags <= 0}>
                          {product.label}
                          {bags > 0 ? ` · ${bags} меш.` : " — нет в наличии"}
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

          <div className="flex items-start gap-2 rounded-xl border border-[var(--soft-blue-border)] bg-[var(--soft-blue)] px-3 py-2.5 text-xs text-[var(--soft-blue-foreground)]">
            <Info className="mt-0.5 size-3.5 shrink-0" />
            {editing
              ? "Позиции и цены можно менять до начала загрузки. Изменения попадут в журнал."
              : template
                ? `Данные взяты из заказа #${template.id}, а цены обновлены из текущего прайса клиента. Проверьте всё перед созданием.`
                : "После создания клиент, валюта и отдел закрепятся за заказом. Перед сохранением проверьте итог."}
          </div>
        </div>
      )}

      {error && (
        <p
          role="alert"
          className="rounded-xl border border-[var(--soft-red-border)] bg-[var(--soft-red)] px-3 py-2.5 text-sm font-medium text-[var(--destructive)]"
        >
          {error}
        </p>
      )}

      <div className="sticky -bottom-5 z-10 flex items-center justify-between gap-3 border-t border-[var(--border)] bg-[var(--card)]/95 pb-1 pt-4 backdrop-blur-md">
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
          <Button type="button" onClick={nextStep} disabled={step === 1 && (!client || !dept)}>
            Продолжить <ArrowRight className="size-4" />
          </Button>
        ) : (
          <Button type="submit" disabled={busy || !client || !dept || !validRows.length || !allPriced}>
            {busy ? "Сохранение…" : editing ? "Сохранить изменения" : "Создать заказ"}
            {!busy && <Check className="size-4" />}
          </Button>
        )}
      </div>
    </form>
  );
}
