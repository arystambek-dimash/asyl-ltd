"use client";
import { Network } from "lucide-react";
import { tableParts, type DataMap } from "@/lib/assistant";
import { cn } from "@/lib/utils";

const WIDTH = 360;
const HEIGHT = 230;
const NODE_W = 112;
const NODE_H = 36;
const MAX_LABEL = 15;

type Point = { x: number; y: number };

/** Узлы по эллипсу: первый — сверху, дальше по часовой; один узел — в центре. */
function layout(count: number): Point[] {
  if (count === 1) return [{ x: WIDTH / 2, y: HEIGHT / 2 }];
  const rx = WIDTH / 2 - NODE_W / 2 - 6;
  const ry = HEIGHT / 2 - NODE_H / 2 - 6;
  return Array.from({ length: count }, (_, index) => {
    const angle = -Math.PI / 2 + (index * 2 * Math.PI) / count;
    return { x: WIDTH / 2 + rx * Math.cos(angle), y: HEIGHT / 2 + ry * Math.sin(angle) };
  });
}

function shorten(text: string): string {
  return text.length > MAX_LABEL ? `${text.slice(0, MAX_LABEL - 1)}…` : text;
}

/** Изгиб ребра к центру карты: связи не сливаются с краями узлов. */
function edgePath(from: Point, to: Point): string {
  const mid = { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };
  const pull = 0.18;
  const control = { x: mid.x + (WIDTH / 2 - mid.x) * pull, y: mid.y + (HEIGHT / 2 - mid.y) * pull };
  return `M ${from.x} ${from.y} Q ${control.x} ${control.y} ${to.x} ${to.y}`;
}

/**
 * Карта данных: какие таблицы ассистент открыл и как они связаны внешними ключами.
 * Новые узлы и связи появляются с анимацией; active — таблицы текущего шага.
 */
export function DataMapView({ map, active = [], className }: { map: DataMap; active?: string[]; className?: string }) {
  const points = layout(map.tables.length);
  const position = new Map(map.tables.map((table, index) => [table, points[index]]));
  return (
    <div className={cn("rounded-xl border border-[var(--border)] bg-[var(--muted)]/30 p-2", className)}>
      <p className="flex items-center gap-1.5 px-1 pb-1 text-xs font-medium text-[var(--muted-foreground)]">
        <Network className="size-3.5" />
        Карта данных
        {map.tables.length > 0 && <span className="tabular-nums">· {map.tables.length}</span>}
      </p>
      {map.tables.length === 0 ? (
        <p className="flex h-[150px] items-center justify-center px-6 text-center text-xs text-[var(--muted-foreground)]">
          Здесь появятся таблицы, которые откроет ассистент
        </p>
      ) : (
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="h-auto w-full"
          role="img"
          aria-label={`Затронутые таблицы: ${map.tables.join(", ")}`}
        >
          {map.links.map(([from, to]) => {
            const a = position.get(from);
            const b = position.get(to);
            if (!a || !b) return null;
            return (
              <path
                key={`${from}->${to}`}
                d={edgePath(a, b)}
                fill="none"
                stroke="var(--ring)"
                strokeOpacity={0.45}
                strokeWidth={1.5}
                className="assistant-edge"
              />
            );
          })}
          {map.tables.map((table) => {
            const point = position.get(table) as Point;
            const { app, model } = tableParts(table);
            const isActive = active.includes(table);
            const x = point.x - NODE_W / 2;
            const y = point.y - NODE_H / 2;
            return (
              <g key={table} className="assistant-node">
                <title>{table}</title>
                {isActive && (
                  <rect
                    x={x}
                    y={y}
                    width={NODE_W}
                    height={NODE_H}
                    rx={9}
                    fill="none"
                    stroke="var(--ring)"
                    strokeWidth={2}
                    className="assistant-ping"
                  />
                )}
                <rect
                  x={x}
                  y={y}
                  width={NODE_W}
                  height={NODE_H}
                  rx={9}
                  fill="var(--card)"
                  stroke={isActive ? "var(--ring)" : "var(--border)"}
                  strokeWidth={isActive ? 1.5 : 1}
                />
                <text x={point.x} y={point.y - 3} textAnchor="middle" fontSize={9} fill="var(--muted-foreground)">
                  {app}
                </text>
                <text
                  x={point.x}
                  y={point.y + 10}
                  textAnchor="middle"
                  fontSize={11}
                  fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                  fill="var(--foreground)"
                >
                  {shorten(model)}
                </text>
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}
