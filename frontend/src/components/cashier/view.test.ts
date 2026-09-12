import { describe, expect, it } from "vitest";
import type { Me } from "@/lib/types";
import { cashierPerms, defaultView, mobileMenu, resolveView } from "./view";

function me(permissions: string[], is_superuser = false): Me {
  return {
    id: 1,
    username: "u",
    is_client: false,
    is_superuser,
    permissions,
    position: null,
    client_id: null,
    sales_department: null,
  };
}
const all = cashierPerms(me([], true));
const viewer = cashierPerms(me(["payments.view"]));

describe("resolveView", () => {
  it("opens the mobile home and the desktop overview by default", () => {
    expect(resolveView(null, all, true)).toBe("home");
    expect(resolveView(null, all, false)).toBe("overview");
  });
  it("maps values foreign to the layout", () => {
    expect(resolveView("overview", all, true)).toBe("home");
    expect(resolveView("report", all, false)).toBe("overview");
    expect(resolveView("debts", all, false)).toBe("overview");
    expect(resolveView("home", all, false)).toBe("overview");
  });
  it("falls back when the screen is not allowed or unknown", () => {
    expect(resolveView("confirm", viewer, false)).toBe("transactions");
    expect(resolveView("garbage", all, true)).toBe("home");
  });
  it("skips the home screen when only one section is available", () => {
    expect(mobileMenu(viewer)).toEqual(["transactions"]);
    expect(defaultView(viewer, true)).toBe("transactions");
    expect(resolveView("home", viewer, true)).toBe("transactions");
  });
  it("opens POS only on phones and only with payments.create", () => {
    expect(resolveView("pos", all, true)).toBe("pos");
    expect(resolveView("pos", all, false)).toBe("overview");
    expect(resolveView("pos", viewer, true)).toBe("transactions");
  });
});

describe("cashierPerms", () => {
  it("derives combined permissions", () => {
    const perms = cashierPerms(me(["payments.create", "orders.view"]));
    expect(perms.canDebtEntry).toBe(true);
    expect(perms.canReviewOrders).toBe(false);
    expect(mobileMenu(perms)).toEqual(["debts"]);
    expect(mobileMenu(all)).toEqual(["confirm", "debts", "transactions", "journal", "report"]);
  });
});
