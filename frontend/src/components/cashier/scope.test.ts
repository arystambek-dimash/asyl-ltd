import { beforeEach, describe, expect, it } from "vitest";
import type { Department, Me } from "@/lib/types";
import {
  ALL_DEPARTMENTS,
  DEPARTMENT_STORAGE_KEY,
  cashierName,
  departmentScope,
  readStoredDepartment,
  scopeLabel,
  storeDepartment,
} from "./scope";

const mill = { id: 1, code: "main", name: "Мельница", color: "#123456" };
const departments = [
  { ...mill, is_active: true, is_default: true, order_count: 0 },
  { id: 2, code: "bran", name: "Отруби", color: "#654321", is_active: true, is_default: false, order_count: 0 },
] as Department[];

function me(patch: Partial<Me> = {}): Me {
  return {
    id: 1,
    username: "kassa",
    is_client: false,
    is_superuser: false,
    permissions: [],
    position: null,
    client_id: null,
    sales_department: null,
    ...patch,
  };
}

describe("departmentScope", () => {
  it("locks a cashier to the department from the employee card", () => {
    expect(departmentScope(me({ sales_department: mill }))).toEqual({ assigned: mill, switchable: false });
  });
  it("lets staff without a department and superusers switch, starting from their own department", () => {
    expect(departmentScope(me())).toEqual({ assigned: null, switchable: true });
    expect(departmentScope(me({ is_superuser: true, sales_department: mill }))).toEqual({
      assigned: null,
      switchable: true,
    });
    expect(departmentScope(null).switchable).toBe(true);
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

describe("cashierName", () => {
  it("prefers the full name and falls back to the login", () => {
    expect(cashierName(me({ first_name: "Асель", last_name: "Нурланова" }))).toBe("Асель Нурланова");
    expect(cashierName(me({ first_name: "", last_name: "" }))).toBe("kassa");
    expect(cashierName(null)).toBe("");
  });
});

describe("stored department", () => {
  beforeEach(() => localStorage.clear());
  it("round-trips through localStorage, separately per user", () => {
    expect(readStoredDepartment()).toBeNull();
    storeDepartment("bran");
    expect(localStorage.getItem(DEPARTMENT_STORAGE_KEY)).toBe("bran");
    expect(readStoredDepartment()).toBe("bran");
    storeDepartment("main", 7);
    expect(localStorage.getItem(`${DEPARTMENT_STORAGE_KEY}:7`)).toBe("main");
    expect(readStoredDepartment(7)).toBe("main");
    expect(readStoredDepartment(8)).toBeNull();
  });
});
