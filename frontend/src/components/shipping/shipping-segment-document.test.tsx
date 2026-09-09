import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ShippingSegmentDetail } from "@/lib/shipping-sessions";
import { ShippingSegmentDocument } from "./shipping-segment-document";

const segment: ShippingSegmentDetail = {
  id: 101,
  session_id: 10,
  order_id: null,
  camera: "cam2",
  recognition_model: "vehicle_number",
  number: "111AAA01",
  number_source: "gpt",
  identity_status: "identified",
  identity_error: "",
  started_at: "2026-01-01T05:00:00Z",
  last_counted_at: "2026-01-01T05:10:00Z",
  ended_at: "2026-01-01T05:15:00Z",
  total_bags: 120,
  photo_url: "/fixture/segment-101.jpg",
  photo_taken_at: "2026-01-01T05:00:02Z",
  idle_timeout_seconds: 300,
  can_identify: false,
};

describe("ShippingSegmentDocument", () => {
  it("prints the actual individual segment count, number, period and image, without making up commercial details", async () => {
    const user = userEvent.setup();
    const print = vi.spyOn(window, "print").mockImplementation(() => undefined);
    render(<ShippingSegmentDocument segment={segment} />);
    expect(screen.getByRole("heading", { name: "Накладная отрезка #101" })).toBeInTheDocument();
    expect(screen.getByText("120 меш.")).toBeInTheDocument();
    expect(screen.getByText("111AAA01")).toBeInTheDocument();
    expect(screen.getByText("GPT")).toBeInTheDocument();
    expect(screen.getByText("01.01.2026, 10:00:00")).toBeInTheDocument();
    expect(screen.getByText("01.01.2026, 10:15:00")).toBeInTheDocument();
    expect(screen.queryByText(/Цена|Сумма|НДС/)).toBeNull();
    expect(screen.getByRole("button", { name: "Загружаем кадр…" })).toBeDisabled();
    const photo = screen.getByRole("img");
    expect(photo).toHaveAttribute("src", expect.stringContaining(segment.photo_url!));
    fireEvent.load(photo);
    await user.click(await screen.findByRole("button", { name: "Печать накладной" }));
    expect(print).toHaveBeenCalledOnce();
  });
  it("marks an active segment as intermediate and visibly records a missing photo", () => {
    render(
      <ShippingSegmentDocument
        segment={{ ...segment, ended_at: null, photo_url: null, number: "", number_source: "" }}
      />,
    );
    expect(screen.getByText(/Промежуточная накладная/)).toBeInTheDocument();
    expect(screen.getByText("Ещё не завершён")).toBeInTheDocument();
    expect(screen.getByText("Кадр номера недоступен")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Печать накладной" })).toBeEnabled();
  });
  it("does not leave printing stuck when the saved photo fails to load", () => {
    render(<ShippingSegmentDocument segment={segment} />);
    fireEvent.error(screen.getByRole("img"));
    expect(screen.getByText("Кадр номера недоступен")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Печать накладной" })).toBeEnabled();
  });
});
