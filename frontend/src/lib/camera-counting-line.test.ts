import { describe, expect, it } from "vitest";
import {
  COUNT_LINE_COLOR,
  countingLineSaveBody,
  defaultCountingLine,
  lineSetupError,
  MAX_VERIFICATION_LINES,
  nextVerificationLine,
  normalizeCountingLine,
  normalizeVerificationLines,
  resolveCountingLine,
  verificationLinesSupported,
  VERIFICATION_LINE_COLORS,
  type NormalizedLine,
  type VerificationLine,
} from "./camera-counting-line";

describe("camera counting line contract", () => {
  it("parses the compact line reported by a running processor", () => {
    expect(normalizeCountingLine("0.08,0.61,0.93,0.58")).toEqual({
      x1: 0.08,
      y1: 0.61,
      x2: 0.93,
      y2: 0.58,
    });
  });

  it("keeps the browser fallback identical to the camera-PC default", () => {
    expect(defaultCountingLine()).toEqual({ x1: 0, y1: 0.5, x2: 1, y2: 0.5 });
  });

  it("prefers the line actually applied to the live processor", () => {
    expect(
      resolveCountingLine(
        { line: "0.1,0.2,0.8,0.9", direction: "down" },
        { line: { x1: 0, y1: 0.5, x2: 1, y2: 0.5 }, direction: "up" },
      ),
    ).toEqual({
      line: { x1: 0.1, y1: 0.2, x2: 0.8, y2: 0.9 },
      direction: "down",
    });
  });

  it("uses persisted inventory only as an old-service/offline fallback", () => {
    expect(resolveCountingLine(null, { line: { x1: 0.2, y1: 0.3, x2: 0.7, y2: 0.8 }, direction: "positive" })).toEqual({
      line: { x1: 0.2, y1: 0.3, x2: 0.7, y2: 0.8 },
      direction: "positive",
    });
  });

  it.each(["bad", "0.1,,0.8,0.9", "0.1,0.2,2,0.9", "0.5,0.5,0.5,0.5", null])(
    "does not draw a malformed line: %s",
    (line) => {
      expect(resolveCountingLine({ line }, null)).toBeNull();
    },
  );
});

const COUNT: NormalizedLine = { x1: 0.1, y1: 0.5, x2: 0.9, y2: 0.5 };

function check(id: string, name: string, x: number): VerificationLine {
  return { id, name, line: { x1: x, y1: 0.1, x2: x, y2: 0.9 } };
}

describe("verification lines contract", () => {
  it("reads saved lines, keeping server ids and the legacy line_spec form", () => {
    expect(
      normalizeVerificationLines([
        { id: "before", name: "До механизма", line: { x1: 0.714, y1: 0.354, x2: 0.744, y2: 0.716 } },
        { id: "after", line_spec: "0.189,0.327,0.189,0.851" },
        { id: "broken", name: "Без геометрии" },
        "garbage",
      ]),
    ).toEqual([
      { id: "before", name: "До механизма", line: { x1: 0.714, y1: 0.354, x2: 0.744, y2: 0.716 } },
      { id: "after", name: "after", line: { x1: 0.189, y1: 0.327, x2: 0.189, y2: 0.851 } },
    ]);
    expect(normalizeVerificationLines(undefined)).toEqual([]);
  });

  it("keeps a very short saved line instead of silently deleting it on the next save", () => {
    expect(normalizeVerificationLines([{ id: "tiny", name: "Малая", line: [0.5, 0.5, 0.501, 0.5] }])).toHaveLength(1);
  });

  it("detects an AI service that does not know verification lines", () => {
    expect(verificationLinesSupported({ verification_lines: [], verification_lines_supported: false })).toBe(false);
    expect(verificationLinesSupported({ verification_lines: [], verification_lines_supported: true })).toBe(true);
    expect(verificationLinesSupported({ verification_lines: [] })).toBe(true);
    expect(verificationLinesSupported({})).toBe(false);
  });

  it("creates stable slug ids and default names without colliding with existing lines", () => {
    const first = nextVerificationLine([], COUNT);
    expect(first).toMatchObject({ id: "check-1", name: "Проверка 1" });
    const second = nextVerificationLine([first], COUNT);
    expect(second).toMatchObject({ id: "check-2", name: "Проверка 2" });
    expect(second.line).not.toEqual(first.line);
    expect(lineSetupError(COUNT, [first, second])).toBeNull();
  });

  it("never hands a deleted line's id to a new, different segment", () => {
    const [first, second] = [nextVerificationLine([], COUNT), check("check-2", "Проверка 2", 0.75)];
    const replacement = nextVerificationLine([second], COUNT);

    expect(replacement.id).toBe("check-3");
    expect(replacement.id).not.toBe(first.id);
    // Only the default name reuses the free number.
    expect(replacement.name).toBe("Проверка 1");
    expect(nextVerificationLine([check("before", "До механизма", 0.3)], COUNT).id).toBe("check-1");
  });

  it("staggers suggested lines so neighbouring name tags do not overprint", () => {
    const lines: VerificationLine[] = [];
    for (let index = 0; index < MAX_VERIFICATION_LINES; index += 1) {
      lines.push(nextVerificationLine(lines, COUNT));
    }
    for (const [index, left] of lines.entries()) {
      for (const right of lines.slice(index + 1)) {
        // «Проверка N» spans ~0.2 of a phone-width frame; a row is ~0.1 of its height.
        const apart = Math.abs(left.line.x1 - right.line.x1) >= 0.25 || Math.abs(left.line.y1 - right.line.y1) >= 0.1;
        expect(apart, `${left.name} / ${right.name}`).toBe(true);
      }
    }
    expect(lineSetupError(COUNT, lines)).toBeNull();
  });

  it("keeps every verification colour clearly apart from the counting line and each other", () => {
    const hue = (hex: string) => {
      const [r, g, b] = [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16) / 255);
      const [max, min] = [Math.max(r, g, b), Math.min(r, g, b)];
      if (max - min < 0.1) return null; // neutral
      const degrees =
        max === r
          ? 60 * ((g - b) / (max - min))
          : max === g
            ? 60 * (2 + (b - r) / (max - min))
            : 60 * (4 + (r - g) / (max - min));
      return (degrees + 360) % 360;
    };
    const apart = (left: number, right: number) => Math.min(Math.abs(left - right), 360 - Math.abs(left - right));
    const hues = VERIFICATION_LINE_COLORS.map(hue).filter((value): value is number => value !== null);

    expect(VERIFICATION_LINE_COLORS.length - hues.length).toBeLessThanOrEqual(1);
    for (const [index, value] of hues.entries()) {
      expect(apart(value, hue(COUNT_LINE_COLOR)!)).toBeGreaterThanOrEqual(45);
      for (const other of hues.slice(index + 1)) expect(apart(value, other)).toBeGreaterThanOrEqual(30);
    }
  });

  it.each<[string, NormalizedLine, VerificationLine[], string]>([
    ["short counting line", { x1: 0.5, y1: 0.5, x2: 0.501, y2: 0.5 }, [], "Линия слишком короткая"],
    [
      "too many lines",
      COUNT,
      Array.from({ length: 9 }, (_, index) => check(`c${index}`, `П${index}`, (index + 1) / 10)),
      "Не больше 8 линий проверки",
    ],
    ["bad id", COUNT, [check("имя", "Проверка", 0.3)], "некорректный идентификатор"],
    ["reserved id", COUNT, [check("count", "Проверка", 0.3)], "идентификатор повторяется"],
    ["duplicate id", COUNT, [check("a", "Первая", 0.3), check("a", "Вторая", 0.4)], "идентификатор повторяется"],
    ["empty name", COUNT, [check("a", "   ", 0.3)], "Укажите название"],
    ["long name", COUNT, [check("a", "Я".repeat(81), 0.3)], "не длиннее 80 символов"],
    [
      "out of frame",
      COUNT,
      [{ id: "a", name: "Край", line: { x1: 0.3, y1: -0.1, x2: 0.3, y2: 0.9 } }],
      "координаты должны быть от 0 до 1",
    ],
    [
      "degenerate",
      COUNT,
      [{ id: "a", name: "Точка", line: { x1: 0.3, y1: 0.3, x2: 0.3, y2: 0.3 } }],
      "«Точка»: концы совпадают",
    ],
    [
      "same as counting line (reversed)",
      COUNT,
      [{ id: "a", name: "Копия", line: { x1: 0.9, y1: 0.5, x2: 0.1, y2: 0.5 } }],
      "«Копия» совпадает с основной линией или другой линией проверки",
    ],
    ["same as another check", COUNT, [check("a", "Первая", 0.3), check("b", "Вторая", 0.3)], "«Вторая» совпадает"],
  ])("rejects %s like the server does", (_case, count, lines, message) => {
    expect(lineSetupError(count, lines)).toContain(message);
  });

  it("accepts a short but non-zero verification line, like the camera PC", () => {
    const tiny = { id: "tiny", name: "Малая", line: { x1: 0.5, y1: 0.5, x2: 0.505, y2: 0.5 } };
    expect(lineSetupError(COUNT, [tiny])).toBeNull();
  });

  it("sends verification lines only when the AI service supports them", () => {
    const lines = [{ ...check("check-1", "  До механизма ", 0.3) }];
    expect(countingLineSaveBody(COUNT, "down", lines)).toEqual({
      line: COUNT,
      direction: "down",
      verification_lines: [{ id: "check-1", name: "До механизма", line: lines[0].line }],
    });
    expect(countingLineSaveBody(COUNT, "any", [])).toEqual({ line: COUNT, direction: "any", verification_lines: [] });
    expect(countingLineSaveBody(COUNT, "any", null)).toEqual({ line: COUNT, direction: "any" });
  });
});
