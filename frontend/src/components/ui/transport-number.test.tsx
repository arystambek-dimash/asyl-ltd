import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { formatTransportNumber, TransportNumberBadge } from "./transport-number";

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
    expect(screen.getByText("123 ABC")).toBeInTheDocument();
    expect(screen.getByText("02")).toBeInTheDocument();
  });
});
