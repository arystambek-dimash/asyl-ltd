"use client";
import { use, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Camera, Check, LoaderCircle, Scale, TrainFront, Trash2, Warehouse } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { LiveScaleStatus } from "@/components/grain/live-scale-status";
import { RequirePerm } from "@/components/require-perm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataGate } from "@/components/ui/data-state";
import { GrainWagonDeleteDialog } from "@/components/grain/wagon-delete-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { GRAIN_STATUS_TONE, formatKg, isGrainWagonDeleteSupported } from "@/lib/grain";
import { useApi } from "@/lib/use-api";
import { cn, formatDateTime } from "@/lib/utils";
import { useAuth } from "@/store/auth";
import type { GrainSilo, GrainSupply, GrainTimelineEvent, GrainWagon } from "@/lib/types";

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-[var(--border)]/60 py-2 text-sm last:border-0">
      <span className="text-[var(--muted-foreground)]">{label}</span>
      <span className="text-right font-medium">{children}</span>
    </div>
  );
}

function ScaleCaptureButton({ busy, label, onClick }: { busy: boolean; label: string; onClick: () => void }) {
  return (
    <Button
      className="h-auto min-h-10 whitespace-normal py-2.5 text-center"
      disabled={busy}
      aria-busy={busy}
      onClick={onClick}
    >
      {busy ? <LoaderCircle className="animate-spin" /> : <Scale />}
      {busy ? "Получаю вес с весов…" : label}
    </Button>
  );
}

function WagonScalePending() {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
      <TrainFront className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div>
        <p className="font-semibold">Вагонные весы пока не подключены</p>
        <p className="mt-1 text-amber-900/80">
          Получение веса для прихода станет доступно после подключения отдельного оборудования. Весы машин вывоза здесь
          не используются.
        </p>
      </div>
    </div>
  );
}

/** Крупное действие текущего этапа — оператору не нужно искать кнопки. */
function StageAction({ wagon, onChanged }: { wagon: GrainWagon; onChanged: () => void }) {
  const { me } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const [decision, setDecision] = useState("accepted");
  const [moisture, setMoisture] = useState("");
  const [impurity, setImpurity] = useState("");
  const [labNote, setLabNote] = useState("");
  const [siloId, setSiloId] = useState("");
  const [supplyId, setSupplyId] = useState("");

  const needSilos = ["unloading_allowed", "quarantine", "insufficient_capacity"].includes(wagon.status);
  const { data: silos } = useApi<GrainSilo[]>(needSilos ? `/grain/wagons/${wagon.id}/suggest-silos/` : null);
  const { data: supplies } = useApi<GrainSupply[]>(
    wagon.status === "waiting_for_approval" ? "/grain/supplies/?status=expected" : null,
  );

  const selectedSiloId = siloId || (silos?.[0] ? String(silos[0].id) : "");

  async function act(path: string, body: Record<string, unknown> = {}) {
    setBusy(true);
    setError("");
    try {
      await api.post(`/grain/wagons/${wagon.id}/${path}/`, body);
      onChanged();
    } catch (e) {
      setError(apiError(e));
      // A mutation can commit even when its HTTP response is lost. Reloading
      // prevents an operator from repeating an already completed weighing.
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  let body: React.ReactNode = null;
  const passage = wagon.direction === "passage";
  if (wagon.workflow === "simple" && wagon.status === "arrived" && can(me, "grain.weigh")) {
    body = passage ? (
      <>
        <div className="rounded-xl border border-sky-100 bg-sky-50 p-3 text-sm text-sky-950">
          Поставьте пустую машину на весы перед погрузкой «{wagon.cargo_name || "груза"}». Система сама получит текущий
          вес.
        </div>
        <ScaleCaptureButton
          busy={busy}
          label="Получить вес пустой и отправить на погрузку"
          onClick={() => void act("entry-weight")}
        />
      </>
    ) : (
      <WagonScalePending />
    );
  } else if (wagon.workflow === "simple" && wagon.status === "at_silo" && can(me, "grain.weigh")) {
    body = passage ? (
      <>
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
          После погрузки «{wagon.cargo_name || "груза"}» поставьте машину на весы. Система получит вес, рассчитает
          вывезенное нетто и завершит рейс.
        </div>
        <ScaleCaptureButton
          busy={busy}
          label="Получить вес гружёной и завершить вывоз"
          onClick={() => void act("exit-weight")}
        />
      </>
    ) : (
      <WagonScalePending />
    );
  } else if (wagon.workflow === "simple" && wagon.status === "weight_discrepancy" && can(me, "grain.inventory")) {
    body = (
      <>
        <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-950">
          Фактическое нетто {formatKg(wagon.net_weight_kg)} отличается от ожидаемого{" "}
          {formatKg(wagon.expected_weight_kg)}
          {wagon.weight_difference_percent != null ? ` на ${wagon.weight_difference_percent}%` : ""}.
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Причина подтверждения</Label>
          <Input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="номер акта или пояснение"
          />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void act("resolve-simple-discrepancy", { action: "reweigh" })}
          >
            Повторно взвесить
          </Button>
          <Button
            disabled={busy || !reason}
            onClick={() => void act("resolve-simple-discrepancy", { action: "confirm", reason })}
          >
            Подтвердить фактическое нетто
          </Button>
        </div>
      </>
    );
  } else if (wagon.status === "waiting_for_approval" && can(me, "grain.dispatch")) {
    body = (
      <>
        <div className="flex flex-col gap-1.5">
          <Label>Привязать к поставке</Label>
          <Select value={supplyId} onChange={(e) => setSupplyId(e.target.value)}>
            <option value="">Без поставки</option>
            {(supplies ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                #{item.id} · {item.supplier} · {item.culture}
              </option>
            ))}
          </Select>
        </div>
        <Button disabled={busy} onClick={() => void act("approve", { supply: supplyId || null })}>
          Подтвердить вагон
        </Button>
      </>
    );
  } else if (wagon.status === "arrived" && can(me, "grain.weigh")) {
    body = passage ? (
      <>
        <div className="rounded-xl border border-sky-100 bg-sky-50 p-3 text-sm text-sky-950">
          Поставьте машину на весы. Система сама получит текущее брутто и сохранит его.
        </div>
        <ScaleCaptureButton busy={busy} label="Получить вес брутто" onClick={() => void act("gross")} />
      </>
    ) : (
      <WagonScalePending />
    );
  } else if (wagon.status === "lab_pending" && can(me, "grain.lab")) {
    body = (
      <>
        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <Label>Влажность, %</Label>
            <Input type="number" value={moisture} onChange={(e) => setMoisture(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Сорность, %</Label>
            <Input type="number" value={impurity} onChange={(e) => setImpurity(e.target.value)} />
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Решение</Label>
          <Select value={decision} onChange={(e) => setDecision(e.target.value)}>
            <option value="accepted">Принято</option>
            <option value="accepted_with_restrictions">Принято с ограничениями</option>
            <option value="rejected">Отклонено</option>
            <option value="quarantine">Карантин</option>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Комментарий лаборатории</Label>
          <Input value={labNote} onChange={(e) => setLabNote(e.target.value)} />
        </div>
        <Button
          disabled={busy}
          onClick={() =>
            void act("lab", {
              decision,
              moisture: moisture || null,
              impurity: impurity || null,
              note: labNote,
            })
          }
        >
          Сохранить решение
        </Button>
      </>
    );
  } else if (needSilos && can(me, "grain.dispatch")) {
    body = (
      <>
        <div className="flex flex-col gap-1.5">
          <Label>Подходящие силосы</Label>
          <Select value={selectedSiloId} onChange={(e) => setSiloId(e.target.value)}>
            <option value="">Выберите силос</option>
            {(silos ?? []).map((silo) => (
              <option key={silo.id} value={silo.id}>
                {silo.is_default_route ? "★ " : ""}
                {silo.name} · свободно {formatKg(silo.free_capacity_kg)}
              </option>
            ))}
          </Select>
        </div>
        {(silos ?? []).length === 0 && (
          <p className="text-sm text-[var(--warning)]">
            Подходящих силосов нет: проверьте культуру, класс и свободное место.
          </p>
        )}
        {silos?.[0]?.is_default_route && (
          <p className="text-xs text-[var(--muted-foreground)]">
            Основной маршрут для этого типа зерна выбран автоматически: «{silos[0].name}».
          </p>
        )}
        <Button
          disabled={busy || !selectedSiloId}
          onClick={() => void act("assign-silo", { silo: Number(selectedSiloId) })}
        >
          Назначить силос
        </Button>
      </>
    );
  } else if (wagon.status === "silo_assigned" && can(me, "grain.unload")) {
    body = (
      <Button disabled={busy} onClick={() => void act("start-unloading")}>
        Начать разгрузку в «{wagon.assigned_silo_name}»
      </Button>
    );
  } else if (wagon.status === "unloading" && can(me, "grain.unload")) {
    body = (
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => void act("pause-unloading", { paused: !wagon.unloading_paused })}
        >
          {wagon.unloading_paused ? "Продолжить" : "Приостановить"}
        </Button>
        <Button disabled={busy} onClick={() => void act("finish-unloading")}>
          Завершить разгрузку
        </Button>
      </div>
    );
  } else if (
    (wagon.status === "unloading_completed" || wagon.status === "reweighing_required") &&
    can(me, "grain.weigh")
  ) {
    body = passage ? (
      <>
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
          После разгрузки поставьте машину на весы. Система сама получит тару и рассчитает нетто.
        </div>
        <ScaleCaptureButton busy={busy} label="Получить вес тары" onClick={() => void act("tare")} />
      </>
    ) : (
      <WagonScalePending />
    );
  } else if (wagon.status === "weight_discrepancy" && can(me, "grain.inventory")) {
    body = (
      <>
        <div className="flex flex-col gap-1.5">
          <Label>Обоснование подтверждения</Label>
          <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="акт сверки, номер документа" />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void act("resolve-discrepancy", { action: "reweigh" })}
          >
            Перевесить
          </Button>
          <Button
            disabled={busy || !reason}
            onClick={() => void act("resolve-discrepancy", { action: "confirm", reason })}
          >
            Подтвердить фактический вес
          </Button>
        </div>
      </>
    );
  } else if (wagon.status === "tare_weighed" && can(me, "grain.inventory")) {
    body = (
      <Button disabled={busy} onClick={() => void act("inventory")}>
        Оприходовать {formatKg(wagon.net_weight_kg)} в «{wagon.assigned_silo_name}»
      </Button>
    );
  } else if (wagon.status === "exit_allowed" && can(me, "grain.exit")) {
    body = (
      <Button disabled={busy} onClick={() => void act("exit")}>
        Выпустить вагон
      </Button>
    );
  }

  if (!body) return null;
  return (
    <Card>
      <CardHeader className="p-4 pb-2">
        <CardTitle>Действие сейчас</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 p-4 pt-2">
        {body}
        {error && (
          <p role="alert" className="text-sm text-[var(--destructive)]">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function SimpleFlowProgress({ wagon }: { wagon: GrainWagon }) {
  if (wagon.workflow !== "simple") return null;
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
          { label: "Вес гружёной и вывоз", icon: TrainFront },
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

function WagonPageInner({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { me } = useAuth();
  const { data: wagon, loading, error, reload } = useApi<GrainWagon>(`/grain/wagons/${id}/`);
  const { data: timeline, reload: reloadTimeline } = useApi<GrainTimelineEvent[]>(`/grain/wagons/${id}/timeline/`);
  const [deleteOpen, setDeleteOpen] = useState(false);

  if (!wagon) {
    return (
      <AppShell title="Приход и вывоз" section="Работа">
        <DataGate loading={loading} error={error} onRetry={reload} />
      </AppShell>
    );
  }

  function refresh() {
    void reload();
    void reloadTimeline();
  }

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
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <Link
          href="/grain"
          aria-label="К вагонам"
          className="flex size-9 shrink-0 items-center justify-center rounded-lg border text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/60"
        >
          <ArrowLeft className="size-4" />
        </Link>
        <h2 className="text-xl font-semibold tracking-tight">Вагон {wagon.number || `#${wagon.id}`}</h2>
        <Badge tone={GRAIN_STATUS_TONE[wagon.status] ?? "muted"} dot>
          {wagon.status_label}
        </Badge>
        {wagon.unplanned && <Badge tone="warning">внеплановый</Badge>}
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
              { label: "Брутто", value: formatKg(wagon.gross_weight_kg) },
              { label: "Тара", value: formatKg(wagon.tare_weight_kg) },
              { label: "Нетто", value: formatKg(wagon.net_weight_kg), strong: true },
              { label: "Ожидаемый вес", value: formatKg(wagon.document_weight_kg ?? wagon.expected_weight_kg) },
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

          <StageAction
            key={`${wagon.status}:${wagon.gross_weight_kg}:${wagon.tare_weight_kg}`}
            wagon={wagon}
            onChanged={refresh}
          />

          <Card>
            <CardHeader className="p-4 pb-2">
              <CardTitle>Реквизиты</CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <InfoRow label="Поставщик">{wagon.supplier || "—"}</InfoRow>
              <InfoRow label="Тип зерна">{wagon.grain_type_name || wagon.culture || "—"}</InfoRow>
              <InfoRow label="Источник номера">
                {wagon.number_source === "camera"
                  ? `Камера ${wagon.number_camera_source || "проходной"}`
                  : "Ручной ввод"}
              </InfoRow>
              <InfoRow label="Прибыл">{wagon.arrived_at ? formatDateTime(wagon.arrived_at) : "—"}</InfoRow>
              <InfoRow label="Силос">{wagon.assigned_silo_name ?? "не назначен"}</InfoRow>
              <InfoRow label="Точка разгрузки">{wagon.unloading_point || "—"}</InfoRow>
              {wagon.exited_at && <InfoRow label="Выехал">{formatDateTime(wagon.exited_at)}</InfoRow>}
            </CardContent>
          </Card>

          {(wagon.allocations ?? []).length > 0 && (
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
              <CardTitle>История вагона</CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <div className="relative space-y-3 before:absolute before:bottom-2 before:left-[5px] before:top-2 before:w-px before:bg-[var(--border)]">
                {(timeline ?? []).length === 0 ? (
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
                  <InfoRow key={row.id} label={row.kind === "gross" ? "Брутто" : "Тара"}>
                    <span className="tabular-nums">{formatKg(row.weight_kg)}</span>
                    <span className="block text-[10px] font-normal text-[var(--muted-foreground)]">
                      {formatDateTime(row.created_at)}
                      {row.operator_name ? ` · ${row.operator_name}` : ""}
                      {row.manual_reason ? ` · ${row.manual_reason}` : ""}
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
        onDeleted={() => router.replace("/grain")}
      />
    </AppShell>
  );
}

export default function GrainWagonPage(props: { params: Promise<{ id: string }> }) {
  return (
    <RequirePerm perm="grain.view" title="Приход и вывоз">
      <WagonPageInner {...props} />
    </RequirePerm>
  );
}
