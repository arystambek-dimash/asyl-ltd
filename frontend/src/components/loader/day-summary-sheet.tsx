"use client";
import { useState } from "react";
import { DataGate } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { loaderUrl, type LoaderDaySummary } from "@/lib/loader";
import { LOADER_TRANSPORTS, type LoaderTransport } from "@/lib/loader-groups";
import { useApi } from "@/lib/use-api";
import { todayLocalIsoDate } from "@/lib/utils";
import { BagsHeadline, LoaderItemList } from "./loader-order-card";

/** «Аналитика дня» открытой вкладки: всего отгружено за день и мешки каждой муки. Считает сервер. */
export function DaySummarySheet({ transport, onClose }: { transport: LoaderTransport; onClose: () => void }) {
  const [day, setDay] = useState(todayLocalIsoDate);
  const { data, loading, error, reload } = useApi<LoaderDaySummary>(loaderUrl("day-summary", { transport, day }));
  return (
    <Modal
      open
      onClose={onClose}
      variant="sheet"
      eyebrow={LOADER_TRANSPORTS.find((tab) => tab.key === transport)?.label}
      title="Аналитика дня"
    >
      <div className="flex flex-col gap-4">
        <Input
          type="date"
          aria-label="День"
          value={day}
          onChange={(event) => event.target.value && setDay(event.target.value)}
          className="h-11 w-auto self-start text-base"
        />
        {data ? (
          <section aria-label="Отгружено" className="flex flex-col gap-4">
            <div>
              <div className="mb-2 text-sm font-semibold text-[var(--muted-foreground)]">Всего отгружено</div>
              <BagsHeadline load={{ ...data, transport_type: transport }} />
            </div>
            {data.products.length > 0 && (
              <LoaderItemList items={data.products} className="border-t-2 border-[var(--loader-border)]/50! pt-4" />
            )}
          </section>
        ) : (
          <DataGate loading={loading} error={error} onRetry={reload} />
        )}
      </div>
    </Modal>
  );
}
