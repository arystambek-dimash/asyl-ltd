import { beforeEach, describe, expect, it } from "vitest";
import type { Department } from "@/lib/types";
import { makeMe } from "@/test-utils/factories";
import {
  ALL_DEPARTMENTS,
  canOpenQueueOrder,
  cashierName,
  departmentScope,
  queueDepartment,
  readStoredDepartment,
  scopeLabel,
  storeDepartment,
} from "./scope";

const mill = { id: 1, code: "main", name: "Мельница", color: "#123456" };
const departments = [
  { ...mill, is_active: true, is_default: true, order_count: 0 },
  { id: 2, code: "bran", name: "Отруби", color: "#654321", is_active: true, is_default: false, order_count: 0 },
] as Department[];

describe("departmentScope", () => {
  it("locks a cashier to the department from the employee card", () => {
    expect(departmentScope(makeMe({ sales_department: mill }))).toEqual({ assigned: mill });
  });
  it("lets staff without a department switch", () => {
    expect(departmentScope(makeMe())).toEqual({ assigned: null });
    expect(departmentScope(null)).toEqual({ assigned: null });
  });
});

describe("scopeLabel", () => {
  it("names the selected department, all departments or the locked one", () => {
    expect(scopeLabel(ALL_DEPARTMENTS, departments, null)).toEqual({ name: "Все отделы", color: null });
    expect(scopeLabel("bran", departments, null)).toEqual({ name: "Отруби", color: "#654321" });
    expect(scopeLabel("gone", departments, null)).toEqual({ name: "Отдел", color: null });
    expect(scopeLabel("bran", departments, mill)).toEqual({ name: "Мельница", color: "#123456" });
  });
});

describe("queueDepartment", () => {
  it("never narrows the shared queue to the department from the employee card", () => {
    expect(queueDepartment({ assigned: mill }, "main")).toBeNull();
    expect(queueDepartment({ assigned: mill }, "bran")).toBeNull();
  });
  it("keeps the department explicitly chosen by staff without one", () => {
    expect(queueDepartment({ assigned: null }, "bran")).toBe("bran");
    expect(queueDepartment({ assigned: null }, ALL_DEPARTMENTS)).toBeNull();
  });
});

describe("canOpenQueueOrder", () => {
  it("opens only orders of the cashier's own department", () => {
    expect(canOpenQueueOrder(mill, "main")).toBe(true);
    expect(canOpenQueueOrder(mill, "bran")).toBe(false);
    expect(canOpenQueueOrder(null, "bran")).toBe(true);
  });
});

describe("cashierName", () => {
  it("prefers the full name and falls back to the login", () => {
    expect(cashierName(makeMe({ first_name: "Асель", last_name: "Нурланова" }))).toBe("Асель Нурланова");
    expect(cashierName(makeMe({ username: "kassa", first_name: "", last_name: "" }))).toBe("kassa");
    expect(cashierName(null)).toBe("");
  });
});

describe("stored department", () => {
  beforeEach(() => localStorage.clear());
  it("round-trips through localStorage, separately per user", () => {
    expect(readStoredDepartment()).toBeNull();
    storeDepartment("bran");
    expect(localStorage.getItem("asyl_cashier_department")).toBe("bran");
    expect(readStoredDepartment()).toBe("bran");
    storeDepartment("main", 7);
    expect(localStorage.getItem("asyl_cashier_department:7")).toBe("main");
    expect(readStoredDepartment(7)).toBe("main");
    expect(readStoredDepartment(8)).toBeNull();
  });
});
