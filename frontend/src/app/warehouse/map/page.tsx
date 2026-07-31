"use client";

import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import {
  Beaker,
  Boxes,
  Check,
  Copy,
  Factory,
  Fence,
  Grid3X3,
  Map as MapIcon,
  MousePointer2,
  PencilRuler,
  Plus,
  Route,
  Ruler,
  Save,
  Scale,
  Sparkles,
  Trash2,
  TrainFront,
  Warehouse,
  X,
  type LucideIcon,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { FactoryTabs } from "@/components/factory-tabs";
import { RequirePerm } from "@/components/require-perm";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import { showSuccess } from "@/lib/toast";
import type { FactoryMap as FactoryMapData, FactoryZone, FactoryZoneKind } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn, formatDateTime } from "@/lib/utils";
import { useAuth } from "@/store/auth";

const MAP_WIDTH = 1200;
const MAP_HEIGHT = 680;
const GRID = 10;
const MIN_ZONE_SIZE = 48;

type Tool = "select" | "draw";
type Point = { x: number; y: number };
type Interaction =
  | { mode: "draw"; start: Point }
  | { mode: "move"; zoneId: string; start: Point; original: FactoryZone }
  | { mode: "resize"; zoneId: string; start: Point; original: FactoryZone };

type KindConfig = {
  label: string;
  defaultName: string;
  code: string;
  color: string;
  icon: LucideIcon;
};

const KINDS: Record<FactoryZoneKind, KindConfig> = {
  gate: { label: "Проходная", defaultName: "Проходная", code: "КПП", color: "#C58A35", icon: Fence },
  scale: { label: "Весовая", defaultName: "Весовая", code: "ВЕС", color: "#3D7187", icon: Scale },
  warehouse: { label: "Склад", defaultName: "Склад", code: "СКЛ", color: "#4E6B55", icon: Boxes },
  silos: { label: "Силосы", defaultName: "Силосный парк", code: "СЛС", color: "#A66A20", icon: Warehouse },
  production: {
    label: "Производство",
    defaultName: "Производственный корпус",
    code: "ЦЕХ",
    color: "#315D74",
    icon: Factory,
  },
  lab: { label: "Лаборатория", defaultName: "Лаборатория", code: "ЛАБ", color: "#6E5B84", icon: Beaker },
  rail: { label: "Ж/д путь", defaultName: "Железнодорожный путь", code: "Ж/Д", color: "#59636B", icon: TrainFront },
  utility: { label: "Служебный", defaultName: "Служебный участок", code: "ТЕХ", color: "#697386", icon: Ruler },
};

const KIND_ROWS = Object.entries(KINDS) as [FactoryZoneKind, KindConfig][];

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

function MapLegend({ zones }: { zones: FactoryZone[] }) {
  const counts = useMemo(() => {
    const result = new Map<FactoryZoneKind, number>();
    zones.forEach((zone) => result.set(zone.kind, (result.get(zone.kind) ?? 0) + 1));
    return result;
  }, [zones]);

  return (
    <div className="flex flex-wrap gap-x-4 gap-y-2" aria-label="Легенда схемы">
      {KIND_ROWS.filter(([kind]) => counts.has(kind)).map(([kind, config]) => {
        const Icon = config.icon;
        return (
          <span key={kind} className="flex items-center gap-1.5 text-[11px] text-slate-400">
            <span
              className="flex size-6 items-center justify-center rounded-md border border-white/10"
              style={{ backgroundColor: `${config.color}30`, color: config.color }}
            >
              <Icon className="size-3.5" />
            </span>
            {config.label} · {counts.get(kind)}
          </span>
        );
      })}
    </div>
  );
}

function FactoryCanvas({
  zones,
  editing,
  tool,
  drawKind,
  selectedId,
  onSelect,
  onChange,
  onCreated,
}: {
  zones: FactoryZone[];
  editing: boolean;
  tool: Tool;
  drawKind: FactoryZoneKind;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onChange: (id: string, patch: Partial<FactoryZone>) => void;
  onCreated: (zone: FactoryZone) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [interaction, setInteraction] = useState<Interaction | null>(null);
  const [preview, setPreview] = useState<FactoryZone | null>(null);

  function pointFromEvent(event: ReactPointerEvent<SVGElement>): Point {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return {
      x: clamp(((event.clientX - rect.left) / rect.width) * MAP_WIDTH, 0, MAP_WIDTH),
      y: clamp(((event.clientY - rect.top) / rect.height) * MAP_HEIGHT, 0, MAP_HEIGHT),
    };
  }

  function beginDraw(event: ReactPointerEvent<SVGSVGElement>) {
    if (!editing || tool !== "draw" || event.button !== 0) {
      if (event.target === event.currentTarget) onSelect(null);
      return;
    }
    const point = pointFromEvent(event);
    event.currentTarget.setPointerCapture(event.pointerId);
    setInteraction({ mode: "draw", start: point });
    setPreview({
      id: "preview",
      name: KINDS[drawKind].defaultName,
      kind: drawKind,
      x: snap(point.x),
      y: snap(point.y),
      width: 0,
      height: 0,
      color: KINDS[drawKind].color,
      note: "",
    });
    onSelect(null);
  }

  function beginMove(event: ReactPointerEvent<SVGGElement>, zone: FactoryZone) {
    event.stopPropagation();
    onSelect(zone.id);
    if (!editing || tool !== "select" || event.button !== 0) return;
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
    if (interaction.mode === "draw") {
      const x = snap(Math.min(interaction.start.x, point.x));
      const y = snap(Math.min(interaction.start.y, point.y));
      setPreview((current) =>
        current
          ? {
              ...current,
              x,
              y,
              width: clamp(snap(Math.abs(point.x - interaction.start.x)), 0, MAP_WIDTH - x),
              height: clamp(snap(Math.abs(point.y - interaction.start.y)), 0, MAP_HEIGHT - y),
            }
          : null,
      );
      return;
    }
    if (interaction.mode === "move") {
      const dx = point.x - interaction.start.x;
      const dy = point.y - interaction.start.y;
      onChange(interaction.zoneId, {
        x: clamp(snap(interaction.original.x + dx), 0, MAP_WIDTH - interaction.original.width),
        y: clamp(snap(interaction.original.y + dy), 0, MAP_HEIGHT - interaction.original.height),
      });
      return;
    }
    const dx = point.x - interaction.start.x;
    const dy = point.y - interaction.start.y;
    onChange(interaction.zoneId, {
      width: clamp(snap(interaction.original.width + dx), MIN_ZONE_SIZE, MAP_WIDTH - interaction.original.x),
      height: clamp(snap(interaction.original.height + dy), MIN_ZONE_SIZE, MAP_HEIGHT - interaction.original.y),
    });
  }

  function finishInteraction() {
    if (interaction?.mode === "draw" && preview) {
      if (preview.width >= MIN_ZONE_SIZE && preview.height >= MIN_ZONE_SIZE) {
        onCreated({ ...preview, id: newZoneId() });
      }
      setPreview(null);
    }
    setInteraction(null);
  }

  function renderZone(zone: FactoryZone, isPreview = false) {
    const config = KINDS[zone.kind];
    const selected = selectedId === zone.id;
    const compact = zone.height < 90 || zone.width < 140;
    return (
      <g
        key={zone.id}
        transform={`translate(${zone.x} ${zone.y})`}
        className={cn(editing && tool === "select" && !isPreview && "cursor-move")}
        onPointerDown={isPreview ? undefined : (event) => beginMove(event, zone)}
        onClick={(event) => {
          event.stopPropagation();
          if (!isPreview) onSelect(zone.id);
        }}
      >
        <rect x="4" y="6" width={zone.width} height={zone.height} rx="14" fill="#020617" opacity="0.34" />
        <rect
          width={zone.width}
          height={zone.height}
          rx="14"
          fill={zone.color}
          fillOpacity={isPreview ? 0.26 : 0.18}
          stroke={selected || isPreview ? "#F4C86A" : zone.color}
          strokeWidth={selected || isPreview ? 3 : 1.5}
          strokeDasharray={isPreview ? "10 7" : undefined}
        />
        <rect width={zone.width} height={Math.min(9, zone.height)} rx="4" fill={zone.color} />
        <circle cx="25" cy={compact ? 29 : 32} r={compact ? 13 : 16} fill={zone.color} />
        <text
          x="25"
          y={compact ? 33 : 36}
          textAnchor="middle"
          fill="white"
          fontSize={compact ? 7 : 9}
          fontWeight="800"
          letterSpacing=".08em"
        >
          {config.code}
        </text>
        <foreignObject
          x={compact ? 46 : 52}
          y="18"
          width={Math.max(0, zone.width - 62)}
          height={Math.max(0, zone.height - 26)}
        >
          <div className="flex h-full flex-col overflow-hidden text-white">
            <p className={cn("truncate font-bold leading-tight", compact ? "text-[10px]" : "text-[13px]")}>
              {zone.name}
            </p>
            {!compact && zone.note && (
              <p className="mt-1 line-clamp-2 text-[10px] leading-4 text-white/48">{zone.note}</p>
            )}
            {!compact && (
              <span className="mt-auto pb-1 text-[8px] font-semibold uppercase tracking-[0.16em] text-white/35">
                {config.label}
              </span>
            )}
          </div>
        </foreignObject>
        {editing && selected && !isPreview && (
          <rect
            x={zone.width - 14}
            y={zone.height - 14}
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
  }

  return (
    <div className="overflow-x-auto rounded-[22px] border border-slate-700/80 bg-[#0b151a] shadow-[0_24px_70px_rgba(2,6,23,.3)]">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
        role="img"
        aria-label="Схема участков завода"
        className={cn(
          "block min-w-[860px] touch-none select-none",
          editing && tool === "draw" ? "cursor-crosshair" : "cursor-default",
        )}
        onPointerDown={beginDraw}
        onPointerMove={handlePointerMove}
        onPointerUp={finishInteraction}
        onPointerCancel={finishInteraction}
      >
        <defs>
          <pattern id="factory-small-grid" width="10" height="10" patternUnits="userSpaceOnUse">
            <path d="M10 0H0V10" fill="none" stroke="#9FB2B9" strokeOpacity=".055" strokeWidth="1" />
          </pattern>
          <pattern id="factory-grid" width="50" height="50" patternUnits="userSpaceOnUse">
            <rect width="50" height="50" fill="url(#factory-small-grid)" />
            <path d="M50 0H0V50" fill="none" stroke="#9FB2B9" strokeOpacity=".12" strokeWidth="1" />
          </pattern>
          <linearGradient id="factory-background" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#13262E" />
            <stop offset=".54" stopColor="#0D1C22" />
            <stop offset="1" stopColor="#11191E" />
          </linearGradient>
        </defs>
        <rect width={MAP_WIDTH} height={MAP_HEIGHT} fill="url(#factory-background)" />
        <rect width={MAP_WIDTH} height={MAP_HEIGHT} fill="url(#factory-grid)" />
        <path d="M0 338H1200" stroke="#738087" strokeWidth="42" strokeOpacity=".12" />
        <path d="M0 326H1200M0 350H1200" stroke="#9BA6AB" strokeWidth="3" strokeOpacity=".24" />
        {Array.from({ length: 61 }).map((_, index) => (
          <path key={index} d={`M${index * 20} 319v39`} stroke="#9BA6AB" strokeWidth="2" strokeOpacity=".18" />
        ))}
        <rect x="24" y="24" width="1152" height="632" rx="24" fill="none" stroke="#D6B56D" strokeOpacity=".22" />
        <text x="48" y="55" fill="#D6B56D" fillOpacity=".58" fontSize="10" fontWeight="700" letterSpacing=".24em">
          ASYL-LTD · ГЕНЕРАЛЬНЫЙ ПЛАН
        </text>
        <text x="1152" y="55" textAnchor="end" fill="#9FB2B9" fillOpacity=".42" fontSize="9" letterSpacing=".16em">
          СЕВЕР ↑
        </text>
        {zones.map((zone) => renderZone(zone))}
        {preview && renderZone(preview, true)}
        {!zones.length && !preview && (
          <g transform="translate(600 330)">
            <circle r="52" fill="#C58A35" fillOpacity=".12" stroke="#C58A35" strokeOpacity=".3" />
            <path d="M-18 14V-15l18-10 18 10v29M-26 14h52" fill="none" stroke="#E2B86D" strokeWidth="3" />
            <text y="88" textAnchor="middle" fill="white" fontSize="18" fontWeight="700">
              Схема пока пустая
            </text>
            <text y="114" textAnchor="middle" fill="#94A3B8" fontSize="12">
              Суперадмин может включить редактор и нарисовать участки
            </text>
          </g>
        )}
      </svg>
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

  return (
    <Card className="overflow-hidden border-slate-200 bg-white shadow-[0_18px_45px_rgba(15,23,42,.09)]">
      <div className="border-b border-slate-200 bg-slate-950 px-4 py-4 text-white">
        <div className="flex items-center gap-3">
          <span
            className="flex size-10 items-center justify-center rounded-xl"
            style={{ backgroundColor: `${zone.color}35`, color: zone.color }}
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
          <Label htmlFor="factory-zone-kind">Тип участка</Label>
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
          <Label htmlFor="factory-zone-note">Назначение</Label>
          <textarea
            id="factory-zone-note"
            value={zone.note}
            maxLength={160}
            rows={3}
            onChange={(event) => onChange({ note: event.target.value })}
            className="w-full resize-none rounded-md border border-[var(--input)] bg-white px-3.5 py-2 text-sm shadow-sm outline-none focus-visible:border-[var(--ring)] focus-visible:ring-2 focus-visible:ring-[var(--ring)]/20"
          />
        </div>
        <div>
          <Label htmlFor="factory-zone-color">Цвет контура</Label>
          <div className="flex items-center gap-2">
            <input
              id="factory-zone-color"
              type="color"
              value={zone.color}
              onChange={(event) => onChange({ color: event.target.value.toUpperCase() })}
              className="h-10 w-14 cursor-pointer rounded-md border border-slate-200 bg-white p-1"
            />
            <Input value={zone.color} readOnly className="font-mono uppercase" />
          </div>
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
  const [title, setTitle] = useState("Схема завода");
  const [editing, setEditing] = useState(false);
  const [tool, setTool] = useState<Tool>("select");
  const [drawKind, setDrawKind] = useState<FactoryZoneKind>("production");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    if (!map || editing) return;
    setZones(copyZones(map.zones));
    setTitle(map.title);
  }, [editing, map]);

  const selectedZone = zones.find((zone) => zone.id === selectedId) ?? null;

  function updateZone(id: string, patch: Partial<FactoryZone>) {
    setZones((current) => current.map((zone) => (zone.id === id ? { ...zone, ...patch } : zone)));
  }

  function beginEditing() {
    if (!map) return;
    setZones(copyZones(map.zones));
    setTitle(map.title);
    setSelectedId(null);
    setTool("select");
    setSaveError("");
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
      setSaveError("У каждого участка должно быть название");
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
      showSuccess("Схема завода сохранена");
    } catch (cause) {
      setSaveError(apiError(cause));
    } finally {
      setSaving(false);
    }
  }

  function createZone(zone: FactoryZone) {
    setZones((current) => [...current, zone]);
    setSelectedId(zone.id);
    setTool("select");
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
      title="Завод"
      section="Работа"
      description="Единая карта ответственных участков и физического движения по территории."
      tabs={<FactoryTabs />}
      actions={pageActions}
    >
      {!map ? (
        <DataGate loading={loading} error={error} onRetry={reload} />
      ) : (
        <div className="space-y-4">
          {(error || saveError) && <ErrorAlert message={saveError || error} onRetry={error ? reload : undefined} />}

          <section className="relative overflow-hidden rounded-2xl border border-slate-800 bg-[#14262d] px-4 py-4 text-white shadow-[0_18px_55px_rgba(15,23,42,.16)] sm:px-5">
            <div className="absolute inset-0 opacity-20 [background-image:linear-gradient(rgba(255,255,255,.06)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,.06)_1px,transparent_1px)] [background-size:28px_28px]" />
            <div className="relative flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <span className="flex size-11 items-center justify-center rounded-xl border border-[#e2b86d]/25 bg-[#e2b86d]/10 text-[#e2b86d]">
                  <MapIcon className="size-5" />
                </span>
                <div>
                  {editing ? (
                    <Input
                      aria-label="Название схемы"
                      value={title}
                      maxLength={100}
                      onChange={(event) => setTitle(event.target.value)}
                      className="h-9 w-[260px] border-white/15 bg-white/10 font-bold text-white shadow-none placeholder:text-white/30"
                    />
                  ) : (
                    <h1 className="text-xl font-black tracking-[-0.025em]">{map.title}</h1>
                  )}
                  <p className="mt-1 text-xs text-white/45">
                    {zones.length} участков
                    {map.updated_at ? ` · обновлено ${formatDateTime(map.updated_at)}` : ""}
                    {map.updated_by_name ? ` · ${map.updated_by_name}` : ""}
                  </p>
                </div>
              </div>
              <MapLegend zones={zones} />
            </div>
          </section>

          {editing && (
            <section className="rounded-2xl border border-amber-200 bg-[#fffaf0] p-3 shadow-[0_12px_36px_rgba(166,106,32,.1)]">
              <div className="flex flex-wrap items-center gap-2">
                <span className="mr-1 flex items-center gap-2 px-2 text-[10px] font-bold uppercase tracking-[0.14em] text-amber-800">
                  <Sparkles className="size-3.5" /> Редактор карты
                </span>
                <Button size="sm" variant={tool === "select" ? "default" : "outline"} onClick={() => setTool("select")}>
                  <MousePointer2 className="size-4" /> Выбрать и двигать
                </Button>
                <div className="mx-1 hidden h-7 w-px bg-amber-200 sm:block" />
                {KIND_ROWS.map(([kind, config]) => {
                  const Icon = config.icon;
                  const active = tool === "draw" && drawKind === kind;
                  return (
                    <Button
                      key={kind}
                      size="sm"
                      variant={active ? "default" : "outline"}
                      className={cn(active && "bg-[#a66a20] hover:bg-[#8e5919]")}
                      onClick={() => {
                        setDrawKind(kind);
                        setTool("draw");
                      }}
                    >
                      <Icon className="size-4" /> {config.label}
                    </Button>
                  );
                })}
              </div>
              <div className="mt-3 flex items-center gap-2 rounded-xl border border-amber-200/70 bg-white/60 px-3 py-2 text-xs text-amber-950/65">
                {tool === "draw" ? (
                  <>
                    <Plus className="size-4 text-amber-700" /> Зажмите мышь на схеме и протяните прямоугольник нового
                    участка.
                  </>
                ) : (
                  <>
                    <Grid3X3 className="size-4 text-amber-700" /> Перетаскивайте объекты; жёлтый угол меняет размер с
                    привязкой к сетке.
                  </>
                )}
              </div>
            </section>
          )}

          <div className={cn("grid gap-4", editing && selectedZone && "xl:grid-cols-[minmax(0,1fr)_310px]")}>
            <div className="min-w-0">
              <FactoryCanvas
                zones={zones}
                editing={editing}
                tool={tool}
                drawKind={drawKind}
                selectedId={selectedId}
                onSelect={setSelectedId}
                onChange={updateZone}
                onCreated={createZone}
              />
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2 px-1 text-[11px] text-[var(--muted-foreground)]">
                <span className="flex items-center gap-1.5">
                  <Route className="size-3.5" /> Ж/д линия показана как ориентир движения по территории
                </span>
                {editing && (
                  <span className="flex items-center gap-1.5 font-medium text-amber-700">
                    <Check className="size-3.5" /> Изменения попадут сотрудникам после сохранения
                  </span>
                )}
              </div>
            </div>
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

          {!canEdit && (
            <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
              <span className="flex size-8 items-center justify-center rounded-lg bg-white text-slate-500 shadow-sm">
                <MapIcon className="size-4" />
              </span>
              Схема доступна для просмотра. Изменять расположение участков может только суперадмин.
            </div>
          )}
        </div>
      )}
    </AppShell>
  );
}

export default function FactoryMapPage() {
  return (
    <RequirePerm perm={["warehouse.view", "grain.view"]} title="Завод · Схема">
      <FactoryMapPageInner />
    </RequirePerm>
  );
}
