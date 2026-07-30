"use client";
import { useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { buttonVariants } from "@/components/ui/button";
import { DataGate } from "@/components/ui/data-state";
import { useApi } from "@/lib/use-api";
import { formatCurrency, currencySymbol } from "@/lib/utils";
import { Boxes, ShoppingCart, Tag } from "lucide-react";

interface PortalProduct {
  id: number;
  label: string;
  weight_kg: string;
  available_bags: number;
  price: string | null;
  currency: "KZT" | "USD";
}

export default function PortalCatalogPage() {
  const [currency, setCurrency] = useState<"KZT" | "USD" | null>(null);
  const {
    data: products,
    loading,
    error,
    reload,
  } = useApi<PortalProduct[]>(currency ? `/portal/catalog/?currency=${currency}` : "/portal/catalog/");
  const selectedCurrency = currency ?? products?.[0]?.currency ?? "KZT";
  return (
    <AppShell title="Товары" portal>
      <div className="mb-5 flex flex-col items-stretch justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <p className="font-semibold">Доступные позиции</p>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            Актуальные цены, фасовка и остатки для оформления заказа.
          </p>
        </div>
        <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
          <div
            className="inline-flex w-full rounded-xl border border-[var(--border)] bg-[var(--muted)]/60 p-1 sm:w-auto"
            role="group"
            aria-label="Валюта каталога"
          >
            {(["KZT", "USD"] as const).map((code) => (
              <button
                key={code}
                type="button"
                onClick={() => setCurrency(code)}
                aria-pressed={selectedCurrency === code}
                className={
                  "min-h-10 flex-1 rounded-lg px-3 py-2 text-xs font-semibold transition-[background-color,color,box-shadow] sm:flex-none " +
                  (selectedCurrency === code
                    ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm"
                    : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]")
                }
              >
                {code} {currencySymbol(code)}
              </button>
            ))}
          </div>
          <Link
            href="/portal/orders/new"
            className={buttonVariants({ size: "sm", className: "w-full justify-center sm:w-auto" })}
          >
            <ShoppingCart className="size-4" /> Оформить заказ
          </Link>
        </div>
      </div>
      {!products ? (
        <DataGate loading={loading} error={error} onRetry={reload} />
      ) : products.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-2 py-14 text-center">
            <Boxes className="size-8 text-[var(--muted-foreground)]" />
            <div className="text-sm font-medium">Товаров пока нет</div>
            <p className="max-w-sm text-xs text-[var(--muted-foreground)]">
              Как только менеджер добавит активные товары, они появятся здесь для заказа.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {products.map((p) => (
            <Card
              key={p.id}
              className="p-5 transition-[border-color,box-shadow,transform] hover:-translate-y-0.5 hover:border-[var(--ring)]/25 hover:shadow-card sm:p-6"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 break-words font-semibold leading-5">{p.label}</div>
                <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-[var(--soft-blue)] text-[var(--ring)]">
                  <Tag aria-hidden="true" className="size-4" />
                </span>
              </div>
              <div className="mt-1 text-xs text-[var(--muted-foreground)]">{p.weight_kg} кг / мешок</div>
              <div className="mt-5 border-t border-[var(--border)] pt-4">
                <div className="text-[11px] font-medium uppercase tracking-[0.06em] text-[var(--muted-foreground)]">
                  Цена за мешок
                </div>
                {p.price ? (
                  <div className="mt-1.5 break-words text-xl font-bold tracking-[-0.02em] tabular-nums">
                    {formatCurrency(p.price, p.currency)}
                  </div>
                ) : (
                  <div className="mt-1 text-sm font-medium text-[var(--muted-foreground)]">Цена уточняется</div>
                )}
              </div>
              <div
                className={
                  p.available_bags > 0
                    ? "mt-4 flex items-center gap-2 rounded-xl bg-[var(--soft-green)] px-3 py-2.5 text-xs font-semibold text-[var(--success)]"
                    : "mt-4 flex items-center gap-2 rounded-xl bg-[var(--muted)]/55 px-3 py-2.5 text-xs font-medium text-[var(--muted-foreground)]"
                }
              >
                <span
                  aria-hidden="true"
                  className={
                    p.available_bags > 0
                      ? "size-1.5 rounded-full bg-[var(--success)]"
                      : "size-1.5 rounded-full bg-current"
                  }
                />
                {p.available_bags > 0 ? `В наличии: ${p.available_bags} меш.` : "Остаток уточнит оператор"}
              </div>
            </Card>
          ))}
        </div>
      )}
    </AppShell>
  );
}
