"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, Camera, Check, Clock3, Plus, Scale, ScanLine, TrainFront, Warehouse } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { WagonNumberCameraWorkspace } from "@/components/grain/wagon-number-camera";
import { RequirePerm } from "@/components/require-perm";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadMore } from "@/components/ui/load-more";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { Tabs } from "@/components/ui/tabs";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { formatKg, GRAIN_STATUS_TONE } from "@/lib/grain";
import type { GrainSilo, GrainSupply, GrainType, GrainWagon, Me } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { cn, formatDateTime } from "@/lib/utils";
import { useAuth } from "@/store/auth";

type GrainTab = "expected" | "on_site" | "finished" | "camera";

function WagonStatusBadge({ wagon }: { wagon: Pick<GrainWagon, "status" | "status_label"> }) {
  return (
    <Badge tone={GRAIN_STATUS_TONE[wagon.status] ?? "muted"} dot>
      {wagon.status_label}
    </Badge>
  );
}

function GrainTypeCreator({ onCreated, onCancel }: { onCreated: (type: GrainType) => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [color, setColor] = useState("#B78132");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post<GrainType>("/grain/types/", { name, description, color });
      onCreated(data);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-2xl border border-amber-200 bg-amber-50/70 p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-amber-700">Новый справочник</p>
          <p className="mt-1 text-sm font-bold">Создать тип зерна</p>
        </div>
        <button type="button" onClick={onCancel} className="text-xs text-slate-500 hover:text-slate-900">
          Закрыть
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
        <div>
          <Label>Название *</Label>
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Пшеница продовольственная"
          />
        </div>
        <div>
          <Label>Описание</Label>
          <Input
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Краткое назначение"
          />
        </div>
        <div>
          <Label>Цвет</Label>
          <input
            type="color"
            value={color}
            onChange={(event) => setColor(event.target.value.toUpperCase())}
            className="h-10 w-14 cursor-pointer rounded-md border border-amber-200 bg-white p-1"
          />
        </div>
      </div>
      {error && <p className="mt-3 text-sm text-[var(--destructive)]">{error}</p>}
      <div className="mt-3 flex justify-end">
        <Button size="sm" disabled={busy || !name.trim()} onClick={() => void submit()}>
          <Plus className="size-4" /> {busy ? "Создание…" : "Создать тип"}
        </Button>
      </div>
    </div>
  );
}

function SupplyForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const {
    data: types,
    loading: typesLoading,
    error: typesError,
    reload: reloadTypes,
  } = useApi<GrainType[]>("/grain/types/");
  const {
    data: silos,
    loading: silosLoading,
    error: silosError,
    reload: reloadSilos,
  } = useApi<GrainSilo[]>("/grain/silos/");
  const [supplier, setSupplier] = useState("");
  const [grainType, setGrainType] = useState("");
  const [expectedTons, setExpectedTons] = useState("");
  const [siloId, setSiloId] = useState("");
  const [note, setNote] = useState("");
  const [creatingType, setCreatingType] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const suitableSilos = useMemo(
    () =>
      (silos ?? []).filter(
        (silo) =>
          silo.status === "active" && (!grainType || silo.silo_type == null || silo.silo_type === Number(grainType)),
      ),
    [grainType, silos],
  );

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.post<GrainSupply>("/grain/supplies/", {
        supplier: supplier.trim(),
        grain_type: Number(grainType),
        assigned_silo: Number(siloId),
        expected_total_kg: Math.round(Number(expectedTons) * 1000),
        simple_flow: true,
        note: note.trim(),
      });
      onDone();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  if (!types || !silos) {
    return (
      <DataGate
        loading={typesLoading || silosLoading}
        error={typesError || silosError}
        onRetry={() => void Promise.all([reloadTypes(), reloadSilos()])}
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-4 gap-2">
        {[
          ["1", "Поставщик"],
          ["2", "Тип зерна"],
          ["3", "Вес"],
          ["4", "Силос"],
        ].map(([number, label]) => (
          <div key={number} className="rounded-xl border border-slate-200 bg-slate-50 px-2 py-2 text-center">
            <span className="mx-auto flex size-5 items-center justify-center rounded-full bg-slate-900 text-[10px] font-bold text-white">
              {number}
            </span>
            <p className="mt-1 text-[10px] font-semibold text-slate-600">{label}</p>
          </div>
        ))}
      </div>

      <div>
        <Label htmlFor="grain-supplier">Название поставщика *</Label>
        <Input
          id="grain-supplier"
          value={supplier}
          onChange={(event) => setSupplier(event.target.value)}
          placeholder="ТОО Колос"
          autoFocus
        />
      </div>

      <div>
        <div className="mb-1.5 flex items-center justify-between gap-3">
          <Label htmlFor="grain-type" className="mb-0">
            Тип зерна *
          </Label>
          <button
            type="button"
            className="text-xs font-semibold text-amber-700 hover:text-amber-900"
            onClick={() => setCreatingType((current) => !current)}
          >
            + Создать новый тип
          </button>
        </div>
        <Select
          id="grain-type"
          value={grainType}
          onChange={(event) => {
            setGrainType(event.target.value);
            setSiloId("");
          }}
        >
          <option value="">Выберите тип зерна</option>
          {types.map((type) => (
            <option key={type.id} value={type.id}>
              {type.name}
            </option>
          ))}
        </Select>
      </div>

      {creatingType && (
        <GrainTypeCreator
          onCancel={() => setCreatingType(false)}
          onCreated={(type) => {
            reloadTypes().then(() => setGrainType(String(type.id)));
            setCreatingType(false);
          }}
        />
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label htmlFor="grain-expected-weight">Ожидаемый вес, тонн *</Label>
          <Input
            id="grain-expected-weight"
            type="number"
            min="0.001"
            step="0.1"
            value={expectedTons}
            onChange={(event) => setExpectedTons(event.target.value)}
            placeholder="68.3"
          />
        </div>
        <div>
          <Label htmlFor="grain-silo">Силос назначения *</Label>
          <Select
            id="grain-silo"
            value={siloId}
            onChange={(event) => setSiloId(event.target.value)}
            disabled={!grainType}
          >
            <option value="">{grainType ? "Выберите силос" : "Сначала выберите тип зерна"}</option>
            {suitableSilos.map((silo) => (
              <option key={silo.id} value={silo.id}>
                {silo.name} · свободно {formatKg(silo.free_capacity_kg)}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {grainType && suitableSilos.length === 0 && (
        <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          Для этого типа зерна нет доступного силоса. Назначьте тип силосу или проверьте свободное место.
        </p>
      )}

      <div>
        <Label htmlFor="grain-note">Комментарий</Label>
        <Input
          id="grain-note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Необязательно"
        />
      </div>

      <div className="rounded-2xl border border-emerald-100 bg-emerald-50/70 p-3 text-sm text-emerald-950">
        <div className="flex items-start gap-2">
          <Check className="mt-0.5 size-4 shrink-0 text-emerald-700" />
          После создания приход сразу появится в «Ожидаются». Номер поезда заполнит камера на проходной.
        </div>
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2 border-t pt-4">
        <Button variant="outline" disabled={busy} onClick={onCancel}>
          Отмена
        </Button>
        <Button
          disabled={busy || !supplier.trim() || !grainType || !expectedTons || !siloId}
          onClick={() => void submit()}
        >
          {busy ? "Создание…" : "Создать приход"} <ArrowRight className="size-4" />
        </Button>
      </div>
    </div>
  );
}

function ArrivalForm({
  supplies,
  initialSupply,
  onDone,
  onCancel,
}: {
  supplies: GrainSupply[];
  initialSupply?: number | null;
  onDone: (wagon: GrainWagon) => void;
  onCancel: () => void;
}) {
  const [number, setNumber] = useState("");
  const [supply, setSupply] = useState(initialSupply ? String(initialSupply) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post<GrainWagon>("/grain/wagons/arrive/", {
        number,
        supply: Number(supply),
      });
      onDone(data);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-sky-200 bg-[#10222a] p-4 text-white">
        <div className="flex items-center gap-3">
          <span className="flex size-11 items-center justify-center rounded-xl bg-sky-400/10 text-sky-300">
            <Camera className="size-5" />
          </span>
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-sky-300">Основной источник</p>
            <p className="mt-1 text-sm font-bold">Номер приходит от камеры проходной</p>
          </div>
        </div>
      </div>
      <div>
        <Label htmlFor="arrival-supply">Ожидаемый приход *</Label>
        <Select id="arrival-supply" value={supply} onChange={(event) => setSupply(event.target.value)}>
          <option value="">Выберите приход</option>
          {supplies.map((item) => (
            <option key={item.id} value={item.id}>
              #{item.id} · {item.supplier} · {item.grain_type_name} → {item.assigned_silo_name}
            </option>
          ))}
        </Select>
      </div>
      <div>
        <Label htmlFor="arrival-number">Номер поезда / вагона *</Label>
        <Input
          id="arrival-number"
          value={number}
          onChange={(event) => setNumber(event.target.value)}
          placeholder="Распознанный номер"
          autoFocus
        />
        <p className="mt-1.5 text-xs text-[var(--muted-foreground)]">
          Ручной ввод — резервный вариант, пока OCR-сервис не передал номер автоматически.
        </p>
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2 border-t pt-4">
        <Button variant="outline" disabled={busy} onClick={onCancel}>
          Отмена
        </Button>
        <Button disabled={busy || !number.trim() || !supply} onClick={() => void submit()}>
          {busy ? "Регистрация…" : "Передать на входные весы"}
        </Button>
      </div>
    </div>
  );
}

function ExpectedIntakes({
  supplies,
  canArrive,
  onArrival,
}: {
  supplies: GrainSupply[];
  canArrive: boolean;
  onArrival: (supply: GrainSupply) => void;
}) {
  if (!supplies.length) {
    return <FlowEmptyState text="Ожидаемых приходов нет — создайте «Новый приход»" />;
  }

  return (
    <div className="grid gap-3 xl:grid-cols-2">
      {supplies.map((supply) => {
        const wagon = supply.wagons[0];
        return (
          <article
            key={supply.id}
            className="group relative overflow-hidden rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_10px_32px_rgba(15,23,42,.06)] transition hover:-translate-y-0.5 hover:shadow-[0_16px_38px_rgba(15,23,42,.1)]"
          >
            <div
              className="absolute inset-y-0 left-0 w-1.5"
              style={{ backgroundColor: supply.grain_type_color || "#B78132" }}
            />
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">Приход #{supply.id}</p>
                <h3 className="mt-1 truncate text-lg font-bold tracking-tight text-slate-900">{supply.supplier}</h3>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Badge tone="warning" dot>
                    Ожидает камеру
                  </Badge>
                  <span className="text-xs text-slate-500">{supply.grain_type_name}</span>
                </div>
              </div>
              <span className="flex size-11 shrink-0 items-center justify-center rounded-2xl bg-amber-50 text-amber-700">
                <TrainFront className="size-5" />
              </span>
            </div>
            <div className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-3">
              <div className="rounded-xl bg-slate-50 p-3">
                <p className="text-[10px] uppercase tracking-wide text-slate-400">Ожидаемый вес</p>
                <p className="mt-1 font-bold tabular-nums">{formatKg(supply.expected_total_kg)}</p>
              </div>
              <div className="rounded-xl bg-slate-50 p-3 sm:col-span-2">
                <p className="text-[10px] uppercase tracking-wide text-slate-400">Назначенный силос</p>
                <p className="mt-1 truncate font-bold">{supply.assigned_silo_name || "—"}</p>
              </div>
            </div>
            <div className="mt-4 flex items-center justify-between gap-3 border-t border-slate-100 pt-4">
              <span className="flex items-center gap-2 text-xs text-slate-400">
                <ScanLine className="size-4" /> {wagon?.number || "Номер ещё не получен"}
              </span>
              {canArrive && (
                <Button size="sm" onClick={() => onArrival(supply)}>
                  Принять поезд <ArrowRight className="size-4" />
                </Button>
              )}
            </div>
          </article>
        );
      })}
    </div>
  );
}

/**
 * Маршрут поезда в 4 шага. Номер шага по статусу; 4 — вагон завершил маршрут.
 * Неизвестные статусы легаси-потока прижимаются к силосному этапу.
 */
const WAGON_STEPS = [
  { label: "Камера", icon: Camera },
  { label: "Входные весы", icon: Scale },
  { label: "Силос", icon: Warehouse },
  { label: "Выходные весы", icon: TrainFront },
] as const;

const STEP_BY_STATUS: Record<string, number> = {
  expected: 0,
  waiting_for_approval: 0,
  unplanned: 0,
  arrived: 1,
  gross_weighed: 2,
  lab_pending: 2,
  unloading_allowed: 2,
  silo_assigned: 2,
  at_silo: 2,
  unloading: 2,
  quarantine: 2,
  insufficient_capacity: 2,
  blocked: 2,
  rejected: 2,
  unloading_completed: 3,
  reweighing_required: 3,
  tare_weighed: 3,
  weight_discrepancy: 3,
  inventoried: 3,
  exit_allowed: 3,
  return_to_supplier: 3,
  exited: 4,
  completed: 4,
  cancelled: 4,
};

const PROBLEM_STATUSES = new Set([
  "weight_discrepancy",
  "reweighing_required",
  "quarantine",
  "rejected",
  "blocked",
  "insufficient_capacity",
  "return_to_supplier",
]);

function wagonStepIndex(wagon: GrainWagon) {
  return STEP_BY_STATUS[wagon.status] ?? 2;
}

function WagonStepper({ wagon }: { wagon: GrainWagon }) {
  const activeIndex = wagonStepIndex(wagon);
  const problem = PROBLEM_STATUSES.has(wagon.status);

  return (
    <ol className="flex items-start" aria-label="Маршрут поезда">
      {WAGON_STEPS.map((step, index) => {
        const Icon = step.icon;
        const done = index < activeIndex;
        const current = index === activeIndex;
        return (
          <li key={step.label} className={cn("flex items-start", index > 0 && "flex-1")}>
            {index > 0 && (
              <span
                aria-hidden
                className={cn("mx-1.5 mt-4 h-0.5 flex-1 rounded-full", done || current ? "bg-slate-900" : "bg-slate-200")}
              />
            )}
            <span className="flex w-14 shrink-0 flex-col items-center gap-1 text-center">
              <span
                className={cn(
                  "flex size-8 items-center justify-center rounded-full border-2 transition-colors",
                  done && "border-slate-900 bg-slate-900 text-white",
                  current && !problem && "border-amber-500 bg-amber-50 text-amber-700",
                  current && problem && "border-red-500 bg-red-50 text-red-600",
                  !done && !current && "border-slate-200 bg-white text-slate-300",
                )}
              >
                {done ? <Check className="size-4" /> : <Icon className="size-3.5" />}
              </span>
              <span
                className={cn(
                  "text-[9.5px] font-semibold leading-tight",
                  current ? (problem ? "text-red-600" : "text-amber-700") : done ? "text-slate-600" : "text-slate-300",
                )}
              >
                {step.label}
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function wagonCta(wagon: GrainWagon, me: Me | null) {
  if (wagon.workflow === "simple") {
    if (wagon.status === "arrived" && can(me, "grain.weigh"))
      return { label: "Внести входной вес", variant: "default" as const };
    if (wagon.status === "at_silo" && can(me, "grain.weigh"))
      return { label: "Внести выходной вес", variant: "default" as const };
    if (wagon.status === "weight_discrepancy" && can(me, "grain.inventory"))
      return { label: "Разобрать расхождение", variant: "destructive" as const };
  }
  if (wagonStepIndex(wagon) >= 4) return { label: "Карточка вагона", variant: "outline" as const };
  return { label: "Открыть этап", variant: "outline" as const };
}

function WagonMetric({ label, value, hint }: { label: string; value: string | null; hint: string }) {
  return (
    <div className="rounded-xl bg-slate-50 p-3">
      <p className="text-[10px] uppercase tracking-wide text-slate-400">{label}</p>
      {value ? (
        <p className="mt-1 font-bold tabular-nums">{value}</p>
      ) : (
        <p className="mt-1 text-xs font-medium leading-5 text-slate-400">{hint}</p>
      )}
    </div>
  );
}

function WagonCards({ wagons, me, emptyText }: { wagons: GrainWagon[]; me: Me | null; emptyText: string }) {
  if (!wagons.length) {
    return <FlowEmptyState text={emptyText} />;
  }

  return (
    <div className="grid gap-3 xl:grid-cols-2">
      {wagons.map((wagon) => {
        const cta = wagonCta(wagon, me);
        const finished = wagonStepIndex(wagon) >= 4;
        const timeline = finished
          ? wagon.exited_at
            ? `выехал ${formatDateTime(wagon.exited_at)}`
            : null
          : wagon.status === "at_silo" && wagon.silo_arrived_at
            ? `у силоса с ${formatDateTime(wagon.silo_arrived_at)}`
            : wagon.arrived_at
              ? `на территории с ${formatDateTime(wagon.arrived_at)}`
              : null;
        return (
          <article
            key={wagon.id}
            className="flex flex-col rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_10px_30px_rgba(15,23,42,.06)]"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">Поезд / вагон</p>
                <h3 className="mt-1 truncate text-xl font-black tracking-tight">{wagon.number || `#${wagon.id}`}</h3>
                <p className="mt-1 truncate text-sm text-slate-500">
                  {wagon.supplier}
                  {wagon.grain_type_name || wagon.culture ? ` · ${wagon.grain_type_name || wagon.culture}` : ""}
                  {wagon.assigned_silo_name ? ` → ${wagon.assigned_silo_name}` : ""}
                </p>
              </div>
              <WagonStatusBadge wagon={wagon} />
            </div>

            <div className="mt-4 rounded-xl border border-slate-100 bg-white px-3 py-3">
              <WagonStepper wagon={wagon} />
            </div>

            <div className="mt-3 grid grid-cols-3 gap-2 text-sm">
              <WagonMetric label="Ожидается" value={wagon.expected_weight_kg != null ? formatKg(wagon.expected_weight_kg) : null} hint="без плана" />
              <WagonMetric label="Входной вес" value={wagon.gross_weight_kg != null ? formatKg(wagon.gross_weight_kg) : null} hint="после входных весов" />
              <WagonMetric label="Нетто" value={wagon.net_weight_kg != null ? formatKg(wagon.net_weight_kg) : null} hint="после выходных весов" />
            </div>

            {wagon.weight_difference_kg != null && wagon.net_weight_kg != null && (
              <p
                className={cn(
                  "mt-2 inline-flex w-fit items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold",
                  wagon.weight_matches === false ? "bg-red-50 text-red-700" : "bg-emerald-50 text-emerald-700",
                )}
              >
                Δ к ожиданию: {wagon.weight_difference_kg > 0 ? "+" : ""}
                {formatKg(wagon.weight_difference_kg)}
                {wagon.weight_difference_percent != null ? ` (${wagon.weight_difference_percent}%)` : ""}
              </p>
            )}

            <div className="mt-auto flex items-center justify-between gap-3 border-t border-slate-100 pt-4">
              <span className="truncate text-xs text-slate-400">{timeline}</span>
              <Link
                href={`/grain/wagons/${wagon.id}`}
                className={buttonVariants({ size: "sm", variant: cta.variant })}
              >
                {cta.label} <ArrowRight className="size-4" />
              </Link>
            </div>
          </article>
        );
      })}
    </div>
  );
}

/** Пустое состояние с объяснением маршрута — вместо постоянного баннера. */
function FlowEmptyState({ text }: { text: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center">
      <Clock3 className="mx-auto size-8 text-slate-300" />
      <p className="mt-3 text-sm font-semibold text-slate-700">{text}</p>
      <div className="mx-auto mt-6 flex max-w-md items-center justify-between gap-1">
        {WAGON_STEPS.map((step, index) => {
          const Icon = step.icon;
          return (
            <div key={step.label} className="flex flex-1 items-center">
              {index > 0 && <span className="mx-1 h-px flex-1 bg-slate-200" aria-hidden />}
              <span className="flex flex-col items-center gap-1.5">
                <span className="flex size-9 items-center justify-center rounded-xl bg-white text-slate-500 shadow-sm">
                  <Icon className="size-4" />
                </span>
                <span className="text-[10px] font-semibold text-slate-400">{step.label}</span>
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function GrainPageInner() {
  const { me } = useAuth();
  const canSupply = can(me, "grain.supply");
  const canArrive = can(me, "grain.arrive");
  const [tab, setTab] = useState<GrainTab>("on_site");
  const [supplyOpen, setSupplyOpen] = useState(false);
  const [arriveOpen, setArriveOpen] = useState(false);
  const [arrivalSupply, setArrivalSupply] = useState<number | null>(null);
  const [notice, setNotice] = useState("");

  const supplies = usePagedApi<GrainSupply>(
    tab === "expected" ? "/grain/supplies/?status=expected&awaiting_arrival=1" : null,
    50,
  );
  const wagons = usePagedApi<GrainWagon>(
    tab === "on_site" || tab === "finished" ? `/grain/wagons/?scope=${tab}` : null,
    50,
  );
  const arrivalSupplies = usePagedApi<GrainSupply>(
    arriveOpen ? "/grain/supplies/?status=expected&awaiting_arrival=1" : null,
    100,
  );

  function refreshAll() {
    void supplies.reload();
    void wagons.reload();
    void arrivalSupplies.reload();
  }

  function openArrival(supply?: GrainSupply) {
    setArrivalSupply(supply?.id ?? null);
    setArriveOpen(true);
  }

  return (
    <AppShell
      title="Приход и проход"
      section="Работа"
      description="Короткий маршрут поезда: камера, входной вес, назначенный силос, выходной вес и фактическое нетто."
      actions={
        tab !== "camera" ? (
          <div className="flex items-center gap-2">
            {canArrive && (
              <Button size="sm" variant="outline" onClick={() => openArrival()}>
                <TrainFront className="size-4" /> Принять поезд
              </Button>
            )}
            {canSupply && (
              <Button size="sm" onClick={() => setSupplyOpen(true)}>
                <Plus className="size-4" /> Новый приход
              </Button>
            )}
          </div>
        ) : undefined
      }
    >
      <div className="space-y-4">
        <Tabs
          tabs={[
            { key: "expected", label: "Ожидаются" },
            { key: "on_site", label: "На территории" },
            { key: "finished", label: "Завершённые" },
            { key: "camera", label: "Камера проходной", icon: ScanLine },
          ]}
          active={tab}
          onChange={(key) => setTab(key as GrainTab)}
        />

        {notice && (
          <p className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            {notice}
          </p>
        )}

        {tab === "camera" ? (
          <WagonNumberCameraWorkspace canManage={Boolean(me?.is_superuser)} />
        ) : tab === "expected" ? (
          <>
            {supplies.error && <ErrorAlert message={supplies.error} onRetry={refreshAll} />}
            <ExpectedIntakes supplies={supplies.items} canArrive={canArrive} onArrival={openArrival} />
            <LoadMore
              shown={supplies.items.length}
              total={supplies.count}
              hasMore={supplies.hasMore}
              loading={supplies.loadingMore}
              onClick={supplies.loadMore}
            />
          </>
        ) : (
          <>
            {wagons.error && <ErrorAlert message={wagons.error} onRetry={() => void wagons.reload()} />}
            <WagonCards
              wagons={wagons.items}
              me={me}
              emptyText={tab === "on_site" ? "Поездов на территории нет" : "Завершённых приходов пока нет"}
            />
            <LoadMore
              shown={wagons.items.length}
              total={wagons.count}
              hasMore={wagons.hasMore}
              loading={wagons.loadingMore}
              onClick={wagons.loadMore}
            />
          </>
        )}
      </div>

      <Modal
        open={supplyOpen}
        onClose={() => setSupplyOpen(false)}
        eyebrow="Приход · 4 поля"
        title="Новый приход зерна"
        description="Создайте ожидаемый поезд и сразу задайте его конечный силос."
        className="max-w-2xl"
      >
        {supplyOpen && (
          <SupplyForm
            onCancel={() => setSupplyOpen(false)}
            onDone={() => {
              setSupplyOpen(false);
              setTab("expected");
              setNotice("Приход создан. Ожидаем номер от камеры проходной.");
              refreshAll();
            }}
          />
        )}
      </Modal>

      <Modal
        open={arriveOpen}
        onClose={() => setArriveOpen(false)}
        eyebrow="Проходная · Камера"
        title="Номер поезда получен"
        description="Свяжите распознанный номер с ожидаемым приходом."
        className="max-w-lg"
      >
        {arriveOpen && (
          <ArrivalForm
            supplies={arrivalSupplies.items}
            initialSupply={arrivalSupply}
            onCancel={() => setArriveOpen(false)}
            onDone={(wagon) => {
              setArriveOpen(false);
              setNotice(`Поезд ${wagon.number} направлен на входные весы.`);
              setTab("on_site");
              refreshAll();
            }}
          />
        )}
      </Modal>
    </AppShell>
  );
}

export default function GrainPage() {
  return (
    <RequirePerm perm="grain.view" title="Приход и проход">
      <GrainPageInner />
    </RequirePerm>
  );
}
