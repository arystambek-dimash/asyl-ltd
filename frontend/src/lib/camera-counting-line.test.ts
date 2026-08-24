import { describe, expect, it } from "vitest";
import { defaultCountingLine, normalizeCountingLine, resolveCountingLine } from "./camera-counting-line";

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
