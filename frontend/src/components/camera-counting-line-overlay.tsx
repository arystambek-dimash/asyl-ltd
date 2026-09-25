"use client";

import { useId, useState } from "react";
import {
  COUNT_LINE_COLOR,
  COUNT_LINE_ID,
  verificationLineColor,
  type LineDirection,
  type NormalizedLine,
  type VerificationLine,
} from "@/lib/camera-counting-line";
import { clampUnit, useVideoBox, videoBoxStyle } from "@/lib/use-video-box";
import { cn } from "@/lib/utils";

const VIEWBOX_WIDTH = 1000;
const HANDLE_HIT_PX = 24;
// A fingertip lands less precisely than a mouse pointer.
const LINE_HIT_PX = { mouse: 12, touch: 24 };
// A redraw starts only once the pointer really moves: a tap is not a line.
const DRAW_START_PX = 6;
const LABEL_FONT_SIZE = 22;
// Smallest on-screen sizes, in CSS pixels, however small the frame is drawn.
const LABEL_MIN_PX = { editable: 11, readOnly: 9 };
const HANDLE_MIN_RADIUS_PX = 9;

function directionalArrow(line: NormalizedLine, direction: LineDirection, height: number) {
  const { x1, y1, x2, y2 } = scaled(line, height);
  const middle = { x: (x1 + x2) / 2, y: (y1 + y2) / 2 };
  const dx = x2 - x1;
  const dy = y2 - y1;
  const length = Math.max(1, Math.hypot(dx, dy));
  let vx = (-dy / length) * 52;
  let vy = (dx / length) * 52;

  if (direction === "up") [vx, vy] = [0, -52];
  if (direction === "down") [vx, vy] = [0, 52];
  if (direction === "negative") [vx, vy] = [-vx, -vy];

  return {
    x1: middle.x - vx / 2,
    y1: middle.y - vy / 2,
    x2: middle.x + vx / 2,
    y2: middle.y + vy / 2,
  };
}

type Point = { x: number; y: number };
type Size = { width: number; height: number };

/** Pixel distance between two normalized points on the rendered surface. */
function pixelDistance(a: Point, b: Point, size: Size) {
  return Math.hypot((a.x - b.x) * size.width, (a.y - b.y) * size.height);
}

/** Pixel distance from a point to a segment on the rendered surface. */
function distanceToSegment(point: Point, line: NormalizedLine, size: Size) {
  const [px, py] = [point.x * size.width, point.y * size.height];
  const [ax, ay] = [line.x1 * size.width, line.y1 * size.height];
  const [bx, by] = [line.x2 * size.width, line.y2 * size.height];
  const [dx, dy] = [bx - ax, by - ay];
  const lengthSquared = dx * dx + dy * dy;
  const t = lengthSquared ? clampUnit(((px - ax) * dx + (py - ay) * dy) / lengthSquared) : 0;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function scaled(line: NormalizedLine, height: number) {
  return {
    x1: line.x1 * VIEWBOX_WIDTH,
    y1: line.y1 * height,
    x2: line.x2 * VIEWBOX_WIDTH,
    y2: line.y2 * height,
  };
}

/** Name tag next to the upper end of a segment, kept inside the frame. */
function LineLabel({
  line,
  height,
  color,
  text,
  fontSize,
}: {
  line: NormalizedLine;
  height: number;
  color: string;
  text: string;
  fontSize: number;
}) {
  const upperFirst = line.y1 <= line.y2;
  const x = (upperFirst ? line.x1 : line.x2) * VIEWBOX_WIDTH;
  const y = (upperFirst ? line.y1 : line.y2) * height;
  const gap = fontSize * 0.6;
  // Flip to the left only when the name would run off the frame.
  const nearRight = x + gap + text.length * fontSize * 0.58 > VIEWBOX_WIDTH;
  return (
    <text
      x={nearRight ? x - gap : x + gap}
      y={y < fontSize * 1.5 ? y + fontSize * 1.5 : y - gap}
      textAnchor={nearRight ? "end" : "start"}
      fontSize={fontSize}
      fontWeight="600"
      fill={color}
      stroke="rgba(15,23,42,.85)"
      strokeWidth="5"
      paintOrder="stroke"
    >
      {text}
    </text>
  );
}

function Handles({
  line,
  height,
  color,
  scale,
}: {
  line: NormalizedLine;
  height: number;
  color: string;
  /** ≥ 1: keeps handles grabbable on a phone-width frame. */
  scale: number;
}) {
  return (
    <>
      {(
        [
          [line.x1, line.y1],
          [line.x2, line.y2],
        ] as const
      ).map(([x, y], index) => (
        <g key={index}>
          <circle cx={x * VIEWBOX_WIDTH} cy={y * height} r={18 * scale} fill="rgba(15,23,42,.7)" />
          <circle
            cx={x * VIEWBOX_WIDTH}
            cy={y * height}
            r={11 * scale}
            fill="#f8fafc"
            stroke={color}
            strokeWidth={5 * scale}
          />
        </g>
      ))}
    </>
  );
}

export function CameraCountingLineOverlay({
  line,
  direction,
  verificationLines = [],
  activeLineId = COUNT_LINE_ID,
  editable = false,
  disabled = false,
  onLineChange,
  onVerificationLineChange,
  onActiveLineChange,
}: {
  line: NormalizedLine;
  direction: LineDirection;
  /** Sampling lines: classify the bag again, never count it. */
  verificationLines?: VerificationLine[];
  /** Line that an empty-spot drag redraws in the editor. */
  activeLineId?: string;
  editable?: boolean;
  disabled?: boolean;
  onLineChange?: (line: NormalizedLine) => void;
  onVerificationLineChange?: (id: string, line: NormalizedLine) => void;
  onActiveLineChange?: (id: string) => void;
}) {
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const [dragging, setDragging] = useState<
    { id: string; end: "start" | "end" } | { id: string; from: { x: number; y: number }; drawing: boolean } | null
  >(null);
  const box = useVideoBox(container);
  const viewBoxHeight = box?.width ? (VIEWBOX_WIDTH * box.height) / box.width : 562.5;
  const arrow = directionalArrow(line, direction, viewBoxHeight);
  const unitsPerPx = box?.width ? VIEWBOX_WIDTH / box.width : 1;
  const labelSize = Math.max(LABEL_FONT_SIZE, (editable ? LABEL_MIN_PX.editable : LABEL_MIN_PX.readOnly) * unitsPerPx);
  const handleScale = Math.max(1, (HANDLE_MIN_RADIUS_PX * unitsPerPx) / 18);
  const svgId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const glowId = `counting-line-glow-${svgId}`;
  const arrowId = `counting-line-arrow-${svgId}`;

  const segments = [{ id: COUNT_LINE_ID, line }, ...verificationLines];
  const activeId = segments.some((segment) => segment.id === activeLineId) ? activeLineId : COUNT_LINE_ID;
  const countActive = activeId === COUNT_LINE_ID;
  const changeLine = (id: string, next: NormalizedLine) => {
    if (id === COUNT_LINE_ID) onLineChange?.(next);
    else onVerificationLineChange?.(id, next);
  };

  const pointAt = (clientX: number, clientY: number) => {
    const rect = container?.getBoundingClientRect();
    if (!rect || !rect.width || !rect.height) return null;
    return {
      x: clampUnit((clientX - rect.left) / rect.width),
      y: clampUnit((clientY - rect.top) / rect.height),
      rect,
    };
  };

  const begin = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!box || !editable || disabled || !onLineChange) return;
    const point = pointAt(event.clientX, event.clientY);
    if (!point) return;
    const distance = (x: number, y: number) => pixelDistance(point, { x, y }, point.rect);
    const active = segments.find((segment) => segment.id === activeId)!;
    const others = segments.filter((segment) => segment.id !== activeId);

    // The selected line's handles win when handles of two lines overlap.
    for (const segment of [active, ...others]) {
      const end =
        distance(segment.line.x1, segment.line.y1) <= HANDLE_HIT_PX
          ? "start"
          : distance(segment.line.x2, segment.line.y2) <= HANDLE_HIT_PX
            ? "end"
            : null;
      if (end) {
        event.currentTarget.setPointerCapture(event.pointerId);
        if (segment.id !== activeId) onActiveLineChange?.(segment.id);
        setDragging({ id: segment.id, end });
        return;
      }
    }
    // Touching another line only selects it; nothing is redrawn by accident.
    const reach = event.pointerType === "touch" ? LINE_HIT_PX.touch : LINE_HIT_PX.mouse;
    const touched = others.find((segment) => distanceToSegment(point, segment.line, point.rect) <= reach);
    if (touched) {
      onActiveLineChange?.(touched.id);
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging({ id: activeId, from: { x: point.x, y: point.y }, drawing: false });
  };

  const move = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!box || !dragging || disabled) return;
    const point = pointAt(event.clientX, event.clientY);
    const segment = segments.find((item) => item.id === dragging.id);
    if (!point || !segment) return;
    if ("from" in dragging) {
      const { from } = dragging;
      if (!dragging.drawing) {
        const moved = pixelDistance(point, from, point.rect);
        if (moved < DRAW_START_PX) return;
        setDragging({ ...dragging, drawing: true });
      }
      changeLine(segment.id, { x1: from.x, y1: from.y, x2: point.x, y2: point.y });
    } else if (dragging.end === "start") {
      changeLine(segment.id, { ...segment.line, x1: point.x, y1: point.y });
    } else {
      changeLine(segment.id, { ...segment.line, x2: point.x, y2: point.y });
    }
  };

  const finish = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDragging(null);
  };

  return (
    <div
      aria-hidden
      data-camera-counting-line
      data-video-box-ready={box ? "true" : "false"}
      ref={setContainer}
      onPointerDown={begin}
      onPointerMove={move}
      onPointerUp={finish}
      onPointerCancel={finish}
      className={cn(
        "absolute inset-0 overflow-hidden",
        editable ? "touch-none select-none" : "pointer-events-none",
        editable && (disabled ? "cursor-wait" : "cursor-crosshair"),
        !box && "pointer-events-none opacity-0",
      )}
      style={videoBoxStyle(box)}
    >
      <svg
        viewBox={`0 0 ${VIEWBOX_WIDTH} ${viewBoxHeight}`}
        preserveAspectRatio="none"
        className="pointer-events-none absolute inset-0 size-full"
      >
        <defs>
          <filter id={glowId} x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur stdDeviation="6" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          <marker
            id={arrowId}
            viewBox="0 0 10 10"
            refX="7"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#f8fafc" />
          </marker>
        </defs>
        {verificationLines.map((item, index) => {
          const color = verificationLineColor(index);
          const selected = editable && item.id === activeId;
          const coordinates = scaled(item.line, viewBoxHeight);
          return (
            <g key={item.id} data-verification-line={item.id} opacity={editable ? 1 : 0.75}>
              <line {...coordinates} stroke="rgba(15,23,42,.6)" strokeWidth={editable ? 10 : 6} strokeLinecap="round" />
              <line
                data-verification-stroke
                {...coordinates}
                stroke={color}
                strokeWidth={selected ? 6 : editable ? 4 : 3}
                strokeLinecap="round"
                // Read-only views keep the checks visibly secondary to counting.
                strokeDasharray={editable ? undefined : "16 12"}
              />
              <LineLabel line={item.line} height={viewBoxHeight} color={color} text={item.name} fontSize={labelSize} />
              {selected && <Handles line={item.line} height={viewBoxHeight} color={color} scale={handleScale} />}
            </g>
          );
        })}
        <line
          data-counting-line="shadow"
          {...scaled(line, viewBoxHeight)}
          stroke="rgba(15,23,42,.75)"
          strokeWidth="13"
          strokeLinecap="round"
        />
        <line
          data-counting-line="primary"
          {...scaled(line, viewBoxHeight)}
          stroke={COUNT_LINE_COLOR}
          strokeWidth="6"
          strokeLinecap="round"
          filter={`url(#${glowId})`}
        />
        <line
          data-counting-direction={direction}
          {...arrow}
          stroke="#f8fafc"
          strokeWidth="4"
          strokeLinecap="round"
          markerStart={direction === "any" ? `url(#${arrowId})` : undefined}
          markerEnd={`url(#${arrowId})`}
        />
        {editable && verificationLines.length > 0 && (
          <LineLabel line={line} height={viewBoxHeight} color={COUNT_LINE_COLOR} text="Подсчёт" fontSize={labelSize} />
        )}
        {editable && countActive && (
          <Handles line={line} height={viewBoxHeight} color={COUNT_LINE_COLOR} scale={handleScale} />
        )}
      </svg>
    </div>
  );
}
