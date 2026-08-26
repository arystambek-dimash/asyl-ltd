"use client";

import { useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Camera, Check, Plus, ScanLine, TrainFront, Truck } from "lucide-react";
import { GrainToolbar } from "@/components/grain/grain-toolbar";
import { AppShell } from "@/components/layout/app-shell";
import { VehiclePlateCameraWorkspace } from "@/components/grain/vehicle-plate-camera";
import { WagonNumberCameraWorkspace } from "@/components/grain/wagon-number-camera";
import { FlowEmptyState, WagonTable } from "@/components/grain/wagon-table";
import { RequirePerm } from "@/components/require-perm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadMore } from "@/components/ui/load-more";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { Tabs } from "@/components/ui/tabs";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { formatKg } from "@/lib/grain";
import type { GrainSilo, GrainSupply, GrainType, GrainWagon, VehiclePlateCandidate } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { formatDateTime } from "@/lib/utils";
import { useAuth } from "@/store/auth";

type GrainTab = "expected" | "on_site" | "finished" | "camera";
type GrainDirection = GrainWagon["direction"];

const DIRECTION_TABS = [
  { key: "intake", label: "Приход", icon: TrainFront },
  { key: "passage", label: "Вывоз", icon: Truck },
];

const INTAKE_TABS = [
  { key: "expected", label: "Ожидаются" },
  { key: "on_site", label: "На территории" },
  { key: "finished", label: "Завершённые" },
  { key: "camera", label: "Камера проходной", icon: ScanLine },
];

const PASSAGE_TABS = [
  { key: "on_site", label: "На территории" },
  { key: "finished", label: "Завершённые" },
  { key: "camera", label: "Камера проходной", icon: ScanLine },
];

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

/**
 * Регистрация вывоза. Ожидаемый вес не спрашиваем: сколько заберут — решают
 * на погрузке, факт станет известен только на выездных весах.
 */
function ocrConfidenceLabel(value: VehiclePlateCandidate["ocr_confidence"]) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${Math.round(numeric * 100)}%` : "—";
}

function candidateDetails(candidate: VehiclePlateCandidate) {
  return (
    <>
      Камера {candidate.camera} · {candidate.source} · {formatDateTime(candidate.detected_at)} · OCR{" "}
      {ocrConfidenceLabel(candidate.ocr_confidence)}
    </>
  );
}

function hasApiErrorCode(cause: unknown, expectedCode: string) {
  const code = (cause as { response?: { data?: { code?: unknown } } }).response?.data?.code;
  return code === expectedCode;
}

function PassageForm({ onDone, onCancel }: { onDone: (wagon: GrainWagon) => void; onCancel: () => void }) {
  const [number, setNumber] = useState("");
  const [cargo, setCargo] = useState("Отруби");
  const [note, setNote] = useState("");
  const [selectedCandidate, setSelectedCandidate] = useState<VehiclePlateCandidate | null>(null);
  const selectedCandidateRef = useRef<VehiclePlateCandidate | null>(null);
  const [selectedCandidateExpired, setSelectedCandidateExpired] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const {
    data: plateCandidates,
    loading: candidatesLoading,
    error: candidatesError,
    reload: reloadCandidates,
  } = useApi<VehiclePlateCandidate[]>("/grain/wagons/vehicle-plate-candidates/");
  useVisiblePolling(reloadCandidates, 10_000);
  const visibleCandidates = (plateCandidates ?? [])
    .filter((candidate) => candidate.event_id !== selectedCandidate?.event_id)
    .slice(0, 5);

  function selectCandidate(candidate: VehiclePlateCandidate) {
    if (busy) return;
    setNumber(candidate.vehicle_number);
    selectedCandidateRef.current = candidate;
    setSelectedCandidate(candidate);
    setSelectedCandidateExpired(false);
    setError("");
  }

  function switchToManualNumber() {
    if (busy) return;
    setNumber("");
    selectedCandidateRef.current = null;
    setSelectedCandidate(null);
    setSelectedCandidateExpired(false);
    setError("");
  }

  async function submit() {
    if (busy || selectedCandidateExpired) return;
    const submittedCandidate = selectedCandidateRef.current;
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post<GrainWagon>("/grain/wagons/passage/", {
        number,
        cargo_name: cargo,
        note,
        ...(submittedCandidate ? { vehicle_plate_event_id: submittedCandidate.event_id } : {}),
      });
      onDone(data);
    } catch (cause) {
      if (
        hasApiErrorCode(cause, "vehicle_plate_event_unavailable") &&
        submittedCandidate !== null &&
        selectedCandidateRef.current?.event_id === submittedCandidate.event_id
      ) {
        setSelectedCandidateExpired(true);
        setError("Выбранный номер больше недоступен. Выберите другой номер или перейдите на ручной ввод.");
      } else {
        setError(apiError(cause));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-[var(--border)] bg-[var(--muted)] px-4 py-3">
        <p className="text-sm font-semibold">Машина заезжает пустой</p>
        <p className="mt-1 text-xs leading-relaxed text-[var(--muted-foreground)]">
          Взвесьте её на въезде, затем загрузите и взвесьте на выезде. Вывезенный вес система посчитает сама — заранее
          указывать его не нужно.
        </p>
      </div>
      <div>
        <Label htmlFor="passage-cargo">Что вывозят *</Label>
        <Input
          id="passage-cargo"
          value={cargo}
          onChange={(event) => setCargo(event.target.value)}
          placeholder="Отруби"
        />
      </div>
      <div>
        <Label htmlFor="passage-number">Номер машины</Label>
        <Input
          id="passage-number"
          value={number}
          onChange={(event) => setNumber(event.target.value)}
          readOnly={selectedCandidate !== null}
          placeholder="123 ABC 02"
        />
        {selectedCandidate && (
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            Номер закреплён за выбранным событием камеры. Для изменения перейдите на ручной ввод.
          </p>
        )}
      </div>
      {(selectedCandidate || candidatesLoading || candidatesError || visibleCandidates.length > 0) && (
        <section aria-label="Распознанные номера" className="rounded-xl border bg-[var(--muted)]/40 p-3">
          <div className="flex items-baseline justify-between gap-3">
            <div>
              <p className="text-sm font-semibold">Распознанные номера</p>
              <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                Выберите машину явно; ручной ввод включается отдельной кнопкой.
              </p>
            </div>
            {candidatesLoading && <span className="text-xs text-[var(--muted-foreground)]">Обновление…</span>}
          </div>
          <div className="mt-2 space-y-2">
            {selectedCandidate && (
              <div
                className={
                  selectedCandidateExpired
                    ? "rounded-lg border border-amber-300 bg-amber-50 px-3 py-2"
                    : "rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2"
                }
              >
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <div className="min-w-0">
                    <p className="font-semibold tabular-nums">
                      {selectedCandidate.vehicle_number} {selectedCandidateExpired ? "· недоступен" : "· выбран"}
                    </p>
                    <p className="text-[11px] text-[var(--muted-foreground)]">{candidateDetails(selectedCandidate)}</p>
                  </div>
                  <Button size="sm" variant="outline" disabled={busy} onClick={switchToManualNumber}>
                    Перейти на ручной ввод
                  </Button>
                </div>
              </div>
            )}
            {candidatesError ? (
              <p className="text-xs text-[var(--destructive)]">{candidatesError}</p>
            ) : (
              visibleCandidates.map((candidate) => {
                return (
                  <div
                    key={candidate.event_id}
                    className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-lg border bg-[var(--card)] px-3 py-2"
                  >
                    <div className="min-w-0">
                      <p className="font-semibold tabular-nums">{candidate.vehicle_number}</p>
                      <p className="text-[11px] text-[var(--muted-foreground)]">{candidateDetails(candidate)}</p>
                    </div>
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => selectCandidate(candidate)}>
                      Использовать
                    </Button>
                  </div>
                );
              })
            )}
          </div>
        </section>
      )}
      <div>
        <Label htmlFor="passage-note">Примечание</Label>
        <Input
          id="passage-note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Кому отгружаем, номер заявки"
        />
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel} disabled={busy}>
          Отмена
        </Button>
        <Button disabled={busy || !cargo.trim() || selectedCandidateExpired} onClick={() => void submit()}>
          {busy ? "Оформление…" : "Оформить вывоз"} <ArrowRight className="size-4" />
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
          {busy ? "Регистрация…" : "Зарегистрировать приход"}
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

function GrainPageInner() {
  const router = useRouter();
  const { me } = useAuth();
  const canSupply = can(me, "grain.supply");
  const canArrive = can(me, "grain.arrive");
  const canWeigh = can(me, "grain.weigh");
  const [direction, setDirection] = useState<GrainDirection>("intake");
  const [tabByDirection, setTabByDirection] = useState<Record<GrainDirection, GrainTab>>({
    intake: "on_site",
    passage: "on_site",
  });
  const tab = tabByDirection[direction];
  const [supplyOpen, setSupplyOpen] = useState(false);
  const [arriveOpen, setArriveOpen] = useState(false);
  const [passageOpen, setPassageOpen] = useState(false);
  const [arrivalSupply, setArrivalSupply] = useState<number | null>(null);
  const [notice, setNotice] = useState("");

  const supplies = usePagedApi<GrainSupply>(
    direction === "intake" && tab === "expected" ? "/grain/supplies/?status=expected&awaiting_arrival=1" : null,
    50,
  );
  const wagons = usePagedApi<GrainWagon>(
    tab === "on_site" || tab === "finished" ? `/grain/wagons/?scope=${tab}&direction=${direction}` : null,
    50,
  );
  const arrivalSupplies = usePagedApi<GrainSupply>(
    arriveOpen ? "/grain/supplies/?status=expected&awaiting_arrival=1" : null,
    100,
  );
  useVisiblePolling(wagons.reload, 10_000, tab === "on_site" || tab === "finished");

  function refreshAll() {
    void supplies.reload();
    void wagons.reload();
    void arrivalSupplies.reload();
  }

  function selectDirection(next: GrainDirection) {
    if (next !== direction) setNotice("");
    setDirection(next);
  }

  function selectStatusTab(key: string) {
    setDirectionTab(direction, key as GrainTab);
  }

  function setDirectionTab(nextDirection: GrainDirection, nextTab: GrainTab) {
    setTabByDirection((current) => ({ ...current, [nextDirection]: nextTab }));
  }

  function openArrival(supply?: GrainSupply) {
    selectDirection("intake");
    setArrivalSupply(supply?.id ?? null);
    setArriveOpen(true);
  }

  function openPassage() {
    selectDirection("passage");
    setPassageOpen(true);
  }

  function changeDirection(key: string) {
    if (key === "intake" || key === "passage") selectDirection(key);
  }

  return (
    <AppShell
      title="Приход и вывоз"
      section="Работа"
      description={
        direction === "intake"
          ? "Приход: поезд привозит зерно. Вагонные весы пока не подключены; весы машин вывоза здесь не используются."
          : "Вывоз: камера автоматически создаёт рейс и фиксирует оба веса; ручное оформление остаётся резервным вариантом."
      }
      actions={
        <GrainToolbar
          direction={direction}
          canArrive={canArrive}
          canSupply={canSupply}
          canWeigh={canWeigh}
          onPassage={openPassage}
          onArrival={() => openArrival()}
          onSupply={() => {
            selectDirection("intake");
            setSupplyOpen(true);
          }}
        />
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-[var(--card)] p-2">
          <Tabs
            tabs={DIRECTION_TABS}
            active={direction}
            onChange={changeDirection}
            variant="segment"
            label="Направление рейса"
            className="w-full sm:w-auto [&>button]:flex-1 sm:[&>button]:min-w-36"
          />
          <p className="hidden pr-2 text-xs text-[var(--muted-foreground)] lg:block">
            {direction === "intake" ? "Таблица поездов на приём зерна" : "Таблица машин на вывоз груза"}
          </p>
        </div>

        <Tabs
          tabs={direction === "intake" ? INTAKE_TABS : PASSAGE_TABS}
          active={tab}
          onChange={selectStatusTab}
          label="Статус рейсов"
        />

        {notice && (
          <p className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            {notice}
          </p>
        )}

        {tab === "camera" ? (
          direction === "intake" ? (
            <WagonNumberCameraWorkspace canManage={Boolean(me?.is_superuser)} />
          ) : (
            <VehiclePlateCameraWorkspace />
          )
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
            <WagonTable
              wagons={wagons.items}
              me={me}
              direction={direction}
              emptyText={
                direction === "intake"
                  ? tab === "on_site"
                    ? "На территории нет поездов на приём"
                    : "Завершённых приходов пока нет"
                  : tab === "on_site"
                    ? "На территории нет машин на вывоз"
                    : "Завершённых вывозов пока нет"
              }
              onDeleted={() => {
                setNotice(tab === "finished" ? "Рейс удалён, остаток силоса пересчитан." : "Активный рейс удалён.");
                refreshAll();
              }}
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
              setDirection("intake");
              setDirectionTab("intake", "expected");
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
              setDirection("intake");
              setNotice(`Поезд ${wagon.number} зарегистрирован. Вагонные весы пока не подключены.`);
              setDirectionTab("intake", "on_site");
              refreshAll();
            }}
          />
        )}
      </Modal>

      <Modal
        open={passageOpen}
        onClose={() => setPassageOpen(false)}
        eyebrow="Проходная · Вывоз"
        title="Оформить вывоз"
        description="Машина приехала забрать отруби. Зафиксируем вес на въезде и на выезде."
        className="max-w-lg"
      >
        {passageOpen && (
          <PassageForm
            onCancel={() => setPassageOpen(false)}
            onDone={(wagon) => {
              setPassageOpen(false);
              setDirection("passage");
              setNotice(`Вывоз ${wagon.number || `#${wagon.id}`} оформлен — взвесьте пустую машину на въезде.`);
              setDirectionTab("passage", "on_site");
              refreshAll();
              router.push(`/grain/wagons/${wagon.id}`);
            }}
          />
        )}
      </Modal>
    </AppShell>
  );
}

export default function GrainPage() {
  return (
    <RequirePerm perm="grain.view" title="Приход и вывоз">
      <GrainPageInner />
    </RequirePerm>
  );
}
