import { describe, expect, it } from "vitest";

import { EVENT_TYPE_GROUPS, eventTypeMeta } from "./event-types";

describe("event types", () => {
  const entries = EVENT_TYPE_GROUPS.flatMap((group) => Object.entries(group.types));

  it("каждый тип встречается в одном разделе", () => {
    const codes = entries.map(([code]) => code);
    expect(new Set(codes).size).toBe(codes.length);
  });

  it("в фильтре нет двух одинаковых подписей", () => {
    const labels = entries.map(([, meta]) => meta.label);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("известный тип получает подпись, неизвестный — свой код", () => {
    expect(eventTypeMeta("order_backdated").label).toBe("Задним числом");
    expect(eventTypeMeta("some_new_event").label).toBe("some_new_event");
  });
});
