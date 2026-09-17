import { describe, expect, it } from "vitest";
import { PERMISSION_PRESETS, SECTION_ORDER, applyPreset, sectionRank } from "./permission-presets";

describe("permission presets", () => {
  it("replace the selection but skip codes the admin cannot grant", () => {
    const cashier = PERMISSION_PRESETS.find((preset) => preset.key === "cashier")!;
    const next = applyPreset(cashier, new Set(["reports.view"]));
    expect(next.has("payments.confirm")).toBe(true);
    expect(next.has("reports.view")).toBe(false);
  });

  it("use only sections of the catalog", () => {
    for (const preset of PERMISSION_PRESETS) {
      for (const code of preset.codes) expect(SECTION_ORDER).toContain(code.split(".")[0]);
    }
  });

  it("rank unknown sections last", () => {
    expect(sectionRank("orders")).toBeLessThan(sectionRank("tasks"));
    expect(sectionRank("legacy")).toBe(SECTION_ORDER.length);
  });
});
