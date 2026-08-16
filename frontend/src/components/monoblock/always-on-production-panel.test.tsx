import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AlwaysOnProductionPayload } from "@/lib/types";
import { AlwaysOnProductionPanel } from "./always-on-production-panel";

const payload: AlwaysOnProductionPayload = {
  camera: "cam1",
  timezone: "Asia/Almaty",
  close_time: "19:00",
  current_business_day: "2026-08-16",
  next_run_at: "2026-08-16T14:00:00Z",
  fully_configured: false,
  available_colors: ["red", "blue"],
  mappings: [{ color: "red", product: 1, product_label: "Мука красная · 50 кг" }],
  products: [
    { id: 1, label: "Мука красная · 50 кг", color: "Red", color_label: "Красный", weight_kg: "50.00" },
    { id: 2, label: "Мука синяя · 25 кг", color: "Blue", color_label: "Синий", weight_kg: "25.00" },
    { id: 3, label: "Мука зелёная · 50 кг", color: "Green", color_label: "Зелёный", weight_kg: "50.00" },
  ],
  runs: [
    {
      id: 7,
      camera: "cam1",
      business_day: "2026-08-16",
      color: "red",
      started_at: "2026-08-16T04:30:00Z",
      last_counted_at: "2026-08-16T04:45:00Z",
      ended_at: null,
      model_bags: 126,
      is_approximate: false,
      status: "active",
    },
  ],
  preview: [
    {
      color: "red",
      detected_bags: 126,
      correction_bags: 0,
      net_bags: 126,
      product: 1,
      product_label: "Мука красная · 50 кг",
      configured: true,
    },
  ],
  batches: [],
};

describe("AlwaysOnProductionPanel", () => {
  it("показывает активный цвет и следующий автоприход", () => {
    render(<AlwaysOnProductionPanel payload={payload} loading={false} error={null} saving={false} onSave={vi.fn()} />);

    expect(screen.getByText("идёт сейчас")).toBeInTheDocument();
    expect(screen.getAllByText("126")).toHaveLength(2);
    expect(screen.getByText("19:00")).toBeInTheDocument();
    expect(screen.getByText(/Не настроено:/)).toHaveTextContent("Синий");
  });

  it("предлагает товар совпадающего цвета и сохраняет выбранное сопоставление", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(<AlwaysOnProductionPanel payload={payload} loading={false} error={null} saving={false} onSave={onSave} />);

    const blueSelect = screen.getByLabelText("Товар для цвета Синий");
    expect(within(blueSelect).getAllByRole("option")).toHaveLength(2);
    expect(within(blueSelect).queryByRole("option", { name: /зелёная/ })).not.toBeInTheDocument();

    await user.selectOptions(blueSelect, "2");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(onSave).toHaveBeenCalledWith([
      { color: "red", product: 1, product_label: "Мука красная · 50 кг" },
      { color: "blue", product: 2, product_label: "Мука синяя · 25 кг" },
    ]);
  });
});
