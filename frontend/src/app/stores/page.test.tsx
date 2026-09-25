import { render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import StoresPage from "./page";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: { permissions: ["stores.view"] }, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("@/components/require-perm", () => import("@/test-utils/require-perm"));
vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mocks.get(...args) },
  apiError: (e: unknown) => (e as Error).message,
  isCanceledRequest: () => false,
}));

beforeEach(() => {
  mocks.get.mockReset();
});

it("counts all stores from the server total, not just the loaded page", async () => {
  mocks.get.mockResolvedValue({
    data: {
      results: [{ id: 1, name: "Магазин у дома", client: 7, client_name: "Клиент", address: "", phone: "" }],
      count: 120,
      next: "next",
      previous: null,
    },
  });
  render(<StoresPage />);
  expect(await screen.findByText("Магазин у дома")).toBeInTheDocument();
  const stat = screen.getByText("Всего магазинов").closest("section") as HTMLElement;
  expect(within(stat).getByText("120")).toBeInTheDocument();
});

it("shows a load error with retry instead of an empty list", async () => {
  mocks.get.mockRejectedValue(new Error("Магазины не загрузились"));
  render(<StoresPage />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Магазины не загрузились");
  expect(screen.getByRole("button", { name: /Повторить/ })).toBeInTheDocument();
});
