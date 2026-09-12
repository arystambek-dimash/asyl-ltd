import { describe, expect, it } from "vitest";
import { safeBackPath, withBack } from "./navigation";

describe("safeBackPath", () => {
  it("keeps an internal app path unchanged", () => {
    expect(safeBackPath("/accounting?view=debts")).toBe("/accounting?view=debts");
  });

  it.each([["https://evil.example"], ["//evil"], [null]])("rejects unsafe input %s", (value) => {
    expect(safeBackPath(value)).toBeNull();
  });

  it("rejects backslash bypass /\\evil.com", () => {
    expect(safeBackPath("/\\evil.com")).toBeNull();
  });

  it("rejects tab bypass /\\t/evil.com", () => {
    expect(safeBackPath("/\t/evil.com")).toBeNull();
  });

  it("rejects javascript: protocol", () => {
    expect(safeBackPath("javascript:alert(1)")).toBeNull();
  });

  it("preserves query and hash in safe paths", () => {
    expect(safeBackPath("/orders/1?x=1#y")).toBe("/orders/1?x=1#y");
  });

  it("accepts root path", () => {
    expect(safeBackPath("/")).toBe("/");
  });
});

describe("withBack", () => {
  it("appends an encoded back parameter", () => {
    expect(withBack("/orders/12", "/accounting?view=confirm")).toBe("/orders/12?back=%2Faccounting%3Fview%3Dconfirm");
  });
});
