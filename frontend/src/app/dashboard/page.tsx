"use client";
import Link from "next/link";
import { Truck } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { CameraWall } from "@/components/camera-wall";
import { SurveillancePanels } from "@/components/surveillance-panels";
import { StatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useApi } from "@/lib/use-api";
import { formatPlate } from "@/components/ui/license-plate-input";
import type { Order } from "@/lib/types";

function ShippingQueue() {
  const { data: orders } = useApi<Order[]>("/orders/");
  const queue = (orders ?? []).filter((o) => ["arrived", "loading", "loaded"].includes(o.status));
  return (
    <Card>
      <CardHeader className="flex-row items-center gap-2">
        <Truck className="size-4 text-[var(--muted-foreground)]" />
        <CardTitle>Очередь отгрузки</CardTitle>
        <span className="ml-auto text-sm text-[var(--muted-foreground)]">{queue.length} в работе</span>
      </CardHeader>
      <CardContent>
        {queue.length === 0 ? (
          <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Нет машин в работе.</p>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {queue.map((o) => (
              <Link key={o.id} href={`/orders/${o.id}`}
                className="group flex items-center gap-3 rounded-lg border bg-[var(--card)] p-3 transition-colors hover:border-[var(--ring)]/40">
                <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-[var(--secondary)]">
                  <Truck className="size-5 text-[var(--muted-foreground)]" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-semibold tabular-nums">
                    {o.truck_number ? formatPlate(o.truck_number) : `Заказ #${o.id}`}
                  </div>
                  <div className="truncate text-xs text-[var(--muted-foreground)]">
                    #{o.id} · {o.client_name || "—"}
                  </div>
                </div>
                <StatusBadge status={o.status} dot />
              </Link>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  return (
    <AppShell title="Командный центр">
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_340px]">
        {/* Камеры + очередь отгрузки — основной фокус */}
        <div className="flex min-w-0 flex-col gap-4">
          <CameraWall />
          <ShippingQueue />
        </div>
        {/* Боковая панель: аналитика + сводка */}
        <SurveillancePanels />
      </div>
    </AppShell>
  );
}
