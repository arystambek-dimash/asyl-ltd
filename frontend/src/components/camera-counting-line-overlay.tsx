"use client";

import { useCallback, useId, useRef, useState } from "react";
import type { LineDirection, NormalizedLine } from "@/lib/camera-counting-line";
import { useVideoBox } from "@/lib/use-video-box";
import { cn } from "@/lib/utils";

const VIEWBOX_WIDTH = 1000;

function clamp(value: number) {
  return Math.max(0, Math.min(1, value));
}

function directionalArrow(line: NormalizedLine, direction: LineDirection, height: number) {
  const start = { x: line.x1 * VIEWBOX_WIDTH, y: line.y1 * height };
  const end = { x: line.x2 * VIEWBOX_WIDTH, y: line.y2 * height };
  const middle = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
  const dx = end.x - start.x;
  const dy = end.y - start.y;
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

export function CameraCountingLineOverlay({
  line,
  direction,
  editable = false,
  disabled = false,
  onLineChange,
  className,
}: {
  line: NormalizedLine;
  direction: LineDirection;
  editable?: boolean;
  disabled?: boolean;
  onLineChange?: (line: NormalizedLine) => void;
  className?: string;
}) {
  const surfaceRef = useRef<HTMLDivElement>(null);
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const [dragging, setDragging] = useState<"start" | "end" | "draw" | null>(null);
  const box = useVideoBox(container);
  const viewBoxHeight = box?.width ? (VIEWBOX_WIDTH * box.height) / box.width : 562.5;
  const arrow = directionalArrow(line, direction, viewBoxHeight);
  const svgId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const glowId = `counting-line-glow-${svgId}`;
  const arrowId = `counting-line-arrow-${svgId}`;
  const setSurface = useCallback((node: HTMLDivElement | null) => {
    surfaceRef.current = node;
    setContainer(node);
  }, []);

  const pointAt = (clientX: number, clientY: number) => {
    const rect = surfaceRef.current?.getBoundingClientRect();
    if (!rect || !rect.width || !rect.height) return null;
    return {
      x: clamp((clientX - rect.left) / rect.width),
      y: clamp((clientY - rect.top) / rect.height),
      rect,
    };
  };

  const begin = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!box || !editable || disabled || !onLineChange) return;
    const point = pointAt(event.clientX, event.clientY);
    if (!point) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const distance = (x: number, y: number) =>
      Math.hypot((point.x - x) * point.rect.width, (point.y - y) * point.rect.height);
    if (distance(line.x1, line.y1) <= 24) {
      setDragging("start");
    } else if (distance(line.x2, line.y2) <= 24) {
      setDragging("end");
    } else {
      setDragging("draw");
      onLineChange({ x1: point.x, y1: point.y, x2: point.x, y2: point.y });
    }
  };

  const move = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!box || !dragging || disabled || !onLineChange) return;
    const point = pointAt(event.clientX, event.clientY);
    if (!point) return;
    if (dragging === "start") onLineChange({ ...line, x1: point.x, y1: point.y });
    else onLineChange({ ...line, x2: point.x, y2: point.y });
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
      ref={setSurface}
      onPointerDown={begin}
      onPointerMove={move}
      onPointerUp={finish}
      onPointerCancel={finish}
      className={cn(
        "absolute inset-0 overflow-hidden",
        editable ? "touch-none select-none" : "pointer-events-none",
        editable && (disabled ? "cursor-wait" : "cursor-crosshair"),
        !box && "pointer-events-none opacity-0",
        className,
      )}
      style={
        box
          ? { left: box.left, top: box.top, width: box.width, height: box.height, right: "auto", bottom: "auto" }
          : undefined
      }
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
        <line
          data-counting-line="shadow"
          x1={line.x1 * VIEWBOX_WIDTH}
          y1={line.y1 * viewBoxHeight}
          x2={line.x2 * VIEWBOX_WIDTH}
          y2={line.y2 * viewBoxHeight}
          stroke="rgba(15,23,42,.75)"
          strokeWidth="13"
          strokeLinecap="round"
        />
        <line
          data-counting-line="primary"
          x1={line.x1 * VIEWBOX_WIDTH}
          y1={line.y1 * viewBoxHeight}
          x2={line.x2 * VIEWBOX_WIDTH}
          y2={line.y2 * viewBoxHeight}
          stroke="#38bdf8"
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
        {editable &&
          ([line.x1, line.x2] as const).map((x, index) => {
            const y = index === 0 ? line.y1 : line.y2;
            return (
              <g key={index}>
                <circle cx={x * VIEWBOX_WIDTH} cy={y * viewBoxHeight} r="18" fill="rgba(15,23,42,.7)" />
                <circle
                  cx={x * VIEWBOX_WIDTH}
                  cy={y * viewBoxHeight}
                  r="11"
                  fill="#f8fafc"
                  stroke="#38bdf8"
                  strokeWidth="5"
                />
              </g>
            );
          })}
      </svg>
    </div>
  );
}
