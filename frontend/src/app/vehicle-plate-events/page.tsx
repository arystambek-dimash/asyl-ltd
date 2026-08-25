"use client";

import { useMemo, useState } from "react";
import { CarFront, Radio, Search, X } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PlateBadge, formatPlate } from "@/components/ui/license-plate-input";
import { LoadMore } from "@/components/ui/load-more";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useDebounced } from "@/lib/use-debounced";
import { usePagedApi } from "@/lib/use-paged-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { formatDateTime } from "@/lib/utils";

type NumericValue = number | string;

interface VehiclePlateEvent {
  id: number;
  event_id: string;
  vehicle_number: string;
  camera: string;
  source: "main" | "sub";
  detected_at: string;
  stationary_seconds: NumericValue;
  confirmation_votes: number;
  detector_confidence: NumericValue;
  ocr_confidence: NumericValue;
  processing_status: string;
}

type StatusTone = "muted" | "primary" | "success" | "warning" | "destructive" | "outline";

const PAGE_SIZE = 100;
const POLL_INTERVAL_MS = 10_000;

const STATUS_META: Record<string, { label: string; tone: StatusTone }> = {
  received: { label: "Получено", tone: "primary" },
  pending: { label: "Ожидает", tone: "warning" },
  processed: { label: "Обработано", tone: "success" },
  matched: { label: "Сопоставлено", tone: "success" },
  ignored: { label: "Пропущено", tone: "muted" },
  failed: { label: "Ошибка", tone: "destructive" },
  error: { label: "Ошибка", tone: "destructive" },
};

function finiteNumber(value: NumericValue): number | null {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatSeconds(value: NumericValue): string {
  const seconds = finiteNumber(value);
  return seconds === null ? "—" : `${seconds.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} с`;
}

function formatConfidence(value: NumericValue): string {
  const confidence = finiteNumber(value);
  if (confidence === null) return "—";
  const percent = confidence <= 1 ? confidence * 100 : confidence;
  return `${Math.round(percent)}%`;
}

function statusMeta(status: string) {
  return STATUS_META[status.toLowerCase()] ?? { label: status || "Неизвестно", tone: "outline" as const };
}

function normalizedPlateFilter(value: string): string {
  return value.toUpperCase().replace(/[^0-9A-Z]/g, "");
}

function VehiclePlateEventsPageInner() {
  const [vehicleNumber, setVehicleNumber] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [camera, setCamera] = useState("");
  const debouncedVehicleNumber = useDebounced(vehicleNumber);
  const debouncedCamera = useDebounced(camera);

  const url = useMemo(() => {
    const query = new URLSearchParams();
    const plate = normalizedPlateFilter(debouncedVehicleNumber);
    if (plate) query.set("vehicle_number", plate);
    if (dateFrom) query.set("date_from", dateFrom);
    if (dateTo) query.set("date_to", dateTo);
    if (debouncedCamera.trim()) query.set("camera", debouncedCamera.trim().toLowerCase());
    const search = query.toString();
    return `/vehicle-plate-events${search ? `?${search}` : ""}`;
  }, [dateFrom, dateTo, debouncedCamera, debouncedVehicleNumber]);

  const {
    items: events,
    count,
    hasMore,
    loading,
    loadingMore,
    error,
    reload,
    loadMore,
  } = usePagedApi<VehiclePlateEvent>(url, PAGE_SIZE);

  useVisiblePolling(reload, POLL_INTERVAL_MS);

  const hasFilters = Boolean(vehicleNumber || dateFrom || dateTo || camera);

  function resetFilters() {
    setVehicleNumber("");
    setDateFrom("");
    setDateTo("");
    setCamera("");
  }

  return (
    <AppShell
      title="Журнал машин"
      section="Управление"
      description="Подтверждённые автомобильные номера с проходной. Фото и видео не передаются и не сохраняются."
    >
      <Card className="mb-4">
        <CardContent className="pt-6">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="vehicle-number-search">Номер машины</Label>
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
                <Input
                  id="vehicle-number-search"
                  className="pl-8 uppercase"
                  autoComplete="off"
                  placeholder="123 ABC 02"
                  value={vehicleNumber}
                  onChange={(event) => setVehicleNumber(event.target.value.toUpperCase())}
                />
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="vehicle-date-from">Дата с</Label>
              <Input
                id="vehicle-date-from"
                type="date"
                max={dateTo || undefined}
                value={dateFrom}
                onChange={(event) => setDateFrom(event.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="vehicle-date-to">Дата по</Label>
              <Input
                id="vehicle-date-to"
                type="date"
                min={dateFrom || undefined}
                value={dateTo}
                onChange={(event) => setDateTo(event.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="vehicle-camera">Камера</Label>
              <Input
                id="vehicle-camera"
                autoComplete="off"
                placeholder="cam1"
                value={camera}
                onChange={(event) => setCamera(event.target.value)}
              />
            </div>
          </div>
          {hasFilters && (
            <div className="mt-3 flex justify-end">
              <Button variant="ghost" size="sm" onClick={resetFilters}>
                <X className="size-4" /> Сбросить фильтры
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-[var(--muted-foreground)]" aria-live="polite">
              {loading && events.length === 0 ? "Загрузка…" : `Найдено: ${count}`}
            </p>
            <span className="inline-flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
              <Radio className="size-3.5 text-[var(--success)]" /> Обновляется автоматически
            </span>
          </div>

          {loading && events.length === 0 ? (
            <div className="py-12 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</div>
          ) : error && events.length === 0 ? (
            <ErrorAlert message={error} onRetry={reload} />
          ) : events.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-14 text-center">
              <span className="flex size-11 items-center justify-center rounded-full bg-[var(--muted)]">
                <CarFront className="size-5 text-[var(--muted-foreground)]" />
              </span>
              <p className="font-medium">{hasFilters ? "Машины по фильтрам не найдены" : "Событий пока нет"}</p>
              <p className="text-sm text-[var(--muted-foreground)]">
                Новые распознанные номера появятся здесь автоматически.
              </p>
            </div>
          ) : (
            <>
              <Table>
                <THead>
                  <TR>
                    <TH>Номер машины</TH>
                    <TH>Дата и время</TH>
                    <TH>Камера</TH>
                    <TH className="text-right">Стоянка</TH>
                    <TH className="text-right">OCR</TH>
                    <TH>Статус</TH>
                  </TR>
                </THead>
                <TBody>
                  {events.map((event) => {
                    const status = statusMeta(event.processing_status);
                    return (
                      <TR key={event.id} title={`event_id: ${event.event_id}`}>
                        <TD aria-label={`Номер машины ${formatPlate(event.vehicle_number)}`}>
                          <PlateBadge value={event.vehicle_number} />
                        </TD>
                        <TD>
                          <time dateTime={event.detected_at} className="whitespace-nowrap tabular-nums">
                            {formatDateTime(event.detected_at)}
                          </time>
                        </TD>
                        <TD>
                          <span className="font-medium">{event.camera}</span>
                          <span className="ml-1.5 text-xs text-[var(--muted-foreground)]">{event.source}</span>
                        </TD>
                        <TD className="text-right tabular-nums">{formatSeconds(event.stationary_seconds)}</TD>
                        <TD className="text-right font-medium tabular-nums">
                          {formatConfidence(event.ocr_confidence)}
                        </TD>
                        <TD>
                          <Badge tone={status.tone} dot>
                            {status.label}
                          </Badge>
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
              {error && <ErrorAlert message={error} onRetry={reload} />}
              <LoadMore
                shown={events.length}
                total={count}
                hasMore={hasMore}
                loading={loadingMore}
                onClick={loadMore}
              />
            </>
          )}
        </CardContent>
      </Card>
    </AppShell>
  );
}

export default function VehiclePlateEventsPage() {
  return (
    <RequirePerm perm="events.view" title="Журнал машин">
      <VehiclePlateEventsPageInner />
    </RequirePerm>
  );
}
