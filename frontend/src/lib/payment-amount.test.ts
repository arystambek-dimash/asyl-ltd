import { describe, expect, it } from "vitest";
import { eraseAmount, fullAmount, paymentAmountError, pressAmountDigit } from "./payment-amount";

describe("сумма оплаты", () => {
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
    expect(pressAmountDigit("12", "x")).toBe("12");
    expect(eraseAmount("500")).toBe("50");
    expect(eraseAmount("0.01")).toBe("");
    expect(eraseAmount("")).toBe("");
  });

  it("клавиатура не выходит за доступную сумму", () => {
    expect(pressAmountDigit("12", "3", 1000)).toBe("123");
    expect(pressAmountDigit("999", "9", 1000)).toBe("999");
  });

  it("не пропускает пустую, нулевую, дробнее тиына и больше доступного сумму", () => {
    expect(paymentAmountError("", 10000)).toBe("Введите сумму оплаты.");
    expect(paymentAmountError("0", 10000)).toBe("Сумма должна быть больше нуля.");
    expect(paymentAmountError("0.001", 10000)).toBe("Сумма указывается с точностью до тиына.");
    expect(paymentAmountError("150", 10000)).toBe("Доступно не более 100.");
    expect(paymentAmountError("1,5", 10000)).toBe("");
    expect(paymentAmountError("0.01", 1)).toBe("");
    expect(paymentAmountError("1", 10000)).toBe("");
  });
});
