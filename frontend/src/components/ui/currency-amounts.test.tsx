import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CurrencyAmounts } from "./currency-amounts";

describe("CurrencyAmounts", () => {
  it("renders unlike currencies separately in deterministic order", () => {
    render(<CurrencyAmounts byCurrency={{ USD: "5.00", KZT: "1000.00" }} fallbackCurrency="KZT" />);

    const amounts = screen.getAllByText(/[₸$]/);
    expect(amounts).toHaveLength(2);
    // jest-dom normalizes all whitespace (including NBSP) before matching.
    expect(amounts[0]).toHaveTextContent("1 000 ₸");
    expect(amounts[1]).toHaveTextContent("5 $");
    expect(screen.queryByText(/1.?005/)).not.toBeInTheDocument();
  });

  it("uses an explicitly-currency fallback only for an empty breakdown", () => {
    const { rerender } = render(<CurrencyAmounts byCurrency={{}} fallbackAmount="25" fallbackCurrency="USD" />);
    expect(screen.getByText("25 $")).toBeInTheDocument();

    rerender(<CurrencyAmounts byCurrency={{}} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
