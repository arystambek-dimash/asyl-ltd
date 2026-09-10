"use client";
import { use, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Camera, Check, Scale, TrainFront, Trash2, Truck, Warehouse } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { LiveScaleStatus } from "@/components/grain/live-scale-status";
import { RequirePerm } from "@/components/require-perm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { GrainWagonDeleteDialog } from "@/components/grain/wagon-delete-dialog";
import { PassageNumberEditor } from "@/components/grain/passage-number-editor";
import { WagonPhotos } from "@/components/grain/wagon-photos";
import { HistoricalTareDialog } from "@/components/grain/historical-tare-dialog";
import { ExitWeightCorrectionDialog } from "@/components/grain/exit-weight-correction-dialog";
import { UnassignedWeighingsPanel } from "@/components/grain/unassigned-weighings";
import { can } from "@/lib/can";
import {
  GRAIN_STATUS_TONE,
  formatKg,
  grainTripHref,
  grainWorkspaceHref,
  isGrainWagonDeleteSupported,
} from "@/lib/grain";
import { PassageStageAction } from "./passage-stage-action";
import { IntakeStageAction } from "./intake-stage-action";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { useApi } from "@/lib/use-api";
import { cn, formatDateTime } from "@/lib/utils";
import { useAuth } from "@/store/auth";
import type { GrainTimelineEvent, GrainWagon } from "@/lib/types";

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-[var(--border)]/60 py-2 text-sm last:border-0">
      <span className="text-[var(--muted-foreground)]">{label}</span>
      <span className="text-right font-medium">{children}</span>
    </div>
  );
}

function SimpleFlowProgress({ wagon }: { wagon: GrainWagon }) {
  if (wagon.workflow !== "simple") return null;
  if (["cancelled", "return_to_supplier", "blocked"].includes(wagon.status)) return null;
  const statusIndex =
    wagon.status === "expected"
      ? 0
      : wagon.status === "arrived"
        ? 1
        : wagon.status === "at_silo" || wagon.status === "weight_discrepancy"
          ? 2
          : 3;
  const steps =
    wagon.direction === "passage"
      ? [
          { label: "Заезд", icon: Camera },
          { label: "Вес пустой", icon: Scale },
          { label: "Погрузка", icon: Warehouse },
          { label: "Вес гружёной и вывоз", icon: Truck },
        ]
      : [
          { label: "Номер камеры", icon: Camera },
          { label: "Входной вес", icon: Scale },
          { label: "Назначенный силос", icon: Warehouse },
          { label: "Выходной вес и нетто", icon: TrainFront },
        ];
  return (
    <Card className="overflow-hidden border-slate-200">
      <div className="grid grid-cols-2 gap-px bg-slate-200 lg:grid-cols-4">
        {steps.map((step, index) => {
          const Icon = step.icon;
          const done = index < statusIndex || wagon.status === "completed";
          const active = index === statusIndex && wagon.status !== "completed";
          return (
            <div key={step.label} className={cn("flex items-center gap-3 bg-white p-4", active && "bg-amber-50")}>
              <span
                className={cn(
                  "flex size-9 shrink-0 items-center justify-center rounded-xl",
                  done
                    ? "bg-emerald-100 text-emerald-700"
                    : active
                      ? "bg-amber-500 text-white"
                      : "bg-slate-100 text-slate-400",
                )}
              >
                {done ? <Check className="size-4" /> : <Icon className="size-4" />}
              </span>
              <div>
                <p className="text-[10px] font-bold uppercase tracking-wide text-slate-400">Этап {index + 1}</p>
                <p className="mt-0.5 text-xs font-bold text-slate-800">{step.label}</p>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function recognitionStatusLabel(wagon: GrainWagon) {
  const capture = wagon.vehicle_recognition_captures?.[0];
  if (!capture) return "ещё не запускалось";
  if (capture.status === "completed") {
    return `подтверждено · ${capture.vehicle_number || "номер сохранён"}`;
  }
  if (capture.status === "failed") {
    const detail = capture.error_detail || capture.error_code;
    const boundedDetail = detail.length > 140 ? `${detail.slice(0, 139)}…` : detail;
    return `ошибка · ${boundedDetail}`;
  }
  if (capture.stage === "applying") return "номер получен · сохраняется";
  if (capture.stage === "recognizing") return "камера распознаёт номер";
  return "ожидает запуска камеры";
}

function TripPageInner({ params, direction }: TripPageProps) {
  const { id } = use(params);
  const router = useRouter();
  const { me } = useAuth();
  const apiBase = direction === "passage" ? "/grain/passages" : "/grain/wagons";
  const { data: wagon, loading, error, reload, setData } = useApi<GrainWagon>(`${apiBase}/${id}/`);
  const {
    data: timeline,
    loading: timelineLoading,
    error: timelineError,
    reload: reloadTimeline,
  } = useApi<GrainTimelineEvent[]>(`${apiBase}/${id}/timeline/`);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const [mutationBusy, setMutationBusy] = useState(false);
  const wrongRoute = Boolean(wagon && wagon.direction !== direction);
  useEffect(() => {
    if (wagon && wagon.direction !== direction) router.replace(grainTripHref(wagon));
  }, [direction, router, wagon]);
  useVisiblePolling(
    async () => {
      await Promise.all([reload(), reloadTimeline()]);
    },
    5000,
    !mutationBusy && !deleteOpen && !wrongRoute,
  );

  if (!wagon || wrongRoute) {
    return (
      <AppShell title="Приход и вывоз" section="Работа">
        <DataGate loading={loading || wrongRoute} error={error} onRetry={reload} />
      </AppShell>
    );
  }

  function refresh() {
    void reload();
    void reloadTimeline();
  }

  function handleBusyChange(busy: boolean) {
    // Invalidate a read already in flight before the command. Otherwise its
    // old stage could replace this action while the scale request is running.
    if (busy) setData(wagon);
    setMutationBusy(busy);
  }

  const passage = wagon.direction === "passage";
  const backHref = grainWorkspaceHref(wagon.direction);
  const StageAction = passage ? PassageStageAction : IntakeStageAction;
  const canDelete = can(me, "grain.delete") && isGrainWagonDeleteSupported(wagon.status);

  return (
    <AppShell
      title="Приход и вывоз"
      section="Работа"
      actions={
        wagon.direction === "passage" && can(me, "grain.weigh") ? (
          <LiveScaleStatus active scaleKey="truck" label="Вывоз" />
        ) : undefined
      }
    >
      {error && <ErrorAlert message={error} onRetry={reload} />}
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <Link
          href={backHref}
          aria-label={passage ? "К вывозам" : "К приходам"}
          className="flex size-9 shrink-0 items-center justify-center rounded-lg border text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/60"
        >
          <ArrowLeft className="size-4" />
        </Link>
        <h2 className="text-xl font-semibold tracking-tight">
          {wagon.direction === "passage" ? "Машина" : "Вагон"} {wagon.number || `#${wagon.id}`}
        </h2>
        <Badge tone={GRAIN_STATUS_TONE[wagon.status] ?? "muted"} dot>
          {wagon.status_label}
        </Badge>
        {wagon.unplanned && <Badge tone="warning">внеплановый</Badge>}
        <PassageNumberEditor
          key={`${wagon.id}:${wagon.number}`}
          wagon={wagon}
          canEdit={can(me, "grain.arrive")}
          onChanged={refresh}
          onBusyChange={handleBusyChange}
        />
        {canDelete && (
          <Button
            className="ml-auto"
            size="sm"
            variant="destructive"
            onClick={() => {
              setDeleteOpen(true);
            }}
          >
            <Trash2 /> Удалить рейс
          </Button>
        )}
      </div>

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex min-w-0 flex-col gap-4">
          <SimpleFlowProgress wagon={wagon} />
          {/* Ключевые числа одной полосой. */}
          <Card className="flex flex-wrap items-center gap-x-10 gap-y-3 p-4">
            {[
              {
                label: passage
                  ? wagon.weighings?.some((row) => row.source === "historical")
                    ? "Сохранённая тара"
                    : "Вес пустой · въезд"
                  : "Брутто",
                value: formatKg(wagon.entry_weight_kg),
              },
              { label: passage ? "Вес гружёной · выезд" : "Тара", value: formatKg(wagon.exit_weight_kg) },
              { label: passage ? "Вывезено · нетто" : "Нетто", value: formatKg(wagon.net_weight_kg), strong: true },
              ...(!passage
                ? [{ label: "Ожидаемый вес", value: formatKg(wagon.document_weight_kg ?? wagon.expected_weight_kg) }]
                : []),
            ].map((item) => (
              <div key={item.label} className="min-w-0">
                <div className="text-xs text-[var(--muted-foreground)]">{item.label}</div>
                <div
                  className={cn(
                    "mt-1 text-lg font-semibold leading-none tabular-nums",
                    item.strong && "text-[var(--success)]",
                  )}
                >
                  {item.value}
                </div>
              </div>
            ))}
          </Card>

          {!error && (
            <ExitWeightCorrectionDialog
              wagon={wagon}
              onChanged={refresh}
              onBusyChange={handleBusyChange}
              disabled={mutationBusy || deleteOpen}
            />
          )}

          {!error && (
            <fieldset disabled={mutationBusy} className="min-w-0">
              <StageAction
                key={`${wagon.id}:${wagon.status}:${wagon.gross_weight_kg}:${wagon.tare_weight_kg}`}
                wagon={wagon}
                onChanged={refresh}
                onBusyChange={handleBusyChange}
              />
            </fieldset>
          )}

          {passage && wagon.status === "at_silo" && wagon.exit_weight_kg == null && (
            <UnassignedWeighingsPanel
              key={wagon.id}
              exitWagon={wagon}
              canWeigh={can(me, "grain.weigh") && !error && !mutationBusy}
              active={!mutationBusy && !deleteOpen}
              onChanged={refresh}
              onBusyChange={handleBusyChange}
            />
          )}

          <WagonPhotos wagon={wagon} />
          {passage &&
            wagon.status === "at_silo" &&
            can(me, "grain.weigh") &&
            !error &&
            wagon.weighings?.length === 1 &&
            wagon.weighings[0].orientation === "rear" && (
              <HistoricalTareDialog wagon={wagon} onChanged={refresh} onBusyChange={handleBusyChange} />
            )}

          <Card>
            <CardHeader className="p-4 pb-2">
              <CardTitle>Реквизиты</CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              {passage ? (
                <InfoRow label="Груз на вывоз">{wagon.cargo_name}</InfoRow>
              ) : (
                <>
                  <InfoRow label="Поставщик">{wagon.supplier || "—"}</InfoRow>
                  <InfoRow label="Тип зерна">{wagon.grain_type_name || wagon.culture || "—"}</InfoRow>
                </>
              )}
              <InfoRow label="Источник номера">
                {wagon.number_source === "camera"
                  ? `Камера ${wagon.number_camera_source || "проходной"}`
                  : "Ручной ввод"}
              </InfoRow>
              {wagon.direction === "passage" && Boolean(wagon.vehicle_recognition_captures?.length) && (
                <InfoRow label="Распознавание номера">{recognitionStatusLabel(wagon)}</InfoRow>
              )}
              <InfoRow label="Прибыл">{wagon.arrived_at ? formatDateTime(wagon.arrived_at) : "—"}</InfoRow>
              {!passage && (
                <>
                  <InfoRow label="Силос">{wagon.assigned_silo_name ?? "не назначен"}</InfoRow>
                  <InfoRow label="Точка разгрузки">{wagon.unloading_point || "—"}</InfoRow>
                </>
              )}
              {wagon.note && <InfoRow label="Примечание">{wagon.note}</InfoRow>}
              {wagon.exited_at && <InfoRow label="Выехал">{formatDateTime(wagon.exited_at)}</InfoRow>}
            </CardContent>
          </Card>

          {!passage && (wagon.allocations ?? []).length > 0 && (
            <Card>
              <CardHeader className="p-4 pb-2">
                <CardTitle>Распределение по силосам</CardTitle>
              </CardHeader>
              <CardContent className="p-4 pt-0">
                {(wagon.allocations ?? []).map((allocation) => (
                  <InfoRow key={allocation.id} label={allocation.silo_name}>
                    {formatKg(allocation.amount_kg)}
                  </InfoRow>
                ))}
              </CardContent>
            </Card>
          )}
        </div>

        <aside className="flex flex-col gap-4 self-start">
          <Card>
            <CardHeader className="p-4 pb-3">
              <CardTitle>{passage ? "История рейса" : "История вагона"}</CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <div className="relative space-y-3 before:absolute before:bottom-2 before:left-[5px] before:top-2 before:w-px before:bg-[var(--border)]">
                {timelineError ? (
                  <ErrorAlert message={timelineError} onRetry={reloadTimeline} />
                ) : timelineLoading && !timeline ? (
                  <p>Загрузка истории…</p>
                ) : (timeline ?? []).length === 0 ? (
                  <p className="text-sm text-[var(--muted-foreground)]">Событий пока нет.</p>
                ) : (
                  (timeline ?? []).map((event, index) => (
                    <div key={event.id} className="relative flex gap-3 text-xs">
                      <span
                        className={cn(
                          "relative z-10 mt-1 size-2.5 shrink-0 rounded-full ring-4 ring-[var(--card)]",
                          index === (timeline ?? []).length - 1
                            ? "bg-[var(--success)]"
                            : "bg-[var(--muted-foreground)]/45",
                        )}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="font-medium">{event.message}</div>
                        <div className="mt-0.5 text-[10px] text-[var(--muted-foreground)]">
                          {formatDateTime(event.created_at)}
                          {event.user_name ? ` · ${event.user_name}` : ""}
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </CardContent>
          </Card>

          {(wagon.weighings ?? []).length > 0 && (
            <Card>
              <CardHeader className="p-4 pb-3">
                <CardTitle>Взвешивания</CardTitle>
              </CardHeader>
              <CardContent className="p-4 pt-0 text-sm">
                {(wagon.weighings ?? []).map((row) => (
                  <InfoRow
                    key={row.id}
                    label={
                      row.source === "historical"
                        ? "Сохранённая тара"
                        : wagon.direction === "passage"
                          ? row.kind === "gross"
                            ? "Въезд"
                            : "Выезд"
                          : row.kind === "gross"
                            ? "Брутто"
                            : "Тара"
                    }
                  >
                    <span className="tabular-nums">
                      {row.previous_weight_kg != null && (
                        <span className="font-normal text-[var(--muted-foreground)]">
                          {formatKg(row.previous_weight_kg)} →{" "}
                        </span>
                      )}
                      {formatKg(row.weight_kg)}
                    </span>
                    <span className="block text-[10px] font-normal text-[var(--muted-foreground)]">
                      {formatDateTime(row.created_at)}
                      {row.source === "manual" ? " · ручной ввод" : ""}
                      {row.reference_record_at
                        ? ` · исходное взвешивание ${formatDateTime(row.reference_record_at)}`
                        : ""}
                      {row.operator_name ? ` · ${row.operator_name}` : ""}
                      {row.manual_reason ? ` · ${row.manual_reason}` : ""}
                      {row.photo_url ? " · есть фото" : ""}
                    </span>
                  </InfoRow>
                ))}
              </CardContent>
            </Card>
          )}
        </aside>
      </div>

      <GrainWagonDeleteDialog
        wagon={wagon}
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onDeleted={() => router.replace(backHref)}
      />
    </AppShell>
  );
}

type TripPageProps = { params: Promise<{ id: string }>; direction: GrainWagon["direction"] };

export function GrainTripDetail(props: TripPageProps) {
  return (
    <RequirePerm perm="grain.view" title="Приход и вывоз">
      <TripPageInner {...props} />
    </RequirePerm>
  );
}
