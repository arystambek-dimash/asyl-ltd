"use client";

import { useId, useState } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowDownToLine,
  Boxes,
  Gauge,
  History,
  Plus,
  Route,
  Settings2,
  ShieldAlert,
  Sprout,
  Warehouse,
} from "lucide-react";
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
    <div className="flex min-w-0 items-center gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className={cn("flex size-10 shrink-0 items-center justify-center rounded-xl", tones[tone])}>
        <Icon className="size-5" />
      </div>
      <div className="min-w-0">
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">{label}</div>
        <div className="mt-0.5 truncate text-xl font-bold tabular-nums text-slate-900">{value}</div>
        <div className="truncate text-xs text-slate-400">{note}</div>
      </div>
    </div>
  );
}

function SiloTank({ silo }: { silo: GrainSilo }) {
  const rawId = useId();
  const svgId = rawId.replace(/:/g, "");
  const fill = clampPercent(silo.fill_percent);
  const reserve = clampPercent((silo.reserved_kg / Math.max(1, silo.total_capacity_kg)) * 100);
  const tankTop = 72;
  const tankBottom = 326;
  const tankHeight = tankBottom - tankTop;
  const fillY = tankBottom - (tankHeight * fill) / 100;
  const reserveHeight = (tankHeight * reserve) / 100;
  const reserveY = Math.max(tankTop, fillY - reserveHeight);
  const color = silo.silo_type_color || (silo.is_quarantine ? "#B8463B" : DEFAULT_TYPE_COLOR);
  const inactive = silo.status !== "active";

  return (
    <div className="relative mx-auto w-full max-w-[210px]">
      {silo.is_default_route && (
        <div className="absolute left-1/2 top-2 z-10 flex -translate-x-1/2 items-center gap-1.5 whitespace-nowrap rounded-full bg-[#173947] px-3 py-1.5 text-[11px] font-bold uppercase tracking-[0.12em] text-white shadow-lg">
          <ArrowDownToLine className="size-3.5" />
          Приход сюда
        </div>
      )}
      <svg
        viewBox="0 0 320 390"
        role="img"
        aria-label={`${silo.name}: заполнено ${fill}%`}
        className="h-auto w-full drop-shadow-[0_24px_24px_rgba(15,23,42,0.18)]"
      >
        <defs>
          <clipPath id={`tank-${svgId}`}>
            <path d="M54 91 82 40h156l28 51v211l-71 56h-70l-71-56Z" />
          </clipPath>
          <linearGradient id={`steel-${svgId}`} x1="0" x2="1">
            <stop offset="0" stopColor="#d3d7d8" />
            <stop offset=".18" stopColor="#f4f5f2" />
            <stop offset=".5" stopColor="#c8cdce" />
            <stop offset=".78" stopColor="#f7f7f4" />
            <stop offset="1" stopColor="#afb6b8" />
          </linearGradient>
          <linearGradient id={`grain-${svgId}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={color} stopOpacity=".76" />
            <stop offset="1" stopColor={color} />
          </linearGradient>
          <pattern
            id={`reserve-${svgId}`}
            width="9"
            height="9"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <rect width="4" height="9" fill="#f1b447" fillOpacity=".88" />
          </pattern>
          <pattern id={`grid-${svgId}`} width="18" height="18" patternUnits="userSpaceOnUse">
            <path d="M18 0H0v18" fill="none" stroke="#64748b" strokeOpacity=".13" strokeWidth="1" />
          </pattern>
        </defs>

        {silo.is_default_route && (
          <g className="animate-pulse">
            <path d="M160 0v31" stroke="#173947" strokeWidth="5" strokeLinecap="round" />
            <path d="m150 21 10 10 10-10" fill="none" stroke="#173947" strokeWidth="5" strokeLinecap="round" />
          </g>
        )}

        <path
          d="M54 91 82 40h156l28 51v211l-71 56h-70l-71-56Z"
          fill={`url(#steel-${svgId})`}
          stroke="#667174"
          strokeWidth="3"
          opacity={inactive ? 0.58 : 1}
        />
        <g clipPath={`url(#tank-${svgId})`} opacity={inactive ? 0.55 : 1}>
          <rect x="44" y={fillY} width="232" height={tankBottom - fillY + 40} fill={`url(#grain-${svgId})`} />
          {reserveHeight > 0 && (
            <rect x="44" y={reserveY} width="232" height={fillY - reserveY} fill={`url(#reserve-${svgId})`} />
          )}
          <rect x="44" y="40" width="232" height="318" fill={`url(#grid-${svgId})`} />
        </g>

        <path
          d="M54 91h212M54 132h212M54 176h212M54 220h212M54 264h212M82 40l-28 51m184-51 28 51"
          fill="none"
          stroke="#758083"
          strokeOpacity=".52"
          strokeWidth="1.4"
        />
        <path d="M91 45v266M229 45v266" stroke="white" strokeOpacity=".43" strokeWidth="3" />
        <path d="M125 358v17m70-17v17M105 375h110" fill="none" stroke="#667174" strokeWidth="4" strokeLinecap="round" />

        <g transform="translate(270 92)">
          <path d="M0 0v218" stroke="#596568" strokeWidth="3" />
          {Array.from({ length: 12 }).map((_, index) => (
            <path key={index} d={`M0 ${index * 18 + 4}h18`} stroke="#596568" strokeWidth="2" />
          ))}
          <path d="M18 0v218" stroke="#596568" strokeWidth="3" />
        </g>

        <rect
          x="92"
          y="100"
          width="136"
          height="72"
          rx="10"
          fill="#152b32"
          fillOpacity=".9"
          stroke="white"
          strokeOpacity=".16"
        />
        <text x="160" y="125" textAnchor="middle" fill="#aebbc0" fontSize="10" fontWeight="700" letterSpacing="2">
          УРОВЕНЬ
        </text>
        <text x="160" y="154" textAnchor="middle" fill="white" fontSize="30" fontWeight="800">
          {fill}%
        </text>
        <circle cx="77" cy="113" r="5" fill={inactive ? "#d8a443" : "#55b977"} stroke="white" strokeWidth="2" />

        {silo.sensor_difference_kg != null && Math.abs(silo.sensor_difference_kg) > 0 && (
          <g transform="translate(78 284)">
            <rect width="164" height="32" rx="8" fill="#fff" fillOpacity=".88" />
            <text x="82" y="21" textAnchor="middle" fill="#475569" fontSize="11" fontWeight="700">
              ДАТЧИК Δ {formatKg(silo.sensor_difference_kg)}
            </text>
          </g>
        )}
      </svg>
      <div className="pointer-events-none absolute inset-x-[17%] bottom-[11%] h-4 rounded-[50%] bg-slate-950/20 blur-md" />
    </div>
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
  index,
  onAdjust,
  onMovements,
}: {
  silo: GrainSilo;
  canAdjust: boolean;
  canOpenWagons: boolean;
  index: number;
  onAdjust: () => void;
  onMovements: () => void;
}) {
  const grainDescription = silo.silo_type_name || "Тип зерна не назначен";

  return (
    <Card
      className="group relative overflow-hidden border-slate-200/80 bg-[#f7f5ef] shadow-[0_16px_44px_rgba(39,50,54,0.08)] animate-fade-up"
      style={{ animationDelay: `${Math.min(index * 70, 350)}ms` }}
    >
      <div className="absolute inset-0 opacity-[0.22] [background-image:radial-gradient(#657276_0.8px,transparent_0.8px)] [background-size:16px_16px]" />
      <div className="relative grid items-center gap-4 p-4 sm:p-5 md:grid-cols-[200px_minmax(0,1fr)]">
        <SiloTank silo={silo} />
        <div className="flex min-w-0 flex-col self-stretch py-1">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="truncate text-lg font-black tracking-[-0.025em] text-[#253136]">{silo.name}</h2>
              <div className="mt-0.5 flex items-center gap-1.5 text-sm text-[#6d777a]">
                <Sprout className="size-4" />
                {grainDescription}
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {silo.is_default_route && <Badge tone="success">основной приход</Badge>}
              {silo.is_quarantine && <Badge tone="destructive">карантин</Badge>}
              {silo.status !== "active" && (
                <Badge tone="warning">{silo.status === "blocked" ? "заблокирован" : "обслуживание"}</Badge>
              )}
            </div>
          </div>

          <div className="my-4 grid grid-cols-2 gap-px overflow-hidden rounded-xl border bg-slate-200">
            <div className="bg-white/85 p-2.5">
              <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">В силосе</div>
              <div className="mt-0.5 text-[15px] font-bold tabular-nums text-slate-900">
                {formatKg(silo.current_balance_kg)}
              </div>
            </div>
            <div className="bg-white/85 p-2.5">
              <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">Вместимость</div>
              <div className="mt-0.5 text-[15px] font-bold tabular-nums text-slate-900">
                {formatKg(silo.total_capacity_kg)}
              </div>
            </div>
            <div className="bg-white/85 p-2.5">
              <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">Резерв</div>
              <div className="mt-0.5 text-[15px] font-bold tabular-nums text-[#a66a20]">
                {formatKg(silo.reserved_kg)}
              </div>
            </div>
            <div className="bg-white/85 p-2.5">
              <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">Свободно</div>
              <div className="mt-0.5 text-[15px] font-bold tabular-nums text-[#356f48]">
                {formatKg(silo.free_capacity_kg)}
              </div>
            </div>
          </div>

          <div className="flex flex-wrap gap-1.5 text-xs">
            {silo.unloading_line && (
              <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-300/70 bg-white/70 px-2.5 py-1 font-medium text-slate-600">
                <Route className="size-3.5" /> {silo.unloading_line}
              </span>
            )}
            <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-300/70 bg-white/70 px-2.5 py-1 font-medium text-slate-600">
              <Boxes className="size-3.5" /> смешивание {silo.allow_mixing ? "разрешено" : "запрещено"}
            </span>
          </div>

          {silo.active_wagons.length > 0 && (
            <div className="mt-3 rounded-xl border border-[#315d74]/20 bg-[#315d74]/7 p-3">
              <div className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.12em] text-[#315d74]">
                <Activity className="size-4" />
                Вагоны в работе
              </div>
              <div className="flex flex-wrap gap-1.5">
                {silo.active_wagons.map((wagon) =>
                  canOpenWagons ? (
                    <Link
                      key={wagon.id}
                      href={`/grain/wagons/${wagon.id}`}
                      className="rounded-full border border-[#315d74]/25 bg-white px-2.5 py-1 text-xs font-semibold text-[#315d74] transition-transform hover:-translate-y-0.5"
                    >
                      {wagon.number || `#${wagon.id}`}
                    </Link>
                  ) : (
                    <span
                      key={wagon.id}
                      className="rounded-full border border-[#315d74]/25 bg-white px-2.5 py-1 text-xs font-semibold text-[#315d74]"
                    >
                      {wagon.number || `#${wagon.id}`}
                    </span>
                  ),
                )}
              </div>
            </div>
          )}

          <div className="mt-auto flex flex-wrap justify-end gap-2 border-t border-slate-300/70 pt-3">
            <Button size="sm" variant="ghost" onClick={onMovements}>
              <History className="size-4" /> Движения
            </Button>
            {canAdjust && (
              <Button size="sm" variant="outline" onClick={onAdjust}>
                <Gauge className="size-4" /> Корректировка
              </Button>
            )}
          </div>
        </div>
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
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
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
            note={`${totalCapacity ? Math.round((totalBalance / totalCapacity) * 100) : 0}% парка`}
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
            note="типов направлено"
            tone="blue"
          />
        </div>
        <p className="mt-2.5 flex items-center gap-1.5 px-1 text-xs text-[var(--muted-foreground)]">
          <ShieldAlert className="size-3.5 shrink-0 text-[#a66a20]" />
          Остаток рассчитывается по неизменяемому журналу движений — правки только через «Корректировку».
        </p>
      </section>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {siloRows.map((silo, index) => (
          <SiloCard
            key={silo.id}
            silo={silo}
            canAdjust={canAdjust}
            canOpenWagons={can(me, "grain.view")}
            index={index}
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
