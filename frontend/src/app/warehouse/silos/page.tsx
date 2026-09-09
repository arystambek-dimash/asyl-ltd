"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { Activity, Gauge, History, Plus, Route, Settings2, ShieldAlert, Sprout, Warehouse } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadMore } from "@/components/ui/load-more";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { formatKg, GRAIN_MOVEMENT_LABELS } from "@/lib/grain";
import type { GrainMovement, GrainSilo, GrainSiloType } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { cn, formatDateTime } from "@/lib/utils";
import { useAuth } from "@/store/auth";

const DEFAULT_TYPE_COLOR = "#C58A35";

function clampPercent(value: number) {
  return Math.max(0, Math.min(100, value));
}

function routeLabel(type: GrainSiloType) {
  return type.default_silo_name ? `Приход → ${type.default_silo_name}` : "Основной силос не назначен";
}

function SummaryMetric({
  icon: Icon,
  label,
  value,
  note,
  tone = "steel",
}: {
  icon: typeof Warehouse;
  label: string;
  value: string;
  note: string;
  tone?: "steel" | "grain" | "green" | "blue";
}) {
  const tones = {
    steel: "bg-slate-900 text-white",
    grain: "bg-[#a66a20] text-white",
    green: "bg-[#356f48] text-white",
    blue: "bg-[#315d74] text-white",
  };
  return (
    <div className="flex min-w-0 items-center gap-3 rounded-xl border bg-[var(--card)] p-3 sm:p-4">
      <div className={cn("hidden size-10 shrink-0 items-center justify-center rounded-xl sm:flex", tones[tone])}>
        <Icon className="size-5" />
      </div>
      <div className="min-w-0">
        <div className="text-xs text-[var(--muted-foreground)]">{label}</div>
        <div className="mt-0.5 truncate text-xl font-semibold tabular-nums">{value}</div>
        <div className="truncate text-xs text-[var(--muted-foreground)]">{note}</div>
      </div>
    </div>
  );
}

function SiloTank({ silo }: { silo: GrainSilo }) {
  const clipId = useId().replace(/:/g, "");
  const fill = clampPercent(silo.fill_percent);
  const reserve = Math.min(100 - fill, clampPercent((silo.reserved_kg / Math.max(1, silo.total_capacity_kg)) * 100));
  const color = silo.silo_type_color || DEFAULT_TYPE_COLOR;
  return (
    <svg
      viewBox="0 0 120 170"
      role="img"
      aria-label={`${silo.name}: заполнено ${fill}%`}
      className="mx-auto w-16 shrink-0 sm:w-24"
    >
      <defs>
        <clipPath id={clipId}>
          <path d="M20 30 Q60 5 100 30 V130 L72 150 H48 L20 130Z" />
        </clipPath>
      </defs>
      <path
        d="M20 30 Q60 5 100 30 V130 L72 150 H48 L20 130Z"
        fill="var(--muted)"
        stroke="var(--muted-foreground)"
        strokeWidth="1.5"
      />
      <g clipPath={`url(#${clipId})`}>
        {fill > 0 && <rect x="20" y={150 - fill * 1.3} width="80" height={fill * 1.3} fill={color} fillOpacity=".75" />}
        {reserve > 0 && (
          <rect
            x="20"
            y={150 - (fill + reserve) * 1.3}
            width="80"
            height={reserve * 1.3}
            fill={color}
            fillOpacity=".2"
          />
        )}
        {[45, 70, 95, 120].map((y) => (
          <path key={y} d={`M20 ${y}H100`} stroke="var(--muted-foreground)" strokeOpacity=".2" />
        ))}
      </g>
      <path
        d="M20 30 Q60 50 100 30 M48 150 V160 M72 150 V160 M38 160 H82"
        fill="none"
        stroke="var(--muted-foreground)"
        strokeWidth="1.5"
      />
      <rect x="27" y="62" width="66" height="36" rx="8" fill="var(--card)" />
      <text x="60" y="86" textAnchor="middle" fill="var(--foreground)" fontSize="20" fontWeight="600">
        {Math.round(fill)}%
      </text>
    </svg>
  );
}

function SiloForm({ types, onDone, onCancel }: { types: GrainSiloType[]; onDone: () => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [capacityTons, setCapacityTons] = useState("");
  const [siloType, setSiloType] = useState("");
  const [line, setLine] = useState("");
  const [allowMixing, setAllowMixing] = useState(false);
  const [isQuarantine, setIsQuarantine] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.post("/grain/silos/", {
        name,
        total_capacity_kg: Math.round(Number(capacityTons) * 1000),
        silo_type: siloType ? Number(siloType) : null,
        unloading_line: line,
        allow_mixing: allowMixing,
        is_quarantine: isQuarantine,
      });
      onDone();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-lg border border-[#c58a35]/25 bg-[#c58a35]/8 px-3 py-2.5 text-sm">
        Один «Тип зерна» используется в приходе и в силосах. Основной маршрут можно задать в «Типах зерна».
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label>Название *</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Силос-3" />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Вместимость, т *</Label>
          <Input type="number" min="1" value={capacityTons} onChange={(e) => setCapacityTons(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <Label>Тип зерна</Label>
          <Select value={siloType} onChange={(e) => setSiloType(e.target.value)}>
            <option value="">Не назначен</option>
            {types.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <Label>Линия разгрузки</Label>
          <Input value={line} onChange={(e) => setLine(e.target.value)} placeholder="Линия 1" />
        </div>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={allowMixing} onChange={(e) => setAllowMixing(e.target.checked)} />
        Разрешить смешивание разных типов зерна
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={isQuarantine} onChange={(e) => setIsQuarantine(e.target.checked)} />
        Карантинный силос
      </label>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2 border-t pt-3">
        <Button variant="outline" disabled={busy} onClick={onCancel}>
          Отмена
        </Button>
        <Button disabled={busy || !name.trim() || !capacityTons} onClick={() => void submit()}>
          {busy ? "Сохранение…" : "Создать силос"}
        </Button>
      </div>
    </div>
  );
}

function SiloTypeForm({ silos, onDone }: { silos: GrainSilo[]; onDone: () => void }) {
  const [name, setName] = useState("");
  const [color, setColor] = useState(DEFAULT_TYPE_COLOR);
  const [description, setDescription] = useState("");
  const [defaultSilo, setDefaultSilo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.post("/grain/silo-types/", {
        name,
        color,
        description,
        default_silo: defaultSilo ? Number(defaultSilo) : null,
      });
      setName("");
      setDescription("");
      setDefaultSilo("");
      onDone();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border bg-[var(--muted)]/35 p-4">
      <div className="mb-3 flex items-center gap-2">
        <div className="flex size-8 items-center justify-center rounded-lg bg-[#173947] text-white">
          <Plus className="size-4" />
        </div>
        <div>
          <div className="font-semibold">Новый тип зерна</div>
          <div className="text-xs text-[var(--muted-foreground)]">Например: «Пшеница 3 класса»</div>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label>Название *</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Пшеница 3 класса" />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Цвет на схеме</Label>
          <div className="flex gap-2">
            <input
              type="color"
              value={color}
              onChange={(e) => setColor(e.target.value.toUpperCase())}
              className="h-9 w-12 cursor-pointer rounded-md border bg-transparent p-1"
              aria-label="Цвет типа зерна"
            />
            <Input value={color} onChange={(e) => setColor(e.target.value)} />
          </div>
        </div>
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <Label>Куда направлять приход</Label>
          <Select value={defaultSilo} onChange={(e) => setDefaultSilo(e.target.value)}>
            <option value="">Назначить позже</option>
            {silos
              .filter((silo) => silo.silo_type == null)
              .map((silo) => (
                <option key={silo.id} value={silo.id}>
                  {silo.name} · свободно {formatKg(silo.free_capacity_kg)}
                </option>
              ))}
          </Select>
        </div>
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <Label>Описание</Label>
          <Input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Назначение или особые условия хранения"
          />
        </div>
      </div>
      {error && <p className="mt-3 text-sm text-[var(--destructive)]">{error}</p>}
      <div className="mt-3 flex justify-end">
        <Button disabled={busy || !name.trim()} onClick={() => void submit()}>
          {busy ? "Добавление…" : "Добавить тип"}
        </Button>
      </div>
    </div>
  );
}

function TypeRouteRow({ type, silos, onDone }: { type: GrainSiloType; silos: GrainSilo[]; onDone: () => void }) {
  const [target, setTarget] = useState(type.default_silo ? String(type.default_silo) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const available = silos.filter((silo) => silo.silo_type == null || silo.silo_type === type.id);
  const changed = target !== (type.default_silo ? String(type.default_silo) : "");

  async function save() {
    setBusy(true);
    setError("");
    try {
      await api.patch(`/grain/silo-types/${type.id}/`, {
        default_silo: target ? Number(target) : null,
      });
      onDone();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border bg-[var(--card)] p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span
            className="mt-0.5 size-10 shrink-0 rounded-xl border-4 border-white shadow-md"
            style={{ backgroundColor: type.color }}
          />
          <div className="min-w-0">
            <div className="truncate font-semibold">{type.name}</div>
            <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">{routeLabel(type)}</div>
            {type.description && <div className="mt-1 text-xs text-[var(--muted-foreground)]">{type.description}</div>}
          </div>
        </div>
        <Badge tone={type.default_silo ? "success" : "warning"}>
          {type.default_silo ? "маршрут задан" : "без маршрута"}
        </Badge>
      </div>
      <div className="mt-4 grid gap-2 sm:grid-cols-[1fr_auto]">
        <div className="flex flex-col gap-1.5">
          <Label>Основной силос прихода</Label>
          <Select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Не назначен</option>
            {available.map((silo) => (
              <option key={silo.id} value={silo.id}>
                {silo.name} · свободно {formatKg(silo.free_capacity_kg)}
              </option>
            ))}
          </Select>
        </div>
        <Button
          className="self-end"
          variant={changed ? "default" : "outline"}
          disabled={busy || !changed}
          onClick={() => void save()}
        >
          {busy ? "Сохранение…" : "Сохранить"}
        </Button>
      </div>
      {available.length === 0 && (
        <p className="mt-2 text-xs text-[var(--warning)]">
          Сначала создайте свободный силос или выберите этот тип при создании силоса.
        </p>
      )}
      {error && <p className="mt-2 text-xs text-[var(--destructive)]">{error}</p>}
    </div>
  );
}

function SiloTypesModal({
  types,
  silos,
  onClose,
  onReload,
}: {
  types: GrainSiloType[];
  silos: GrainSilo[];
  onClose: () => void;
  onReload: () => void;
}) {
  return (
    <Modal
      open
      onClose={onClose}
      eyebrow="Силосный парк · маршрутизация"
      title="Типы зерна"
      description="Для каждого типа зерна укажите основной силос. Диспетчер увидит его первым при назначении вагона."
      className="max-w-4xl"
      footer={<Button onClick={onClose}>Готово</Button>}
    >
      <div className="space-y-4">
        <SiloTypeForm silos={silos} onDone={onReload} />
        <div>
          <div className="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
            Настроенные типы · {types.length}
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            {types.map((type) => (
              <TypeRouteRow key={type.id} type={type} silos={silos} onDone={onReload} />
            ))}
          </div>
          {types.length === 0 && (
            <div className="rounded-xl border border-dashed p-8 text-center text-sm text-[var(--muted-foreground)]">
              Добавьте первый тип — затем назначьте, в какой силос направлять его приходы.
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}

function AdjustForm({ silo, onDone, onCancel }: { silo: GrainSilo; onDone: () => void; onCancel: () => void }) {
  const [deltaKg, setDeltaKg] = useState("");
  const [movementType, setMovementType] = useState("adjustment");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.post(`/grain/silos/${silo.id}/adjust/`, {
        delta_kg: Number(deltaKg),
        movement_type: movementType,
        note,
      });
      onDone();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-[var(--muted-foreground)]">
        Остаток меняется только отдельной операцией — она попадает в историю движений.
      </p>
      <div className="flex flex-col gap-1.5">
        <Label>Тип операции</Label>
        <Select value={movementType} onChange={(e) => setMovementType(e.target.value)}>
          <option value="adjustment">Корректировка</option>
          <option value="inventory_correction">Инвентаризация</option>
          <option value="expense">Расход</option>
          <option value="transfer_in">Перемещение (в)</option>
          <option value="transfer_out">Перемещение (из)</option>
        </Select>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>Изменение, кг (± целое) *</Label>
        <Input type="number" value={deltaKg} onChange={(e) => setDeltaKg(e.target.value)} placeholder="-1500" />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>Причина *</Label>
        <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="акт инвентаризации №…" />
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2 border-t pt-3">
        <Button variant="outline" disabled={busy} onClick={onCancel}>
          Отмена
        </Button>
        <Button disabled={busy || !deltaKg || !note.trim()} onClick={() => void submit()}>
          {busy ? "Проведение…" : "Провести операцию"}
        </Button>
      </div>
    </div>
  );
}

function MovementsModal({ silo, onClose }: { silo: GrainSilo; onClose: () => void }) {
  const movements = usePagedApi<GrainMovement>(`/grain/silos/${silo.id}/movements/`, 50);
  return (
    <Modal
      open
      onClose={onClose}
      eyebrow={`Силос «${silo.name}»`}
      title="История движений"
      className="max-w-2xl"
      footer={<Button onClick={onClose}>Закрыть</Button>}
    >
      {movements.error && <ErrorAlert message={movements.error} onRetry={() => void movements.reload()} />}
      <Table>
        <THead>
          <TR>
            <TH>Дата</TH>
            <TH>Операция</TH>
            <TH className="text-right">Изменение</TH>
            <TH className="text-right">Остаток</TH>
            <TH>Основание</TH>
          </TR>
        </THead>
        <TBody>
          {movements.items.length === 0 ? (
            <TR>
              <TD colSpan={5} className="py-8 text-center text-sm text-[var(--muted-foreground)]">
                Движений пока нет.
              </TD>
            </TR>
          ) : (
            movements.items.map((movement) => (
              <TR key={movement.id}>
                <TD className="tabular-nums">{formatDateTime(movement.created_at)}</TD>
                <TD>{GRAIN_MOVEMENT_LABELS[movement.movement_type] ?? movement.movement_type}</TD>
                <TD className="text-right font-medium tabular-nums">
                  {movement.delta_kg > 0 ? "+" : ""}
                  {formatKg(movement.delta_kg)}
                </TD>
                <TD className="text-right tabular-nums">{formatKg(movement.balance_after_kg)}</TD>
                <TD className="max-w-[220px] truncate text-xs text-[var(--muted-foreground)]">
                  {movement.wagon_number ? `Вагон ${movement.wagon_number} · ` : ""}
                  {movement.note}
                </TD>
              </TR>
            ))
          )}
        </TBody>
      </Table>
      <LoadMore
        shown={movements.items.length}
        total={movements.count}
        hasMore={movements.hasMore}
        loading={movements.loadingMore}
        onClick={movements.loadMore}
      />
    </Modal>
  );
}

function SiloCard({
  silo,
  canAdjust,
  canOpenWagons,
  onAdjust,
  onMovements,
}: {
  silo: GrainSilo;
  canAdjust: boolean;
  canOpenWagons: boolean;
  onAdjust: () => void;
  onMovements: () => void;
}) {
  return (
    <Card className="flex flex-col overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-2 border-b px-5 py-4">
        <div>
          <h2 className="text-base font-semibold">{silo.name}</h2>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            {silo.silo_type_name || "Тип зерна не назначен"}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {silo.is_default_route && <Badge tone="success">Основной приход</Badge>}
          {silo.is_quarantine && <Badge tone="destructive">Карантин</Badge>}
          {silo.status !== "active" && (
            <Badge tone="warning">{silo.status === "blocked" ? "Заблокирован" : "Обслуживание"}</Badge>
          )}
          {silo.status === "active" && !silo.is_quarantine && (
            <Badge tone="muted">{silo.current_balance_kg ? "В хранении" : "Пустой"}</Badge>
          )}
        </div>
      </div>
      <div className="flex flex-1 flex-col p-5">
        <div className="flex items-center gap-3 sm:gap-5">
          <SiloTank silo={silo} />
          <dl className="grid min-w-0 flex-1 grid-cols-2 gap-x-4 gap-y-4 text-sm">
            <div>
              <dt className="text-[var(--muted-foreground)]">В хранении</dt>
              <dd className="mt-1 text-lg font-semibold tabular-nums">{formatKg(silo.current_balance_kg)}</dd>
            </div>
            <div>
              <dt className="text-[var(--muted-foreground)]">Свободно</dt>
              <dd className="mt-1 text-lg font-semibold tabular-nums text-[var(--success)]">
                {formatKg(silo.free_capacity_kg)}
              </dd>
            </div>
            <div>
              <dt className="text-[var(--muted-foreground)]">Вместимость</dt>
              <dd className="mt-1 font-medium tabular-nums">{formatKg(silo.total_capacity_kg)}</dd>
            </div>
            <div>
              <dt className="text-[var(--muted-foreground)]">Резерв под приход</dt>
              <dd className="mt-1 font-medium tabular-nums">{formatKg(silo.reserved_kg)}</dd>
            </div>
          </dl>
        </div>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--muted-foreground)]">
          {silo.unloading_line && (
            <span className="inline-flex items-center gap-1">
              <Route className="size-3" />
              {silo.unloading_line}
            </span>
          )}
          <span>{silo.allow_mixing ? "Смешивание разрешено" : "Без смешивания зерна"}</span>
        </div>
        {silo.sensor_difference_kg != null && Math.abs(silo.sensor_difference_kg) > 0 && (
          <p className="mt-3 text-xs text-[var(--warning)]">
            Расхождение с датчиком: {formatKg(silo.sensor_difference_kg)}
          </p>
        )}
        {silo.active_wagons.length > 0 && (
          <div className="mt-3 rounded-xl border border-[#315d74]/20 bg-[#315d74]/7 p-3">
            <div className="mb-2 flex items-center gap-2 text-xs font-medium text-[#315d74]">
              <Activity className="size-4" />
              Вагоны в работе
            </div>
            <div className="flex flex-wrap gap-1.5">
              {silo.active_wagons.map((wagon) =>
                canOpenWagons ? (
                  <Link
                    key={wagon.id}
                    href={`/grain/wagons/${wagon.id}`}
                    className="rounded-full border border-[#315d74]/25 bg-[var(--card)] px-2.5 py-1 text-xs font-semibold text-[#315d74] transition-transform hover:-translate-y-0.5"
                  >
                    {wagon.number || `#${wagon.id}`}
                  </Link>
                ) : (
                  <span
                    key={wagon.id}
                    className="rounded-full border border-[#315d74]/25 bg-[var(--card)] px-2.5 py-1 text-xs font-semibold text-[#315d74]"
                  >
                    {wagon.number || `#${wagon.id}`}
                  </span>
                ),
              )}
            </div>
          </div>
        )}
      </div>
      <div className="flex flex-wrap justify-end gap-2 border-t px-5 py-3">
        <Button size="sm" variant="ghost" onClick={onMovements}>
          <History className="size-4" />
          Движения
        </Button>
        {canAdjust && (
          <Button size="sm" variant="outline" onClick={onAdjust}>
            <Gauge className="size-4" />
            Корректировка
          </Button>
        )}
      </div>
    </Card>
  );
}

function SilosPageInner() {
  const { me } = useAuth();
  const canAdmin = can(me, "grain.admin");
  const canAdjust = can(me, "grain.inventory");
  const { data: silos, error, reload } = useApi<GrainSilo[]>("/grain/silos/");
  const { data: types, error: typesError, reload: reloadTypes } = useApi<GrainSiloType[]>("/grain/silo-types/");
  const [createOpen, setCreateOpen] = useState(false);
  const [typesOpen, setTypesOpen] = useState(false);
  const [adjustFor, setAdjustFor] = useState<GrainSilo | null>(null);
  const [movementsFor, setMovementsFor] = useState<GrainSilo | null>(null);

  const siloRows = silos ?? [];
  const typeRows = types ?? [];
  const totalCapacity = siloRows.reduce((sum, silo) => sum + silo.total_capacity_kg, 0);
  const totalBalance = siloRows.reduce((sum, silo) => sum + silo.current_balance_kg, 0);
  const totalReserved = siloRows.reduce((sum, silo) => sum + silo.reserved_kg, 0);
  const totalFree = siloRows.reduce((sum, silo) => sum + silo.free_capacity_kg, 0);
  const configuredRoutes = typeRows.filter((type) => type.default_silo).length;

  function reloadAll() {
    void reload();
    void reloadTypes();
  }

  return (
    <AppShell
      title="Силосы"
      section="Работа"
      description="Живая схема хранения, резервы под вагоны и маршруты прихода."
      actions={
        <div className="flex flex-wrap items-center gap-2">
          {canAdmin && (
            <>
              <Button
                size="sm"
                variant="outline"
                aria-label="Типы зерна"
                title="Типы зерна"
                onClick={() => setTypesOpen(true)}
              >
                <Settings2 className="size-4" /> <span className="hidden 2xl:inline">Типы зерна</span>
              </Button>
              <Button size="sm" onClick={() => setCreateOpen(true)}>
                <Plus className="size-4" /> Новый силос
              </Button>
            </>
          )}
        </div>
      }
    >
      {(error || typesError) && <ErrorAlert message={error || typesError || ""} onRetry={reloadAll} />}

      <section className="mb-5">
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          <SummaryMetric
            icon={Warehouse}
            label="Общая ёмкость"
            value={formatKg(totalCapacity)}
            note={`${siloRows.length} силосов`}
          />
          <SummaryMetric
            icon={Sprout}
            label="В хранении"
            value={formatKg(totalBalance)}
            note={`${totalCapacity ? Math.round((totalBalance / totalCapacity) * 100) : 0}% общей ёмкости`}
            tone="grain"
          />
          <SummaryMetric
            icon={Gauge}
            label="Свободно"
            value={formatKg(totalFree)}
            note={`резерв ${formatKg(totalReserved)}`}
            tone="green"
          />
          <SummaryMetric
            icon={Route}
            label="Маршруты"
            value={`${configuredRoutes} / ${typeRows.length}`}
            note="типов зерна с маршрутом"
            tone="blue"
          />
        </div>
        <p className="mt-2.5 flex items-center gap-1.5 px-1 text-xs text-[var(--muted-foreground)]">
          <ShieldAlert className="size-3.5 shrink-0 text-[#a66a20]" />
          Свободное место указано с учётом резерва под приход. Для изменения остатка используйте «Корректировку».
        </p>
      </section>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {siloRows.map((silo) => (
          <SiloCard
            key={silo.id}
            silo={silo}
            canAdjust={canAdjust}
            canOpenWagons={can(me, "grain.view")}
            onAdjust={() => setAdjustFor(silo)}
            onMovements={() => setMovementsFor(silo)}
          />
        ))}
        {siloRows.length === 0 && !error && (
          <Card className="xl:col-span-2">
            <CardContent className="py-14 text-center">
              <Warehouse className="mx-auto size-12 text-[var(--muted-foreground)]/40" />
              <div className="mt-3 font-semibold">Силосный парк пуст</div>
              <div className="mt-1 text-sm text-[var(--muted-foreground)]">
                {canAdmin ? "Добавьте тип зерна и создайте первый силос." : "Силосы пока не настроены."}
              </div>
            </CardContent>
          </Card>
        )}
      </div>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} eyebrow="Силосный парк" title="Новый силос">
        {createOpen && (
          <SiloForm
            types={typeRows}
            onCancel={() => setCreateOpen(false)}
            onDone={() => {
              setCreateOpen(false);
              reloadAll();
            }}
          />
        )}
      </Modal>

      {typesOpen && (
        <SiloTypesModal types={typeRows} silos={siloRows} onClose={() => setTypesOpen(false)} onReload={reloadAll} />
      )}

      <Modal
        open={!!adjustFor}
        onClose={() => setAdjustFor(null)}
        eyebrow={adjustFor ? `Силос «${adjustFor.name}»` : ""}
        title="Корректировка остатка"
      >
        {adjustFor && (
          <AdjustForm
            silo={adjustFor}
            onCancel={() => setAdjustFor(null)}
            onDone={() => {
              setAdjustFor(null);
              void reload();
            }}
          />
        )}
      </Modal>
      {movementsFor && <MovementsModal silo={movementsFor} onClose={() => setMovementsFor(null)} />}
    </AppShell>
  );
}

export default function FactorySilosPage() {
  return (
    <RequirePerm perm="silos.view" title="Силосы">
      <SilosPageInner />
    </RequirePerm>
  );
}
