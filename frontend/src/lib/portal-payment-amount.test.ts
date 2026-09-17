import { describe, expect, it } from "vitest";
import { eraseAmount, fullAmount, paymentAmountError, pressAmountDigit } from "./portal-payment-amount";

describe("сумма оплаты в кабинете", () => {
  it("весь остаток без лишних нулей, но с тиынами", () => {
    expect(fullAmount("50000.00")).toBe("50000");
    expect(fullAmount("0.01")).toBe("0.01");
    expect(fullAmount("1250.50")).toBe("1250.5");
    expect(fullAmount(null)).toBe("");
  });

  it("клавиатура набирает целые тенге, после «Весь остаток» с тиынами начинает заново", () => {
    expect(pressAmountDigit("", "0")).toBe("");
    expect(pressAmountDigit("", "5")).toBe("5");
    expect(pressAmountDigit("50", "0")).toBe("500");
    expect(pressAmountDigit("1250.5", "3")).toBe("3");
    expect(pressAmountDigit("9999999999", "1")).toBe("9999999999");
    expect(eraseAmount("500")).toBe("50");
    expect(eraseAmount("0.01")).toBe("");
  });

  it("не пропускает пустую, нулевую и больше доступного сумму", () => {
    expect(paymentAmountError("", 100)).toBe("Введите сумму оплаты.");
    expect(paymentAmountError("0", 100)).toBe("Сумма должна быть больше нуля.");
    expect(paymentAmountError("150", 100)).toBe("Доступно не более 100.");
    expect(paymentAmountError("0.01", 0.01)).toBe("");
  });
});
