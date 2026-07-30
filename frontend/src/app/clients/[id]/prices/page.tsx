"use client";

import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { DataGate } from "@/components/ui/data-state";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatDateTime, currencySymbol } from "@/lib/utils";
import type { ClientPriceSheet } from "@/lib/types";
import { ArrowLeft, CheckCircle2, Save, Search, Tags } from "lucide-react";

function ClientPricesPageInner({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, loading, error: loadError, reload } = useApi<ClientPriceSheet>(`/clients/${id}/prices/`);
  const [values, setValues] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!data) return;
    setValues(Object.fromEntries(data.prices.map((row) => [`${row.product}:${row.currency}`, row.price ?? ""])));
    setDirty(false);
  }, [data]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const products = new Map<number, { product: number; product_label: string }>();
    for (const row of data?.prices ?? []) {
      if (!products.has(row.product)) products.set(row.product, row);
    }
    return [...products.values()].filter((row) => !needle || row.product_label.toLowerCase().includes(needle));
  }, [data, query]);

  if (!data) {
    return (
      <AppShell title="Прайс-лист" section="Работа">
        <DataGate loading={loading} error={loadError} onRetry={reload} />
      </AppShell>
    );
  }

  const priceRows = data.prices;
  const assigned = priceRows.filter((row) => Number(values[`${row.product}:${row.currency}`]) > 0).length;
  const productsTotal = new Set(priceRows.map((row) => row.product)).size;

  function setPrice(product: number, currency: "KZT" | "USD", value: string) {
    setValues((current) => ({ ...current, [`${product}:${currency}`]: value }));
    setDirty(true);
    setSaved(false);
  }

  async function save() {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      await api.put(`/clients/${id}/prices/`, {
        prices: priceRows.map((row) => ({
          product: row.product,
          currency: row.currency,
          price: values[`${row.product}:${row.currency}`]?.trim() || null,
        })),
      });
      await reload();
      setDirty(false);
      setSaved(true);
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell title="Прайс-лист клиента" section="Работа">
      <div className="mb-5 flex flex-col gap-4 border-b pb-4 md:flex-row md:items-center">
        <div className="flex min-w-0 items-start gap-3">
          <Link
            href="/clients"
            aria-label="К клиентам"
            className="grid size-11 shrink-0 place-items-center rounded-xl border bg-[var(--card)] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/60 hover:text-[var(--foreground)]"
          >
            <ArrowLeft className="size-4" />
          </Link>
          <div className="hidden size-11 shrink-0 place-items-center rounded-xl bg-[var(--soft-blue)] text-[var(--soft-blue-foreground)] min-[400px]:grid">
            <Tags className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="break-words text-xl font-semibold tracking-tight">{data.client.name}</h2>
            <p className="mt-1 text-sm leading-relaxed text-[var(--muted-foreground)]">
              Договорные цены в тенге и долларах для заказов клиента.
            </p>
          </div>
        </div>
        <Button className="ml-auto hidden shrink-0 md:inline-flex" disabled={busy || !dirty} onClick={save}>
          <Save className="size-4" /> {busy ? "Сохранение…" : "Закрепить прайс"}
        </Button>
      </div>

      <div className="mb-4 grid grid-cols-1 gap-3 min-[400px]:grid-cols-2 sm:max-w-lg">
        <Card className="p-4">
          <div className="text-xs text-[var(--muted-foreground)]">Закреплено валютных цен</div>
          <div className="mt-1 text-2xl font-semibold tabular-nums">{assigned}</div>
        </Card>
        <Card className="p-4">
          <div className="text-xs text-[var(--muted-foreground)]">Всего товаров</div>
          <div className="mt-1 text-2xl font-semibold tabular-nums">{productsTotal}</div>
        </Card>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative w-full sm:w-80">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input
            className="pl-9"
            placeholder="Найти товар"
            aria-label="Поиск товара"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        {saved && (
          <span className="flex items-center gap-1.5 text-sm text-[var(--success)]">
            <CheckCircle2 className="size-4" /> Прайс закреплён
          </span>
        )}
      </div>

      {error && (
        <p className="mb-4 rounded-lg border border-[var(--destructive)]/20 bg-[var(--destructive)]/8 p-3 text-sm text-[var(--destructive)]">
          {error}
        </p>
      )}

      <Card>
        <CardContent className="p-0">
          <div className="hidden md:block">
            <Table>
              <THead>
                <TR>
                  <TH>Товар</TH>
                  <TH className="text-right">KZT · тенге</TH>
                  <TH className="text-right">USD · доллар</TH>
                </TR>
              </THead>
              <TBody>
                {filtered.map((row) => {
                  const byCurrency = Object.fromEntries(
                    priceRows.filter((item) => item.product === row.product).map((item) => [item.currency, item]),
                  ) as Record<"KZT" | "USD", (typeof priceRows)[number]>;
                  return (
                    <TR key={row.product}>
                      <TD className="font-medium">{row.product_label}</TD>
                      {(["KZT", "USD"] as const).map((currency) => {
                        const item = byCurrency[currency];
                        return (
                          <TD key={currency}>
                            <div className="ml-auto max-w-52">
                              <div className="relative">
                                <Input
                                  type="number"
                                  min="0.01"
                                  step="0.01"
                                  inputMode="decimal"
                                  className="pr-9 text-right tabular-nums"
                                  aria-label={`${row.product_label}, цена в ${currency}`}
                                  placeholder="Не закреплена"
                                  value={values[`${row.product}:${currency}`] ?? ""}
                                  onChange={(event) => setPrice(row.product, currency, event.target.value)}
                                />
                                <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm font-medium text-[var(--muted-foreground)]">
                                  {currencySymbol(currency)}
                                </span>
                              </div>
                              <p className="mt-1 text-right text-[10px] text-[var(--muted-foreground)]">
                                {item?.updated_at ? formatDateTime(item.updated_at) : "Ещё не закреплена"}
                                {item?.updated_by_name ? ` · ${item.updated_by_name}` : ""}
                              </p>
                            </div>
                          </TD>
                        );
                      })}
                    </TR>
                  );
                })}
                {filtered.length === 0 && (
                  <TR>
                    <TD colSpan={3} className="py-12 text-center text-[var(--muted-foreground)]">
                      Товары не найдены
                    </TD>
                  </TR>
                )}
              </TBody>
            </Table>
          </div>

          <div className="divide-y md:hidden">
            {filtered.map((row) => {
              const byCurrency = Object.fromEntries(
                priceRows.filter((item) => item.product === row.product).map((item) => [item.currency, item]),
              ) as Record<"KZT" | "USD", (typeof priceRows)[number]>;
              return (
                <section key={row.product} aria-labelledby={`product-${row.product}`} className="p-4">
                  <h3 id={`product-${row.product}`} className="break-words font-semibold">
                    {row.product_label}
                  </h3>
                  <div className="mt-4 grid gap-4">
                    {(["KZT", "USD"] as const).map((currency) => {
                      const item = byCurrency[currency];
                      const inputId = `mobile-price-${row.product}-${currency}`;
                      return (
                        <div key={currency}>
                          <label
                            htmlFor={inputId}
                            className="mb-1.5 flex items-center justify-between gap-3 text-sm font-medium"
                          >
                            <span>{currency === "KZT" ? "Цена в тенге" : "Цена в долларах"}</span>
                            <span className="text-xs text-[var(--muted-foreground)]">{currency}</span>
                          </label>
                          <div className="relative">
                            <Input
                              id={inputId}
                              type="number"
                              min="0.01"
                              step="0.01"
                              inputMode="decimal"
                              className="min-h-11 pr-10 tabular-nums"
                              placeholder="Не закреплена"
                              value={values[`${row.product}:${currency}`] ?? ""}
                              onChange={(event) => setPrice(row.product, currency, event.target.value)}
                            />
                            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm font-medium text-[var(--muted-foreground)]">
                              {currencySymbol(currency)}
                            </span>
                          </div>
                          <p className="mt-1.5 text-xs leading-relaxed text-[var(--muted-foreground)]">
                            {item?.updated_at ? formatDateTime(item.updated_at) : "Ещё не закреплена"}
                            {item?.updated_by_name ? ` · ${item.updated_by_name}` : ""}
                          </p>
                        </div>
                      );
                    })}
                  </div>
                </section>
              );
            })}
            {filtered.length === 0 && (
              <div className="px-4 py-12 text-center text-sm text-[var(--muted-foreground)]">Товары не найдены</div>
            )}
          </div>
        </CardContent>
      </Card>
      <p className="mt-3 text-xs text-[var(--muted-foreground)]">
        Очистите конкретное поле и сохраните, чтобы убрать цену только в этой валюте.
      </p>

      <div className="sticky bottom-3 z-20 mt-4 rounded-2xl border bg-[var(--card)]/95 p-2 shadow-[var(--shadow-float)] backdrop-blur md:hidden">
        <Button className="w-full" disabled={busy || !dirty} onClick={save}>
          <Save className="size-4" /> {busy ? "Сохранение…" : dirty ? "Закрепить прайс" : "Изменений нет"}
        </Button>
      </div>
    </AppShell>
  );
}

export default function ClientPricesPage(props: { params: Promise<{ id: string }> }) {
  return (
    <RequirePerm perm="clients.set_price" title="Прайс-лист">
      <ClientPricesPageInner {...props} />
    </RequirePerm>
  );
}
