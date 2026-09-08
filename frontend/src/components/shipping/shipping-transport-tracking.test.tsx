import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ShippingTransportTracking } from "@/lib/types";
import { ShippingTransportTrackingDetails } from "./shipping-transport-tracking";

const tracking: ShippingTransportTracking = {
  schema_version: 1,
  basis: "transport_body",
  presence: "present",
  motion: "stationary",
  visit_id: "visit-a",
  observed_at: "2026-09-08T05:03:00Z",
  present_since: "2026-09-08T05:00:00Z",
  last_seen_at: "2026-09-08T05:03:00Z",
  stationary_since: "2026-09-08T05:01:00Z",
  absent_since: null,
  detection_count: 1,
  reason: "",
  number_associated: true,
};

describe("ShippingTransportTrackingDetails", () => {
  it("distinguishes standing, moving and detected transport without inventing departure", () => {
    const { rerender } = render(<ShippingTransportTrackingDetails tracking={tracking} />);
    expect(screen.getByText("Транспорт стоит")).toBeInTheDocument();
    expect(screen.getByText(/Стоит с:/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    rerender(<ShippingTransportTrackingDetails tracking={{ ...tracking, motion: "moving" }} />);
    expect(screen.getByText("Транспорт движется")).toBeInTheDocument();
    expect(screen.queryByText(/Стоит с:/)).not.toBeInTheDocument();
    rerender(<ShippingTransportTrackingDetails tracking={{ ...tracking, motion: "unknown" }} />);
    expect(screen.getByText("Транспорт обнаружен")).toBeInTheDocument();
    rerender(
      <ShippingTransportTrackingDetails
        tracking={{ ...tracking, presence: "absent", absent_since: "2026-09-08T05:04:00Z" }}
      />,
    );
    expect(screen.getByText("Транспорт не обнаружен")).toBeInTheDocument();
    expect(screen.getByText(/Не обнаружен с:/)).toBeInTheDocument();
    expect(screen.queryByText(/уехал|выехал|Погрузка завершена/i)).not.toBeInTheDocument();
  });

  it("requires the body detector and explains an unavailable model without claiming absence", () => {
    const { rerender } = render(
      <ShippingTransportTrackingDetails tracking={{ ...tracking, presence: "unknown", reason: "model_unavailable" }} />,
    );
    expect(screen.getByText("Присутствие неизвестно")).toBeInTheDocument();
    expect(screen.getByText("Детектор транспорта не настроен или недоступен")).toBeInTheDocument();
    expect(screen.queryByText("Транспорт не обнаружен")).not.toBeInTheDocument();
    rerender(
      <ShippingTransportTrackingDetails
        tracking={{ ...tracking, basis: "number_ocr" } as unknown as ShippingTransportTracking}
      />,
    );
    expect(screen.getByText("Присутствие неизвестно")).toBeInTheDocument();
    expect(screen.queryByText("Транспорт стоит")).not.toBeInTheDocument();
  });

  it("marks stale live observations unknown while preserving the recorded historical state", () => {
    const { rerender } = render(<ShippingTransportTrackingDetails tracking={tracking} stale />);
    expect(screen.getByText("Присутствие неизвестно")).toBeInTheDocument();
    expect(screen.getByText("Нет свежих данных о присутствии транспорта")).toBeInTheDocument();
    rerender(<ShippingTransportTrackingDetails tracking={tracking} stale historical />);
    expect(screen.getByText("Транспорт стоит")).toBeInTheDocument();
    expect(screen.getByText("На момент наблюдения")).toBeInTheDocument();
    expect(screen.getByText(/Последнее обнаружение:/)).toBeInTheDocument();
  });

  it.each([
    ["body_detector_unavailable", "Детектор транспорта не настроен или недоступен"],
    ["body_inference_failed", "Не удалось определить присутствие транспорта"],
    ["frame_unusable", "Нет пригодного кадра для определения присутствия транспорта"],
    ["different_transport_type", "Тип транспорта в кадре не соответствует настройке камеры"],
    ["confirming_presence", "Подтверждаем присутствие транспорта по нескольким кадрам"],
    ["confirming_absence", "Проверяем отсутствие транспорта по нескольким кадрам"],
    ["non_fresh_frame", "Нет свежего кадра транспорта"],
  ])("explains the CV reason %s without claiming presence or departure", (reason, message) => {
    render(
      <ShippingTransportTrackingDetails tracking={{ ...tracking, presence: "unknown", motion: "unknown", reason }} />,
    );
    expect(screen.getByText("Присутствие неизвестно")).toBeInTheDocument();
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.queryByText("Транспорт не обнаружен")).not.toBeInTheDocument();
    expect(screen.queryByText("Транспорт стоит")).not.toBeInTheDocument();
  });

  it("keeps body presence distinct from number association and displays an operator alert", () => {
    render(
      <ShippingTransportTrackingDetails
        tracking={{ ...tracking, number_associated: false }}
        alert="Транспорт сменился во время погрузки. Проверьте конвейер."
      />,
    );
    expect(screen.getByText("Транспорт стоит")).toBeInTheDocument();
    expect(screen.getByText("Номер ещё не связан с обнаруженным транспортом")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Транспорт сменился во время погрузки");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
