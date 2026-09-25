import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OrderTransportBadge, PlateBadge } from "./transport-number";

const wagon = { number: "28087658", product_label: "Мука", bags: 1360, weight_kg: "68000" };

describe("transport numbers", () => {
  it("preserves all wagon digits and leading zeros", () => {
    render(<OrderTransportBadge order={{ transport_type: "train", truck_number: "00123456" }} />);
    expect(screen.getByText("00123456")).toBeInTheDocument();
    expect(screen.queryByText("KZ")).not.toBeInTheDocument();
  });

  it("shows a shipment by the wagon report as a headline", () => {
    render(
      <OrderTransportBadge
        order={{ transport_type: "train", truck_number: "", rail_station: "Раустан", wagons: [wagon, wagon] }}
      />,
    );
    expect(screen.getByText("2 вагона · ст. Раустан")).toBeInTheDocument();
  });

  it("keeps vehicle plate formatting", () => {
    render(<OrderTransportBadge order={{ transport_type: "truck", truck_number: "123ABC02" }} />);
    expect(screen.getByText("KZ")).toBeInTheDocument();
    expect(screen.getByText("123 ABC 02")).toBeInTheDocument();
  });

  it("shows the trailer next to the truck", () => {
    render(
      <OrderTransportBadge
        order={{ transport_type: "truck", truck_number: "07KG695ADT", trailer_number: "07KG837PB" }}
      />,
    );
    expect(screen.getByText("07 KG 695 ADT")).toBeInTheDocument();
    expect(screen.getByText("07 KG 837 PB")).toBeInTheDocument();
    expect(screen.getAllByText("KG")).toHaveLength(2);
  });

  it("marks an order without a number", () => {
    render(<OrderTransportBadge order={{ transport_type: "truck", truck_number: "" }} />);
    expect(screen.getByText("Без номера")).toBeInTheDocument();
  });

  it("an ambiguous plate is shown without a country", () => {
    render(<PlateBadge value="01123ABC" />);
    expect(screen.getByText("01 123 ABC")).toBeInTheDocument();
    expect(screen.queryByText("KG")).not.toBeInTheDocument();
    expect(screen.queryByText("UZ")).not.toBeInTheDocument();
  });
});
