"use client";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useApi } from "@/lib/use-api";
import { formatMoney } from "@/lib/utils";
import { ShoppingCart } from "lucide-react";

interface PortalProduct { id: number; label: string; price: string; weight_kg: string; }

export default function PortalCatalogPage() {
  const { data: products, loading } = useApi<PortalProduct[]>("/portal/catalog/");
  return (
    <AppShell title="Каталог" portal>
      <div className="mb-4 flex justify-between">
        <p className="text-sm text-[var(--muted-foreground)]">Доступные товары</p>
        <Link href="/portal/orders/new">
          <Button size="sm"><ShoppingCart className="size-4" /> Оформить заказ</Button>
        </Link>
      </div>
      {loading ? (
        <p className="text-sm text-[var(--muted-foreground)]">Загрузка…</p>
      ) : (
        <div className="grid grid-cols-3 gap-4">
          {(products ?? []).map((p) => (
            <Card key={p.id} className="p-6">
              <div className="font-medium">{p.label}</div>
              <div className="mt-1 text-xs text-[var(--muted-foreground)]">{p.weight_kg} кг / мешок</div>
              <div className="mt-3 text-2xl font-bold tabular-nums">{formatMoney(p.price)} ₸</div>
            </Card>
          ))}
        </div>
      )}
    </AppShell>
  );
}
