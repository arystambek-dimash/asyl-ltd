import { describe, expect, it } from "vitest";
import { PERMISSION_PRESETS, applyPreset } from "./permission-presets";

describe("permission presets", () => {
  it("replace the selection but skip codes the admin cannot grant", () => {
    const cashier = PERMISSION_PRESETS.find((preset) => preset.key === "cashier")!;
    const next = applyPreset(cashier, new Set(["reports.view"]));
    expect(next.has("payments.confirm")).toBe(true);
    expect(next.has("reports.view")).toBe(false);
  });

  it("offer a loader template per area: trucks and wagons", () => {
    const loaders = PERMISSION_PRESETS.filter((preset) => preset.codes.includes("loader.confirm"));

    expect(loaders.map((preset) => preset.label)).toEqual(["Грузчик: фуры", "Грузчик: вагоны"]);
    expect(loaders[0].codes).toContain("loader.trucks");
    expect(loaders[0].codes).not.toContain("loader.wagons");
    expect(loaders[1].codes).toContain("loader.wagons");
    expect(loaders[1].codes).not.toContain("loader.trucks");
  });
});
