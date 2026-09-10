import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { GrainWagon, GrainWeighing } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";
import { WagonPhotos } from "./wagon-photos";

function weighing(extra: Partial<GrainWeighing> = {}): GrainWeighing {
  return {
    id: 21,
    kind: "gross",
    weight_kg: 4100,
    scale_number: "truck",
    source: "manual",
    manual_reason: "Пропущенный заезд подтверждён по журналу",
    previous_weight_kg: null,
    operator_name: "Арман Весовщик",
    photo_url: null,
    photo_status: "pending",
    created_at: "2026-01-02T09:00:00Z",
    ...extra,
  };
}
function wagon(extra: Partial<GrainWagon>): GrainWagon {
  return {
    direction: "passage",
    entry_weight_kg: 4100,
    exit_weight_kg: 9000,
    entry_photo_url: null,
    exit_photo_url: null,
    ...extra,
  } as GrainWagon;
}

describe("weighing photo provenance", () => {
  it("identifies saved manual tare by its original date instead of waiting for an entry photo", () => {
    const originalDate = "2025-12-01T08:00:00Z";
    render(
      <WagonPhotos
        wagon={wagon({
          exit_photo_url: "https://crm.test/exit.jpg",
          weighings: [
            weighing({
              source: "historical",
              reference_record: 11,
              reference_record_source: "manual",
              reference_record_at: originalDate,
            }),
          ],
        })}
      />,
    );
    expect(screen.getByText(`Сохранённая тара · ручной ввод · ${formatDateTime(originalDate)}`)).toBeInTheDocument();
    expect(screen.getByText("Сохранённая тара введена вручную без фото")).toBeInTheDocument();
    expect(screen.queryByText("Въезд")).not.toBeInTheDocument();
    expect(screen.queryByText("Фото ожидается")).not.toBeInTheDocument();
    expect(screen.queryByText(/появится после взвешивания/)).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Выезд: фото машины" })).toHaveAttribute("src", "https://crm.test/exit.jpg");
  });

  it("marks manual entry and exit weights as records without photos, not pending camera captures", () => {
    render(
      <WagonPhotos
        wagon={wagon({
          weighings: [weighing(), weighing({ id: 22, kind: "tare", weight_kg: 9000, photo_status: "retrying" })],
        })}
      />,
    );
    expect(screen.getByText("Заезд внесён вручную без фото")).toBeInTheDocument();
    expect(screen.getByText("Выездной вес внесён вручную без фото")).toBeInTheDocument();
    expect(screen.queryByText("Фото ожидается")).not.toBeInTheDocument();
    expect(screen.queryByText("Фото загружается повторно")).not.toBeInTheDocument();
    expect(screen.queryByText(/появится после взвешивания/)).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("keeps a saved scale tare's original photo and does not relabel it as manual", () => {
    const originalDate = "2025-12-01T08:00:00Z";
    render(
      <WagonPhotos
        wagon={wagon({
          entry_photo_url: "https://crm.test/original-tare.jpg",
          exit_photo_url: "https://crm.test/exit.jpg",
          weighings: [
            weighing({
              source: "historical",
              reference_record: 11,
              reference_record_source: "scale",
              reference_record_at: originalDate,
            }),
          ],
        })}
      />,
    );
    expect(screen.getByText(`Сохранённая тара · ${formatDateTime(originalDate)}`)).toBeInTheDocument();
    expect(screen.queryByText(/ручной ввод/)).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Сохранённая тара/ })).toHaveAttribute(
      "src",
      "https://crm.test/original-tare.jpg",
    );
    expect(screen.queryByText("Фото ожидается")).not.toBeInTheDocument();
  });
});
