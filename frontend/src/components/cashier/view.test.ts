import { describe, expect, it } from "vitest";
import { makeMe } from "@/test-utils/factories";
import { cashierPerms, hasHomeScreen, mobileMenu, resolveView } from "./view";

const all = cashierPerms(makeMe({ is_superuser: true }));
const viewer = cashierPerms(makeMe({ permissions: ["payments.view"] }));

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
    expect(resolveView(null, viewer, true)).toBe("transactions");
    expect(resolveView("home", viewer, true)).toBe("transactions");
  });
  it("opens POS only on phones and only with payments.create", () => {
    expect(resolveView("pos", all, true)).toBe("pos");
    expect(resolveView("pos", all, false)).toBe("overview");
    expect(resolveView("pos", viewer, true)).toBe("transactions");
    expect(resolveView("remote", all, true)).toBe("remote");
    expect(resolveView("remote", all, false)).toBe("overview");
    expect(resolveView("remote", viewer, true)).toBe("transactions");
  });
});

describe("hasHomeScreen", () => {
  it("counts POS as a section so a role that can only take payments keeps a home screen", () => {
    expect(hasHomeScreen(viewer)).toBe(false);
    const cashier = cashierPerms(makeMe({ permissions: ["payments.create"] }));
    expect(mobileMenu(cashier)).toEqual(["debts"]);
    expect(hasHomeScreen(cashier)).toBe(true);
    expect(resolveView(null, cashier, true)).toBe("home");
  });
});

describe("cashierPerms", () => {
  it("derives combined permissions", () => {
    const perms = cashierPerms(makeMe({ permissions: ["payments.create", "orders.view"] }));
    expect(perms.canDebtEntry).toBe(true);
    expect(mobileMenu(perms)).toEqual(["debts"]);
    expect(mobileMenu(all)).toEqual(["confirm", "debts", "transactions", "report"]);
  });
});
