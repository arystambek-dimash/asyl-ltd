"use client";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useApi } from "@/lib/use-api";
import { formatCurrency } from "@/lib/utils";
import { Boxes, ShoppingCart, Tag } from "lucide-react";

interface PortalProduct {
  id: number; label: string; weight_kg: string; available_bags: number;
  price: string | null; currency: "KZT" | "USD";
}

export default function PortalCatalogPage() {
  const { data: products, loading } = useApi<PortalProduct[]>("/portal/catalog/");
  return (
    <AppShell title="Товары" portal>
      <div className="mb-4 flex justify-between">
        <div>
          <p className="font-medium">Ваш личный прайс-лист</p>
          <p className="text-xs text-[var(--muted-foreground)]">Цены закреплены специально для вашей компании</p>
        </div>
        <Link href="/portal/orders/new">
          <Button size="sm"><ShoppingCart className="size-4" /> Оформить заказ</Button>
        </Link>
      </div>
      {loading ? (
        <p className="text-sm text-[var(--muted-foreground)]">Загрузка…</p>
      ) : (
        (products ?? []).length === 0 ? (
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
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {(products ?? []).map((p) => (
              <Card key={p.id} className="p-6">
                <div className="flex items-start justify-between gap-3">
                  <div className="font-medium">{p.label}</div>
                  <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-[var(--primary)]/8 text-[var(--primary)]">
                    <Tag className="size-4" />
                  </span>
                </div>
                <div className="mt-1 text-xs text-[var(--muted-foreground)]">{p.weight_kg} кг / мешок</div>
                <div className="mt-4 border-t pt-4">
                  <div className="text-[11px] text-[var(--muted-foreground)]">Ваша цена за мешок</div>
                  {p.price ? (
                    <div className="mt-1 text-xl font-semibold tabular-nums">
                      {formatCurrency(p.price, p.currency)}
                    </div>
                  ) : (
                    <div className="mt-1 text-sm font-medium text-[var(--muted-foreground)]">Цена уточняется</div>
                  )}
                </div>
                <div className={p.available_bags > 0
                  ? "mt-3 text-xs font-medium text-[var(--success)]"
                  : "mt-3 text-xs font-medium text-[var(--muted-foreground)]"}>
                  {p.available_bags > 0
                    ? `В наличии: ${p.available_bags} меш.`
                    : "Остаток уточнит оператор"}
                </div>
              </Card>
            ))}
          </div>
        )
      )}
    </AppShell>
  );
}
