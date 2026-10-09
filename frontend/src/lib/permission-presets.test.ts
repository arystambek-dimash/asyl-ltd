import { describe, expect, it } from "vitest";
import { PERMISSION_PRESETS, applyPreset, newEmployeePermissions } from "./permission-presets";

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

  it("give the storekeeper the returns page next to the warehouse", () => {
    const storekeeper = PERMISSION_PRESETS.find((preset) => preset.key === "storekeeper")!;

    expect(storekeeper.label).toBe("Кладовщик");
    expect(storekeeper.codes).toEqual(
      expect.arrayContaining(["warehouse.view", "warehouse.adjust", "storekeeper.view", "storekeeper.confirm"]),
    );
  });

  it("keep «Главная» and «Задачи» in every template: hiding them is a manual untick", () => {
    for (const preset of PERMISSION_PRESETS) {
      expect(preset.codes).toEqual(expect.arrayContaining(["dashboard.view", "tasks.own"]));
    }
  });

  it("pre-select both pages for a new employee unless the admin cannot grant them", () => {
    expect(newEmployeePermissions(new Set())).toEqual(new Set(["dashboard.view", "tasks.own"]));
    expect(newEmployeePermissions(new Set(["dashboard.view"]))).toEqual(new Set(["tasks.own"]));
  });
});
