export type LineDirection = "any" | "up" | "down" | "positive" | "negative";

export interface NormalizedLine {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface CountingLineConfig {
  line: NormalizedLine;
  direction: LineDirection;
}

// Keep the browser fallback identical to the camera-PC default. The editor
// normally replaces it with the canonical GET response before it is enabled.
const DEFAULT_LINE: NormalizedLine = { x1: 0, y1: 0.5, x2: 1, y2: 0.5 };
const DIRECTIONS = new Set<LineDirection>(["any", "up", "down", "positive", "negative"]);

function tooShort(line: NormalizedLine) {
  return Math.hypot(line.x2 - line.x1, line.y2 - line.y1) < 0.01;
}

export function defaultCountingLine(): NormalizedLine {
  return { ...DEFAULT_LINE };
}

export function validCountingLine(line: NormalizedLine): boolean {
  return !tooShort(line);
}

export function normalizeLineDirection(value: unknown): LineDirection {
  return DIRECTIONS.has(value as LineDirection) ? (value as LineDirection) : "any";
}

/** Accept the persisted object and the processor's compact string contract. */
export function normalizeCountingLine(value: unknown): NormalizedLine | null {
  let raw: unknown[];
  if (typeof value === "string") {
    raw = value.split(",");
  } else if (Array.isArray(value)) {
    raw = value;
  } else if (value && typeof value === "object") {
    const object = value as Partial<Record<keyof NormalizedLine, unknown>>;
    raw = [object.x1, object.y1, object.x2, object.y2];
  } else {
    return null;
  }

  if (raw.length !== 4) return null;
  const coordinates = raw.map((coordinate) => {
    if (typeof coordinate === "number") return coordinate;
    if (typeof coordinate === "string" && coordinate.trim()) return Number(coordinate);
    return Number.NaN;
  });
  if (!coordinates.every((coordinate) => Number.isFinite(coordinate) && coordinate >= 0 && coordinate <= 1)) {
    return null;
  }
  const [x1, y1, x2, y2] = coordinates;
  const line = { x1, y1, x2, y2 };
  return validCountingLine(line) ? line : null;
}

/** The running processor is authoritative; inventory is an offline fallback. */
export function resolveCountingLine(
  live: { line?: unknown; direction?: unknown } | null | undefined,
  persisted: { line?: unknown; direction?: unknown } | null | undefined,
): CountingLineConfig | null {
  const liveLine = normalizeCountingLine(live?.line);
  if (liveLine) {
    return { line: liveLine, direction: normalizeLineDirection(live?.direction) };
  }
  const persistedLine = normalizeCountingLine(persisted?.line);
  if (!persistedLine) return null;
  return { line: persistedLine, direction: normalizeLineDirection(persisted?.direction) };
}
