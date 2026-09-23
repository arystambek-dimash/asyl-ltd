import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { formatTransportNumber, PlateBadge, TransportNumberBadge } from "./transport-number";

describe("transport numbers", () => {
  it("does not truncate an identifier when an older DTO omits the transport type", () => {
    expect(formatTransportNumber("00123456", undefined)).toBe("00123456");
    render(<TransportNumberBadge value="00123456" transportType={undefined} />);
    expect(screen.getByText("00123456")).toBeInTheDocument();
    expect(screen.queryByText("KZ")).not.toBeInTheDocument();
  });

  it("preserves all wagon digits and leading zeros in text and badge", () => {
    expect(formatTransportNumber("00123456", "train")).toBe("00123456");
    render(<TransportNumberBadge value="00123456" transportType="train" />);
    expect(screen.getByText("Вагон 00123456")).toBeInTheDocument();
    expect(screen.queryByText("KZ")).not.toBeInTheDocument();
  });

  it("keeps vehicle plate formatting", () => {
    expect(formatTransportNumber("123ABC02", "truck")).toBe("123 ABC 02");
    render(<TransportNumberBadge value="123ABC02" transportType="truck" />);
    expect(screen.getByText("KZ")).toBeInTheDocument();
    expect(screen.getByText("123 ABC 02")).toBeInTheDocument();
  });

  it("shows the trailer next to the truck", () => {
    expect(formatTransportNumber("07KG695ADT", "truck", "07KG837PB")).toBe("07 KG 695 ADT / 07 KG 837 PB");
    render(<TransportNumberBadge value="07KG695ADT" transportType="truck" trailer="07KG837PB" />);
    expect(screen.getByText("07 KG 695 ADT")).toBeInTheDocument();
    expect(screen.getByText("07 KG 837 PB")).toBeInTheDocument();
    expect(screen.getAllByText("KG")).toHaveLength(2);
  });

  it("a wagon has no trailer", () => {
    expect(formatTransportNumber("00123456", "train", "07KG837PB")).toBe("00123456");
  });

  it("an ambiguous plate is shown without a country", () => {
    render(<PlateBadge value="01123ABC" />);
    expect(screen.getByText("01 123 ABC")).toBeInTheDocument();
    expect(screen.queryByText("KG")).not.toBeInTheDocument();
    expect(screen.queryByText("UZ")).not.toBeInTheDocument();
  });
});
