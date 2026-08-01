"use client";

import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { useRouter } from "next/navigation";
import {
  Boxes,
  Camera,
  Check,
  Copy,
  LayoutTemplate,
  Map as MapIcon,
  PencilRuler,
  Save,
  Sprout,
  TrainFront,
  Trash2,
  Videotape,
  X,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { CameraStream } from "@/components/camera-stream";
import { FactoryTabs } from "@/components/factory-tabs";
import { ZoneAsset, type FactoryLive } from "@/components/factory-map/assets";
import { FactoryMapDefs } from "@/components/factory-map/assets/defs";
import { WagonArt } from "@/components/factory-map/assets/wagon";
import { CATEGORIES, KIND_ROWS, KINDS, type ZoneCategory } from "@/components/factory-map/kinds";
import { MAP_PALETTE as P } from "@/components/factory-map/palette";
import { PRESET_TITLE, PRESET_ZONES } from "@/components/factory-map/preset";
import { RequirePerm } from "@/components/require-perm";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { formatKg } from "@/lib/grain";
import { showSuccess } from "@/lib/toast";
import type {
  FactoryMap as FactoryMapData,
  FactoryZone,
  FactoryZoneKind,
  GrainSilo,
  GrainWagon,
  Me,
  StockItem,
} from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { cn, formatDateTime } from "@/lib/utils";
import { useAuth } from "@/store/auth";

const MAP_WIDTH = 1200;
const MAP_HEIGHT = 680;
const GRID = 10;
const MIN_ZONE_SIZE = 48;
const LIVE_REFRESH_MS = 20_000;

type Point = { x: number; y: number };
type Interaction =
  | { mode: "move"; zoneId: string; start: Point; original: FactoryZone }
  | { mode: "resize"; zoneId: string; start: Point; original: FactoryZone };

type FloatCard = { x: number; y: number };
type TipState = FloatCard & ({ kind: "zone"; zone: FactoryZone } | { kind: "wagon"; wagon: GrainWagon });
type CamPreview = FloatCard & { zone: FactoryZone };

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function snap(value: number) {
  return Math.round(value / GRID) * GRID;
}

function newZoneId() {
  return `zone-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

function copyZones(zones: FactoryZone[]) {
  return zones.map((zone) => ({ ...zone }));
}

function formatTons(kg: number) {
  return `${Math.round(kg / 1000).toLocaleString("ru-RU")} т`;
}

/** Куда ведёт клик по участку в режиме просмотра. */
function zoneLink(zone: FactoryZone, me: Me | null): string | null {
  switch (zone.kind) {
    case "silos":
      return can(me, "grain.view") ? "/warehouse/silos" : null;
    case "warehouse":
      return can(me, "warehouse.view") ? "/warehouse" : null;
    case "gate":
      return can(me, "grain.view") ? "/grain" : null;
    case "dock":
      return can(me, "shipping.view") || can(me, "train.view") ? "/shipping" : null;
    default:
      return null;
  }
}

/** Живые данные карты: только те источники, на которые есть права. */
function useFactoryLive(me: Me | null, paused: boolean) {
  const canGrain = can(me, "grain.view");
  const canStock = can(me, "warehouse.view");
  const silosApi = useApi<GrainSilo[]>(canGrain ? "/grain/silos/" : null);
  const stockApi = useApi<StockItem[]>(canStock ? "/stock/" : null);
  const wagonsApi = usePagedApi<GrainWagon>(canGrain ? "/grain/wagons/?scope=on_site" : null, 10);

  const reloadSilos = silosApi.reload;
  const reloadStock = stockApi.reload;
  const reloadWagons = wagonsApi.reload;
  useEffect(() => {
    if (paused) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      void reloadSilos();
      void reloadStock();
      void reloadWagons();
    }, LIVE_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [paused, reloadSilos, reloadStock, reloadWagons]);

  const live: FactoryLive = {
    silos: silosApi.data,
    stock: stockApi.data,
    wagons: canGrain ? wagonsApi.items : null,
  };
  return { live, wagonCount: wagonsApi.count };
}

function KpiTile({
  icon: Icon,
  label,
  value,
  sub,
  barPct,
  barColor,
}: {
  icon: typeof Sprout;
  label: string;
  value: string;
  sub: string;
  barPct?: number;
  barColor?: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between text-[13px] text-slate-500">
        {label}
        <Icon className="size-4 text-slate-400" />
      </div>
      <div className="mt-1.5 text-2xl font-bold tabular-nums tracking-[-0.02em]">{value}</div>
      <div className="mt-0.5 truncate text-xs text-slate-400">{sub}</div>
      {barPct != null && (
        <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-slate-100">
          <div
            className="h-full rounded-full transition-[width] duration-700"
            style={{ width: `${clamp(barPct, 0, 100)}%`, backgroundColor: barColor ?? "#2563eb" }}
          />
        </div>
      )}
    </div>
  );
}

/** Подложка сцены: трава, ж/д ветка, кольцевая и внутренняя дороги. */
function SceneGround() {
  return (
    <g>
      <rect width={MAP_WIDTH} height={MAP_HEIGHT} fill={P.grass} />
      <g opacity=".5" fill={P.grassDark}>
        <circle cx="150" cy="300" r="30" />
        <circle cx="200" cy="318" r="18" />
        <circle cx="660" cy="640" r="34" />
        <circle cx="722" cy="656" r="20" />
        <circle cx="580" cy="86" r="20" />
        <circle cx="56" cy="600" r="24" />
        <circle cx="1120" cy="640" r="26" />
      </g>
      <g>
        <rect y="30" width={MAP_WIDTH} height="36" fill={P.grassDark} opacity=".6" />
        <g stroke={P.asphaltDark} strokeWidth="2" opacity=".7">
          {Array.from({ length: Math.floor(MAP_WIDTH / 24) }).map((_, index) => (
            <line key={index} x1={8 + index * 24} y1="34" x2={8 + index * 24} y2="62" />
          ))}
        </g>
        <g stroke={P.asphaltDark} strokeWidth="3">
          <line y1="38" x2={MAP_WIDTH} y2="38" />
          <line y1="58" x2={MAP_WIDTH} y2="58" />
        </g>
        <text x="16" y="22" fontSize="10.5" fill={P.textMuted}>
          Ж/Д ветка · подача вагонов
        </text>
      </g>
      <rect x="36" y="96" width="1128" height="548" rx="46" fill="none" stroke={P.asphalt} strokeWidth="30" />
      <rect
        x="36"
        y="96"
        width="1128"
        height="548"
        rx="46"
        fill="none"
        stroke={P.roadLine}
        strokeOpacity=".55"
        strokeWidth="2.5"
        strokeDasharray="16 14"
      />
      <path d="M 52 340 H 846" stroke={P.asphalt} strokeWidth="46" strokeLinecap="round" fill="none" />
      <path
        d="M 70 340 H 828"
        stroke={P.roadLine}
        strokeOpacity=".5"
        strokeWidth="2.5"
        strokeDasharray="16 14"
        fill="none"
      />
    </g>
  );
}

function zoneRenderOrder(zone: FactoryZone) {
  if (zone.kind === "rail" || zone.kind === "conveyor") return 0;
  if (zone.kind === "camera") return 2;
  return 1;
}

function FactoryCanvas({
  zones,
  live,
  me,
  editing,
  selectedId,
  onSelect,
  onChange,
  onHoverZone,
  onHoverWagon,
  onHoverEnd,
}: {
  zones: FactoryZone[];
  live: FactoryLive;
  me: Me | null;
  editing: boolean;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onChange: (id: string, patch: Partial<FactoryZone>) => void;
  onHoverZone: (zone: FactoryZone, event: ReactPointerEvent<SVGGElement>) => void;
  onHoverWagon: (wagon: GrainWagon, event: ReactPointerEvent<SVGGElement>) => void;
  onHoverEnd: () => void;
}) {
  const router = useRouter();
  const svgRef = useRef<SVGSVGElement>(null);
  const [interaction, setInteraction] = useState<Interaction | null>(null);

  function pointFromEvent(event: ReactPointerEvent<SVGElement>): Point {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return {
      x: clamp(((event.clientX - rect.left) / rect.width) * MAP_WIDTH, 0, MAP_WIDTH),
      y: clamp(((event.clientY - rect.top) / rect.height) * MAP_HEIGHT, 0, MAP_HEIGHT),
    };
  }

  function beginMove(event: ReactPointerEvent<SVGGElement>, zone: FactoryZone) {
    event.stopPropagation();
    onSelect(zone.id);
    if (!editing || event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    setInteraction({ mode: "move", zoneId: zone.id, start: pointFromEvent(event), original: { ...zone } });
  }

  function beginResize(event: ReactPointerEvent<SVGRectElement>, zone: FactoryZone) {
    event.stopPropagation();
    if (!editing) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    setInteraction({ mode: "resize", zoneId: zone.id, start: pointFromEvent(event), original: { ...zone } });
  }

  function handlePointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    if (!interaction) return;
    const point = pointFromEvent(event);
    const dx = point.x - interaction.start.x;
    const dy = point.y - interaction.start.y;
    if (interaction.mode === "move") {
      onChange(interaction.zoneId, {
        x: clamp(snap(interaction.original.x + dx), 0, MAP_WIDTH - interaction.original.width),
        y: clamp(snap(interaction.original.y + dy), 0, MAP_HEIGHT - interaction.original.height),
      });
      return;
    }
    onChange(interaction.zoneId, {
      width: clamp(snap(interaction.original.width + dx), MIN_ZONE_SIZE, MAP_WIDTH - interaction.original.x),
      height: clamp(snap(interaction.original.height + dy), MIN_ZONE_SIZE, MAP_HEIGHT - interaction.original.y),
    });
  }

  const orderedZones = [...zones].sort((a, b) => zoneRenderOrder(a) - zoneRenderOrder(b));
  const wagons = (live.wagons ?? []).slice(0, 3);

  return (
    <div className="overflow-x-auto">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
        role="img"
        aria-label="Живая схема территории"
        className="block min-w-[900px] touch-none select-none"
        onPointerDown={(event) => {
          if (event.target === event.currentTarget) onSelect(null);
        }}
        onPointerMove={handlePointerMove}
        onPointerUp={() => setInteraction(null)}
        onPointerCancel={() => setInteraction(null)}
      >
        <FactoryMapDefs />
        <SceneGround />
        {editing && (
          <g pointerEvents="none">
            <rect width={MAP_WIDTH} height={MAP_HEIGHT} fill="url(#fm-edit-grid)" />
            <defs>
              <pattern id="fm-edit-grid" width="50" height="50" patternUnits="userSpaceOnUse">
                <path d="M50 0H0V50" fill="none" stroke="#1c1f24" strokeOpacity=".07" strokeWidth="1" />
              </pattern>
            </defs>
          </g>
        )}

        {orderedZones.map((zone) => {
          const selected = selectedId === zone.id;
          const link = editing ? null : zoneLink(zone, me);
          return (
            <g
              key={zone.id}
              transform={`translate(${zone.x} ${zone.y})`}
              className={cn(editing ? "cursor-move" : link ? "cursor-pointer" : "cursor-default")}
              onPointerDown={(event) => beginMove(event, zone)}
              onPointerMove={(event) => {
                if (!editing && !interaction) onHoverZone(zone, event);
              }}
              onPointerLeave={onHoverEnd}
              onClick={(event) => {
                event.stopPropagation();
                if (editing) onSelect(zone.id);
                else if (link) router.push(link);
              }}
            >
              {/* прозрачная подложка: ховер и перетаскивание по всему боксу */}
              <rect width={zone.width} height={zone.height} fill="transparent" />
              <ZoneAsset zone={zone} live={live} />
              {editing && (
                <rect
                  x="-4"
                  y="-4"
                  width={zone.width + 8}
                  height={zone.height + 8}
                  rx="12"
                  fill="none"
                  stroke={selected ? "#F4C86A" : "#94a3b8"}
                  strokeOpacity={selected ? 1 : 0.5}
                  strokeWidth={selected ? 2.5 : 1.25}
                  strokeDasharray="8 6"
                  pointerEvents="none"
                />
              )}
              {editing && selected && zone.kind !== "camera" && (
                <rect
                  x={zone.width - 12}
                  y={zone.height - 12}
                  width="18"
                  height="18"
                  rx="5"
                  fill="#F4C86A"
                  stroke="#111827"
                  strokeWidth="2"
                  className="cursor-nwse-resize"
                  onPointerDown={(event) => beginResize(event, zone)}
                />
              )}
            </g>
          );
        })}

        {/* живые вагоны на ж/д ветке */}
        {!editing &&
          wagons.map((wagon, index) => (
            <g
              key={wagon.id}
              transform={`translate(${896 - index * 236} 12)`}
              className="cursor-pointer"
              onPointerMove={(event) => onHoverWagon(wagon, event)}
              onPointerLeave={onHoverEnd}
              onClick={() => router.push(`/grain/wagons/${wagon.id}`)}
            >
              <WagonArt number={wagon.number || `#${wagon.id}`} statusLabel={wagon.status_label} />
            </g>
          ))}
        {!editing && (live.wagons?.length ?? 0) > 3 && (
          <text x="180" y="55" fontSize="11" fontWeight="650" fill={P.textMuted}>
            + ещё {(live.wagons?.length ?? 0) - 3} вагона на территории
          </text>
        )}

        {!zones.length && (
          <g transform={`translate(${MAP_WIDTH / 2} ${MAP_HEIGHT / 2})`} pointerEvents="none">
            <circle r="52" fill="#C58A35" fillOpacity=".12" stroke="#C58A35" strokeOpacity=".3" />
            <path d="M-18 14V-15l18-10 18 10v29M-26 14h52" fill="none" stroke="#a66a20" strokeWidth="3" />
            <text y="88" textAnchor="middle" fontSize="18" fontWeight="700" fill={P.text}>
              Схема пока пустая
            </text>
            <text y="112" textAnchor="middle" fontSize="12" fill={P.textMuted}>
              Суперадмин может включить редактор и расставить объекты
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}

function TooltipCard({ tip, live }: { tip: TipState; live: FactoryLive }) {
  const rows: [string, string][] = [];
  let title = "";
  let sub = "";
  let bar: number | null = null;

  if (tip.kind === "wagon") {
    const wagon = tip.wagon;
    title = `Вагон ${wagon.number || `#${wagon.id}`}`;
    sub = wagon.supplier || "приход зерна";
    rows.push(["Статус", wagon.status_label]);
    if (wagon.grain_type_name) rows.push(["Тип зерна", wagon.grain_type_name]);
    if (wagon.assigned_silo_name) rows.push(["Силос", wagon.assigned_silo_name]);
    rows.push(["Ожидается", formatKg(wagon.expected_weight_kg)]);
    if (wagon.gross_weight_kg != null) rows.push(["Входной вес", formatKg(wagon.gross_weight_kg)]);
  } else {
    const zone = tip.zone;
    const config = KINDS[zone.kind];
    title = zone.name;
    sub = zone.note || CATEGORIES[config.category].label;
    if (zone.kind === "silos") {
      const silos = live.silos ?? [];
      const totalCap = silos.reduce((sum, silo) => sum + silo.total_capacity_kg, 0);
      const totalBal = silos.reduce((sum, silo) => sum + silo.current_balance_kg, 0);
      silos.slice(0, 5).forEach((silo) => rows.push([silo.name, `${formatKg(silo.current_balance_kg)} · ${silo.fill_percent}%`]));
      if (silos.length) {
        rows.push(["Резерв под вагоны", formatKg(silos.reduce((sum, silo) => sum + silo.reserved_kg, 0))]);
        bar = totalCap ? (totalBal / totalCap) * 100 : 0;
      } else {
        rows.push(["Данные", "нет доступа или силосы не настроены"]);
      }
    } else if (zone.kind === "warehouse") {
      const stock = [...(live.stock ?? [])].sort((a, b) => b.bags - a.bags);
      stock.slice(0, 5).forEach((item) => rows.push([`${item.grade} · ${item.packaging}`, `${item.bags.toLocaleString("ru-RU")} меш.`]));
      if (stock.length) {
        rows.push(["Всего", `${stock.reduce((sum, item) => sum + item.bags, 0).toLocaleString("ru-RU")} меш.`]);
      } else {
        rows.push(["Данные", "остатков нет или нет доступа"]);
      }
    } else if (zone.kind === "camera") {
      rows.push(["Поток", zone.note.trim() ? zone.note : "не указан — задайте в редакторе"]);
    }
  }

  return (
    <div
      className="pointer-events-none fixed z-50 min-w-[220px] max-w-[300px] rounded-2xl border border-slate-200 bg-white p-4 shadow-[0_18px_45px_rgba(15,23,42,.16)]"
      style={{ left: tip.x, top: tip.y }}
    >
      <p className="text-sm font-bold leading-tight">{title}</p>
      <p className="mt-0.5 text-xs text-slate-400">{sub}</p>
      {rows.length > 0 && (
        <div className="mt-2.5 space-y-1">
          {rows.map(([label, value]) => (
            <div key={label + value} className="flex items-baseline justify-between gap-4 text-[13px]">
              <span className="text-slate-500">{label}</span>
              <span className="font-semibold tabular-nums text-slate-900">{value}</span>
            </div>
          ))}
        </div>
      )}
      {bar != null && (
        <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-slate-100">
          <div className="h-full rounded-full bg-[#d9a83f]" style={{ width: `${clamp(bar, 0, 100)}%` }} />
        </div>
      )}
    </div>
  );
}

function CameraPreviewCard({ preview }: { preview: CamPreview }) {
  const stream = preview.zone.note.trim();
  const [online, setOnline] = useState(false);

  return (
    <div
      className="pointer-events-none fixed z-50 w-[300px] overflow-hidden rounded-2xl border border-slate-700 bg-[#0b0d10] shadow-[0_20px_50px_-12px_rgba(0,0,0,.55)]"
      style={{ left: preview.x, top: preview.y }}
    >
      <div className="relative aspect-video bg-[#101418]">
        {stream ? (
          <>
            <CameraStream src={stream} onStateChange={setOnline} className="absolute inset-0 size-full object-cover" />
            {!online && (
              <div className="absolute inset-0 flex items-center justify-center text-xs font-semibold text-slate-400">
                Подключение к потоку…
              </div>
            )}
          </>
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 text-center">
            <Videotape className="size-6 text-slate-500" />
            <p className="px-6 text-xs text-slate-400">
              Источник не указан. В редакторе впишите имя потока в поле «Источник потока».
            </p>
          </div>
        )}
      </div>
      <div className="flex items-center justify-between px-3 py-2.5">
        <span className="truncate text-xs font-semibold text-slate-100">{preview.zone.name}</span>
        <span className={cn("text-[10px] font-bold", stream && online ? "text-emerald-400" : "text-amber-400")}>
          {stream ? (online ? "● ОНЛАЙН" : "● ОЖИДАНИЕ") : "НЕТ ПОТОКА"}
        </span>
      </div>
    </div>
  );
}

function ZoneInspector({
  zone,
  onChange,
  onDelete,
  onDuplicate,
}: {
  zone: FactoryZone;
  onChange: (patch: Partial<FactoryZone>) => void;
  onDelete: () => void;
  onDuplicate: () => void;
}) {
  const config = KINDS[zone.kind];
  const Icon = config.icon;
  const isCamera = zone.kind === "camera";

  return (
    <Card className="overflow-hidden border-slate-200 bg-white shadow-[0_18px_45px_rgba(15,23,42,.09)]">
      <div className="border-b border-slate-200 bg-slate-950 px-4 py-4 text-white">
        <div className="flex items-center gap-3">
          <span
            className="flex size-10 items-center justify-center rounded-xl"
            style={{ backgroundColor: `${config.color}35`, color: config.color }}
          >
            <Icon className="size-5" />
          </span>
          <div className="min-w-0">
            <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-white/40">Выбранный объект</p>
            <p className="mt-0.5 truncate text-sm font-bold">{zone.name}</p>
          </div>
        </div>
      </div>
      <div className="space-y-4 p-4">
        <div>
          <Label htmlFor="factory-zone-name">Название</Label>
          <Input
            id="factory-zone-name"
            value={zone.name}
            maxLength={80}
            onChange={(event) => onChange({ name: event.target.value })}
          />
        </div>
        <div>
          <Label htmlFor="factory-zone-kind">Тип объекта</Label>
          <Select
            id="factory-zone-kind"
            value={zone.kind}
            onChange={(event) => {
              const kind = event.target.value as FactoryZoneKind;
              onChange({ kind, color: KINDS[kind].color });
            }}
          >
            {KIND_ROWS.map(([kind, row]) => (
              <option key={kind} value={kind}>
                {row.label}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="factory-zone-note">{isCamera ? "Источник потока (go2rtc)" : "Подпись на карте"}</Label>
          <textarea
            id="factory-zone-note"
            value={zone.note}
            maxLength={160}
            rows={isCamera ? 2 : 3}
            placeholder={isCamera ? "например: gate-cam" : "короткая подпись или список через ·"}
            onChange={(event) => onChange({ note: event.target.value })}
            className="w-full resize-none rounded-md border border-[var(--input)] bg-white px-3.5 py-2 text-sm shadow-sm outline-none focus-visible:border-[var(--ring)] focus-visible:ring-2 focus-visible:ring-[var(--ring)]/20"
          />
          {isCamera && (
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              При наведении на камеру карта покажет живое превью этого потока.
            </p>
          )}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label htmlFor="factory-zone-width">Ширина</Label>
            <Input
              id="factory-zone-width"
              type="number"
              min={MIN_ZONE_SIZE}
              max={MAP_WIDTH - zone.x}
              value={zone.width}
              onChange={(event) =>
                onChange({ width: clamp(Number(event.target.value), MIN_ZONE_SIZE, MAP_WIDTH - zone.x) })
              }
            />
          </div>
          <div>
            <Label htmlFor="factory-zone-height">Высота</Label>
            <Input
              id="factory-zone-height"
              type="number"
              min={MIN_ZONE_SIZE}
              max={MAP_HEIGHT - zone.y}
              value={zone.height}
              onChange={(event) =>
                onChange({ height: clamp(Number(event.target.value), MIN_ZONE_SIZE, MAP_HEIGHT - zone.y) })
              }
            />
          </div>
        </div>
        <div className="flex gap-2 border-t border-slate-200 pt-4">
          <Button variant="outline" className="flex-1" onClick={onDuplicate}>
            <Copy className="size-4" /> Копия
          </Button>
          <Button variant="outline" className="text-red-600 hover:bg-red-50 hover:text-red-700" onClick={onDelete}>
            <Trash2 className="size-4" /> Удалить
          </Button>
        </div>
      </div>
    </Card>
  );
}

function FactoryMapPageInner() {
  const { me } = useAuth();
  const canEdit = Boolean(me?.is_superuser);
  const { data: map, loading, error, reload, setData: setMap } = useApi<FactoryMapData>("/factory/map/");
  const [zones, setZones] = useState<FactoryZone[]>([]);
  const [title, setTitle] = useState(PRESET_TITLE);
  const [editing, setEditing] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [tip, setTip] = useState<TipState | null>(null);
  const [camPreview, setCamPreview] = useState<CamPreview | null>(null);
  const { live, wagonCount } = useFactoryLive(me, editing);

  useEffect(() => {
    if (!map || editing) return;
    setZones(copyZones(map.zones));
    setTitle(map.title);
  }, [editing, map]);

  const selectedZone = zones.find((zone) => zone.id === selectedId) ?? null;

  const categories = useMemo(() => {
    const present = new Set<ZoneCategory>();
    zones.forEach((zone) => present.add(KINDS[zone.kind].category));
    return (Object.keys(CATEGORIES) as ZoneCategory[]).filter((category) => present.has(category));
  }, [zones]);

  const totals = useMemo(() => {
    const silos = live.silos ?? [];
    const stock = live.stock ?? [];
    const capacity = silos.reduce((sum, silo) => sum + silo.total_capacity_kg, 0);
    const balance = silos.reduce((sum, silo) => sum + silo.current_balance_kg, 0);
    const free = silos.reduce((sum, silo) => sum + silo.free_capacity_kg, 0);
    const bags = stock.reduce((sum, item) => sum + item.bags, 0);
    const topStock = [...stock].sort((a, b) => b.bags - a.bags)[0];
    const cameraZones = zones.filter((zone) => zone.kind === "camera");
    return {
      capacity,
      balance,
      free,
      bags,
      topStock,
      cameras: cameraZones.length,
      camerasWithStream: cameraZones.filter((zone) => zone.note.trim()).length,
    };
  }, [live.silos, live.stock, zones]);

  function updateZone(id: string, patch: Partial<FactoryZone>) {
    setZones((current) => current.map((zone) => (zone.id === id ? { ...zone, ...patch } : zone)));
  }

  function beginEditing() {
    if (!map) return;
    setZones(copyZones(map.zones));
    setTitle(map.title);
    setSelectedId(null);
    setSaveError("");
    setTip(null);
    setCamPreview(null);
    setEditing(true);
  }

  function cancelEditing() {
    if (map) {
      setZones(copyZones(map.zones));
      setTitle(map.title);
    }
    setSelectedId(null);
    setSaveError("");
    setEditing(false);
  }

  async function saveMap() {
    if (!title.trim()) {
      setSaveError("Укажите название схемы");
      return;
    }
    if (zones.some((zone) => !zone.name.trim())) {
      setSaveError("У каждого объекта должно быть название");
      return;
    }
    setSaving(true);
    setSaveError("");
    try {
      const { data } = await api.put<FactoryMapData>("/factory/map/", {
        title: title.trim(),
        zones: zones.map((zone) => ({ ...zone, name: zone.name.trim(), note: zone.note.trim() })),
      });
      setMap(data);
      setZones(copyZones(data.zones));
      setTitle(data.title);
      setSelectedId(null);
      setEditing(false);
      showSuccess("Схема территории сохранена");
    } catch (cause) {
      setSaveError(apiError(cause));
    } finally {
      setSaving(false);
    }
  }

  function addZone(kind: FactoryZoneKind) {
    const config = KINDS[kind];
    const shift = (zones.length % 5) * 20;
    const zone: FactoryZone = {
      id: newZoneId(),
      name: config.defaultName,
      kind,
      x: clamp(snap((MAP_WIDTH - config.defaultSize.width) / 2 + shift), 0, MAP_WIDTH - config.defaultSize.width),
      y: clamp(snap((MAP_HEIGHT - config.defaultSize.height) / 2 + shift), 0, MAP_HEIGHT - config.defaultSize.height),
      width: config.defaultSize.width,
      height: config.defaultSize.height,
      color: config.color,
      note: "",
    };
    setZones((current) => [...current, zone]);
    setSelectedId(zone.id);
  }

  function applyPreset() {
    setZones(copyZones(PRESET_ZONES));
    setTitle(PRESET_TITLE);
    setSelectedId(null);
  }

  function duplicateSelected() {
    if (!selectedZone) return;
    const duplicate = {
      ...selectedZone,
      id: newZoneId(),
      name: `${selectedZone.name} · копия`,
      x: clamp(snap(selectedZone.x + 30), 0, MAP_WIDTH - selectedZone.width),
      y: clamp(snap(selectedZone.y + 30), 0, MAP_HEIGHT - selectedZone.height),
    };
    setZones((current) => [...current, duplicate]);
    setSelectedId(duplicate.id);
  }

  function placeFloat(event: ReactPointerEvent<SVGGElement>, width: number, height: number): FloatCard {
    const offset = 18;
    let x = event.clientX + offset;
    let y = event.clientY + offset;
    if (x + width > window.innerWidth - 12) x = event.clientX - width - offset;
    if (y + height > window.innerHeight - 12) y = event.clientY - height - offset;
    return { x, y };
  }

  function handleHoverZone(zone: FactoryZone, event: ReactPointerEvent<SVGGElement>) {
    if (zone.kind === "camera") {
      setTip(null);
      setCamPreview({ zone, ...placeFloat(event, 300, 220) });
      return;
    }
    setCamPreview(null);
    setTip({ kind: "zone", zone, ...placeFloat(event, 280, 240) });
  }

  function handleHoverWagon(wagon: GrainWagon, event: ReactPointerEvent<SVGGElement>) {
    setCamPreview(null);
    setTip({ kind: "wagon", wagon, ...placeFloat(event, 280, 240) });
  }

  function handleHoverEnd() {
    setTip(null);
    setCamPreview(null);
  }

  const pageActions = canEdit ? (
    editing ? (
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" onClick={cancelEditing} disabled={saving}>
          <X className="size-4" /> Отмена
        </Button>
        <Button size="sm" onClick={() => void saveMap()} disabled={saving}>
          <Save className="size-4" /> {saving ? "Сохранение…" : "Сохранить схему"}
        </Button>
      </div>
    ) : (
      <Button size="sm" onClick={beginEditing} disabled={!map}>
        <PencilRuler className="size-4" /> Редактировать схему
      </Button>
    )
  ) : undefined;

  return (
    <AppShell
      title="Территория"
      section="Работа"
      description="Живая карта комплекса: остатки, вагоны и камеры прямо на схеме."
      tabs={<FactoryTabs />}
      actions={pageActions}
    >
      {!map ? (
        <DataGate loading={loading} error={error} onRetry={reload} />
      ) : (
        <div className="space-y-4">
          {(error || saveError) && <ErrorAlert message={saveError || error} onRetry={error ? reload : undefined} />}

          {!editing && (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {live.silos && (
                <KpiTile
                  icon={Sprout}
                  label="Зерно в цистернах"
                  value={formatTons(totals.balance)}
                  sub={`из ${formatTons(totals.capacity)} ёмкости · свободно ${formatTons(totals.free)}`}
                  barPct={totals.capacity ? (totals.balance / totals.capacity) * 100 : 0}
                  barColor="#d9a83f"
                />
              )}
              {live.stock && (
                <KpiTile
                  icon={Boxes}
                  label="Мешков на складе"
                  value={totals.bags.toLocaleString("ru-RU")}
                  sub={totals.topStock ? `больше всего: ${totals.topStock.grade} ${totals.topStock.packaging}` : "остатков нет"}
                />
              )}
              {live.wagons && (
                <KpiTile
                  icon={TrainFront}
                  label="Вагоны на территории"
                  value={String(wagonCount)}
                  sub={
                    live.wagons.length
                      ? live.wagons
                          .slice(0, 2)
                          .map((wagon) => wagon.number || `#${wagon.id}`)
                          .join(" · ")
                      : "путь свободен"
                  }
                />
              )}
              <KpiTile
                icon={Camera}
                label="Камеры на схеме"
                value={String(totals.cameras)}
                sub={totals.cameras ? `с живым потоком: ${totals.camerasWithStream}` : "добавьте в редакторе"}
              />
            </div>
          )}

          {editing && (
            <section className="rounded-2xl border border-amber-200 bg-[#fffaf0] p-3 shadow-[0_12px_36px_rgba(166,106,32,.1)]">
              <div className="flex flex-wrap items-center gap-2">
                <span className="mr-1 flex items-center gap-2 px-2 text-[10px] font-bold uppercase tracking-[0.14em] text-amber-800">
                  <PencilRuler className="size-3.5" /> Редактор
                </span>
                <Input
                  aria-label="Название схемы"
                  value={title}
                  maxLength={100}
                  onChange={(event) => setTitle(event.target.value)}
                  className="h-8 w-[220px] bg-white"
                />
                <div className="mx-1 hidden h-7 w-px bg-amber-200 sm:block" />
                {KIND_ROWS.map(([kind, config]) => {
                  const Icon = config.icon;
                  return (
                    <Button key={kind} size="sm" variant="outline" onClick={() => addZone(kind)}>
                      <Icon className="size-4" /> {config.label}
                    </Button>
                  );
                })}
                <div className="mx-1 hidden h-7 w-px bg-amber-200 sm:block" />
                <Button
                  size="sm"
                  variant="outline"
                  className="border-amber-300 text-amber-800 hover:bg-amber-100"
                  onClick={applyPreset}
                >
                  <LayoutTemplate className="size-4" /> Как на эскизе
                </Button>
              </div>
              <p className="mt-2.5 flex items-center gap-2 rounded-xl border border-amber-200/70 bg-white/60 px-3 py-2 text-xs text-amber-950/65">
                <Check className="size-4 shrink-0 text-amber-700" />
                Кнопка добавляет объект в центр — перетащите его на место. Жёлтый угол меняет размер. Изменения попадут
                сотрудникам после «Сохранить схему».
              </p>
            </section>
          )}

          <div className={cn("grid gap-4", editing && selectedZone && "xl:grid-cols-[minmax(0,1fr)_310px]")}>
            <Card className="min-w-0 overflow-hidden border-slate-200 shadow-[0_18px_55px_rgba(15,23,42,.08)]">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 sm:px-5">
                <div className="flex min-w-0 items-center gap-2.5">
                  <span className="flex size-8 items-center justify-center rounded-lg bg-slate-100 text-slate-500">
                    <MapIcon className="size-4" />
                  </span>
                  <div className="min-w-0">
                    <h2 className="truncate text-[15px] font-bold tracking-[-0.01em]">{editing ? title : map.title}</h2>
                    <p className="text-[11px] text-slate-400">
                      {zones.length} объектов
                      {map.updated_at ? ` · обновлено ${formatDateTime(map.updated_at)}` : ""}
                      {map.updated_by_name ? ` · ${map.updated_by_name}` : ""}
                    </p>
                  </div>
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1.5" aria-label="Легенда схемы">
                  {categories.map((category) => (
                    <span key={category} className="flex items-center gap-1.5 text-[11px] text-slate-500">
                      <span className="size-2.5 rounded-[4px]" style={{ backgroundColor: CATEGORIES[category].color }} />
                      {CATEGORIES[category].label}
                    </span>
                  ))}
                </div>
              </div>
              <FactoryCanvas
                zones={zones}
                live={live}
                me={me}
                editing={editing}
                selectedId={selectedId}
                onSelect={setSelectedId}
                onChange={updateZone}
                onHoverZone={handleHoverZone}
                onHoverWagon={handleHoverWagon}
                onHoverEnd={handleHoverEnd}
              />
              <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 px-4 py-3 text-[11px] text-slate-400 sm:px-5">
                <span className="rounded-full border border-slate-200 bg-white px-3 py-1">
                  🖱 Наведите на объект — живая статистика
                </span>
                <span className="rounded-full border border-slate-200 bg-white px-3 py-1">
                  📹 Наведите на камеру — превью потока
                </span>
                {!canEdit && (
                  <span className="rounded-full border border-slate-200 bg-white px-3 py-1">
                    Изменять схему может только суперадмин
                  </span>
                )}
              </div>
            </Card>
            {editing && selectedZone && (
              <ZoneInspector
                zone={selectedZone}
                onChange={(patch) => updateZone(selectedZone.id, patch)}
                onDuplicate={duplicateSelected}
                onDelete={() => {
                  setZones((current) => current.filter((zone) => zone.id !== selectedZone.id));
                  setSelectedId(null);
                }}
              />
            )}
          </div>
        </div>
      )}
      {tip && !editing && <TooltipCard tip={tip} live={live} />}
      {camPreview && !editing && <CameraPreviewCard preview={camPreview} />}
    </AppShell>
  );
}

export default function FactoryMapPage() {
  return (
    <RequirePerm perm={["warehouse.view", "grain.view"]} title="Территория · Схема">
      <FactoryMapPageInner />
    </RequirePerm>
  );
}
