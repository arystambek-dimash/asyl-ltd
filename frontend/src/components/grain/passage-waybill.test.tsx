import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { GrainWagon } from "@/lib/types";
import { makeGrainWagon } from "@/test-utils/grain";
import { PassageWaybill, PassageWaybillPage } from "./passage-waybill";

const mocks = vi.hoisted(() => ({ trip: null as GrainWagon | null, urls: [] as (string | null)[] }));
vi.mock("@/lib/use-api", () => ({
  useApi: (url: string | null) => {
    mocks.urls.push(url);
    return {
      data: url ? mocks.trip : null,
      loading: false,
      error: "",
      errorStatus: null,
      reload: vi.fn(),
      setData: vi.fn(),
    };
  },
}));

function trip(overrides: Partial<GrainWagon> = {}): GrainWagon {
  return makeGrainWagon({
    id: 124,
    number: "904WLY13",
    direction: "passage",
    cargo_name: "Отруби",
    status: "completed",
    status_label: "Завершён",
    arrived_at: "2026-09-10T04:07:00Z",
    gross_weight_kg: 3680,
    tare_weight_kg: 8640,
    net_weight_kg: 4960,
    entry_weight_kg: 3680,
    exit_weight_kg: 8640,
    silo_arrived_at: "2026-09-10T04:07:00Z",
    // 09:59 in Almaty: the release note is dated by the plant's calendar day.
    exited_at: "2026-09-10T04:59:23Z",
    created_at: "2026-09-11T03:36:09Z",
    ...overrides,
  });
}

function field(name: string) {
  return screen.getByRole("textbox", { name });
}

beforeEach(() => {
  mocks.urls = [];
  mocks.trip = trip();
});

describe("PassageWaybill", () => {
  it("fills the release note of a completed truck from its trip", () => {
    render(<PassageWaybill trip={trip()} />);
    expect(field("Номер накладной")).toHaveTextContent("124");
    expect(field("Дата накладной")).toHaveTextContent("10.09.2026");
    expect(field("Номер автомашины")).toHaveTextContent("904WLY13");
    expect(field("Наименование, строка 1")).toHaveTextContent("КЕБЕК");
    expect(field("Пустой груз, строка 1")).toHaveTextContent("3 680");
    expect(field("Груженый груз, строка 1")).toHaveTextContent("8 640");
    expect(field("Масса нетто, строка 1")).toHaveTextContent("4 960");
    expect(field("Покупатель").textContent).toBe("");
    expect(field("Цена за кг, строка 1").textContent).toBe("");
  });

  it("prices the net weight and totals it once a price per kg is typed", () => {
    render(<PassageWaybill trip={trip()} />);
    const price = field("Цена за кг, строка 1");
    price.textContent = "25";
    fireEvent.input(price);
    expect(field("Стоимость, строка 1")).toHaveTextContent("124 000");
    expect(field("Итого")).toHaveTextContent("124 000");
    price.textContent = "25,5";
    fireEvent.input(price);
    expect(field("Стоимость, строка 1")).toHaveTextContent("126 480");
    expect(field("Итого")).toHaveTextContent("126 480");
  });

  it("prints the sheet", async () => {
    const print = vi.spyOn(window, "print").mockImplementation(() => undefined);
    render(<PassageWaybill trip={trip()} />);
    await userEvent.click(screen.getByRole("button", { name: "Печать" }));
    expect(print).toHaveBeenCalledOnce();
  });
});

describe("PassageWaybillPage", () => {
  it("loads the trip from the outbound API", () => {
    render(<PassageWaybillPage tripId={124} />);
    expect(mocks.urls).toContain("/grain/passages/124/");
    expect(field("Номер автомашины")).toHaveTextContent("904WLY13");
  });

  it("explains that an unfinished truck has no release note yet", () => {
    mocks.trip = trip({
      status: "at_silo",
      exit_weight_kg: null,
      tare_weight_kg: null,
      net_weight_kg: null,
      exited_at: null,
    });
    render(<PassageWaybillPage tripId={124} />);
    expect(screen.getByText("Накладная доступна после завершения вывоза.")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Номер накладной" })).toBeNull();
  });
});
