import { describe, expect, it } from "vitest";
import {
  ORDER_MANUAL_STATUSES,
  ORDER_PUBLIC_STATUSES,
  orderStatusGroup,
  orderStatusLabel,
  orderStatusTone,
  translateOrderStatusMessage,
} from "@/lib/constants";

describe("order status presentation", () => {
  it("keeps completed loading separate from physical exit", () => {
    expect(orderStatusGroup("loaded")).toBe("loaded");
    expect(orderStatusLabel("loaded")).toBe("Готов к выезду");
    expect(orderStatusGroup("shipped")).toBe("shipped");
    expect(ORDER_PUBLIC_STATUSES).toContain("loaded");
  });

  it("does not expose loaded as a manual status override", () => {
    expect(ORDER_MANUAL_STATUSES).not.toContain("loaded");
  });

  it("gives every public status its own color", () => {
    const tones = ORDER_PUBLIC_STATUSES.map(orderStatusTone);
    expect(new Set(tones).size).toBe(ORDER_PUBLIC_STATUSES.length);
    expect(orderStatusTone("arrived")).toBe(orderStatusTone("confirmed"));
  });
});

describe("order journal messages", () => {
  it("keeps the backend text as is", () => {
    // Смена транспорта, отдела, частичное подтверждение и запрос ручной смены
    // пишутся событием "status" с payload {from, to}; журнал выводит текст бэка,
    // а не собирает «Статус заказа: from → to» из payload.
    expect(translateOrderStatusMessage("Вид транспорта изменён: Фура → Вагон")).toBe(
      "Вид транспорта изменён: Фура → Вагон",
    );
    expect(translateOrderStatusMessage("Заказ подтверждён: 8 из 10 меш.")).toBe("Заказ подтверждён: 8 из 10 меш.");
    expect(translateOrderStatusMessage("Запрос ручной смены статуса: На рассмотрении → Отгружено")).toBe(
      "Запрос ручной смены статуса: На рассмотрении → Отгружено",
    );
  });

  it("translates raw status codes left in old journal entries", () => {
    expect(translateOrderStatusMessage("Статус: pending → confirmed")).toBe(
      "Статус: На рассмотрении → Ожидает загрузки",
    );
  });
});
