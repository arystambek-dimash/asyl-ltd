export type LineDirection = "any" | "up" | "down" | "positive" | "negative";

export interface NormalizedLine {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

interface CountingLineConfig {
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

function validCountingLine(line: NormalizedLine): boolean {
  return !tooShort(line);
}

function normalizeLineDirection(value: unknown): LineDirection {
  return DIRECTIONS.has(value as LineDirection) ? (value as LineDirection) : "any";
}

/** Any in-frame segment: the persisted object or the compact string form. */
function parseSegment(value: unknown): NormalizedLine | null {
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
  if (!coordinates.every(inFrame)) return null;
  const [x1, y1, x2, y2] = coordinates;
  return { x1, y1, x2, y2 };
}

function inFrame(coordinate: number) {
  return Number.isFinite(coordinate) && coordinate >= 0 && coordinate <= 1;
}

/** Accept the persisted object and the processor's compact string contract. */
export function normalizeCountingLine(value: unknown): NormalizedLine | null {
  const line = parseSegment(value);
  return line && validCountingLine(line) ? line : null;
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

// --- Линии проверки (cv-service normalize_verification_lines) -------------

export const COUNT_LINE_ID = "count";
export const COUNT_LINE_COLOR = "#38bdf8";
export const MAX_VERIFICATION_LINES = 8;
export const VERIFICATION_LINE_NAME_MAX = 80;
const VERIFICATION_LINE_ID = /^[A-Za-z0-9_-]{1,40}$/;
const NEW_LINE_ID_PREFIX = "check-";

/** Extra sampling segment: classifies the bag again, never counts it. */
export interface VerificationLine {
  id: string;
  name: string;
  line: NormalizedLine;
}

interface VerificationLinesConfig {
  verification_lines?: unknown;
  verification_lines_supported?: boolean;
}

/**
 * Hues at least ~30° from each other and ~55° from the sky-blue counting
 * line (COUNT_LINE_COLOR), so every check stays recognisable on video; the
 * eighth is neutral.
 */
export const VERIFICATION_LINE_COLORS = [
  "#f59e0b",
  "#e879f9",
  "#a3e635",
  "#f87171",
  "#a78bfa",
  "#4ade80",
  "#f472b6",
  "#e5e7eb",
] as const;

export function verificationLineColor(index: number): string {
  return VERIFICATION_LINE_COLORS[index % VERIFICATION_LINE_COLORS.length];
}

function isPoint(line: NormalizedLine) {
  return line.x1 === line.x2 && line.y1 === line.y2;
}

/**
 * Saved lines as the editor keeps them. A short-but-valid saved segment is
 * kept: dropping it here would silently delete it on the next save.
 */
export function normalizeVerificationLines(value: unknown): VerificationLine[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const raw = item as Record<string, unknown>;
    if (typeof raw.id !== "string" || !raw.id) return [];
    const line = parseSegment(raw.line ?? raw.line_spec);
    if (!line || isPoint(line)) return [];
    const name = typeof raw.name === "string" && raw.name.trim() ? raw.name : raw.id;
    return [{ id: raw.id, name, line }];
  });
}

/** CRM reports it explicitly; a bare array still means the AI knows the field. */
export function verificationLinesSupported(config: VerificationLinesConfig | null | undefined): boolean {
  return config?.verification_lines_supported ?? Array.isArray(config?.verification_lines);
}

// The camera PC compares segments after formatting them with 8 significant digits.
function segmentKey(line: NormalizedLine) {
  return [line.x1, line.y1, line.x2, line.y2].map((value) => Number(value.toPrecision(8)));
}

function sameSegment(left: NormalizedLine, right: NormalizedLine) {
  const [a, b] = [segmentKey(left), segmentKey(right)];
  const reversed = [b[2], b[3], b[0], b[1]];
  return a.every((value, index) => value === b[index]) || a.every((value, index) => value === reversed[index]);
}

// Vertical cuts across a typical horizontal conveyor, spread over the frame.
// Neighbouring cuts start at different heights: the name tag sits at the upper
// end, and on a phone-width frame two tags on one row would overprint.
const SUGGESTED_CUTS: ReadonlyArray<readonly [x: number, top: number]> = [
  [0.25, 0.15],
  [0.75, 0.15],
  [0.375, 0.25],
  [0.625, 0.25],
  [0.125, 0.35],
  [0.875, 0.35],
  [0.1875, 0.45],
  [0.8125, 0.45],
  [0.3125, 0.55],
  [0.6875, 0.55],
];
const SUGGESTED_BOTTOM = 0.85;
const NEW_LINE_ID = new RegExp(`^${NEW_LINE_ID_PREFIX}(\\d+)$`);

/**
 * A new line with a free segment. Its «check-N» id is never handed out again
 * while a higher one exists, so a deleted line's diagnostics are not
 * confused with a new one; the default name takes the smallest free number.
 */
export function nextVerificationLine(existing: VerificationLine[], countLine: NormalizedLine): VerificationLine {
  const numbers = existing.map((item) => Number(NEW_LINE_ID.exec(item.id)?.[1] ?? 0));
  const highest = Math.max(0, ...numbers.filter(Number.isSafeInteger));
  const names = new Set(existing.map((item) => item.name.trim()));
  let number = 1;
  while (names.has(`Проверка ${number}`)) number += 1;
  const taken = [countLine, ...existing.map((item) => item.line)];
  const [x, top] = SUGGESTED_CUTS.find(
    ([cut, y]) => !taken.some((segment) => sameSegment(segment, { x1: cut, y1: y, x2: cut, y2: SUGGESTED_BOTTOM })),
  ) ?? [0.5, 0.15];
  return {
    id: `${NEW_LINE_ID_PREFIX}${highest + 1}`,
    name: `Проверка ${number}`,
    line: { x1: x, y1: top, x2: x, y2: SUGGESTED_BOTTOM },
  };
}

/** The first reason the server would refuse this setup, in operator words. */
export function lineSetupError(countLine: NormalizedLine, lines: VerificationLine[]): string | null {
  if (!validCountingLine(countLine)) return "Линия слишком короткая. Протяните её между двумя разными точками.";
  if (lines.length > MAX_VERIFICATION_LINES) return `Не больше ${MAX_VERIFICATION_LINES} линий проверки.`;
  const ids = new Set<string>([COUNT_LINE_ID]);
  const segments = [countLine];
  for (const item of lines) {
    const name = item.name.trim();
    const label = `Линия проверки «${name || item.id}»`;
    if (!VERIFICATION_LINE_ID.test(item.id)) return `${label}: некорректный идентификатор.`;
    if (ids.has(item.id)) return `${label}: идентификатор повторяется.`;
    if (!name) return "Укажите название для каждой линии проверки.";
    if (name.length > VERIFICATION_LINE_NAME_MAX) {
      return `Название линии проверки должно быть не длиннее ${VERIFICATION_LINE_NAME_MAX} символов.`;
    }
    const { x1, y1, x2, y2 } = item.line;
    if (![x1, y1, x2, y2].every(inFrame)) return `${label}: координаты должны быть от 0 до 1.`;
    // Like the camera PC: any non-zero length. A short line drawn in its own
    // editor must not block saving an unrelated change here.
    if (isPoint(item.line)) return `${label}: концы совпадают. Протяните её между двумя разными точками.`;
    if (segments.some((segment) => sameSegment(segment, item.line))) {
      return `${label} совпадает с основной линией или другой линией проверки.`;
    }
    ids.add(item.id);
    segments.push(item.line);
  }
  return null;
}

/** PUT body; ``null`` lines = the AI cannot store them, so leave its lines untouched. */
export function countingLineSaveBody(
  line: NormalizedLine,
  direction: LineDirection,
  verificationLines: VerificationLine[] | null,
) {
  return {
    line,
    direction,
    ...(verificationLines
      ? {
          verification_lines: verificationLines.map((item) => ({
            id: item.id,
            name: item.name.trim(),
            line: item.line,
          })),
        }
      : {}),
  };
}
