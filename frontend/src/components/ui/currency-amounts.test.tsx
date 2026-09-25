import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CurrencyAmounts, OtherCurrencyRows } from "./currency-amounts";

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

describe("OtherCurrencyRows", () => {
  it("lists only non-zero currencies other than the primary one", () => {
    render(<OtherCurrencyRows byCurrency={{ KZT: "1000.00", USD: "5.00", EUR: "0.00" }} primary="KZT" />);

    expect(screen.getByText("Также 5 $")).toBeInTheDocument();
    expect(screen.queryByText(/₸/)).not.toBeInTheDocument();
    expect(screen.queryByText(/EUR/)).not.toBeInTheDocument();
  });

  it("renders nothing when the primary currency is the only one", () => {
    const { container } = render(<OtherCurrencyRows byCurrency={{ KZT: "1000.00" }} primary="KZT" />);
    expect(container).toBeEmptyDOMElement();
  });
});
